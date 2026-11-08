"""Stream a portable SQL INSERT seed without loading the data set in memory."""
import argparse
from pathlib import Path
import sqlite3

from database import connect, initialize
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
            out.write('-- Apply database/schema.sql before this seed.\n'
                      '-- DetectionAnalysis is operational state: rerun detection after restore.\n'
                      'PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\nDELETE FROM DetectionAnalysis;\n')
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


def restore_sql_seed(seed_path, database_path, refresh_reports=True):
    """Restore deterministic business rows, rebuild analysis, and verify readiness."""
    seed_path=Path(seed_path);database_path=Path(database_path)
    if not seed_path.is_file():raise FileNotFoundError(seed_path)
    initialize(database_path)
    raw=sqlite3.connect(database_path)
    try:
        raw.executescript(seed_path.read_text(encoding='utf8'))
        raw.execute('DELETE FROM DetectionAnalysis')
        raw.commit()
        violations=raw.execute('PRAGMA foreign_key_check').fetchall()
        if violations:raise ValueError(f'Restored seed has {len(violations)} foreign-key violations')
    finally:raw.close()
    from services.engine import detect_excursions
    db=connect(database_path)
    try:
        shipment_ids=[row[0] for row in db.execute('SELECT shipment_id FROM Shipment ORDER BY shipment_id')]
        for shipment_id in shipment_ids:detect_excursions(db,shipment_id)
    finally:db.close()
    from services.validation import validate, write_reports
    result=write_reports(database_path) if refresh_reports else validate(database_path)
    if result['status']!='PASS':
        raise ValueError('Restored database did not reach validation-ready state')
    return {'database':str(database_path),'shipments_reanalyzed':len(shipment_ids),
            'validation_status':result['status'],'passed_checks':result['passed_checks'],
            'total_checks':result['total_checks']}


def main():
    parser=argparse.ArgumentParser(description='Restore a ColdChain Guardian SQL seed and rebuild operational analysis.')
    parser.add_argument('--restore',required=True,help='path to generated seed.sql')
    parser.add_argument('--database',required=True,help='destination SQLite database path')
    parser.add_argument('--no-reports',action='store_true',help='verify readiness without refreshing report artifacts')
    args=parser.parse_args()
    result=restore_sql_seed(args.restore,args.database,not args.no_reports)
    print(f"READY: {result['shipments_reanalyzed']} shipments reanalyzed; validation "
          f"{result['passed_checks']}/{result['total_checks']} PASS")


if __name__=='__main__':main()
