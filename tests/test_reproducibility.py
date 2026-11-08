import json,subprocess,sys,os
from pathlib import Path

from database import connect
from generator.sql_seed import restore_sql_seed
from services.assessment import assess_all
from services.validation import validate

def test_generator_reproducible(tmp_path):
 root=Path(__file__).resolve().parents[1]
 manifests=[];summaries=[];runs={}
 for run in ('one','two'):
  data=tmp_path/run/'data';generated=data/'generated';reports=tmp_path/run/'reports'
  env=os.environ.copy();env.update({'COLDCHAIN_DB':str(data/'coldchain.db'),
       'COLDCHAIN_GENERATED_DIR':str(generated),'COLDCHAIN_MANIFEST':str(data/'source_manifest.json'),
       'COLDCHAIN_REPORTS_DIR':str(reports)})
  subprocess.run([sys.executable,'generate_data.py','--scale','starter','--seed','20261022','--sql'],
                 cwd=root,check=True,capture_output=True,text=True,env=env)
  runs[run]=(data,generated)
  manifests.append(json.loads((data/'source_manifest.json').read_text(encoding='utf8')))
  summaries.append(json.loads((generated/'generation_summary.json').read_text(encoding='utf8')))
  evaluation=json.loads((generated/'evaluation_summary.json').read_text(encoding='utf8'))
  assert evaluation['detection_confusion']['FP']==0
  assert evaluation['detection_confusion']['FN']==0
 assert manifests[0]==manifests[1]
 assert summaries[0]==summaries[1]
 restored=tmp_path/'restored.db'
 ready=restore_sql_seed(runs['one'][1]/'seed.sql',restored,refresh_reports=False)
 assert ready['shipments_reanalyzed']==12 and ready['validation_status']=='PASS'
 original=connect(runs['one'][0]/'coldchain.db');restored_db=connect(restored)
 try:
  assert assess_all(original)['counts']==assess_all(restored_db)['counts']
  assert restored_db.execute('PRAGMA foreign_key_check').fetchall()==[]
  assert restored_db.execute('SELECT COUNT(*) FROM DetectionAnalysis WHERE status=\'SUCCESS\'').fetchone()[0]==12
  assert validate(restored)['status']=='PASS'
 finally:
  original.close();restored_db.close()
