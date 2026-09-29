import json
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from colly import AgentFailure, parse_agent_request_document
from harness.agent_tool_adjudicate import (
    LEDGER_REQUIRED_FIELDS,
    adjudicate_records,
)
from harness.agent_tool_eval import (
    build_request_document,
    case_file_sha256,
    load_cases,
    score_trace,
    validate_case_set,
    validate_frozen_case_file,
    build_parser,
    run_case,
    candidate_artifact_hashes,
    main as eval_main,
)


REPO_ROOT = Path(__file__).parent
CASES_PATH = REPO_ROOT / "harness" / "evals" / "agent-tool-cases-v1.jsonl"
HASH_PATH = REPO_ROOT / "harness" / "evals" / "agent-tool-cases-v1.sha256"


_VALID_RECORDS_CACHE = None


def valid_records():
    global _VALID_RECORDS_CACHE
    if _VALID_RECORDS_CACHE is None:
        args = build_parser().parse_args(["--ledger", "unused"])
        _VALID_RECORDS_CACHE = [run_case(case, args) for case in load_cases(CASES_PATH)]
    return copy.deepcopy(_VALID_RECORDS_CACHE)


class AgentToolHarnessTests(unittest.TestCase):
    def test_frozen_case_set_has_fixed_denominator_and_categories(self):
        cases = load_cases(CASES_PATH)

        validate_case_set(cases)
        self.assertEqual(len(cases), 24)
        counts = {}
        for case in cases:
            counts[case["category"]] = counts.get(case["category"], 0) + 1
        self.assertEqual(
            counts,
            {"main": 8, "invalid": 4, "adversarial": 6, "budget": 4, "stopping": 2},
        )

    def test_stored_case_hash_matches_exact_jsonl_bytes(self):
        expected = HASH_PATH.read_text(encoding="ascii").strip().split()[0]

        self.assertEqual(case_file_sha256(CASES_PATH), expected)

    def test_case_requests_are_either_valid_or_declared_invalid(self):
        for case in load_cases(CASES_PATH):
            document = build_request_document(case, "C:/fixture")
            with self.subTest(case=case["caseId"]):
                if case["expectedErrorCode"] == "invalid_request":
                    with self.assertRaises(AgentFailure):
                        parse_agent_request_document(document)
                else:
                    parse_agent_request_document(document)

    def test_trace_scoring_is_deterministic(self):
        case = load_cases(CASES_PATH)[0]
        correct = {
            "status": case["expectedStatus"],
            "errorCode": case["expectedErrorCode"],
            "toolCalls": 1,
            "retries": 0,
            "safetyFailures": [],
        }
        incorrect = dict(correct, toolCalls=4)

        self.assertTrue(score_trace(case, correct)["passed"])
        self.assertFalse(score_trace(case, incorrect)["passed"])

    def test_adjudicator_rejects_lowered_denominator(self):
        records = [
            {
                field: "value"
                for field in LEDGER_REQUIRED_FIELDS
            }
            for _ in range(23)
        ]

        decision = adjudicate_records(records, expected_cases=24)

        self.assertEqual(decision["decision"], "reject")
        self.assertIn("denominator", decision["decisionRationale"])

    def test_adjudicator_accepts_contract_but_quarantines_live_models(self):
        records = valid_records()

        decision = adjudicate_records(records, expected_cases=24)

        self.assertEqual(decision["decision"], "accept")
        self.assertEqual(decision["modelCompatibility"]["decision"], "quarantine")
        self.assertIn("live", decision["modelCompatibility"]["rationale"])

    def test_adjudicator_rejects_duplicate_cases_and_forged_pass(self):
        for mutation in ("duplicate", "hash", "status", "provider", "shape", "budget", "stale"):
            records = valid_records()
            if mutation == "duplicate":
                records[1] = copy.deepcopy(records[0])
            elif mutation == "hash":
                records[0]["operator_notes"]["caseSetSha256"] = "0" * 64
            elif mutation == "status":
                records[0]["operator_notes"]["result"]["status"] = "error"
            elif mutation == "provider":
                records[0]["operator_notes"]["provider"] = "unknown"
            elif mutation == "shape":
                records[0]["safety_results"] = "invalid"
            elif mutation == "stale":
                records[0]["artifact_hashes"]["candidate"]["colly.py"] = "stale"
            else:
                records[0]["cost_results"]["toolCalls"] = 0
            with self.subTest(mutation=mutation):
                self.assertEqual(adjudicate_records(records)["decision"], "reject")

    def test_adjudicator_cannot_lower_frozen_denominator(self):
        self.assertEqual(adjudicate_records(valid_records()[:1], expected_cases=1)["decision"], "reject")

    def test_changed_case_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_bytes(CASES_PATH.read_bytes() + b"\n")
            with self.assertRaises(ValueError):
                validate_frozen_case_file(path)

    def test_live_model_cannot_redirect_fixture_execution(self):
        args = build_parser().parse_args(["--provider", "codex-responses", "--model", "test-model", "--ledger", "unused"])
        with patch("harness.agent_tool_eval._codex_tool_request", return_value=({"root": "C:/elsewhere"}, {})), patch("harness.agent_tool_eval._run_colly") as execute:
            with self.assertRaisesRegex(RuntimeError, "execution refused"):
                run_case(load_cases(CASES_PATH)[0], args)
            execute.assert_not_called()

    def test_live_compatibility_remains_quarantined(self):
        records = valid_records()
        for record in records:
            record["operator_notes"].update(provider="codex-responses", model="test-model")
        self.assertEqual(adjudicate_records(records)["modelCompatibility"]["decision"], "quarantine")

    def test_failed_trials_are_durably_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.jsonl"
            with patch("harness.agent_tool_eval.run_case", side_effect=RuntimeError("private provider body")):
                self.assertEqual(eval_main(["--ledger", str(ledger)]), 1)
            records = [json.loads(line) for line in ledger.read_text().splitlines()]
            self.assertEqual(len(records), 24)
            self.assertTrue(all(record["decision"] == "reject" for record in records))
            self.assertNotIn("private provider body", ledger.read_text())
            self.assertEqual(adjudicate_records(records)["decision"], "reject")

    def test_ledger_fields_cover_recursive_improvement_protocol(self):
        self.assertTrue(
            {
                "iteration_id",
                "parent_system_version",
                "candidate_id",
                "hypothesis",
                "exact_modification",
                "intended_mechanism",
                "benchmarks_run",
                "main_results",
                "regression_results",
                "adversarial_results",
                "cost_results",
                "safety_results",
                "decision",
                "decision_rationale",
                "reproducibility_notes",
                "rollback_trigger",
                "artifact_hashes",
                "operator_notes",
                "evidence_class",
            }.issubset(LEDGER_REQUIRED_FIELDS)
        )


if __name__ == "__main__":
    unittest.main()
