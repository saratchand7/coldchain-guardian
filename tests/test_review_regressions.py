import json
from datetime import datetime, timedelta, timezone

import pytest

from database import initialize, connect, transaction
from services.assessment import assess_shipment
from services.engine import detect_excursions, detect_gaps, check_excursion_duration

T0=datetime(2026,1,1,tzinfo=timezone.utc)

def setup_db(path,completed=False):
    initialize(path)
    end=T0+timedelta(minutes=20)
    with transaction(path) as db:
        db.execute("INSERT INTO Product VALUES('P','Product',2,8,30,60,10)")
        db.execute("INSERT INTO Shipment VALUES('S','P','A','B',?,?,?,?,'DELIVERED')" if completed else
                   "INSERT INTO Shipment VALUES('S','P','A','B',?,?,?,NULL,'IN_TRANSIT')",
                   (T0.isoformat(),end.isoformat(),T0.isoformat(),end.isoformat()) if completed else
                   (T0.isoformat(),end.isoformat(),T0.isoformat()))
        db.execute("INSERT INTO ThresholdPolicy VALUES('POL','P','2025-01-01T00:00:00+00:00',NULL,2,8,30,60,10)")
        db.execute("INSERT INTO Container VALUES('C','box',?)",('AVAILABLE' if completed else 'IN_USE',))
        db.execute("INSERT INTO Sensor VALUES('X','C','TEMP','2026-01-01T00:00:00+00:00','VALID')")
        db.execute("INSERT INTO ContainerAssignment VALUES('AS','C','S',?,?)",(T0.isoformat(),end.isoformat() if completed else None))
    return path

def add_reading(db,minute,value,quality='VALID',sensor='X',shipment='S',unit='C'):
    at=(T0+timedelta(minutes=minute)).isoformat()
    db.execute('INSERT INTO SensorReading VALUES(?,?,?,?,?,?,?)',(f'R-{shipment}-{sensor}-{minute}',sensor,shipment,at,value,unit,quality))
    return at

def test_no_readings_and_incomplete_sensor_never_count_as_compliant(tmp_path):
    p=setup_db(tmp_path/'empty.db',completed=True);db=connect(p)
    outcome=assess_shipment(db,'S')
    assert outcome['outcome']=='INCOMPLETE_EVIDENCE'
    assert outcome['expected_readings']==5 and outcome['trusted_readings']==0
    db.close()

def test_boundary_total_off_grid_and_active_as_of_gaps(tmp_path):
    p=setup_db(tmp_path/'gaps.db',completed=True);db=connect(p)
    add_reading(db,10,5);add_reading(db,15,5)
    gaps=detect_gaps(db,'S','X')
    assert [(g['start'],g['missing_count']) for g in gaps if g['missing_count']]==[
        (T0.isoformat(),2),( (T0+timedelta(minutes=20)).isoformat(),1)]
    add_reading(db,2,5)
    assert any(g['classification']=='OFF_GRID_OBSERVATION' for g in detect_gaps(db,'S','X'))
    db.close()
    p=setup_db(tmp_path/'active.db');db=connect(p)
    add_reading(db,0,5);add_reading(db,5,5)
    gaps=detect_gaps(db,'S','X',window_start=T0.isoformat(),window_end=(T0+timedelta(minutes=15)).isoformat())
    assert sum(g['missing_count'] for g in gaps)==2
    assert all(g['classification']=='STALE_OBSERVATION' for g in gaps)
    db.close()

def test_gap_censors_excursion_before_safe_reading(tmp_path):
    p=setup_db(tmp_path/'censor.db');db=connect(p)
    db.execute('UPDATE Shipment SET planned_arrival=? WHERE shipment_id=\'S\'',((T0+timedelta(minutes=120)).isoformat(),));db.commit()
    add_reading(db,0,9);add_reading(db,60,5)
    db.commit()
    detect_excursions(db,'S',(T0+timedelta(minutes=60)).isoformat())
    event=db.execute("SELECT * FROM Excursion WHERE kind='TEMP'").fetchone()
    evidence=json.loads(event['evidence_json'])
    assert event['duration_minutes']==5
    assert event['ended_at']==(T0+timedelta(minutes=5)).isoformat()
    assert evidence['censor_reason']=='GAP_OR_INVALID_OBSERVATION'
    db.close()

def test_open_excursion_revises_incrementally_and_replay_is_idempotent(tmp_path):
    p=setup_db(tmp_path/'revision.db');db=connect(p)
    add_reading(db,0,9);add_reading(db,5,9)
    db.commit()
    ids=detect_excursions(db,'S',(T0+timedelta(minutes=10)).isoformat())
    event=db.execute("SELECT * FROM Excursion WHERE kind='TEMP'").fetchone()
    assert event['duration_minutes']==10 and event['severity']=='LOW'
    identity=event['excursion_id']
    for minute,value in ((10,9),(15,9),(20,5)):add_reading(db,minute,value)
    db.commit()
    detect_excursions(db,'S',(T0+timedelta(minutes=20)).isoformat())
    event=db.execute('SELECT * FROM Excursion WHERE excursion_id=?',(identity,)).fetchone()
    revisions=db.execute('SELECT COUNT(*) FROM ExcursionRevision WHERE excursion_id=?',(identity,)).fetchone()[0]
    assert event['duration_minutes']==20 and event['ended_at']==(T0+timedelta(minutes=20)).isoformat()
    assert event['severity']=='MEDIUM' and revisions==2
    detect_excursions(db,'S',(T0+timedelta(minutes=20)).isoformat())
    assert db.execute('SELECT COUNT(*) FROM ExcursionRevision WHERE excursion_id=?',(identity,)).fetchone()[0]==2
    data=json.loads(event['evidence_json']);data['readings'][0]['at']='2026-01-02T00:00:00+00:00'
    db.execute('UPDATE Excursion SET evidence_json=? WHERE excursion_id=?',(json.dumps(data),identity));db.commit()
    with pytest.raises(ValueError,match='timestamp'):
        check_excursion_duration(db,identity)
    db.close()
