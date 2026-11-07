import csv,json,subprocess,sys
from pathlib import Path
def test_generator_reproducible():
 root=Path(__file__).resolve().parents[1]
 subprocess.run([sys.executable,'generate_data.py','--scale','starter','--seed','20261022'],cwd=root,check=True,capture_output=True,text=True)
 first=(root/'data/source_manifest.json').read_bytes()
 summary1=(root/'data/generated/generation_summary.json').read_bytes()
 subprocess.run([sys.executable,'generate_data.py','--scale','starter','--seed','20261022'],cwd=root,check=True,capture_output=True,text=True)
 second=(root/'data/source_manifest.json').read_bytes()
 summary2=(root/'data/generated/generation_summary.json').read_bytes()
 assert json.loads(first)==json.loads(second)
 assert json.loads(summary1)==json.loads(summary2)
