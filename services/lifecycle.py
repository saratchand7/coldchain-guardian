"""Shipment state transitions and append-only custody history."""
from datetime import datetime, timezone
from database import transaction
from services.monitoring import iso_now

TRANSITIONS={
 'CREATED':{'READY'}, 'READY':{'IN_TRANSIT'}, 'IN_TRANSIT':{'DELAYED','EXCEPTION','DELIVERED'},
 'DELAYED':{'IN_TRANSIT','EXCEPTION','DELIVERED'}, 'EXCEPTION':{'IN_TRANSIT','DELAYED','DELIVERED'},
 'DELIVERED':{'CLOSED'}, 'CLOSED':set()
}

def transition_shipment(shipment_id,new_status,location='Operations',notes=''):
    if new_status not in TRANSITIONS: raise ValueError('Unknown shipment status')
    timeline_invalid=False
    with transaction() as db:
        row=db.execute('SELECT status,actual_departure,actual_arrival FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone()
        if not row: raise ValueError('Shipment not found')
        old=row['status']
        if new_status=='DELIVERED' and row['actual_departure'] is None:
            from services.engine import _exception
            when=iso_now();_exception(db,shipment_id,None,'INVALID_SHIPMENT_TIMELINE',when,None,0,'CRITICAL',{'attempted_status':'DELIVERED','previous_status':old})
            timeline_invalid=True
        if timeline_invalid:
            pass
        else:
            if new_status not in TRANSITIONS[old]: raise ValueError('Invalid lifecycle transition '+old+' -> '+new_status)
            now=iso_now()
            if new_status=='IN_TRANSIT' and row['actual_departure'] is None:
                db.execute('UPDATE Shipment SET actual_departure=? WHERE shipment_id=?',(now,shipment_id))
            if new_status=='DELIVERED':
                departure=row['actual_departure'] or now
                if datetime.fromisoformat(now)<datetime.fromisoformat(departure): raise ValueError('INVALID_SHIPMENT_TIMELINE')
                db.execute('UPDATE Shipment SET actual_departure=COALESCE(actual_departure,?),actual_arrival=? WHERE shipment_id=?',(departure,now,shipment_id))
            db.execute('UPDATE Shipment SET status=? WHERE shipment_id=?',(new_status,shipment_id))
            stamp=now.replace(':','').replace('-','')
            event_id='LCY-'+shipment_id+'-'+stamp+'-'+old+'-'+new_status
            db.execute('INSERT INTO LifecycleEvent VALUES(?,?,?,?,?,?,?)',(event_id,shipment_id,old,new_status,now,location,notes))
    if timeline_invalid: raise ValueError('INVALID_SHIPMENT_TIMELINE: shipment must depart before delivery')
    return {'shipment_id':shipment_id,'previous_status':old,'status':new_status,'occurred_at':now}

def add_custody_event(shipment_id,new_custodian,event_type,location,occurred_at=None,notes=''):
    when=occurred_at or iso_now()
    try:
        parsed=datetime.fromisoformat(when)
        if parsed.tzinfo is None: raise ValueError('occurred_at must include a timezone')
        when=parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError as exc: raise ValueError('occurred_at must be ISO 8601') from exc
    with transaction() as db:
        if not db.execute('SELECT 1 FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone(): raise ValueError('Shipment not found')
        previous=db.execute('SELECT new_custodian,occurred_at FROM CustodyEvent WHERE shipment_id=? ORDER BY occurred_at DESC LIMIT 1',(shipment_id,)).fetchone()
        if previous and datetime.fromisoformat(when)<=datetime.fromisoformat(previous['occurred_at']): raise ValueError('Custody events must be strictly chronological')
        event_id='CST-'+shipment_id+'-'+when.replace(':','').replace('-','')
        db.execute('INSERT INTO CustodyEvent VALUES(?,?,?,?,?,?,?,?)',(event_id,shipment_id,previous['new_custodian'] if previous else None,new_custodian,event_type,location,when,notes))
    return {'event_id':event_id,'shipment_id':shipment_id,'occurred_at':when}
