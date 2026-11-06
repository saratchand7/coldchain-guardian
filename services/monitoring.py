"""Rule-based data quality, dropout and excursion processing."""
from datetime import datetime, timezone
from database import transaction

def iso_now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def detect_gaps(db, shipment_id, sensor_id, interval_minutes=5):
    rows = db.execute("SELECT recorded_at FROM SensorReading WHERE shipment_id=? AND sensor_id=? AND quality='VALID' ORDER BY recorded_at", (shipment_id, sensor_id)).fetchall()
    found=[]
    for left,right in zip(rows, rows[1:]):
        a=datetime.fromisoformat(left['recorded_at']); b=datetime.fromisoformat(right['recorded_at'])
        missing=int((b-a).total_seconds()//(interval_minutes*60))-1
        if missing>0: found.append((a.isoformat(), b.isoformat(), missing))
    return found

def detect_excursions(db, shipment_id):
    shipment=db.execute("SELECT product_id FROM Shipment WHERE shipment_id=?",(shipment_id,)).fetchone()
    if not shipment: raise ValueError("Shipment not found")
    readings=db.execute("SELECT r.*,s.kind,p.temp_min,p.temp_max,p.humidity_min,p.humidity_max,p.max_excursion_minutes FROM SensorReading r JOIN Sensor s USING(sensor_id) JOIN Product p ON p.product_id=? WHERE r.shipment_id=? ORDER BY r.recorded_at",(shipment['product_id'],shipment_id)).fetchall()
    groups=[]; current_by_sensor={}
    for r in readings:
        low,high=(r['temp_min'],r['temp_max']) if r['kind']=='TEMP' else (r['humidity_min'],r['humidity_max'])
        violation=r['quality']=='VALID' and (r['value']<low or r['value']>high)
        sensor=r['sensor_id']
        if violation:
            prev=current_by_sensor.get(sensor)
            if prev is None or (datetime.fromisoformat(r['recorded_at'])-datetime.fromisoformat(prev['last'])).total_seconds()>300:
                prev={'sensor_id':sensor,'kind':r['kind'],'start':r['recorded_at'],'last':r['recorded_at'],'values':[],'limit':r['max_excursion_minutes']}; groups.append(prev); current_by_sensor[sensor]=prev
            prev['last']=r['recorded_at']; prev['values'].append({'reading_id':r['reading_id'],'value':r['value'],'at':r['recorded_at']})
        else: current_by_sensor.pop(sensor,None)
    created=[]
    with transaction() as wdb:
        for g in groups:
            start=datetime.fromisoformat(g['start']); end=datetime.fromisoformat(g['last']); duration=max(0,(end-start).total_seconds()/60)+5
            severity='LOW' if duration<=g['limit'] else ('CRITICAL' if duration>=60 else 'HIGH')
            eid=f"EXC-{shipment_id}-{g['sensor_id']}-{g['start'].replace(':','').replace('-','')}"
            evidence={'basis':'first violating reading through last violating reading plus one expected interval; recovery not observed' if duration else 'interval based','readings':g['values']}
            wdb.execute("INSERT OR IGNORE INTO Excursion VALUES(?,?,?,?,?,?,?,?,?,?)",(eid,shipment_id,g['sensor_id'],g['kind'],g['start'],None,duration,severity,'OPEN',__import__('json').dumps(evidence,sort_keys=True)))
            wdb.execute("INSERT OR IGNORE INTO Notification VALUES(?,?,?, ?,0)",(f'NTF-{eid}',eid,iso_now(),f'{g["kind"]} excursion: {shipment_id}'))
            wdb.execute("INSERT OR IGNORE INTO CorrectiveAction VALUES(?,?,?,?,?,?,NULL,'OPEN','')",(f'ACT-{eid}',eid,'Cold chain team','REVIEW',f'Review {g["kind"]} readings and shipment conditions',iso_now()))
            created.append(eid)
    return created
