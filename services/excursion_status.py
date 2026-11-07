"""Append-only exception acknowledgement and closure transitions."""
from database import transaction
from services.engine import iso_now

ALLOWED = {'OPEN': {'ACKNOWLEDGED'}, 'ACKNOWLEDGED': {'CLOSED'}, 'CLOSED': set()}

def update_excursion_status(excursion_id, status, reason='', actor='demo-user'):
    if not isinstance(status,str) or status not in ALLOWED:
        raise ValueError('Invalid exception status')
    if not isinstance(reason,str) or not isinstance(actor,str):raise ValueError('reason and actor must be strings')
    with transaction() as db:
        row = db.execute('SELECT status FROM Excursion WHERE excursion_id=?', (excursion_id,)).fetchone()
        if not row:
            raise LookupError('Exception not found')
        if status == row['status']:
            return {'excursion_id':excursion_id, 'status':status, 'unchanged':True}
        if status not in ALLOWED[row['status']]:
            raise ValueError('Invalid exception status transition')
        if status == 'CLOSED':
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError('A closure reason or evidence reference is required')
            open_actions = db.execute("SELECT COUNT(*) FROM CorrectiveAction WHERE excursion_id=? "
                                      "AND status IN ('OPEN','IN_PROGRESS')", (excursion_id,)).fetchone()[0]
            if open_actions:
                raise ValueError('Complete or cancel corrective actions before closing the exception')
        db.execute('UPDATE Excursion SET status=? WHERE excursion_id=?', (status, excursion_id))
        number = db.execute('SELECT COUNT(*)+1 FROM ExcursionStatusEvent WHERE excursion_id=?',
                            (excursion_id,)).fetchone()[0]
        db.execute('INSERT INTO ExcursionStatusEvent VALUES(?,?,?,?,?,?,?)',
                   (f'ESE-{excursion_id}-{number:06d}', excursion_id, row['status'], status,
                    iso_now(), actor, reason))
    return {'excursion_id':excursion_id, 'status':status, 'unchanged':False}
