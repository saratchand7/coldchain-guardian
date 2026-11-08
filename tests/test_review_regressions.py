import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from database import initialize, connect, transaction
from services.assessment import assess_shipment
from services.engine import detect_excursions, detect_gaps, check_excursion_duration
from services.corrective_actions import update_action
from services.excursion_status import update_excursion_status
from services.lifecycle import add_custody_event
from services.validation import validate

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

def add_humidity_sensor(db, sensor='H', container='C'):
    db.execute('INSERT INTO Sensor VALUES(?,?,?,?,?)',
               (sensor,container,'HUM',T0.isoformat(),'VALID'))

def add_complete_dual_readings(db,end=20,temp_value=5,hum_value=50):
    add_humidity_sensor(db)
    for minute in range(0,end+1,5):
        add_reading(db,minute,temp_value)
        add_reading(db,minute,hum_value,sensor='H',unit='%')

def test_no_readings_and_incomplete_sensor_never_count_as_compliant(tmp_path):
    p=setup_db(tmp_path/'empty.db',completed=True);db=connect(p)
    outcome=assess_shipment(db,'S')
    assert outcome['outcome']=='INCOMPLETE_EVIDENCE'
    assert outcome['expected_readings']==10 and outcome['trusted_readings']==0
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

def test_exact_policy_duration_boundaries(tmp_path):
    p=setup_db(tmp_path/'boundaries.db');db=connect(p)
    for minute,value in ((0,9),(5,9),(10,5)):add_reading(db,minute,value)
    db.commit();detect_excursions(db,'S',(T0+timedelta(minutes=10)).isoformat())
    event=db.execute("SELECT duration_minutes,severity FROM Excursion WHERE kind='TEMP'").fetchone()
    assert event['duration_minutes']==10 and event['severity']=='LOW' # equal allowance
    db.execute("UPDATE ThresholdPolicy SET max_excursion_minutes=9");db.commit()
    detect_excursions(db,'S',(T0+timedelta(minutes=10)).isoformat())
    assert db.execute("SELECT severity FROM Excursion WHERE kind='TEMP'").fetchone()[0]=='MEDIUM'
    db.close()

def test_temperature_humidity_and_invalid_context_checks(tmp_path):
    p=setup_db(tmp_path/'contexts.db',completed=True);db=connect(p)
    db.execute("INSERT INTO Sensor VALUES('H','C','HUM','2099-01-01T00:00:00+00:00','VALID')")
    db.execute("INSERT INTO SensorReading VALUES('BADUNIT','X','S',?,5,'%','VALID')",(T0.isoformat(),))
    db.execute("INSERT INTO SensorReading VALUES('H1','H','S',?,70,'%','VALID')",(T0.isoformat(),))
    db.commit()
    result=validate(p)
    names={x['check']:x for x in result['checks']}
    assert not names['sensor_kind_unit_semantics']['passed']
    assert not names['valid_calibration_is_current_at_readings']['passed']
    db.close()

def test_tampered_ids_wrong_recovery_and_deleted_source_fail_independently(tmp_path):
    p=setup_db(tmp_path/'tamper.db');db=connect(p)
    for minute,value in ((0,9),(5,9),(10,5)):add_reading(db,minute,value)
    db.commit();detect_excursions(db,'S',(T0+timedelta(minutes=10)).isoformat())
    event=db.execute("SELECT * FROM Excursion WHERE kind='TEMP'").fetchone();evidence=json.loads(event['evidence_json'])
    evidence['recovery_reading_id']='MISSING';db.execute('UPDATE Excursion SET evidence_json=? WHERE excursion_id=?',(json.dumps(evidence),event['excursion_id']));db.commit()
    with pytest.raises(ValueError,match='Recovery'):check_excursion_duration(db,event['excursion_id'])
    db.execute("DELETE FROM SensorReading WHERE reading_id='R-S-X-0'");db.commit()
    with pytest.raises(ValueError,match='missing'):check_excursion_duration(db,event['excursion_id'])
    db.close()

def test_custody_history_actions_and_exception_closure_rules(tmp_path,monkeypatch):
    p=setup_db(tmp_path/'history.db',completed=True)
    import services.corrective_actions as actions,services.excursion_status as statuses,services.lifecycle as lifecycle
    monkeypatch.setattr(actions,'transaction',lambda path=None:transaction(p));monkeypatch.setattr(statuses,'transaction',lambda path=None:transaction(p));monkeypatch.setattr(lifecycle,'transaction',lambda path=None:transaction(p))
    with transaction(p) as db:
        db.execute("INSERT INTO Excursion VALUES('E','S',NULL,'ROUTE_DELAY',?,NULL,0,'MEDIUM','OPEN','{}')",(T0.isoformat(),))
        db.execute("INSERT INTO CorrectiveAction VALUES('A','E','Team','REVIEW','review',?,NULL,'OPEN','')",(T0.isoformat(),))
    update_action('A','IN_PROGRESS');update_action('A','COMPLETED','Evidence reviewed')
    with pytest.raises(ValueError):update_action('A','COMPLETED','Replacement')
    update_excursion_status('E','ACKNOWLEDGED','Reviewed');update_excursion_status('E','CLOSED','Action evidence A')
    add_custody_event('S','Origin','DEPARTURE','Origin',T0.isoformat())
    add_custody_event('S','Destination','DELIVERY','Destination',(T0+timedelta(minutes=20)).isoformat())
    db=connect(p)
    assert db.execute("SELECT COUNT(*) FROM CorrectiveActionEvent WHERE action_id='A'").fetchone()[0]>=2
    assert db.execute("SELECT COUNT(*) FROM ExcursionStatusEvent WHERE excursion_id='E'").fetchone()[0]==2
    db.execute("INSERT INTO Sensor VALUES('H','C','HUM','2026-01-01T00:00:00+00:00','VALID')")
    for minute in range(0,21,5):add_reading(db,minute,50,sensor='H',unit='%')
    db.commit();detect_excursions(db,'S',(T0+timedelta(minutes=20)).isoformat())
    assert validate(p)['status']=='PASS'
    db.close()

def test_compliance_requires_current_successful_detection(tmp_path):
    p=setup_db(tmp_path/'fresh.db',completed=True);db=connect(p)
    add_complete_dual_readings(db,temp_value=9);db.commit()
    before=assess_shipment(db,'S')
    assert before['outcome']=='INCOMPLETE_EVIDENCE' and 'ANALYSIS_NOT_RUN' in before['issues']
    detect_excursions(db,'S')
    assert assess_shipment(db,'S')['outcome']=='NON_COMPLIANT'
    db.execute("UPDATE SensorReading SET value=5 WHERE sensor_id='X'");db.commit();detect_excursions(db,'S')
    assert assess_shipment(db,'S')['outcome']=='COMPLIANT'
    validation=validate(p)
    assert validation['status']=='PASS'
    assert next(x for x in validation['checks'] if x['check']=='excursion_revision_snapshot_hash_integrity')['passed']
    add_reading(db,17,9);db.commit()
    assert 'ANALYSIS_STALE' in assess_shipment(db,'S')['issues']
    db.execute("INSERT INTO ThresholdPolicy VALUES('POL2','P','2025-01-01T00:00:00+00:00',NULL,2,8,30,60,10)");db.commit()
    with pytest.raises(ValueError):detect_excursions(db,'S')
    assert db.execute("SELECT status FROM DetectionAnalysis WHERE shipment_id='S'").fetchone()[0]=='FAILED'
    assert assess_shipment(db,'S')['outcome']=='INCOMPLETE_EVIDENCE';db.close()

def test_delayed_and_active_overdue_windows_continue_past_plan(tmp_path):
    p=setup_db(tmp_path/'delayed.db',completed=True);db=connect(p);end40=T0+timedelta(minutes=40)
    db.execute("UPDATE Shipment SET planned_arrival=?,actual_arrival=? WHERE shipment_id='S'",
               ((T0+timedelta(minutes=20)).isoformat(),end40.isoformat()))
    db.execute("UPDATE ContainerAssignment SET ended_at=? WHERE shipment_id='S'",(end40.isoformat(),));add_humidity_sensor(db)
    for minute in range(0,41,5):
        add_reading(db,minute,5 if minute<=20 else 9)
        if minute!=30:add_reading(db,minute,50,sensor='H',unit='%')
    db.commit();detect_excursions(db,'S')
    assert db.execute("SELECT 1 FROM Excursion WHERE kind='TEMP' AND started_at=?",((T0+timedelta(minutes=25)).isoformat(),)).fetchone()
    assert db.execute("SELECT 1 FROM Excursion WHERE kind='SENSOR_GAP' AND sensor_id='H' AND started_at=?",((T0+timedelta(minutes=30)).isoformat(),)).fetchone()
    assert assess_shipment(db,'S')['outcome']=='INCOMPLETE_EVIDENCE';db.close()
    p=setup_db(tmp_path/'overdue.db');db=connect(p);add_humidity_sensor(db)
    for minute in range(0,41,5):
        add_reading(db,minute,5 if minute<=20 else 9);add_reading(db,minute,50,sensor='H',unit='%')
    db.commit();detect_excursions(db,'S',(T0+timedelta(minutes=40)).isoformat())
    assert db.execute("SELECT 1 FROM Excursion WHERE kind='TEMP' AND started_at=?",((T0+timedelta(minutes=25)).isoformat(),)).fetchone()
    assert db.execute("SELECT observed_through FROM MonitoringWindow WHERE shipment_id='S'").fetchone()[0]==(T0+timedelta(minutes=40)).isoformat();db.close()

def test_required_modalities_and_timed_replacements(tmp_path):
    p=setup_db(tmp_path/'missing-hum.db',completed=True);db=connect(p)
    for minute in range(0,21,5):add_reading(db,minute,5)
    db.commit();detect_excursions(db,'S');result=assess_shipment(db,'S')
    assert result['outcome']=='INCOMPLETE_EVIDENCE' and 'MISSING_REQUIRED_MODALITY:HUM' in result['issues'];db.close()
    p=setup_db(tmp_path/'replacement.db',completed=True);db=connect(p)
    db.execute("UPDATE ContainerAssignment SET ended_at=?",((T0+timedelta(minutes=10)).isoformat(),));add_humidity_sensor(db)
    db.execute("INSERT INTO Container VALUES('C2','box','AVAILABLE')")
    db.execute("INSERT INTO Sensor VALUES('X2','C2','TEMP',?,'VALID')",(T0.isoformat(),))
    db.execute("INSERT INTO Sensor VALUES('H2','C2','HUM',?,'VALID')",(T0.isoformat(),))
    db.execute("INSERT INTO ContainerAssignment VALUES('AS2','C2','S',?,?)",((T0+timedelta(minutes=15)).isoformat(),(T0+timedelta(minutes=20)).isoformat()))
    for minute in (0,5,10):add_reading(db,minute,5);add_reading(db,minute,50,sensor='H',unit='%')
    for minute in (15,20):add_reading(db,minute,5,sensor='X2');add_reading(db,minute,50,sensor='H2',unit='%')
    db.commit();detect_excursions(db,'S');assert assess_shipment(db,'S')['outcome']=='COMPLIANT';db.close()

def _replacement_fixture(path,replacement_temp_values):
    p=setup_db(path,completed=True);db=connect(p)
    db.execute("UPDATE Product SET max_excursion_minutes=15")
    db.execute("UPDATE ThresholdPolicy SET max_excursion_minutes=15")
    db.execute("UPDATE ContainerAssignment SET ended_at=?",((T0+timedelta(minutes=15)).isoformat(),))
    add_humidity_sensor(db)
    db.execute("INSERT INTO Container VALUES('C2','box','AVAILABLE')")
    db.execute("INSERT INTO Sensor VALUES('X2','C2','TEMP',?,'VALID')",(T0.isoformat(),))
    db.execute("INSERT INTO Sensor VALUES('H2','C2','HUM',?,'VALID')",(T0.isoformat(),))
    db.execute("INSERT INTO ContainerAssignment VALUES('AS2','C2','S',?,?)",
               ((T0+timedelta(minutes=15)).isoformat(),(T0+timedelta(minutes=20)).isoformat()))
    for minute in (0,5,10):
        add_reading(db,minute,9);add_reading(db,minute,50,sensor='H',unit='%')
    for minute,value in replacement_temp_values:
        add_reading(db,minute,value,sensor='X2');add_reading(db,minute,50,sensor='H2',unit='%')
    db.commit();detect_excursions(db,'S')
    return p,db

def test_sensor_handoff_preserves_condition_duration_and_traceability(tmp_path):
    p,db=_replacement_fixture(tmp_path/'handoff.db',((15,9),(20,9)))
    events=db.execute("SELECT * FROM Excursion WHERE kind='TEMP' AND json_extract(evidence_json,'$.condition_active')=1").fetchall()
    assert len(events)==1 and events[0]['duration_minutes']==20 and events[0]['severity']=='MEDIUM'
    evidence=json.loads(events[0]['evidence_json'])
    assert evidence['sensor_ids']==['X','X2']
    assert {x['reading_id'] for x in evidence['readings']}=={f'R-S-X-{m}' for m in (0,5,10)}|{f'R-S-X2-{m}' for m in (15,20)}
    assert assess_shipment(db,'S')['outcome']=='NON_COMPLIANT'
    assert validate(p)['status']=='PASS';db.close()

    control=setup_db(tmp_path/'single.db',completed=True);db=connect(control)
    db.execute("UPDATE Product SET max_excursion_minutes=15");db.execute("UPDATE ThresholdPolicy SET max_excursion_minutes=15")
    add_humidity_sensor(db)
    for minute in range(0,21,5):add_reading(db,minute,9);add_reading(db,minute,50,sensor='H',unit='%')
    db.commit();detect_excursions(db,'S')
    assert db.execute("SELECT duration_minutes FROM Excursion WHERE kind='TEMP'").fetchone()[0]==20
    assert assess_shipment(db,'S')['outcome']=='NON_COMPLIANT';db.close()

def test_sensor_handoff_safe_recovery_and_unknown_gap(tmp_path):
    p,db=_replacement_fixture(tmp_path/'recovery.db',((15,5),(20,5)))
    event=db.execute("SELECT * FROM Excursion WHERE kind='TEMP'").fetchone();evidence=json.loads(event['evidence_json'])
    assert event['duration_minutes']==15 and event['severity']=='LOW'
    assert evidence['recovery_reading_id']=='R-S-X2-15' and evidence['sensor_ids']==['X','X2']
    assert assess_shipment(db,'S')['outcome']=='COMPLIANT';db.close()

    p,db=_replacement_fixture(tmp_path/'unknown.db',((20,9),))
    event=db.execute("SELECT * FROM Excursion WHERE kind='TEMP'").fetchone()
    outcome=assess_shipment(db,'S')
    assert event['duration_minutes']==15 and event['severity']=='LOW'
    assert outcome['outcome']=='INCOMPLETE_EVIDENCE' and 'INCOMPLETE_MODALITY_COVERAGE:TEMP' in outcome['issues']
    db.close()

def test_impossible_valid_value_and_late_gap_reconciliation(tmp_path):
    p=setup_db(tmp_path/'physical.db',completed=True);db=connect(p);add_complete_dual_readings(db)
    db.execute("UPDATE SensorReading SET value=150,quality='VALID' WHERE sensor_id='X' AND recorded_at=?",((T0+timedelta(minutes=10)).isoformat(),));db.commit();detect_excursions(db,'S')
    assert db.execute("SELECT 1 FROM Excursion WHERE kind='INVALID_PHYSICAL_RANGE'").fetchone()
    assert not db.execute("SELECT 1 FROM Excursion WHERE kind='TEMP'").fetchone();db.close()
    p=setup_db(tmp_path/'late.db',completed=True);db=connect(p);add_humidity_sensor(db)
    for minute in (0,5,15,20):add_reading(db,minute,5)
    for minute in range(0,21,5):add_reading(db,minute,50,sensor='H',unit='%')
    db.commit();detect_excursions(db,'S');gap=db.execute("SELECT * FROM Excursion WHERE kind='SENSOR_GAP' AND sensor_id='X'").fetchone()
    add_reading(db,10,5);db.commit();detect_excursions(db,'S')
    evidence=json.loads(db.execute('SELECT evidence_json FROM Excursion WHERE excursion_id=?',(gap['excursion_id'],)).fetchone()[0])
    assert evidence['condition_active'] is False and evidence['condition_resolution_reason']=='LATE_OR_CURRENT_EVIDENCE_REMOVED_CONDITION'
    assert db.execute('SELECT COUNT(*) FROM ExcursionRevision WHERE excursion_id=?',(gap['excursion_id'],)).fetchone()[0]>=2
    assert assess_shipment(db,'S')['outcome']=='COMPLIANT';db.close()

def test_failed_generator_attempt_replaces_complete_status(tmp_path):
    root=__import__('pathlib').Path(__file__).resolve().parents[1];data=tmp_path/'run';env=os.environ.copy()
    env.update({'COLDCHAIN_DB':str(data/'coldchain.db'),'COLDCHAIN_GENERATED_DIR':str(data/'generated'),
                'COLDCHAIN_MANIFEST':str(data/'source_manifest.json'),'COLDCHAIN_REPORTS_DIR':str(data/'reports')})
    subprocess.run([sys.executable,'generate_data.py','--scale','starter'],cwd=root,env=env,check=True,capture_output=True,text=True)
    failed=subprocess.run([sys.executable,'generate_data.py','--as-of','not-a-timestamp'],cwd=root,env=env,capture_output=True,text=True)
    status=json.loads((data/'run_status.json').read_text())
    assert failed.returncode!=0 and status['status']=='FAILED' and status['stage']=='ARGUMENT_VALIDATION'

@pytest.mark.parametrize('temperature,expected', [(9, 'NON_COMPLIANT'), (5, 'COMPLIANT')])
def test_concurrent_containers_are_assessed_separately(tmp_path, temperature, expected):
    path = setup_db(tmp_path/'concurrent.db', completed=True)
    with transaction(path) as db:
        add_complete_dual_readings(db)
        db.execute("INSERT INTO Container VALUES('C2','box','AVAILABLE')")
        db.execute("INSERT INTO ContainerAssignment SELECT 'AS2','C2',shipment_id,started_at,ended_at "
                   "FROM ContainerAssignment WHERE assignment_id='AS'")
        db.execute("INSERT INTO Sensor VALUES('X2','C2','TEMP',?,'VALID')", (T0.isoformat(),))
        add_humidity_sensor(db, 'H2', 'C2')
        for minute in range(0, 21, 5):
            add_reading(db, minute, temperature, sensor='X2')
            add_reading(db, minute, 50, sensor='H2', unit='%')
    db = connect(path)
    detect_excursions(db, 'S')
    result = assess_shipment(db, 'S')
    assert result['outcome'] == expected
    assert result['expected_readings'] == result['trusted_readings'] == 20
    if temperature == 9:
        event = db.execute("SELECT * FROM Excursion WHERE kind='TEMP'").fetchone()
        assert event['duration_minutes'] == 20
        assert {r['container_id'] for r in json.loads(event['evidence_json'])['readings']} == {'C2'}
    assert validate(path)['status'] == 'PASS'
    db.close()


def test_overlapping_handoff_observations_disagree_on_condition(tmp_path):
    _, db = _replacement_fixture(tmp_path/'conflict.db', ((15, 5), (20, 5)))
    add_reading(db, 15, 9)
    db.commit()
    detect_excursions(db, 'S')
    result = assess_shipment(db, 'S')
    assert result['outcome'] == 'INCOMPLETE_EVIDENCE'
    assert 'CONFLICTING_MODALITY_OBSERVATIONS:TEMP' in result['issues']
    db.close()
