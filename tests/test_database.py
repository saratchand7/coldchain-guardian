from database import initialize,connect
def test_schema_has_core_entities(tmp_path):
 p=tmp_path/'test.db';initialize(p);db=connect(p)
 names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 assert {'Product','Shipment','Container','Sensor','SensorReading','RouteStop','ThresholdPolicy','Excursion','CustodyEvent','CorrectiveAction'}<=names
 assert db.execute('PRAGMA foreign_keys').fetchone()[0]==1
 db.close()
