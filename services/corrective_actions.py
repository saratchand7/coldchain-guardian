"""Audited corrective action state changes."""
from database import transaction
from services.engine import iso_now
ALLOWED={'OPEN':{'IN_PROGRESS','CANCELLED'},'IN_PROGRESS':{'COMPLETED','CANCELLED'},'COMPLETED':set(),'CANCELLED':set()}
def update_action(action_id,status,notes='',assigned_to=None):
    if not isinstance(status,str) or status not in {'OPEN','IN_PROGRESS','COMPLETED','CANCELLED'}: raise ValueError('Invalid action status')
    if not isinstance(notes,str) or (status=='COMPLETED' and not notes.strip()):
        raise ValueError('Resolution notes are required to complete an action')
    if assigned_to is not None and not isinstance(assigned_to,str):raise ValueError('assigned_to must be a string')
    with transaction() as db:
        row=db.execute('SELECT status,assigned_to,resolution_notes FROM CorrectiveAction WHERE action_id=?',(action_id,)).fetchone()
        if not row: raise ValueError('Action not found')
        if db.execute('SELECT 1 FROM CorrectiveActionEvent WHERE action_id=? LIMIT 1',(action_id,)).fetchone() is None:
            db.execute('INSERT INTO CorrectiveActionEvent VALUES(?,?,?,?,?,?,?,?)',
                       (f'CAE-{action_id}-000001',action_id,None,row['status'],row['assigned_to'],
                        iso_now(),'Initial action state',row['resolution_notes']))
        if status==row['status']:
            if assigned_to and assigned_to!=row['assigned_to'] or notes and notes!=row['resolution_notes']:
                raise ValueError('Closed or unchanged action edits require a separate amendment event')
            return
        if status!=row['status'] and status not in ALLOWED[row['status']]: raise ValueError('Invalid corrective action transition')
        completed=iso_now() if status=='COMPLETED' else None
        next_assignee=assigned_to or row['assigned_to']
        db.execute('UPDATE CorrectiveAction SET status=?,completed_at=COALESCE(?,completed_at),resolution_notes=?,assigned_to=? WHERE action_id=?',(status,completed,notes or row['resolution_notes'],next_assignee,action_id))
        event_no=db.execute('SELECT COUNT(*)+1 FROM CorrectiveActionEvent WHERE action_id=?',(action_id,)).fetchone()[0]
        db.execute('INSERT INTO CorrectiveActionEvent VALUES(?,?,?,?,?,?,?,?)',
                   (f'CAE-{action_id}-{event_no:06d}',action_id,row['status'],status,next_assignee,
                    iso_now(),f'Status changed from {row["status"]} to {status}',notes))
