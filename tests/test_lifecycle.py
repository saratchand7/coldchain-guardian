import pytest
from database import initialize,connect,transaction
from services.corrective_actions import update_action
def test_action_transition_validation(tmp_path,monkeypatch):
 from config import DATABASE
 p=tmp_path/'action.db';initialize(p)
 with transaction(p) as db:
  db.execute("INSERT INTO Product VALUES('P','Example',2,8,30,60,15)")
  db.execute("INSERT INTO Shipment VALUES('S','P','A','B','2026-01-01T00:00:00+00:00','2026-01-02T00:00:00+00:00',NULL,NULL,'CREATED')")
  db.execute("INSERT INTO Excursion VALUES('E','S',NULL,'TEMP','2026-01-01T00:00:00+00:00',NULL,5,'LOW','OPEN','{}')")
  db.execute("INSERT INTO CorrectiveAction VALUES('A','E','X','REVIEW','desc','2026-01-01T00:00:00+00:00',NULL,'OPEN','')")
 monkeypatch.setattr('services.corrective_actions.transaction',lambda: transaction(p))
 with pytest.raises(ValueError): update_action('A','COMPLETED')
 update_action('A','IN_PROGRESS');update_action('A','COMPLETED','Reviewed')
 db=connect(p);row=db.execute("SELECT status,completed_at,resolution_notes FROM CorrectiveAction WHERE action_id='A'").fetchone();assert row['status']=='COMPLETED' and row['completed_at'] and row['resolution_notes']=='Reviewed';db.close()
