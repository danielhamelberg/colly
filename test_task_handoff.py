"""Real foreground handoff checks with a deterministic test solver (not an LLM)."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from harness.artifact_verifier import canonical, load_json, sha
from harness.task_handoff import prepare_handoff, complete_handoff, validate_proposal, bounded_process, DEFAULT_LIMITS


class TaskHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='colly-task-');self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.repo=self.base/'source';self.repo.mkdir()
        (self.repo/'calculator.py').write_text('def add(a,b):\n    return a-b\n',encoding='utf-8')
        (self.repo/'test_calc.py').write_text('import unittest\nfrom calculator import add\nclass TestAdd(unittest.TestCase):\n def test_add(self): self.assertEqual(add(4,2),6)\n',encoding='utf-8')
        (self.repo/'facade.py').write_text('from calculator import add\n',encoding='utf-8')
        subprocess.run(['git','init','-q'],cwd=self.repo,check=True)
        subprocess.run(['git','add','.'],cwd=self.repo,check=True)
        self.task={'taskId':'add','instruction':'Fix addition; preserve API.','files':['calculator.py'],
                   'allowedEdits':['calculator.py'],'testCommand':['{python}','-m','unittest','test_calc','-v']}
        self.run=self.base/'run'

    def prepare(self,mode='colly-none'):
        return prepare_handoff(self.repo,self.run,self.task,mode)

    def proposal(self):
        packet=load_json((self.run/'solver-input.json').read_bytes())
        old=(self.repo/'calculator.py').read_bytes()
        return dict(schemaVersion=1,taskId='add',contextSha256=packet['contextSha256'],
                    edits=[dict(path='calculator.py',sourceSha256=sha(old),newText=old.decode().replace('a-b','a+b'))],
                    usage={'modelCalls':0,'modelCostUSD':0,'kind':'deterministic-test-fixture'})

    def test_context_packet_contains_actual_verified_source(self):
        state=self.prepare();packet=load_json((self.run/'solver-input.json').read_bytes())
        self.assertIn('return a-b',packet['context']);self.assertEqual(state['status'],'prepared')
        self.assertEqual(sha(packet['context'].encode()),packet['contextSha256'])
        self.assertNotIn(str(self.repo),packet['context'])

    def test_patch_passes_tests_without_changing_original(self):
        original=(self.repo/'calculator.py').read_bytes();self.prepare()
        proposal=self.base/'proposal.json';proposal.write_bytes(canonical(self.proposal()))
        receipt=complete_handoff(self.run,proposal_path=proposal,solver_label='deterministic fixture')
        self.assertEqual(receipt['status'],'task-passed');self.assertFalse(receipt['automaticPromotion'])
        self.assertEqual((self.repo/'calculator.py').read_bytes(),original)
        self.assertIsNotNone(receipt['cost']['completionElapsedMs'])
        with self.assertRaises(ValueError):complete_handoff(self.run,proposal_path=proposal)

    def test_all_three_context_modes_are_operational(self):
        for mode in ('direct','colly-none','colly-focused'):
            self.run=self.base/mode;state=self.prepare(mode)
            self.assertGreater(state['budget']['contextBytes'],0)

    def test_focused_expansion_reuses_colly(self):
        self.task['files']=['facade.py'];self.prepare('colly-focused')
        packet=load_json((self.run/'solver-input.json').read_bytes())
        self.assertIn('return a-b',packet['context'])
        self.assertEqual({x['relativePath'] for x in packet['inventory']['files']},{'facade.py','calculator.py'})

    def test_unexposed_or_unapproved_file_edits_rejected(self):
        self.prepare();packet=load_json((self.run/'solver-input.json').read_bytes());contract=load_json((self.run/'contract.json').read_bytes())
        for path in ('test_calc.py','../secret','facade.py'):
            proposal=self.proposal();proposal['edits'][0]['path']=path
            with self.subTest(path=path),self.assertRaises(ValueError):validate_proposal(proposal,packet,contract,self.run/'repository')

    def test_stale_preimage_rejected(self):
        self.prepare();proposal=self.proposal();proposal['edits'][0]['sourceSha256']='0'*64
        with self.assertRaises(ValueError):validate_proposal(proposal,load_json((self.run/'solver-input.json').read_bytes()),load_json((self.run/'contract.json').read_bytes()),self.run/'repository')

    def test_duplicate_and_empty_patches_rejected(self):
        self.prepare();packet=load_json((self.run/'solver-input.json').read_bytes());contract=load_json((self.run/'contract.json').read_bytes())
        for edits in ([],self.proposal()['edits']*2):
            p=self.proposal();p['edits']=edits
            with self.assertRaises(ValueError):validate_proposal(p,packet,contract,self.run/'repository')

    def test_failed_solver_costs_persist_and_no_retry(self):
        self.prepare();receipt=complete_handoff(self.run,solver_command=[sys.executable,'-c','raise SystemExit(9)'],solver_label='failing fixture')
        self.assertEqual(receipt['status'],'failed');self.assertEqual(receipt['cost']['solverCalls'],1)
        self.assertFalse(receipt['cost']['accountingComplete']);self.assertIsNone(receipt['cost']['modelCostUSD'])
        self.assertTrue((self.run/'receipt.json').exists())

    def test_real_solver_receives_packet_on_stdin(self):
        self.prepare()
        # Test-only worker reads only the packet, emits a preimage-bound replacement.
        script=self.base/'worker.py'
        script.write_text('import json,sys\np=json.load(sys.stdin)\nr=p["inventory"]["files"][0]\njson.dump(dict(schemaVersion=1,taskId=p["taskId"],contextSha256=p["contextSha256"],edits=[dict(path=r["relativePath"],sourceSha256=r["sourceSha256"],newText="def add(a,b):\\n    return a+b\\n")],usage={"modelCalls":0,"modelCostUSD":0}),sys.stdout)\n',encoding='utf-8')
        receipt=complete_handoff(self.run,solver_command=[sys.executable,str(script)],solver_label='deterministic fixture')
        self.assertEqual(receipt['status'],'task-passed')

    def test_bound_output_and_timeout(self):
        result=bounded_process([sys.executable,'-c','print("x"*50000)'],b'',self.base,10,1024)
        self.assertTrue(result['outputExceeded']);self.assertLessEqual(len(result['stdout']),1024)
        result=bounded_process([sys.executable,'-c','import time;time.sleep(10)'],b'',self.base,0.05,1024)
        self.assertTrue(result['timedOut'])

    def test_changed_contract_and_packet_rejected(self):
        self.prepare();p=self.run/'contract.json';value=load_json(p.read_bytes());value['limits']['maxEdits']=99;p.write_bytes(canonical(value))
        with self.assertRaises(ValueError):complete_handoff(self.run,solver_command=[sys.executable,'-c','pass'])

    def test_source_and_context_budgets_fail_closed(self):
        limits=dict(DEFAULT_LIMITS,maxContextBytes=1)
        with self.assertRaises(ValueError):prepare_handoff(self.repo,self.run,self.task,'direct',limits)
        self.assertTrue((self.run/'preparation-failed.json').exists())

    def test_run_inside_source_is_refused(self):
        with self.assertRaises(ValueError):prepare_handoff(self.repo,self.repo/'bad-run',self.task,'direct')
        self.assertFalse((self.repo/'bad-run').exists())


    def test_explicit_new_file_creation_is_bounded(self):
        self.task['allowedEdits'].append('new_module.py');self.prepare()
        p=self.proposal();p['edits'].append(dict(path='new_module.py',sourceSha256=None,newText='VALUE=1\n'))
        packet=load_json((self.run/'solver-input.json').read_bytes());contract=load_json((self.run/'contract.json').read_bytes())
        changes=validate_proposal(p,packet,contract,self.run/'repository')
        self.assertEqual(changes['new_module.py'],b'VALUE=1\n')
        p['edits'][-1]['sourceSha256']='0'*64
        with self.assertRaises(ValueError):validate_proposal(p,packet,contract,self.run/'repository')

    def test_failed_snapshot_preserves_attempt_and_unknown_cost(self):
        limits=dict(DEFAULT_LIMITS,maxSnapshotFiles=1)
        with self.assertRaises(ValueError):prepare_handoff(self.repo,self.run,self.task,'direct',limits)
        failed=load_json((self.run/'preparation-failed.json').read_bytes())
        self.assertFalse(failed['budget']['accountingComplete'])
        self.assertIsNone(failed['budget']['sourceSnapshotBytes'])
        self.assertEqual(failed['budget']['contextCalls'],0)
        self.assertGreaterEqual(failed['budget']['contextPreparationMs'],0)

if __name__=='__main__':unittest.main()
