"""Paired context-workflow comparison with fixed budgets and no automatic promotion.

Use independently selected maintenance tasks and a named fresh-process solver
for a capability comparison. The included smoke run tests plumbing only. It is
not evidence that a model solves real maintenance tickets better with Colly.
"""
from __future__ import annotations
import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
from harness.artifact_verifier import canonical, load_json, sha
from harness.task_handoff import DEFAULT_LIMITS, prepare_handoff, complete_handoff

MODES = ('direct','colly-none','colly-focused')


def adoption_decision(report: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed until independent solver/outcome/cost evidence is available."""
    reasons=[]
    if report.get('evidenceKind')!='independent-maintenance-trial':reasons.append('not-independent-maintenance-evidence')
    if not report.get('allAccountingComplete'):reasons.append('total-resource-accounting-incomplete')
    if not report.get('allTrialsComplete'):reasons.append('incomplete-trials')
    # This release does not certify generalization from small observed score differences.
    reasons.append('no-reviewed-generalization-and-adoption-contract')
    return {'decision':'retain-current-policy','automaticPromotion':False,'reasons':reasons,
            'policyChange':None,'comparisonBaseline':'colly-none',
            'scope':'recommendation receipt; active BRIK installation not inspected or changed'}


def run_comparison(repo: Path, tasks: Sequence[Mapping[str, Any]], output: Path,
                   solver_command: Sequence[str], solver_label: str,
                   evidence_kind: str='unreviewed-maintenance-trial',
                   limits: Mapping[str, int] | None=None, seed: int=20260929) -> dict[str, Any]:
    if not tasks or len({t['taskId'] for t in tasks})!=len(tasks):raise ValueError('nonempty unique task IDs required')
    if not solver_label.strip() or not solver_command:raise ValueError('named solver configuration required')
    limits=dict(DEFAULT_LIMITS if limits is None else limits)
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    contract={'schemaVersion':1,'tasks':list(tasks),'modes':list(MODES),'limits':limits,
              'solverCommand':list(solver_command),'solverLabel':solver_label,'orderSeed':seed,
              'evidenceKind':evidence_kind,'baselineScope':'matched operator-selected seed-file reads; not an autonomous search agent',
              'stoppingRule':'exactly one attempt per task per mode; no selection after viewing results'}
    (output/'experiment-contract.json').write_bytes(canonical(contract))
    rng=random.Random(seed);rows=[];expected_hashes=None
    with (output/'trials.jsonl').open('xb') as ledger:
        for index,task in enumerate(tasks):
            modes=list(MODES);rng.shuffle(modes)
            for mode in modes:
                run=output/f'{index:03d}-{mode}'
                try:
                    prepare_handoff(repo,run,task,mode,limits)
                    source=load_json((run/'contract.json').read_bytes())['sourceHashes']
                    if expected_hashes is None:expected_hashes=source
                    if source!=expected_hashes:raise ValueError('repository changed during comparison')
                    receipt=complete_handoff(run,solver_command=solver_command,solver_label=solver_label)
                    row={'taskId':task['taskId'],'mode':mode,'status':receipt['status'],
                         'reason':receipt['reason'],'receiptSha256':sha(canonical(receipt)),
                         'runPath':run.name,'cost':receipt['cost']}
                except Exception as exc:
                    failed=run/'preparation-failed.json'
                    budget=load_json(failed.read_bytes()).get('budget',{}) if failed.exists() else {}
                    row={'taskId':task['taskId'],'mode':mode,'status':'failed','reason':type(exc).__name__,
                         'runPath':run.name,'cost':dict(budget,accountingComplete=False)}
                rows.append(row);ledger.write(canonical(row));ledger.flush()
    summaries={}
    for mode in MODES:
        selected=[r for r in rows if r['mode']==mode]
        summaries[mode]={'attempts':len(selected),'tasksPassed':sum(r['status']=='task-passed' for r in selected),
            'contextBytes':sum(r['cost'].get('contextBytes',0) for r in selected),
            'recordedElapsedMs':round(sum(r['cost'].get('contextPreparationMs',0)+r['cost'].get('completionElapsedMs',0) for r in selected),3),
            'missingCostRecords':sum(not r['cost'].get('accountingComplete',False) for r in selected)}
    report={'schemaVersion':1,'experimentContractSha256':sha(canonical(contract)),
            'ledgerSha256':sha((output/'trials.jsonl').read_bytes()),'evidenceKind':evidence_kind,
            'solverLabel':solver_label,'taskCount':len(tasks),'summaries':summaries,
            'allTrialsComplete':len(rows)==len(tasks)*len(MODES) and all('receiptSha256' in r for r in rows),
            'allAccountingComplete':all(r['cost'].get('accountingComplete') is True for r in rows),
            'limitations':['Direct baseline is matched-seed reading, not a full autonomous search agent.',
                           'Host commands are trusted; disposable snapshots are not OS sandboxes.',
                           'Reported model usage is not independently verified billing.',
                           'No model-performance gain follows from deterministic smoke fixtures.']}
    report['adoption']=adoption_decision(report)
    (output/'comparison.json').write_bytes(canonical(report))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--tasks',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--solver-command-json',required=True)
    parser.add_argument('--solver-label',required=True)
    args=parser.parse_args(argv)
    result=run_comparison(args.repo,load_json(args.tasks.read_bytes()),args.output,
                          load_json(args.solver_command_json.encode()),args.solver_label)
    print(canonical(result).decode(),end='');return 0

if __name__=='__main__':raise SystemExit(main())
