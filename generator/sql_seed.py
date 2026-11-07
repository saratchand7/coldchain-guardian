"""Stream a portable SQL INSERT seed without loading the data set in memory."""
from pathlib import Path
from database import connect
from config import GENERATED

TABLES=('Product','Shipment','Container','ContainerAssignment','MonitoringWindow','Sensor','SensorCalibrationHistory','SensorReading','RouteStop','ThresholdPolicy','Excursion','ExcursionRevision','ExcursionStatusEvent','CustodyEvent','LifecycleEvent','CorrectiveAction','CorrectiveActionEvent','Notification')
DELETE_ORDER=tuple(reversed(TABLES))

def _literal(value):
    if value is None:return 'NULL'
    if isinstance(value,(int,float)):return str(value)
    return "'"+str(value).replace("'","''")+"'"

def export_sql_seed(path=None):
    db=connect(path) if path else connect()
    target=GENERATED/'seed.sql';target.parent.mkdir(parents=True,exist_ok=True)
    try:
        with target.open('w',encoding='utf8',newline='\n') as out:
            out.write('-- Apply database/schema.sql before this seed.\nPRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\n')
            for table in DELETE_ORDER:out.write('DELETE FROM '+table+';\n')
            for table in TABLES:
                cur=db.execute('SELECT * FROM "'+table+'" ORDER BY 1')
                columns=[x[0] for x in cur.description];col_sql=','.join('"'+x+'"' for x in columns)
                while rows:=cur.fetchmany(2000):
                    for row in rows:
                        values=','.join(_literal(v) for v in row)
                        out.write('INSERT INTO "'+table+'" ('+col_sql+') VALUES ('+values+');\n')
            out.write('COMMIT;\nPRAGMA foreign_keys=ON;\n')
    finally:db.close()
    return {'file':target.name,'bytes':target.stat().st_size}
