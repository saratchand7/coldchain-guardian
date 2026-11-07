"""Product-aware excursion, physical range, dropout and calibration detection."""
import json
from datetime import datetime, timedelta, timezone
from database import transaction

INTERVAL_MINUTES = 5
PHYSICAL_RANGES = {'TEMP': (-80.0, 100.0), 'HUM': (0.0, 100.0)}

def iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def applicable_policy(db, product_id, at):
    row=db.execute("SELECT * FROM ThresholdPolicy WHERE product_id=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC LIMIT 1",(product_id,at,at)).fetchone()
    if row is None: raise ValueError(f'No effective threshold policy for {product_id} at {at}')
    return row

def validate_policy_product(db, shipment_id, policy_id):
    row=db.execute('SELECT s.product_id shipment_product,p.product_id policy_product FROM Shipment s JOIN ThresholdPolicy p ON p.policy_id=? WHERE s.shipment_id=?',(policy_id,shipment_id)).fetchone()
    if not row: raise ValueError('Shipment or policy not found')
    if row['shipment_product']!=row['policy_product']: raise ValueError('POLICY_PRODUCT_MISMATCH')
    return True

def flag_policy_mismatch(shipment_id,policy_id):
    with transaction() as out:
        row=out.execute('SELECT s.product_id shipment_product,p.product_id policy_product,s.planned_departure FROM Shipment s JOIN ThresholdPolicy p ON p.policy_id=? WHERE s.shipment_id=?',(policy_id,shipment_id)).fetchone()
        if not row:raise ValueError('Shipment or policy not found')
        if row['shipment_product']==row['policy_product']:return None
        when=iso_now()
        return _exception(out,shipment_id,None,'POLICY_PRODUCT_MISMATCH',when,None,0,'CRITICAL',{'policy_id':policy_id,'shipment_product_id':row['shipment_product'],'policy_product_id':row['policy_product']})

def check_excursion_duration(db, excursion_id):
    """Recompute duration from evidence reading IDs and flag an underreported interval."""
    row=db.execute("SELECT * FROM Excursion WHERE excursion_id=?",(excursion_id,)).fetchone()
    if not row: raise ValueError('Excursion not found')
    evidence=json.loads(row['evidence_json'] or '{}')
    source=evidence.get('readings',[])
    if not source: raise ValueError('Excursion has no source reading evidence')
    first=datetime.fromisoformat(source[0]['at']); recovery_id=evidence.get('recovery_reading_id')
    if recovery_id:
        recovery=db.execute('SELECT recorded_at FROM SensorReading WHERE reading_id=?',(recovery_id,)).fetchone()
        if not recovery: raise ValueError('Recovery reading evidence not found')
        calculated=(datetime.fromisoformat(recovery['recorded_at'])-first).total_seconds()/60
    else:
        last=datetime.fromisoformat(source[-1]['at'])
        calculated=(last-first).total_seconds()/60+INTERVAL_MINUTES
    undercount=calculated-float(row['duration_minutes'])>0.01
    if undercount:
        with transaction() as out:
            _exception(out,row['shipment_id'],row['sensor_id'],'DURATION_UNDERCOUNT',row['started_at'],row['ended_at'],calculated,'MEDIUM',{'source_excursion_id':excursion_id,'reported_duration_minutes':row['duration_minutes'],'recalculated_duration_minutes':calculated,'source_readings':source})
    return {'excursion_id':excursion_id,'reported_duration_minutes':row['duration_minutes'],'recalculated_duration_minutes':calculated,'flagged':undercount}

def detect_gaps(db, shipment_id, sensor_id, interval_minutes=INTERVAL_MINUTES):
    rows=db.execute('SELECT recorded_at FROM SensorReading WHERE shipment_id=? AND sensor_id=? ORDER BY recorded_at',(shipment_id,sensor_id)).fetchall(); found=[]
    for left,right in zip(rows,rows[1:]):
        a=datetime.fromisoformat(left['recorded_at']);b=datetime.fromisoformat(right['recorded_at']);missing=max(0,int((b-a).total_seconds()//(interval_minutes*60))-1)
        if missing: found.append({'start':(a+timedelta(minutes=interval_minutes)).isoformat(),'end':(b-timedelta(minutes=interval_minutes)).isoformat(),'missing_count':missing,'duration_minutes':missing*interval_minutes})
    return found

def _exception(db,shipment_id,sensor_id,kind,start,end,duration,severity,evidence):
    existing=db.execute('SELECT excursion_id FROM Excursion WHERE shipment_id=? AND sensor_id IS ? AND kind=? AND started_at=?',(shipment_id,sensor_id,kind,start)).fetchone()
    if existing:return existing['excursion_id']
    sequence=db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(excursion_id,10) AS INTEGER)),0)+1 FROM Excursion WHERE excursion_id LIKE 'EXC-2026-%'").fetchone()[0]
    eid=f'EXC-2026-{sequence:06d}'
    db.execute('INSERT OR IGNORE INTO Excursion VALUES(?,?,?,?,?,?,?,?,?,?)',(eid,shipment_id,sensor_id,kind,start,end,duration,severity,'OPEN',json.dumps(evidence,sort_keys=True)))
    db.execute('INSERT OR IGNORE INTO Notification VALUES(?,?,?,?,0)',(f'NTF-{eid}',eid,start,f'{kind} detected for shipment {shipment_id}'))
    db.execute("INSERT OR IGNORE INTO CorrectiveAction VALUES(?,?,?,?,?,?,NULL,'OPEN','')",(f'ACT-{eid}',eid,'Cold chain team','INVESTIGATE',f'Investigate {kind} for shipment {shipment_id}',start))
    return eid

def detect_excursions(db,shipment_id):
    shipment=db.execute('SELECT product_id,actual_departure,planned_departure FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone()
    if not shipment: raise ValueError('Shipment not found')
    rows=db.execute('SELECT r.*,s.kind,s.calibration_status FROM SensorReading r JOIN Sensor s USING(sensor_id) WHERE r.shipment_id=? ORDER BY r.sensor_id,r.recorded_at',(shipment_id,)).fetchall()
    policies=db.execute('SELECT * FROM ThresholdPolicy WHERE product_id=? ORDER BY effective_from',(shipment['product_id'],)).fetchall()
    by_sensor={}; quality=[]
    for r in rows:
        lo_phys,hi_phys=PHYSICAL_RANGES[r['kind']]
        if r['value']<lo_phys or r['value']>hi_phys or r['quality']=='INVALID_PHYSICAL_RANGE':
            quality.append({'sensor_id':r['sensor_id'],'kind':'INVALID_PHYSICAL_RANGE','start':r['recorded_at'],'end':r['recorded_at'],'duration':0,'severity':'CRITICAL','evidence':{'reading_id':r['reading_id'],'value':r['value'],'unit':r['unit'],'physical_range':[lo_phys,hi_phys]}});continue
        if r['quality']!='VALID': continue
        policy=next((candidate for candidate in reversed(policies) if candidate['effective_from']<=r['recorded_at'] and (candidate['effective_to'] is None or candidate['effective_to']>r['recorded_at'])),None)
        if policy is None: raise ValueError('No effective threshold policy for '+shipment['product_id']+' at '+r['recorded_at'])
        if policy['product_id']!=shipment['product_id']:
            quality.append({'sensor_id':r['sensor_id'],'kind':'POLICY_PRODUCT_MISMATCH','start':r['recorded_at'],'end':r['recorded_at'],'duration':0,'severity':'CRITICAL','evidence':{'policy_id':policy['policy_id']}});continue
        low,high=(policy['temp_min'],policy['temp_max']) if r['kind']=='TEMP' else (policy['humidity_min'],policy['humidity_max'])
        by_sensor.setdefault(r['sensor_id'],[]).append({'kind':r['kind'],'at':r['recorded_at'],'value':r['value'],'low':low,'high':high,'limit':policy['max_excursion_minutes'],'reading_id':r['reading_id']})
    groups=list(quality)
    for sensor,items in by_sensor.items():
        active=None
        for r in items:
            if r['value']<r['low'] or r['value']>r['high']:
                if active is None or (datetime.fromisoformat(r['at'])-datetime.fromisoformat(active['last'])).total_seconds()>INTERVAL_MINUTES*60:
                    active={'sensor_id':sensor,'kind':r['kind'],'start':r['at'],'last':r['at'],'limit':r['limit'],'readings':[]};groups.append(active)
                active['last']=r['at'];active['readings'].append({'reading_id':r['reading_id'],'value':r['value'],'at':r['at']})
            elif active:
                active['recovery']=r['at'];active['recovery_reading_id']=r['reading_id'];active=None
    ids=[]
    with transaction() as out:
        for g in groups:
            if 'evidence' in g:
                end=g['end'];duration=g['duration'];severity=g['severity'];evidence=g['evidence']
            else:
                start=datetime.fromisoformat(g['start']);last=datetime.fromisoformat(g['last']);recovery=datetime.fromisoformat(g['recovery']) if g.get('recovery') else None
                duration=max(0,((recovery or last+timedelta(minutes=INTERVAL_MINUTES))-start).total_seconds()/60)
                severity='LOW' if duration<=g['limit'] else ('CRITICAL' if duration>=60 else ('MEDIUM' if duration<=2*g['limit'] else 'HIGH'));end=g.get('recovery')
                evidence={'basis':'first violating sample to recovery sample; otherwise last violating sample plus one expected interval','readings':g['readings'],'recovery_reading_id':g.get('recovery_reading_id'),'open_at_end':recovery is None}
            ids.append(_exception(out,shipment_id,g['sensor_id'],g['kind'],g['start'],end,duration,severity,evidence))
        sensors=out.execute('SELECT sensor_id,calibration_status FROM Sensor WHERE container_id IN (SELECT container_id FROM ContainerAssignment WHERE shipment_id=?)',(shipment_id,)).fetchall()
        for sensor in sensors:
            if sensor['calibration_status']!='VALID':
                when=shipment['actual_departure'] or shipment['planned_departure']
                ids.append(_exception(out,shipment_id,sensor['sensor_id'],'CALIBRATION_FAILURE',when,None,0,'CRITICAL',{'calibration_status':sensor['calibration_status']}))
            for gap in detect_gaps(out,shipment_id,sensor['sensor_id']): ids.append(_exception(out,shipment_id,sensor['sensor_id'],'SENSOR_GAP',gap['start'],gap['end'],gap['duration_minutes'],'HIGH',gap))
        for stop in out.execute("SELECT * FROM RouteStop WHERE shipment_id=? AND (status='DELAYED' OR (actual_at IS NOT NULL AND actual_at>planned_at))",(shipment_id,)).fetchall():
            delay=max(0,(datetime.fromisoformat(stop['actual_at'])-datetime.fromisoformat(stop['planned_at'])).total_seconds()/60) if stop['actual_at'] else 0
            ids.append(_exception(out,shipment_id,None,'ROUTE_DELAY',stop['planned_at'],stop['actual_at'],delay,'HIGH' if delay>=60 else 'MEDIUM',{'stop_id':stop['stop_id'],'sequence_no':stop['sequence_no'],'location':stop['location'],'planned_at':stop['planned_at'],'actual_at':stop['actual_at']}))
    return sorted(set(ids))
