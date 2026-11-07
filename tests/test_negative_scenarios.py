import json
from datetime import datetime,timedelta,timezone
import pytest
from database import initialize,connect,transaction
from services.engine import detect_excursions,validate_policy_product,check_excursion_duration,flag_policy_mismatch
from services.lifecycle import transition_shipment,add_custody_event

def fixture_db(p):
 initialize(p)
 with transaction(p) as db:
  db.execute("INSERT INTO Product VALUES('P','Product',2,8,30,60,10)")
  db.execute("INSERT INTO Product VALUES('Q','Other',15,25,30,65,30)")
  db.execute("INSERT INTO Shipment VALUES('S','P','A','B','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00',NULL,NULL,'IN_TRANSIT')")
  db.execute("INSERT INTO Shipment VALUES('S2','P','A','B','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00',NULL,NULL,'CREATED')")
  db.execute("INSERT INTO ThresholdPolicy VALUES('PP','P','2025-01-01T00:00:00+00:00',NULL,2,8,30,60,10)")
  db.execute("INSERT INTO ThresholdPolicy VALUES('PQ','Q','2025-01-01T00:00:00+00:00',NULL,15,25,30,65,30)")
  db.execute("INSERT INTO Container VALUES('C','box','IN_USE')")
  db.execute("INSERT INTO Sensor VALUES('X','C','TEMP','2026-01-01T00:00:00+00:00','VALID')")
  db.execute("INSERT INTO ContainerAssignment VALUES('AS','C','S','2026-01-01T00:00:00+00:00',NULL)")
 return p

def test_wrong_product_policy_is_rejected_and_flagged(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'p.db');db=connect(p)
 with pytest.raises(ValueError,match='POLICY_PRODUCT_MISMATCH'): validate_policy_product(db,'S','PQ')
 db.close()
 import services.engine as engine
 monkeypatch.setattr(engine,'transaction',lambda:transaction(p))
 assert flag_policy_mismatch('S','PQ')
 db=connect(p);assert db.execute("SELECT kind FROM Excursion WHERE kind='POLICY_PRODUCT_MISMATCH'").fetchone()[0]=='POLICY_PRODUCT_MISMATCH';db.close()

def test_delivered_before_departure_is_rejected_and_not_recorded(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'l.db');monkeypatch.setattr('services.lifecycle.transaction',lambda:transaction(p))
 with pytest.raises(ValueError,match='INVALID_SHIPMENT_TIMELINE'): transition_shipment('S2','DELIVERED')
 db=connect(p);assert db.execute("SELECT status FROM Shipment WHERE shipment_id='S2'").fetchone()[0]=='CREATED'
 assert db.execute("SELECT COUNT(*) FROM LifecycleEvent WHERE shipment_id='S2'").fetchone()[0]==0
 assert db.execute("SELECT kind FROM Excursion WHERE shipment_id='S2'").fetchone()[0]=='INVALID_SHIPMENT_TIMELINE';db.close()

def test_physical_range_is_exception_not_product_excursion(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'r.db')
 with transaction(p) as db: db.execute("INSERT INTO SensorReading VALUES('BAD','X','S','2026-01-01T01:00:00+00:00',150,'C','INVALID_PHYSICAL_RANGE')")
 monkeypatch.setattr('services.engine.transaction',lambda:transaction(p))
 db=connect(p);ids=detect_excursions(db,'S');row=db.execute('SELECT kind FROM Excursion WHERE excursion_id=?',(ids[0],)).fetchone();assert row['kind']=='INVALID_PHYSICAL_RANGE';db.close()

def test_sensor_gap_persists_exception_action_and_notification(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'g.db');t=datetime(2026,1,1,tzinfo=timezone.utc)
 with transaction(p) as db:
  for n in (0,4):db.execute('INSERT INTO SensorReading VALUES(?,?,?,?,?,?,?)',(f'R{n}','X','S',(t+timedelta(minutes=5*n)).isoformat(),5,'C','VALID'))
 monkeypatch.setattr('services.engine.transaction',lambda:transaction(p))
 db=connect(p);detect_excursions(db,'S')
 assert db.execute("SELECT COUNT(*) FROM Excursion WHERE kind='SENSOR_GAP'").fetchone()[0]==1
 assert db.execute("SELECT COUNT(*) FROM CorrectiveAction").fetchone()[0]==1
 assert db.execute("SELECT COUNT(*) FROM Notification").fetchone()[0]==1;db.close()

def test_duration_undercount_recalculated_and_flagged(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'d.db');t=datetime(2026,1,1,tzinfo=timezone.utc)
 evidence={'readings':[{'reading_id':'R0','at':t.isoformat()},{'reading_id':'R1','at':(t+timedelta(minutes=5)).isoformat()}],'recovery_reading_id':'R2'}
 with transaction(p) as db:
  for n in range(3):db.execute('INSERT INTO SensorReading VALUES(?,?,?,?,?,?,?)',(f'R{n}','X','S',(t+timedelta(minutes=5*n)).isoformat(),9 if n<2 else 5,'C','VALID'))
  db.execute("INSERT INTO Excursion VALUES('E','S','X','TEMP',?,NULL,5,'LOW','OPEN',?)",(t.isoformat(),json.dumps(evidence)))
 monkeypatch.setattr('services.engine.transaction',lambda:transaction(p))
 db=connect(p);result=check_excursion_duration(db,'E');assert result['flagged'] and result['recalculated_duration_minutes']==10
 assert db.execute("SELECT COUNT(*) FROM Excursion WHERE kind='DURATION_UNDERCOUNT'").fetchone()[0]==1;db.close()

def test_calibration_failure_creates_critical_exception(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'c.db')
 with transaction(p) as db:db.execute("UPDATE Sensor SET calibration_status='FAILED' WHERE sensor_id='X'")
 monkeypatch.setattr('services.engine.transaction',lambda:transaction(p))
 db=connect(p);detect_excursions(db,'S');row=db.execute("SELECT severity FROM Excursion WHERE kind='CALIBRATION_FAILURE'").fetchone();assert row['severity']=='CRITICAL';db.close()

def test_custody_events_are_timezone_aware_and_chronological(tmp_path,monkeypatch):
 p=fixture_db(tmp_path/'cst.db');monkeypatch.setattr('services.lifecycle.transaction',lambda:transaction(p))
 add_custody_event('S','Carrier A','HANDOFF','Hub 1','2026-01-01T01:00:00+01:00')
 with pytest.raises(ValueError,match='strictly chronological'):add_custody_event('S','Carrier B','HANDOFF','Hub 2','2025-12-31T23:30:00+00:00')
 db=connect(p);rows=db.execute("SELECT occurred_at FROM CustodyEvent WHERE shipment_id='S'").fetchall();assert len(rows)==1 and rows[0][0]=='2026-01-01T00:00:00+00:00';db.close()
