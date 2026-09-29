"""Regression checks for recovered Windows and clean-checkout CI failures."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from harness.agent_tool_eval import materialize_fixture, build_request_document
from harness.artifact_verifier import normalized_receipt_request, load_json
from harness.task_handoff import _safe_source

REPO = Path(__file__).resolve().parent

class DeliveryPortabilityTests(unittest.TestCase):
    def test_fixture_resolves_root_before_request_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);(base/'nested').mkdir()
            root=materialize_fixture({'fixture':{}},base/'nested'/'..')
            self.assertEqual(root,root.resolve(strict=True))
            req=build_request_document({'request':{}},str(root))
            self.assertEqual(normalized_receipt_request(req)['root'],root.as_posix())

    def test_receipt_normalization_never_resolves_historical_root(self):
        request={'root':'C:/Users/operator/workspace', 'output':'audit/bundle.md',
                 'inventory':'audit/inventory.json', 'files':['src/a.py']}
        with mock.patch.object(Path,'resolve',side_effect=AssertionError('replay must be portable')):
            self.assertEqual(normalized_receipt_request(request),request)

    def test_source_boundary_accepts_canonical_alias_of_same_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'nested').mkdir();(root/'a.py').write_bytes(b'VALUE=1\n')
            self.assertEqual(_safe_source(root/'nested'/'..','a.py',100),b'VALUE=1\n')
            with self.assertRaises(ValueError):_safe_source(root,'../outside.py',100)

    def test_clean_smoke_runs_and_never_snapshots_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'smoke'
            proc=subprocess.run([sys.executable,str(REPO/'scripts/run_value_smoke.py'),
                                 '--output',str(output)],cwd=REPO,capture_output=True,timeout=90)
            self.assertEqual(proc.returncode,0,proc.stderr.decode('utf-8','replace'))
            report=load_json((output/'comparison.json').read_bytes())
            self.assertEqual({k:v['tasksPassed'] for k,v in report['summaries'].items()},
                             {'direct':2,'colly-none':2,'colly-focused':3})
            self.assertFalse(report['adoption']['automaticPromotion'])
            for manifest in output.glob('*/contract.json'):
                paths=load_json(manifest.read_bytes())['sourceHashes']
                self.assertFalse(any('__pycache__' in p or p.endswith('.pyc') for p in paths))

if __name__=='__main__':unittest.main()
