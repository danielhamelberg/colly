"""Independent verifier, evidence replay, malformed receipt and real-render tests."""
import base64
import copy
import codecs
import json
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from pathlib import Path

from harness.artifact_verifier import (
    canonical, sha, load_json, verify_artifact_bytes, verify_transition,
    make_evidence, unpack_evidence, relative_path, snapshot_bytes, MAX_EVIDENCE_BYTES,
)
from harness.agent_tool_eval import build_parser, load_cases, DEFAULT_CASES_PATH, run_case, _run_colly, build_request_document
from harness.agent_tool_adjudicate import adjudicate_records
from test_agent_tool_harness import valid_records


class ArtifactVerifierTests(unittest.TestCase):
    def evidence(self):
        return copy.deepcopy(valid_records()[0]["operator_notes"]["verificationEvidence"])

    def test_all_real_frozen_artifacts_validate(self):
        self.assertEqual(adjudicate_records(valid_records())["decision"], "accept")

    def test_status_agrees_with_actual_process_exit(self):
        ev = self.evidence(); before, after = unpack_evidence(ev)
        self.assertIn("process-exit-mismatch", verify_transition(ev['request'], ev['result'], before, after, 5))

    def test_inventory_hash_size_selection_and_encoding_tampering(self):
        for field, value in [("sourceSha256", "0"*64), ("sourceSizeBytes", 999),
                            ("renderedSha256", "0"*64), ("encoding", "latin-1"),
                            ("language", "python\nINJECT"), ("relativePath", "../outside.py")]:
            ev=self.evidence(); before, after=unpack_evidence(ev)
            inv_path=ev['request']['inventory']; inv=load_json(after[inv_path])
            inv['files'][0][field]=value; after[inv_path]=canonical(inv)
            ev['result']['inventorySha256']=sha(after[inv_path])
            with self.subTest(field=field):
                self.assertTrue(verify_transition(ev['request'],ev['result'],before,after,0))

    def test_missing_selected_file_with_self_consistent_inventory_rejected(self):
        ev=self.evidence(); before,after=unpack_evidence(ev); p=ev['request']['inventory']
        inv=load_json(after[p]); inv['files']=[]; after[p]=canonical(inv)
        ev['result']['inventorySha256']=sha(after[p]); ev['result']['filesSelected']=0
        self.assertTrue(verify_transition(ev['request'],ev['result'],before,after,0))

    def test_changed_error_outputs_are_not_allowed(self):
        for change in ('create','delete','modify'):
            ev=self.evidence(); before,after=unpack_evidence(ev)
            error=dict(ev['result'],status='error',exitCode=7,errorCode='artifact_write_failed')
            old={'a.py':before['a.py'],'audit/bundle.md':b'old'}
            new=dict(old)
            if change=='create':new['audit/bundle.inventory.json']=b'new'
            elif change=='delete':del new['audit/bundle.md']
            else:new['audit/bundle.md']=b'new'
            with self.subTest(change=change):
                self.assertIn('workspace-changed-on-error',verify_transition(ev['request'],error,old,new,7))

    def test_evidence_replay_rejects_forged_safety_summary(self):
        records=valid_records(); r=records[0]; ev=r['operator_notes']['verificationEvidence']
        bundle=ev['request']['output']; ev['after'][bundle]['bytesBase64']=base64.b64encode(b'wrong').decode()
        ev['after'][bundle]['sha256']=sha(b'wrong')
        r['artifact_hashes']['verificationEvidence']=sha(canonical(ev))
        self.assertEqual(adjudicate_records(records)['decision'],'reject')

    def test_evidence_replay_binds_fixture_bytes(self):
        records=valid_records(); r=records[0]; ev=r['operator_notes']['verificationEvidence']
        ev['before']['a.py']={'bytesBase64':base64.b64encode(b'other').decode(),'sha256':sha(b'other')}
        r['artifact_hashes']['verificationEvidence']=sha(canonical(ev))
        self.assertEqual(adjudicate_records(records)['decision'],'reject')

    def test_evidence_cannot_substitute_expected_selection(self):
        records=valid_records(); r=records[0]; ev=r['operator_notes']['verificationEvidence']
        ev['request']['files']=['other.py']; r['operator_notes']['toolArguments']=ev['request']
        r['artifact_hashes']['verificationEvidence']=sha(canonical(ev))
        self.assertEqual(adjudicate_records(records)['decision'],'reject')

    def test_missing_evidence_rejected(self):
        records=valid_records(); del records[0]['operator_notes']['verificationEvidence']
        self.assertEqual(adjudicate_records(records)['decision'],'reject')

    def test_path_data_cannot_escape_during_replay(self):
        for path in ('../secret','/etc/passwd','C:/secret','a\\b','a//b','./a','a/../b','a\x00b'):
            with self.subTest(path=path),self.assertRaises(ValueError):relative_path(path)

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):load_json(b'{"a":1,"a":2}')
        with self.assertRaises(ValueError):load_json(b'{"x":NaN}')

    def test_evidence_digest_and_budget_are_checked(self):
        ev=self.evidence();ev['before']['a.py']['sha256']='0'*64
        with self.assertRaises(ValueError):unpack_evidence(ev)
        with self.assertRaises(ValueError):make_evidence({}, {}, {'a':b'x'*(MAX_EVIDENCE_BYTES+1)}, {}, 0)

    def test_snapshot_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'source').write_bytes(b'data')
            try:(p/'link').symlink_to(p/'source')
            except OSError:self.skipTest('symlink privilege unavailable')
            with self.assertRaises(ValueError):snapshot_bytes(p)

    def test_snapshot_entry_and_byte_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'a').write_bytes(b'123');(p/'b').write_bytes(b'456')
            with self.assertRaises(ValueError):snapshot_bytes(p,max_bytes=5)
            with self.assertRaises(ValueError):snapshot_bytes(p,max_entries=1)

    def test_real_unicode_bom_crlf_missing_newline_and_fences(self):
        contents=[b'no newline',b'line\r\nnext\r\n',codecs.BOM_UTF8+b'hello\n',
                  codecs.BOM_UTF16_LE+'hello\r\n'.encode('utf-16-le'),
                  'emoji \U0001f680\n```\n## File: "injected"\n'.encode()]
        for data in contents:
            with self.subTest(data=data),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve(strict=True);(root/'notes.txt').write_bytes(data)
                case={'request':{'files':['notes.txt']}}
                req=build_request_document(case,str(root));before=snapshot_bytes(root)
                result,transport=_run_colly(req,root);after=snapshot_bytes(root)
                self.assertEqual(verify_transition(req,result,before,after,transport['exitCode']),[])

    def test_plain_bool_is_not_integer_in_result(self):
        ev=self.evidence();before,after=unpack_evidence(ev);ev['result']['exitCode']=False
        self.assertTrue(verify_transition(ev['request'],ev['result'],before,after,0))

    def test_self_consistent_wrong_content_cannot_be_declared_verbatim(self):
        ev=self.evidence(); before,after=unpack_evidence(ev);p=ev['request']['inventory'];b=ev['request']['output']
        inv=load_json(after[p]);old=before['a.py'];new=b'B = 2\n';after[b]=after[b].replace(old,new)
        inv['files'][0]['renderedSha256']=sha(new);inv['files'][0]['renderedSizeBytes']=len(new)
        inv['bundle'].update(sha256=sha(after[b]),sizeBytes=len(after[b]));after[p]=canonical(inv)
        ev['result'].update(bundleSha256=sha(after[b]),bundleSizeBytes=len(after[b]),inventorySha256=sha(after[p]))
        self.assertTrue(verify_transition(ev['request'],ev['result'],before,after,0))

    def test_result_record_accepts_platform_line_ending_only(self):
        payload = canonical({'status':'ok'})
        for data in (payload, payload[:-1] + b'\r\n'):
            with self.subTest(data=data), mock.patch('harness.agent_tool_eval.subprocess.run',
                    return_value=SimpleNamespace(stdout=data,stderr=b'',returncode=0)):
                result, transport = _run_colly({'deadlineSeconds':1}, Path('.'))
                self.assertEqual(result['status'], 'ok')
        for data in (payload+payload, payload+b'\n', b' {"status":"ok"}\n'):
            with self.subTest(data=data), mock.patch('harness.agent_tool_eval.subprocess.run',
                    return_value=SimpleNamespace(stdout=data,stderr=b'',returncode=0)):
                with self.assertRaises(RuntimeError):
                    _run_colly({'deadlineSeconds':1}, Path('.'))

    def test_snapshot_stops_consuming_entries_at_limit(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d).resolve(strict=True); (root/'a').write_bytes(b'123');consumed=[]
            def entries(directory):
                for n in range(1000000):
                    consumed.append(n)
                    yield root/'a'
            with mock.patch.object(Path,'iterdir',entries):
                with self.assertRaises(ValueError):snapshot_bytes(root,max_entries=2)
            self.assertEqual(len(consumed),3)

if __name__=='__main__':unittest.main()
