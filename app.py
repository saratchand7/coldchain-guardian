"""ColdChain Guardian local Flask application."""
from flask import Flask, jsonify, render_template, request, Response, abort, stream_with_context
from datetime import datetime, timezone
from config import SECRET_KEY
from database import initialize, connect
from services.validation import validate, write_reports
from services.corrective_actions import update_action
from services.engine import detect_excursions, validate_policy_product, check_excursion_duration, flag_policy_mismatch, iso_now, parse_time
from services.lifecycle import transition_shipment, add_custody_event
from services.metrics import calculate_metrics
from services.excursion_status import update_excursion_status

app=Flask(__name__); app.config['SECRET_KEY']=SECRET_KEY

def json_object_body():
    body=request.get_json(silent=True)
    return body if isinstance(body,dict) else None

def page_args(default_limit=100,maximum=1000):
    try:
        page=max(1,int(request.args.get('page',1)))
        limit=max(1,min(maximum,int(request.args.get('limit',default_limit))))
    except (TypeError,ValueError):
        abort(400,description='page and limit must be positive integers')
    return page,limit

def page_result(items,total,page,limit,**extra):
    return jsonify({'items':items,'total':total,'returned':len(items),'page':page,'limit':limit,
                    'pages':(total+limit-1)//limit,'next_page':page+1 if page*limit<total else None,**extra})
@app.before_request
def ready(): initialize()
@app.get('/')
def home(): return render_template('dashboard.html')
@app.get('/shipments')
def shipments_page(): return render_template('shipments.html')
@app.get('/monitoring')
def monitoring_page(): return render_template('monitoring.html')
@app.get('/excursions')
def excursions_page(): return render_template('excursions.html')
@app.get('/actions')
def actions_page(): return render_template('corrective_actions.html')
@app.get('/custody')
def custody_page(): return render_template('custody.html')
@app.get('/routes')
def routes_page(): return render_template('routes.html')
@app.get('/reports')
def reports_page(): return render_template('reports.html')
@app.get('/api/dashboard')
def dashboard():
    db=connect()
    def one(sql): return db.execute(sql).fetchone()[0]
    current="COALESCE(json_extract(evidence_json,'$.condition_active'),1)=1"
    result={'total_shipments':one('SELECT COUNT(*) FROM Shipment'),'active_shipments':one("SELECT COUNT(*) FROM Shipment WHERE status IN ('IN_TRANSIT','DELAYED','EXCEPTION')"),'delivered_shipments':one("SELECT COUNT(*) FROM Shipment WHERE status='DELIVERED'"),'containers':one('SELECT COUNT(*) FROM Container'),'sensors':one('SELECT COUNT(*) FROM Sensor'),'active_excursions':one("SELECT COUNT(*) FROM Excursion WHERE status='OPEN' AND "+current),'critical_exceptions':one("SELECT COUNT(*) FROM Excursion WHERE severity='CRITICAL' AND "+current),'sensor_dropouts':one("SELECT COUNT(*) FROM Excursion WHERE kind='SENSOR_GAP' AND "+current),'pending_actions':one("SELECT COUNT(*) FROM CorrectiveAction WHERE status IN ('OPEN','IN_PROGRESS')"),'compliance_percentage':0}
    outcome_metrics=calculate_metrics(validate_data=False,as_of=iso_now())
    result['compliance_percentage']=outcome_metrics['shipment_compliance_percentage']
    result['assessment_counts']=outcome_metrics['shipment_assessment_counts']
    result['severity']= [dict(r) for r in db.execute('SELECT severity,COUNT(*) count FROM Excursion GROUP BY severity ORDER BY severity')]
    result['lifecycle']=[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM Shipment GROUP BY status ORDER BY status')]
    result['availability']={'expected_interval_minutes':5,'received_readings':one('SELECT COUNT(*) FROM SensorReading'),'missing_intervals_detected':result['sensor_dropouts']}
    result['availability']['percentage']=outcome_metrics['sensor_availability_percentage']
    result['actions']=[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM CorrectiveAction GROUP BY status ORDER BY status')]
    db.close(); return jsonify(result)
@app.get('/api/shipments')
def shipment_list():
    page,limit=page_args(100,1000);offset=(page-1)*limit;db=connect()
    total=db.execute('SELECT COUNT(*) FROM Shipment').fetchone()[0]
    rows=[dict(r) for r in db.execute('SELECT s.*,p.name product_name FROM Shipment s JOIN Product p USING(product_id) ORDER BY planned_departure DESC,shipment_id LIMIT ? OFFSET ?',(limit,offset))]
    db.close();return page_result(rows,total,page,limit,order='planned_departure DESC, shipment_id ASC')
@app.get('/api/shipments/<shipment_id>')
def shipment_detail(shipment_id):
    db=connect(); row=db.execute('SELECT * FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone()
    if not row: db.close(); abort(404)
    result=dict(row); result['stops']=[dict(r) for r in db.execute('SELECT * FROM RouteStop WHERE shipment_id=? ORDER BY sequence_no',(shipment_id,))]; result['excursions']=[dict(r) for r in db.execute('SELECT * FROM Excursion WHERE shipment_id=?',(shipment_id,))]; result['custody']=[dict(r) for r in db.execute('SELECT * FROM CustodyEvent WHERE shipment_id=? ORDER BY occurred_at',(shipment_id,))]; db.close(); return jsonify(result)
@app.route('/api/shipments/<shipment_id>/status',methods=['GET','PATCH'])
def shipment_status(shipment_id):
    if request.method=='GET':
        db=connect(); row=db.execute('SELECT status FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone()
        if not row: db.close(); abort(404)
        history=[dict(x) for x in db.execute('SELECT * FROM LifecycleEvent WHERE shipment_id=? ORDER BY occurred_at',(shipment_id,))];db.close();return jsonify({'status':row['status'],'history':history})
    body=json_object_body()
    if body is None:return jsonify({'error':'JSON body must be an object'}),400
    if not isinstance(body.get('status'),str) or not isinstance(body.get('location','Operations'),str) or not isinstance(body.get('notes',''),str):return jsonify({'error':'status, location and notes must be strings'}),400
    try: return jsonify(transition_shipment(shipment_id,body.get('status',''),body.get('location','Operations'),body.get('notes','')))
    except ValueError as e: return jsonify({'error':str(e)}),(404 if str(e)=='Shipment not found' else 400)
@app.route('/api/shipments/<shipment_id>/custody',methods=['GET','POST'])
def custody(shipment_id):
    if request.method=='GET':
        db=connect()
        if not db.execute('SELECT 1 FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone(): db.close();abort(404)
        rows=[dict(x) for x in db.execute('SELECT * FROM CustodyEvent WHERE shipment_id=? ORDER BY occurred_at',(shipment_id,))];db.close();return jsonify(rows)
    body=json_object_body()
    if body is None:return jsonify({'error':'JSON body must be an object'}),400
    if any(not isinstance(body.get(k),str) for k in ('new_custodian','event_type','location')):return jsonify({'error':'new_custodian, event_type and location must be strings'}),400
    if not isinstance(body.get('notes',''),str) or (body.get('occurred_at') is not None and not isinstance(body.get('occurred_at'),str)):return jsonify({'error':'notes and occurred_at must be strings'}),400
    if not body.get('new_custodian') or not body.get('event_type') or not body.get('location'): return jsonify({'error':'new_custodian, event_type and location are required'}),400
    try: return jsonify(add_custody_event(shipment_id,body['new_custodian'],body['event_type'],body['location'],body.get('occurred_at'),body.get('notes',''))),201
    except ValueError as e: return jsonify({'error':str(e)}),(404 if str(e)=='Shipment not found' else 400)
@app.get('/api/routes')
def route_stops():
    shipment=request.args.get('shipment_id');db=connect();rows=[dict(r) for r in db.execute('SELECT * FROM RouteStop WHERE (? IS NULL OR shipment_id=?) ORDER BY shipment_id,sequence_no',(shipment,shipment))];db.close();return jsonify(rows)
@app.get('/api/custody')
def custody_all():
    db=connect();rows=[dict(r) for r in db.execute('SELECT * FROM CustodyEvent ORDER BY occurred_at DESC LIMIT 5000')];db.close();return jsonify(rows)
@app.get('/api/readings')
def readings():
    shipment=request.args.get('shipment_id');kind=request.args.get('kind','TEMP');sensor=request.args.get('sensor_id');page,limit=page_args(2000,10000);offset=(page-1)*limit
    if kind not in {'TEMP','HUM'}:return jsonify({'error':'kind must be TEMP or HUM'}),400
    db=connect();where='WHERE s.kind=?';params=[kind]
    if shipment:where+=' AND r.shipment_id=?';params.append(shipment)
    if sensor:where+=' AND r.sensor_id=?';params.append(sensor)
    for arg in ('from','to'):
        value=request.args.get(arg)
        if value:
            try:
                parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
                value=parsed.astimezone(timezone.utc).isoformat()
            except ValueError:db.close();return jsonify({'error':f'{arg} must be an ISO 8601 timestamp'}),400
            where+=(' AND r.recorded_at>=?' if arg=='from' else ' AND r.recorded_at<=?');params.append(value)
    total=db.execute('SELECT COUNT(*) FROM SensorReading r JOIN Sensor s USING(sensor_id) '+where,params).fetchone()[0]
    rows=[dict(r) for r in db.execute('SELECT r.reading_id,r.shipment_id,r.sensor_id,r.recorded_at,r.value,r.unit,s.kind,r.quality FROM SensorReading r JOIN Sensor s USING(sensor_id) '+where+' ORDER BY r.recorded_at,r.sensor_id,r.reading_id LIMIT ? OFFSET ?',params+[limit,offset])]
    db.close();return page_result(rows,total,page,limit,order='recorded_at, sensor_id, reading_id',window={'from':request.args.get('from'),'to':request.args.get('to')},downsampled=False)
@app.get('/api/excursions')
def excursion_list():
    db=connect(); rows=[dict(r) for r in db.execute('SELECT e.*,s.product_id FROM Excursion e JOIN Shipment s USING(shipment_id) ORDER BY started_at DESC LIMIT 1000')]; db.close(); return jsonify(rows)
@app.get('/api/excursions/<excursion_id>')
def excursion_detail(excursion_id):
    db=connect();row=db.execute('SELECT * FROM Excursion WHERE excursion_id=?',(excursion_id,)).fetchone()
    if not row:db.close();abort(404)
    result=dict(row);result['corrective_actions']=[dict(x) for x in db.execute('SELECT * FROM CorrectiveAction WHERE excursion_id=? ORDER BY created_at',(excursion_id,))];result['notifications']=[dict(x) for x in db.execute('SELECT * FROM Notification WHERE excursion_id=? ORDER BY created_at',(excursion_id,))];result['status_history']=[dict(x) for x in db.execute('SELECT * FROM ExcursionStatusEvent WHERE excursion_id=? ORDER BY occurred_at,event_id',(excursion_id,))];result['revisions']=[dict(x) for x in db.execute('SELECT * FROM ExcursionRevision WHERE excursion_id=? ORDER BY revision_no',(excursion_id,))];db.close();return jsonify(result)
@app.patch('/api/excursions/<excursion_id>')
def excursion_update(excursion_id):
    body=json_object_body()
    if body is None:return jsonify({'error':'JSON body must be an object'}),400
    if not isinstance(body.get('status'),str) or not isinstance(body.get('reason',''),str) or not isinstance(body.get('actor','demo-user'),str):return jsonify({'error':'status, reason and actor must be strings'}),400
    try:return jsonify(update_excursion_status(excursion_id,body.get('status',''),body.get('reason',''),body.get('actor','demo-user')))
    except LookupError as e:return jsonify({'error':str(e)}),404
    except ValueError as e:return jsonify({'error':str(e)}),400
@app.post('/api/shipments/<shipment_id>/detect')
def detect(shipment_id):
    as_of=request.args.get('as_of') or iso_now()
    try:as_of=parse_time(as_of).isoformat()
    except (TypeError,ValueError):return jsonify({'error':'as_of must be an ISO 8601 timestamp'}),400
    db=connect()
    try: ids=detect_excursions(db,shipment_id,as_of)
    except ValueError as e: db.close(); return jsonify({'error':str(e)}),(404 if str(e)=='Shipment not found' else 422)
    db.close(); return jsonify({'created_or_detected':ids})
@app.post('/api/detect')
def detect_all():
    as_of=request.args.get('as_of') or iso_now()
    try:as_of=parse_time(as_of).isoformat()
    except (TypeError,ValueError):return jsonify({'error':'as_of must be an ISO 8601 timestamp'}),400
    db=connect();ids=[r[0] for r in db.execute('SELECT shipment_id FROM Shipment ORDER BY shipment_id')];db.close();result=[]
    for sid in ids:
        connection=connect()
        try: result.extend(detect_excursions(connection,sid,as_of))
        finally: connection.close()
    return jsonify({'shipments_processed':len(ids),'detected_exceptions':len(set(result)),'exception_ids':sorted(set(result))})
@app.post('/api/shipments/<shipment_id>/validate-policy')
def policy_check(shipment_id):
    body=json_object_body()
    if body is None:return jsonify({'error':'JSON body must be an object'}),400
    if not isinstance(body.get('policy_id'),str):return jsonify({'error':'policy_id is required'}),400
    db=connect()
    try: validate_policy_product(db,shipment_id,body.get('policy_id',''))
    except ValueError as e:
        db.close()
        if str(e)=='POLICY_PRODUCT_MISMATCH':
            try: return jsonify({'error':str(e),'exception_id':flag_policy_mismatch(shipment_id,body.get('policy_id',''))}),422
            except ValueError: pass
        return jsonify({'error':str(e)}),(404 if str(e)=='Shipment or policy not found' else 422)
    db.close();return jsonify({'valid':True,'shipment_id':shipment_id,'policy_id':body['policy_id']})
@app.post('/api/excursions/<excursion_id>/recalculate-duration')
def recalculate_excursion(excursion_id):
    db=connect()
    try: result=check_excursion_duration(db,excursion_id)
    except ValueError as e: db.close();return jsonify({'error':str(e)}),(404 if str(e)=='Excursion not found' else 422)
    db.close();return jsonify(result)
@app.get('/api/actions')
def action_list():
    db=connect(); rows=[dict(r) for r in db.execute('SELECT * FROM CorrectiveAction ORDER BY created_at DESC')]
    for row in rows:row['status_history']=[dict(e) for e in db.execute('SELECT * FROM CorrectiveActionEvent WHERE action_id=? ORDER BY occurred_at,event_id',(row['action_id'],))]
    db.close(); return jsonify(rows)
@app.patch('/api/actions/<action_id>')
def action_update(action_id):
    body=json_object_body()
    if body is None:return jsonify({'error':'JSON body must be an object'}),400
    if not isinstance(body.get('status'),str) or not isinstance(body.get('resolution_notes',''),str) or (body.get('assigned_to') is not None and not isinstance(body.get('assigned_to'),str)):
        return jsonify({'error':'status, resolution_notes and assigned_to must be strings'}),400
    try: update_action(action_id,body.get('status',''),body.get('resolution_notes',''),body.get('assigned_to'))
    except ValueError as e: return jsonify({'error':str(e)}),(404 if str(e)=='Action not found' else 400)
    return jsonify({'updated':action_id})
@app.get('/api/validation')
def validation(): return jsonify(validate())
@app.get('/api/metrics')
def metrics(): return jsonify(calculate_metrics(as_of=iso_now()))
@app.post('/api/reports/validation')
def validation_export(): return jsonify(write_reports())
@app.get('/api/export/<table>.json')
def export_json(table):
    allowed={'products':'Product','shipments':'Shipment','containers':'Container','assignments':'ContainerAssignment','sensors':'Sensor','sensor-calibration-history':'SensorCalibrationHistory','readings':'SensorReading','routes':'RouteStop','policies':'ThresholdPolicy','excursions':'Excursion','excursion-revisions':'ExcursionRevision','excursion-status-events':'ExcursionStatusEvent','custody':'CustodyEvent','lifecycle':'LifecycleEvent','actions':'CorrectiveAction','action-events':'CorrectiveActionEvent','notifications':'Notification'}
    if table not in allowed: abort(404)
    import json
    def generate():
        db=connect()
        try:
            yield '[';first=True
            for row in db.execute(f'SELECT * FROM {allowed[table]} ORDER BY 1'):
                if not first:yield ','
                yield json.dumps(dict(row),sort_keys=True,default=str);first=False
            yield ']'
        finally:db.close()
    return Response(stream_with_context(generate()),mimetype='application/json',headers={'Content-Disposition':f'attachment; filename={table}.json'})
@app.get('/api/export/<table>.csv')
def export_csv(table):
    allowed={'products':'Product','shipments':'Shipment','containers':'Container','assignments':'ContainerAssignment','sensors':'Sensor','sensor-calibration-history':'SensorCalibrationHistory','excursions':'Excursion','excursion-revisions':'ExcursionRevision','excursion-status-events':'ExcursionStatusEvent','actions':'CorrectiveAction','action-events':'CorrectiveActionEvent','readings':'SensorReading','routes':'RouteStop','policies':'ThresholdPolicy','custody':'CustodyEvent','lifecycle':'LifecycleEvent'}
    if table not in allowed: abort(404)
    import csv,io
    def generate():
        db=connect()
        try:
            cur=db.execute(f'SELECT * FROM {allowed[table]} ORDER BY 1');fields=[x[0] for x in cur.description];buffer=io.StringIO();writer=csv.writer(buffer);writer.writerow(fields);yield buffer.getvalue()
            while batch:=cur.fetchmany(2000):
                buffer.seek(0);buffer.truncate(0);writer.writerows(batch);yield buffer.getvalue()
        finally:db.close()
    return Response(stream_with_context(generate()),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename={table}.csv'})
if __name__=='__main__': app.run(debug=True)
