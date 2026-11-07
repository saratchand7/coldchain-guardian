from datetime import datetime,timedelta,timezone
from database import initialize,connect
from services.engine import detect_gaps
def test_gap_detected_not_safe(tmp_path):
 p=tmp_path/'gaps.db';initialize(p);db=connect(p)
 db.execute("INSERT INTO Product VALUES('P','Example',2,8,30,60,15)");db.execute("INSERT INTO Shipment VALUES('S','P','A','B','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00',NULL,NULL,'IN_TRANSIT')");db.execute("INSERT INTO Container VALUES('C','box','IN_USE')");db.execute("INSERT INTO Sensor VALUES('X','C','TEMP','2026-01-01T00:00:00+00:00','VALID')")
 t=datetime(2026,1,1,tzinfo=timezone.utc)
 db.execute("INSERT INTO ContainerAssignment VALUES('A','C','S',?,NULL)",(t.isoformat(),))
 for n in (0,4):db.execute('INSERT INTO SensorReading VALUES(?,?,?,?,?,?,?)',(f'R{n}','X','S',(t+timedelta(minutes=5*n)).isoformat(),5,'C','VALID'))
 gaps=detect_gaps(db,'S','X');assert gaps[0]['missing_count']==3 and gaps[0]['start']==(t+timedelta(minutes=5)).isoformat()
 db.close()
