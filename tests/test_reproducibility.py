import json,subprocess,sys,os
from pathlib import Path

def test_generator_reproducible(tmp_path):
 root=Path(__file__).resolve().parents[1]
 manifests=[];summaries=[]
 for run in ('one','two'):
  data=tmp_path/run/'data';generated=data/'generated';reports=tmp_path/run/'reports'
  env=os.environ.copy();env.update({'COLDCHAIN_DB':str(data/'coldchain.db'),
       'COLDCHAIN_GENERATED_DIR':str(generated),'COLDCHAIN_MANIFEST':str(data/'source_manifest.json'),
       'COLDCHAIN_REPORTS_DIR':str(reports)})
  subprocess.run([sys.executable,'generate_data.py','--scale','starter','--seed','20261022'],
                 cwd=root,check=True,capture_output=True,text=True,env=env)
  manifests.append(json.loads((data/'source_manifest.json').read_text(encoding='utf8')))
  summaries.append(json.loads((generated/'generation_summary.json').read_text(encoding='utf8')))
 assert manifests[0]==manifests[1]
 assert summaries[0]==summaries[1]
