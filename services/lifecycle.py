"""Shipment state transitions and append-only custody history."""
from datetime import datetime, timezone
from database import transaction
from services.monitoring import iso_now

TRANSITIONS={
 'CREATED':{'READY'}, 'READY':{'IN_TRANSIT'}, 'IN_TRANSIT':{'DELAYED','EXCEPTION','DELIVERED'},
 'DELAYED':{'IN_TRANSIT','EXCEPTION','DELIVERED'}, 'EXCEPTION':{'IN_TRANSIT','DELAYED','DELIVERED'},
 'DELIVERED':{'CLOSED'}, 'CLOSED':set()
}

def validate_transition(old,new_status,actual_departure):
    if new_status not in TRANSITIONS:
        raise ValueError('Unknown shipment status')
    if new_status not in TRANSITIONS.get(old,set()):
        raise ValueError('Invalid lifecycle transition '+str(old)+' -> '+new_status)
    if new_status=='DELIVERED' and actual_departure is None:
        raise ValueError('INVALID_SHIPMENT_TIMELINE: shipment must depart before delivery')
    return True

def transition_shipment(shipment_id,new_status,location='Operations',notes=''):
    if not isinstance(new_status,str) or not isinstance(location,str) or not isinstance(notes,str):
        raise ValueError('status, location and notes must be strings')
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
            validate_transition(old,new_status,row['actual_departure'])
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
    if event_type not in {'DEPARTURE','CUSTODY_TRANSFER','DELIVERY'}:
        raise ValueError('Invalid custody event type')
    if not all(isinstance(v,str) and v.strip() for v in (new_custodian,location)):
        raise ValueError('Custodian and location are required')
    if not isinstance(notes,str) or (occurred_at is not None and not isinstance(occurred_at,str)):
        raise ValueError('notes and occurred_at must be strings')
    when=occurred_at or iso_now()
    try:
        parsed=datetime.fromisoformat(when)
        if parsed.tzinfo is None: raise ValueError('occurred_at must include a timezone')
        when=parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError as exc: raise ValueError('occurred_at must be ISO 8601') from exc
    with transaction() as db:
        shipment=db.execute('SELECT actual_departure,actual_arrival FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone()
        if not shipment: raise ValueError('Shipment not found')
        previous=db.execute('SELECT new_custodian,occurred_at FROM CustodyEvent WHERE shipment_id=? ORDER BY occurred_at DESC LIMIT 1',(shipment_id,)).fetchone()
        if previous and datetime.fromisoformat(when)<=datetime.fromisoformat(previous['occurred_at']): raise ValueError('Custody events must be strictly chronological')
        if previous is None and event_type!='DEPARTURE':raise ValueError('The first custody event must record departure')
        if previous is not None and event_type=='DEPARTURE':raise ValueError('Departure custody is already recorded')
        if shipment['actual_departure'] and parse_utc(when)<parse_utc(shipment['actual_departure']):
            raise ValueError('Custody events cannot precede shipment departure')
        if shipment['actual_arrival'] and parse_utc(when)>parse_utc(shipment['actual_arrival']):
            raise ValueError('Custody events cannot follow shipment arrival')
        if event_type=='DELIVERY':
            if shipment['actual_departure'] is None or shipment['actual_arrival'] is None:
                raise ValueError('Delivery custody requires a departed, delivered shipment')
            if parse_utc(when)!=parse_utc(shipment['actual_arrival']):
                raise ValueError('Delivery custody time must match actual shipment arrival')
        if event_type=='DEPARTURE' and shipment['actual_departure'] and parse_utc(when)!=parse_utc(shipment['actual_departure']):
            raise ValueError('Departure custody time must match actual shipment departure')
        event_id='CST-'+shipment_id+'-'+when.replace(':','').replace('-','')
        db.execute('INSERT INTO CustodyEvent VALUES(?,?,?,?,?,?,?,?)',(event_id,shipment_id,previous['new_custodian'] if previous else None,new_custodian,event_type,location,when,notes))
    return {'event_id':event_id,'shipment_id':shipment_id,'occurred_at':when}

def parse_utc(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
