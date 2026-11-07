from datetime import datetime,timedelta,timezone
from database import initialize,connect
from services.engine import detect_excursions,check_excursion_duration
def test_excursion_duration_evidence_and_action(tmp_path,monkeypatch):
 from database import transaction
 p=tmp_path/'exc.db';initialize(p)
 with transaction(p) as db:
  db.execute("INSERT INTO Product VALUES('P','Example',2,8,30,60,5)");db.execute("INSERT INTO Shipment VALUES('S','P','A','B','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00',NULL,NULL,'IN_TRANSIT')");db.execute("INSERT INTO Container VALUES('C','box','IN_USE')");db.execute("INSERT INTO Sensor VALUES('X','C','TEMP','2026-01-01T00:00:00+00:00','VALID')")
  db.execute("INSERT INTO ThresholdPolicy VALUES('POL','P','2025-01-01T00:00:00+00:00',NULL,2,8,30,60,5)");db.execute("INSERT INTO ContainerAssignment VALUES('AS','C','S','2026-01-01T00:00:00+00:00',NULL)")
  t=datetime(2026,1,1,tzinfo=timezone.utc)
  for n,val in enumerate((9,9,5)):db.execute('INSERT INTO SensorReading VALUES(?,?,?,?,?,?,?)',(f'R{n}','X','S',(t+timedelta(minutes=5*n)).isoformat(),val,'C','VALID'))
 monkeypatch.setattr('services.engine.transaction',lambda path=None:transaction(p))
 db=connect(p);ids=detect_excursions(db,'S');row=db.execute('SELECT duration_minutes,evidence_json FROM Excursion').fetchone();assert len(ids)==1 and row['duration_minutes']==10;assert 'R0' in row['evidence_json'];db.close()
