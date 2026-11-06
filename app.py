"""ColdChain Guardian local Flask application."""
from flask import Flask, jsonify, render_template, request, Response, abort, stream_with_context
from config import SECRET_KEY
from database import initialize, connect
from services.validation import validate, write_reports
from services.corrective_actions import update_action
from services.engine import detect_excursions, validate_policy_product, check_excursion_duration, flag_policy_mismatch
from services.lifecycle import transition_shipment, add_custody_event
from services.metrics import calculate_metrics

app=Flask(__name__); app.config['SECRET_KEY']=SECRET_KEY
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
    result={'total_shipments':one('SELECT COUNT(*) FROM Shipment'),'active_shipments':one("SELECT COUNT(*) FROM Shipment WHERE status IN ('IN_TRANSIT','DELAYED','EXCEPTION')"),'delivered_shipments':one("SELECT COUNT(*) FROM Shipment WHERE status='DELIVERED'"),'containers':one('SELECT COUNT(*) FROM Container'),'sensors':one('SELECT COUNT(*) FROM Sensor'),'active_excursions':one("SELECT COUNT(*) FROM Excursion WHERE status='OPEN'"),'critical_exceptions':one("SELECT COUNT(*) FROM Excursion WHERE severity='CRITICAL'"),'sensor_dropouts':one("SELECT COUNT(*) FROM Excursion WHERE kind='SENSOR_GAP'"),'pending_actions':one("SELECT COUNT(*) FROM CorrectiveAction WHERE status IN ('OPEN','IN_PROGRESS')"),'compliance_percentage':0}
    total=result['total_shipments']; result['compliance_percentage']=round(100*one("SELECT COUNT(*) FROM Shipment s WHERE NOT EXISTS (SELECT 1 FROM Excursion e WHERE e.shipment_id=s.shipment_id AND e.severity IN ('MEDIUM','HIGH','CRITICAL'))")/total,1) if total else 0
    result['severity']= [dict(r) for r in db.execute('SELECT severity,COUNT(*) count FROM Excursion GROUP BY severity ORDER BY severity')]
    result['lifecycle']=[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM Shipment GROUP BY status ORDER BY status')]
    result['availability']={'expected_interval_minutes':5,'received_readings':one('SELECT COUNT(*) FROM SensorReading'),'missing_intervals_detected':result['sensor_dropouts']}
    result['availability']['percentage']=calculate_metrics(validate_data=False)['sensor_availability_percentage']
    result['actions']=[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM CorrectiveAction GROUP BY status ORDER BY status')]
    db.close(); return jsonify(result)
@app.get('/api/shipments')
def shipment_list():
    db=connect(); rows=[dict(r) for r in db.execute('SELECT s.*,p.name product_name FROM Shipment s JOIN Product p USING(product_id) ORDER BY planned_departure DESC LIMIT 500')]; db.close(); return jsonify(rows)
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
    body=request.get_json(silent=True) or {}
    try: return jsonify(transition_shipment(shipment_id,body.get('status',''),body.get('location','Operations'),body.get('notes','')))
    except ValueError as e: return jsonify({'error':str(e)}),400
@app.route('/api/shipments/<shipment_id>/custody',methods=['GET','POST'])
def custody(shipment_id):
    if request.method=='GET':
        db=connect()
        if not db.execute('SELECT 1 FROM Shipment WHERE shipment_id=?',(shipment_id,)).fetchone(): db.close();abort(404)
        rows=[dict(x) for x in db.execute('SELECT * FROM CustodyEvent WHERE shipment_id=? ORDER BY occurred_at',(shipment_id,))];db.close();return jsonify(rows)
    body=request.get_json(silent=True) or {}
    if not body.get('new_custodian') or not body.get('event_type') or not body.get('location'): return jsonify({'error':'new_custodian, event_type and location are required'}),400
    try: return jsonify(add_custody_event(shipment_id,body['new_custodian'],body['event_type'],body['location'],body.get('occurred_at'),body.get('notes',''))),201
    except ValueError as e: return jsonify({'error':str(e)}),400
@app.get('/api/routes')
def route_stops():
    shipment=request.args.get('shipment_id');db=connect();rows=[dict(r) for r in db.execute('SELECT * FROM RouteStop WHERE (? IS NULL OR shipment_id=?) ORDER BY shipment_id,sequence_no',(shipment,shipment))];db.close();return jsonify(rows)
@app.get('/api/custody')
def custody_all():
    db=connect();rows=[dict(r) for r in db.execute('SELECT * FROM CustodyEvent ORDER BY occurred_at DESC LIMIT 5000')];db.close();return jsonify(rows)
@app.get('/api/readings')
def readings():
    shipment=request.args.get('shipment_id'); kind=request.args.get('kind','TEMP'); db=connect()
    rows=[dict(r) for r in db.execute('SELECT r.recorded_at,r.value,r.unit,s.kind FROM SensorReading r JOIN Sensor s USING(sensor_id) WHERE (? IS NULL OR r.shipment_id=?) AND s.kind=? ORDER BY r.recorded_at LIMIT 2000',(shipment,shipment,kind))]; db.close(); return jsonify(rows)
@app.get('/api/excursions')
def excursion_list():
    db=connect(); rows=[dict(r) for r in db.execute('SELECT e.*,s.product_id FROM Excursion e JOIN Shipment s USING(shipment_id) ORDER BY started_at DESC LIMIT 1000')]; db.close(); return jsonify(rows)
@app.get('/api/excursions/<excursion_id>')
def excursion_detail(excursion_id):
    db=connect();row=db.execute('SELECT * FROM Excursion WHERE excursion_id=?',(excursion_id,)).fetchone()
    if not row:db.close();abort(404)
    result=dict(row);result['corrective_actions']=[dict(x) for x in db.execute('SELECT * FROM CorrectiveAction WHERE excursion_id=? ORDER BY created_at',(excursion_id,))];result['notifications']=[dict(x) for x in db.execute('SELECT * FROM Notification WHERE excursion_id=? ORDER BY created_at',(excursion_id,))];db.close();return jsonify(result)
@app.patch('/api/excursions/<excursion_id>')
def excursion_update(excursion_id):
    body=request.get_json(silent=True) or {};status=body.get('status','')
    transitions={'OPEN':{'ACKNOWLEDGED'},'ACKNOWLEDGED':{'CLOSED'},'CLOSED':set()}
    if status not in transitions:return jsonify({'error':'Invalid exception status'}),400
    with __import__('database').transaction() as db:
        row=db.execute('SELECT status FROM Excursion WHERE excursion_id=?',(excursion_id,)).fetchone()
        if not row:return jsonify({'error':'Exception not found'}),404
        if status not in transitions[row['status']]:return jsonify({'error':'Invalid exception status transition'}),400
        db.execute('UPDATE Excursion SET status=? WHERE excursion_id=?',(status,excursion_id))
    return jsonify({'excursion_id':excursion_id,'status':status})
@app.post('/api/shipments/<shipment_id>/detect')
def detect(shipment_id):
    db=connect()
    try: ids=detect_excursions(db,shipment_id)
    except ValueError as e: db.close(); return jsonify({'error':str(e)}),404
    db.close(); return jsonify({'created_or_detected':ids})
@app.post('/api/detect')
def detect_all():
    db=connect();ids=[r[0] for r in db.execute('SELECT shipment_id FROM Shipment ORDER BY shipment_id')];db.close();result=[]
    for sid in ids:
        connection=connect()
        try: result.extend(detect_excursions(connection,sid))
        finally: connection.close()
    return jsonify({'shipments_processed':len(ids),'detected_exceptions':len(set(result)),'exception_ids':sorted(set(result))})
@app.post('/api/shipments/<shipment_id>/validate-policy')
def policy_check(shipment_id):
    body=request.get_json(silent=True) or {};db=connect()
    try: validate_policy_product(db,shipment_id,body.get('policy_id',''))
    except ValueError as e:
        db.close()
        if str(e)=='POLICY_PRODUCT_MISMATCH':
            try: return jsonify({'error':str(e),'exception_id':flag_policy_mismatch(shipment_id,body.get('policy_id',''))}),422
            except ValueError: pass
        return jsonify({'error':str(e)}),422
    db.close();return jsonify({'valid':True,'shipment_id':shipment_id,'policy_id':body['policy_id']})
@app.post('/api/excursions/<excursion_id>/recalculate-duration')
def recalculate_excursion(excursion_id):
    db=connect()
    try: result=check_excursion_duration(db,excursion_id)
    except ValueError as e: db.close();return jsonify({'error':str(e)}),404
    db.close();return jsonify(result)
@app.get('/api/actions')
def action_list():
    db=connect(); rows=[dict(r) for r in db.execute('SELECT * FROM CorrectiveAction ORDER BY created_at DESC')]; db.close(); return jsonify(rows)
@app.patch('/api/actions/<action_id>')
def action_update(action_id):
    body=request.get_json(silent=True) or {}
    try: update_action(action_id,body.get('status',''),body.get('resolution_notes',''),body.get('assigned_to'))
    except ValueError as e: return jsonify({'error':str(e)}),400
    return jsonify({'updated':action_id})
@app.get('/api/validation')
def validation(): return jsonify(validate())
@app.get('/api/metrics')
def metrics(): return jsonify(calculate_metrics())
@app.post('/api/reports/validation')
def validation_export(): return jsonify(write_reports())
@app.get('/api/export/<table>.json')
def export_json(table):
    allowed={'products':'Product','shipments':'Shipment','containers':'Container','assignments':'ContainerAssignment','sensors':'Sensor','readings':'SensorReading','routes':'RouteStop','policies':'ThresholdPolicy','excursions':'Excursion','custody':'CustodyEvent','lifecycle':'LifecycleEvent','actions':'CorrectiveAction','notifications':'Notification'}
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
    allowed={'products':'Product','shipments':'Shipment','containers':'Container','assignments':'ContainerAssignment','sensors':'Sensor','excursions':'Excursion','actions':'CorrectiveAction','readings':'SensorReading','routes':'RouteStop','policies':'ThresholdPolicy','custody':'CustodyEvent','lifecycle':'LifecycleEvent'}
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
