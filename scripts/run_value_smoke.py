"""Execute the handoff/comparison plumbing on three deterministic repair fixtures.

These are synthetic fixtures, not independent maintenance tickets. The worker
is a fixed string-repair program, not an LLM or a local SelfInfer installation.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.artifact_verifier import canonical
from harness.value_experiment import run_comparison

WORKER = r'''import json,sys,re
p=json.load(sys.stdin)
# A deliberately simple test worker; uses only supplied packet text.
key=p["taskId"]
name={"addition":"addition.py","multiplication":"multiplication.py","dependency":"dependency.py"}[key]
body={"addition":"def calculate(a,b):\n    return a+b\n","multiplication":"def calculate(a,b):\n    return a*b\n","dependency":"VALUE = 42\n"}[key]
entry=next((f for f in p["inventory"]["files"] if f["relativePath"]==name),None)
edits=[] if entry is None else [dict(path=name,sourceSha256=entry["sourceSha256"],newText=body)]
json.dump(dict(schemaVersion=1,taskId=key,contextSha256=p["contextSha256"],edits=edits,usage=dict(modelCalls=0,modelCostUSD=0,kind="deterministic-fixture")),sys.stdout)
'''


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix='colly-value-smoke-') as d:
        base=Path(d);repo=base/'repo';repo.mkdir();worker=base/'worker.py';worker.write_text(WORKER,encoding='utf-8')
        source={'addition.py':'def calculate(a,b):\n    return a-b\n',
                'multiplication.py':'def calculate(a,b):\n    return a+b\n',
                'dependency.py':'VALUE = 0\n','facade.py':'from dependency import VALUE\n'}
        tests={'addition':'from addition import calculate\nassert calculate(4,2)==6\n',
               'multiplication':'from multiplication import calculate\nassert calculate(4,2)==8\n',
               'dependency':'from facade import VALUE\nassert VALUE==42\n'}
        for path,text in source.items():(repo/path).write_text(text,encoding='utf-8')
        tasks=[]
        for name,text in tests.items():
            test='verify_'+name+'.py';(repo/test).write_text(text,encoding='utf-8')
            tasks.append({'taskId':name,'instruction':'Repair the specified fixture and preserve the public interface.',
                          'files':['facade.py' if name=='dependency' else name+'.py'],
                          'allowedEdits':[name+'.py'],'testCommand':['{python}',test]})
            baseline=subprocess.run([sys.executable,test],cwd=repo,capture_output=True)
            if baseline.returncode==0:raise RuntimeError('fixture must fail before repair')
        subprocess.run(['git','init','-q'],cwd=repo,check=True);subprocess.run(['git','add','.'],cwd=repo,check=True)
        report=run_comparison(repo,tasks,args.output,[sys.executable,str(worker)],
                              'deterministic-fixture-worker-v1','synthetic-handoff-smoke')
        if report['summaries']['direct']['tasksPassed']!=2 or report['summaries']['colly-none']['tasksPassed']!=2 or report['summaries']['colly-focused']['tasksPassed']!=3:
            raise RuntimeError('unexpected fixture outcome')
        if report['adoption']['automaticPromotion']:raise RuntimeError('smoke data must never authorize adoption')
        print(canonical(report).decode(),end='')
    return 0

if __name__=='__main__':raise SystemExit(main())
