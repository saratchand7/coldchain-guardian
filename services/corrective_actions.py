"""Corrective action state changes."""
from database import transaction
from services.monitoring import iso_now
ALLOWED={'OPEN':{'IN_PROGRESS','CANCELLED'},'IN_PROGRESS':{'COMPLETED','CANCELLED'},'COMPLETED':set(),'CANCELLED':set()}
def update_action(action_id,status,notes='',assigned_to=None):
    if status not in {'OPEN','IN_PROGRESS','COMPLETED','CANCELLED'}: raise ValueError('Invalid action status')
    with transaction() as db:
        row=db.execute('SELECT status FROM CorrectiveAction WHERE action_id=?',(action_id,)).fetchone()
        if not row: raise ValueError('Action not found')
        if status!=row['status'] and status not in ALLOWED[row['status']]: raise ValueError('Invalid corrective action transition')
        completed=iso_now() if status=='COMPLETED' else None
        db.execute('UPDATE CorrectiveAction SET status=?,completed_at=COALESCE(?,completed_at),resolution_notes=?,assigned_to=COALESCE(?,assigned_to) WHERE action_id=?',(status,completed,notes,assigned_to,action_id))
