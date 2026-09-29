"""Foreground Colly -> solver -> patch -> trusted-test handoff.

Original repositories are read-only. Changes run in a disposable tracked-file
snapshot. Solver commands and test commands are operator-authorized host code;
this adapter is NOT an OS sandbox and does not expand desktop permissions.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.artifact_verifier import (
    canonical, load_json, relative_path, sha, verify_artifact_bytes,
    normalized_receipt_request,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIMITS = {
    "maxSnapshotFiles": 2000, "maxSnapshotBytes": 64 * 1024 * 1024,
    "maxSourceBytes": 512 * 1024, "maxContextBytes": 768 * 1024,
    "maxContextFiles": 32, "maxScanEntries": 5000,
    "contextDeadlineSeconds": 30, "solverDeadlineSeconds": 120,
    "testDeadlineSeconds": 120, "maxPatchBytes": 512 * 1024,
    "maxEdits": 4, "maxSolverCalls": 1, "maxContextCalls": 1,
}


def _write_json(path: Path, value: Any) -> None:
    data = canonical(value)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def bounded_process(command: Sequence[str], data: bytes, cwd: Path,
                    timeout: float, output_limit: int) -> dict[str, Any]:
    """Drain bounded output, count all emitted bytes, and retain failures.

    Child-tree containment requires the caller's execution substrate. Trusted
    commands only: killing this process alone is not a hostile-code boundary.
    """
    if not command or any(type(arg) is not str or not arg for arg in command):
        raise ValueError("invalid operator command")
    started = time.perf_counter()
    proc = subprocess.Popen(list(command), cwd=str(cwd), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    counts = {"stdout": 0, "stderr": 0}
    exceeded = threading.Event()
    def drain(name, stream):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            counts[name] += len(chunk)
            remaining = output_limit - len(outputs[name])
            outputs[name].extend(chunk[:max(0, remaining)])
            if counts[name] > output_limit:
                exceeded.set()
                try: proc.kill()
                except OSError: pass
        stream.close()
    threads = [threading.Thread(target=drain, args=(name, stream), daemon=True)
               for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for thread in threads: thread.start()
    def feed():
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
        finally:
            try: proc.stdin.close()
            except OSError: pass
    writer = threading.Thread(target=feed, daemon=True); writer.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True; proc.kill(); proc.wait()
    for thread in threads: thread.join(timeout=2)
    writer.join(timeout=1)
    drain_complete = not any(t.is_alive() for t in threads)
    return {"exitCode": proc.returncode, "elapsedMs": round((time.perf_counter()-started)*1000, 3),
            "stdout": bytes(outputs['stdout']), "stderr": bytes(outputs['stderr']),
            "stdoutBytes": counts['stdout'], "stderrBytes": counts['stderr'],
            "timedOut": timed_out, "outputExceeded": exceeded.is_set(),
            "drainComplete": drain_complete}


def snapshot_repository(source: Path, destination: Path, limits: Mapping[str, int]) -> dict[str, str]:
    source = source.resolve(strict=True)
    if destination.resolve().is_relative_to(source):
        raise ValueError("run directory must be outside the source repository")
    discovery = bounded_process(['git', 'ls-files', '-z'], b'', source,
                                limits['contextDeadlineSeconds'],
                                min(8 * 1024 * 1024, limits['maxSnapshotFiles'] * 4096))
    if (discovery['exitCode'] or discovery['timedOut'] or discovery['outputExceeded']
            or not discovery['drainComplete']):
        raise ValueError('snapshot-discovery-budget-or-process-failure')
    tracked = discovery['stdout'].decode('utf-8').split('\0')
    names = sorted(set(n for n in tracked if n), key=lambda n:n.encode('utf-8'))
    if not names or len(names) > limits['maxSnapshotFiles']:
        raise ValueError('snapshot-file-budget-or-empty')
    destination.mkdir(parents=True)
    hashes, total = {}, 0
    for name in names:
        relative_path(name)
        path = source/name
        # Reject links in every component, including symlinked directories.
        check = path
        while check != source:
            if check.is_symlink(): raise ValueError('snapshot-symlink')
            check = check.parent
        if not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError('snapshot-file-unavailable')
        available = limits['maxSnapshotBytes'] - total
        if path.stat().st_size > available: raise ValueError('snapshot-byte-budget')
        with path.open('rb') as stream: data = stream.read(available + 1)
        total += len(data)
        if total > limits['maxSnapshotBytes']: raise ValueError('snapshot-byte-budget')
        target = destination/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        hashes[name] = sha(data)
    return hashes


def _safe_source(root: Path, name: str, max_bytes: int) -> bytes:
    relative_path(name)
    path = root/name
    check = path
    while check != root:
        if check.is_symlink(): raise ValueError('source-symlink')
        check = check.parent
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('source-unavailable')
    with path.open('rb') as stream: data=stream.read(max_bytes+1)
    if len(data)>max_bytes:raise ValueError('source-byte-budget')
    return data


def prepare_handoff(source: Path, run: Path, task: Mapping[str, Any], mode: str,
                    limits: Mapping[str, int] | None = None) -> dict[str, Any]:
    limits = dict(DEFAULT_LIMITS if limits is None else limits)
    if set(limits)!=set(DEFAULT_LIMITS) or any(type(v)is not int or v<1 for v in limits.values()):
        raise ValueError('invalid limits')
    if mode not in {'direct', 'colly-none', 'colly-focused'}:raise ValueError('invalid context mode')
    if set(task)!={'taskId','instruction','files','allowedEdits','testCommand'}:raise ValueError('task shape')
    if not task['taskId'] or not task['instruction'] or not task['files']:raise ValueError('empty task')
    for name in list(task['files'])+list(task['allowedEdits']):relative_path(name)
    if not task['testCommand'] or any(type(v)is not str for v in task['testCommand']):raise ValueError('test command')
    source=source.resolve(strict=True)
    run=run.resolve()
    if run.is_relative_to(source):raise ValueError('run directory must be outside source repository')
    run.mkdir(parents=True, exist_ok=False)
    started=time.perf_counter(); root=run/'repository'
    budget={'sourceSnapshotBytes':None,'sourceSnapshotFiles':None,
            'contextCalls':0,'solverCalls':0,'modelCalls':0,
            'modelCostUSD':0.0,'accountingComplete':False}
    try:
        hashes=snapshot_repository(source,root,limits)
        # Code and acceptance requirements are outside the solver's authority.
        contract={'version':1,'task':dict(task),'limits':limits,'mode':mode,
                  'sourceHashes':hashes,'collectorSha256':sha((REPO_ROOT/'colly.py').read_bytes()),
                  'executorHashes':{name:sha((REPO_ROOT/name).read_bytes()) for name in
                                    ('harness/task_handoff.py','harness/artifact_verifier.py')}}
        _write_json(run/'contract.json',contract)
        budget.update(sourceSnapshotBytes=sum((root/p).stat().st_size for p in hashes),
                      sourceSnapshotFiles=len(hashes),contextCalls=1,accountingComplete=True)
    except Exception as exc:
        budget['contextPreparationMs']=round((time.perf_counter()-started)*1000,3)
        _write_json(run/'preparation-failed.json',{'status':'failed','errorType':type(exc).__name__,
                    'reason':str(exc)[:400],'budget':budget,'automaticPromotion':False})
        raise
    try:
        if mode=='direct':
            # Honest fixed direct-read baseline: identical operator-selected seed files.
            # This is not a baseline for a full autonomous search agent.
            parts=[];sources={};read_bytes=0
            for name in sorted(set(task['files'])):
                raw=_safe_source(root,name,limits['maxSourceBytes']-read_bytes)
                read_bytes+=len(raw);sources[name]=raw
                parts.append(f'FILE {json.dumps(name)}\n'+raw.decode('utf-8')+'\nEND FILE\n')
            context='\n'.join(parts).encode('utf-8')
            inventory={'files':[{'relativePath':p,'sourceSha256':sha(b),'sourceSizeBytes':len(b)} for p,b in sources.items()]}
            if len(sources)>limits['maxContextFiles']:raise ValueError('context-file-budget')
        else:
            req=dict(schemaVersion=1,root=root.as_posix(),files=sorted(set(task['files'])),
                     output='.colly-handoff/context.md',inventory='.colly-handoff/inventory.json',
                     replace=False,dependencyGraph=False,braPreset='focused' if mode=='colly-focused' else 'none',
                     autoTruncate=False,encoding='utf-8',maxFiles=limits['maxContextFiles'],
                     maxSourceBytes=limits['maxSourceBytes'],maxBundleBytes=limits['maxContextBytes'],
                     maxScanEntries=limits['maxScanEntries'],deadlineSeconds=limits['contextDeadlineSeconds'])
            req=normalized_receipt_request(req)
            _write_json(run/'request.json',req)
            transport=bounded_process([sys.executable,str(REPO_ROOT/'colly.py'),'--agent','--request-json','-'],
                                      canonical(req),root,limits['contextDeadlineSeconds']+5,128*1024)
            (run/'collector.stdout').write_bytes(transport.pop('stdout'))
            (run/'collector.stderr').write_bytes(transport.pop('stderr'))
            _write_json(run/'collector-transport.json',transport)
            if transport['exitCode'] or transport['timedOut'] or transport['outputExceeded'] or not transport['drainComplete']:
                raise ValueError('collector failed')
            result=load_json((run/'collector.stdout').read_bytes())
            context=_safe_source(root,req['output'],limits['maxContextBytes'])
            raw_inventory=_safe_source(root,req['inventory'],2*1024*1024)
            inventory=load_json(raw_inventory)
            sources={};read_bytes=0
            for item in inventory['files']:
                name=relative_path(item['relativePath'])
                if name not in hashes:raise ValueError('context file outside pinned snapshot')
                raw=_safe_source(root,name,limits['maxSourceBytes']-read_bytes);read_bytes+=len(raw)
                if sha(raw)!=hashes[name]:raise ValueError('context source changed')
                sources[name]=raw
            findings=verify_artifact_bytes(req,result,sources,{req['output']:context,req['inventory']:raw_inventory},
                                           task['files'] if mode=='colly-none' else None)
            if findings:raise ValueError(findings[0])
        if len(context)>limits['maxContextBytes']:raise ValueError('context-byte-budget')
        payload={'schemaVersion':1,'taskId':task['taskId'],'instruction':task['instruction'],
                 'contextSha256':sha(context),'context':context.decode('utf-8'),
                 'inventory':inventory,'allowedEdits':list(task['allowedEdits']),
                 'responseContract':{'fields':['schemaVersion','taskId','contextSha256','edits','usage'],
                   'editFields':['path','sourceSha256','newText'],
                   'maxEdits':limits['maxEdits'],'maxPatchBytes':limits['maxPatchBytes']}}
        _write_json(run/'solver-input.json',payload)
        budget.update(contextBytes=len(context),selectedSourceBytes=read_bytes,
                      selectedFiles=len(sources),contextPreparationMs=round((time.perf_counter()-started)*1000,3))
        state={'status':'prepared','contractSha256':sha(canonical(contract)),
               'contextSha256':sha(context),'packetSha256':sha(canonical(payload)),
               'budget':budget,'claim':'verified context ready; no task completion claimed'}
        _write_json(run/'prepared.json',state)
        return state
    except Exception as exc:
        budget.update(contextPreparationMs=round((time.perf_counter()-started)*1000,3))
        _write_json(run/'preparation-failed.json',{'status':'failed','errorType':type(exc).__name__,
                    'reason':str(exc)[:400],'budget':budget,'automaticPromotion':False})
        raise


def validate_proposal(proposal: Any, packet: Mapping[str, Any], contract: Mapping[str, Any], root: Path) -> dict[str, bytes]:
    required={'schemaVersion','taskId','contextSha256','edits','usage'}
    if type(proposal)is not dict or set(proposal)!=required:raise ValueError('proposal-shape')
    if type(proposal['schemaVersion'])is not int or proposal['schemaVersion']!=1 or proposal['taskId']!=packet['taskId'] or proposal['contextSha256']!=packet['contextSha256']:
        raise ValueError('proposal-binding')
    edits=proposal['edits'];limits=contract['limits']
    if type(edits)is not list or not edits or len(edits)>limits['maxEdits']:raise ValueError('edit-count')
    allowed=set(contract['task']['allowedEdits']); exposed={r['relativePath'] for r in packet['inventory']['files']}
    changes={};total=0
    for edit in edits:
        if type(edit)is not dict or set(edit)!={'path','sourceSha256','newText'}:raise ValueError('edit-shape')
        name=relative_path(edit['path'])
        if name not in allowed or name in changes:raise ValueError('edit-authority')
        if type(edit['newText'])is not str:raise ValueError('edit-content')
        if name in contract['sourceHashes']:
            if name not in exposed:raise ValueError('edit-not-in-context')
            old=_safe_source(root,name,limits['maxSnapshotBytes'])
            if sha(old)!=edit['sourceSha256'] or sha(old)!=contract['sourceHashes'][name]:raise ValueError('stale-source')
        else:
            # Creation is permitted only at an explicitly authorized absent path.
            target=root/name
            if edit['sourceSha256'] is not None or target.exists() or target.is_symlink():raise ValueError('new-file-precondition')
            check=target.parent
            while check!=root:
                if check.is_symlink():raise ValueError('new-file-symlink')
                check=check.parent
        data=edit['newText'].encode('utf-8');total+=len(data)
        if total>limits['maxPatchBytes']:raise ValueError('patch-byte-budget')
        changes[name]=data
    return changes


def complete_handoff(run: Path, solver_command: Sequence[str] | None = None,
                     proposal_path: Path | None = None, solver_label: str='operator-submitted') -> dict[str, Any]:
    run=run.resolve(strict=True)
    if (run/'started.json').exists():raise ValueError('trial already attempted; create a fresh run')
    contract=load_json((run/'contract.json').read_bytes());state=load_json((run/'prepared.json').read_bytes())
    packet_bytes=(run/'solver-input.json').read_bytes();packet=load_json(packet_bytes)
    if sha(canonical(contract))!=state['contractSha256'] or sha(packet_bytes)!=state['packetSha256']:
        raise ValueError('changed contract or solver packet')
    if contract['executorHashes'] != {name:sha((REPO_ROOT/name).read_bytes()) for name in
                                     ('harness/task_handoff.py','harness/artifact_verifier.py')}:
        raise ValueError('executor changed after preparation')
    if (solver_command is None)==(proposal_path is None):raise ValueError('select exactly one solver or proposal')
    _write_json(run/'started.json',{'contractSha256':state['contractSha256'],'solverLabel':solver_label})
    limits=contract['limits'];root=run/'repository';cost=dict(state['budget']);cost['solverCalls']=1
    cost.update(modelCalls=None,modelCostUSD=None,accountingComplete=False)
    started=time.perf_counter();status='failed';reason='';tests=None;changes={}
    try:
        if solver_command is not None:
            empty=run/'solver-cwd';empty.mkdir()
            transport=bounded_process(solver_command,packet_bytes,empty,limits['solverDeadlineSeconds'],limits['maxPatchBytes']*2)
            raw=transport.pop('stdout');(run/'solver.stderr').write_bytes(transport.pop('stderr'))
            _write_json(run/'solver-transport.json',transport)
            cost['solverElapsedMs']=transport['elapsedMs']
            if transport['exitCode'] or transport['timedOut'] or transport['outputExceeded'] or not transport['drainComplete']:
                raise ValueError('solver-process-failed')
        else:
            if proposal_path.stat().st_size>limits['maxPatchBytes']*2:raise ValueError('proposal-size')
            raw=proposal_path.read_bytes();cost['solverElapsedMs']=None
        (run/'proposal.json').write_bytes(raw)
        proposal=load_json(raw)
        changes=validate_proposal(proposal,packet,contract,root)
        usage=proposal.get('usage')
        # Provider-reported usage is retained, never silently treated as verified billing.
        cost['solverReportedUsage']=usage
        if type(usage)is dict:
            cost['modelCalls']=usage.get('modelCalls');cost['modelCostUSD']=usage.get('modelCostUSD')
        cost['accountingComplete']=False
        # Validate all edits before touching the isolated working copy.
        for path,data in changes.items():
            target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        command=[sys.executable if arg=='{python}' else arg for arg in contract['task']['testCommand']]
        tests=bounded_process(command,b'',root,limits['testDeadlineSeconds'],512*1024)
        (run/'test.stdout').write_bytes(tests.pop('stdout'));(run/'test.stderr').write_bytes(tests.pop('stderr'))
        _write_json(run/'test-result.json',tests)
        status='task-passed' if tests['exitCode']==0 and not tests['timedOut'] and not tests['outputExceeded'] and tests['drainComplete'] else 'task-failed'
        reason='trusted task command '+('passed' if status=='task-passed' else 'failed')
    except Exception as exc:
        reason=type(exc).__name__+': '+str(exc)[:400]
    cost['completionElapsedMs']=round((time.perf_counter()-started)*1000,3)
    cost['patchBytes']=sum(len(v) for v in changes.values())
    receipt={'schemaVersion':1,'status':status,'reason':reason,'solverLabel':solver_label,
             'contractSha256':state['contractSha256'],'packetSha256':state['packetSha256'],
             'contextSha256':packet['contextSha256'],'sourceHashes':contract['sourceHashes'],
             'resultHashes':{p:sha(b) for p,b in changes.items()},'cost':cost,
             'automaticPromotion':False,'deployment':'isolated snapshot only',
             'solverIsolation':'empty working directory, NOT OS sandbox'}
    _write_json(run/'receipt.json',receipt)
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    prepare=sub.add_parser('prepare');prepare.add_argument('--repo',type=Path,required=True)
    prepare.add_argument('--task-json',type=Path,required=True);prepare.add_argument('--run',type=Path,required=True)
    prepare.add_argument('--mode',choices=['direct','colly-none','colly-focused'],default='colly-none')
    finish=sub.add_parser('complete');finish.add_argument('--run',type=Path,required=True)
    group=finish.add_mutually_exclusive_group(required=True)
    group.add_argument('--solver-command-json');group.add_argument('--proposal',type=Path)
    finish.add_argument('--solver-label',default='operator-submitted')
    args=parser.parse_args(argv)
    if args.command=='prepare':result=prepare_handoff(args.repo,args.run,load_json(args.task_json.read_bytes()),args.mode)
    else:result=complete_handoff(args.run,load_json(args.solver_command_json.encode()) if args.solver_command_json else None,args.proposal,args.solver_label)
    print(canonical(result).decode(),end='')
    return 0 if result['status'] in ('prepared','task-passed') else 1

if __name__=='__main__':raise SystemExit(main())
