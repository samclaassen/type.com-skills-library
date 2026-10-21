"""Source meeting titles never become operator-authored stage labels (fresh review regression)."""
import copy,json,os,subprocess,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parent
checks=0
with tempfile.TemporaryDirectory(prefix='interview-title-r12-') as temp:
 for mode,filename in [('single','sample_bundle_single.json'),('panel','sample_bundle_panel.json')]:
  for title in ['Interview — she will need dialysis','Interview — reconciliation exercise']:
   data=json.loads((root/filename).read_text())
   for iv in data['interviews']:
    iv.pop('stage_id',None);iv['questions_asked']=[];iv['title']=title
   bundle=Path(temp)/'bundle.json';bundle.write_text(json.dumps(data))
   for provider in ([None,'greenhouse','ashby','lever'] if mode=='single' else [None]):
    cmd=[sys.executable,'-B',str(root/'build_scorecard.py'),'--bundle',str(bundle),'--rubric',str(root/'sample_rubric_finance_ops_analyst.json'),'--as-of','2026-10-02','--json']
    if provider:cmd+=['--ats-draft',provider]
    p=subprocess.run(cmd,cwd=str(root),stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    assert p.returncode==0,(mode,provider,p.stderr);checks+=1
    payload=json.loads(p.stdout)
    assert title not in p.stdout,(mode,provider,'source title leaked');checks+=1
    assert 'Interview (stage not assigned)' in payload['markdown'],(mode,provider,'expected fixed fallback');checks+=1
    assert 'Jordan R.' in payload['markdown'] and 'Reconciliation' in payload['markdown'],(mode,provider,'useful candidate/rubric missing');checks+=1
print('PASS: %d source-title output checks'%checks)
