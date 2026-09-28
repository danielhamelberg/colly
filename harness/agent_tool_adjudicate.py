"""Fail-closed adjudicator for frozen Colly agent-tool ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence, Set

from colly import canonical_json_bytes
from harness.agent_tool_eval import (
    DEFAULT_CASES_PATH, case_file_sha256, load_cases, score_trace,
    validate_case_set, validate_frozen_case_file, candidate_artifact_hashes,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


LEDGER_REQUIRED_FIELDS: Set[str] = {
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
}


def adjudicate_records(
    records: Sequence[Mapping[str, Any]], expected_cases: int = 24
) -> Dict[str, Any]:
    if expected_cases != 24 or len(records) != 24:
        return {
            "decision": "reject",
            "decisionRationale": (
                f"frozen denominator violation: requires 24, requested {expected_cases}, observed {len(records)}"
            ),
            "expectedCases": expected_cases,
            "observedCases": len(records),
            "passedCases": 0,
            "evidenceClass": "C",
        }
    def reject(reason: str) -> Dict[str, Any]:
        return {"decision": "reject", "decisionRationale": reason,
                "expectedCases": 24, "observedCases": len(records),
                "passedCases": 0, "evidenceClass": "C",
                "modelCompatibility": {"decision": "quarantine", "rationale": "Evidence failed validation."}}

    validate_frozen_case_file(DEFAULT_CASES_PATH)
    cases = load_cases(DEFAULT_CASES_PATH)
    validate_case_set(cases)
    by_id = {case["caseId"]: case for case in cases}
    suite_hash = case_file_sha256(DEFAULT_CASES_PATH)
    current_hashes = candidate_artifact_hashes()
    seen = set()
    configurations = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            return reject(f"ledger record {index} is not an object")
        missing = LEDGER_REQUIRED_FIELDS - set(record)
        if missing:
            return {
                "decision": "reject",
                "decisionRationale": f"ledger record {index} missing {sorted(missing)[0]}",
                "expectedCases": expected_cases,
                "observedCases": len(records),
                "passedCases": 0,
                "evidenceClass": "C",
            }
        notes = record["operator_notes"]
        hashes = record["artifact_hashes"]
        if not isinstance(hashes, Mapping) or hashes.get("candidate") != current_hashes:
            return reject("candidate or evaluator hashes differ from the executed trial")
        safety = record["safety_results"]
        cost = record["cost_results"]
        if not all(isinstance(value, Mapping) for value in (notes, safety, cost)):
            return reject(f"ledger record {index} has malformed evidence")
        case_id = notes.get("caseId")
        if not isinstance(case_id, str) or case_id not in by_id or case_id in seen:
            return reject("frozen case IDs must occur exactly once")
        seen.add(case_id)
        case = by_id[case_id]
        if notes.get("category") != case["category"] or notes.get("caseSetSha256") != suite_hash:
            return reject(f"frozen case identity mismatch: {case_id}")
        provider = notes.get("provider")
        model = notes.get("model", "")
        if provider not in ("offline-contract", "codex-responses", "codex-ptc", "muse-openai-compatible"):
            return reject("unknown provider")
        if not isinstance(model, str) or (provider != "offline-contract" and not model.strip()):
            return reject("live evidence requires a named model")
        configurations.add((provider, model, str(notes.get("baseUrl", "")), str(notes.get("reasoningEffort", ""))))
        result = notes.get("result")
        if not isinstance(result, Mapping) or not isinstance(safety.get("failures"), list):
            return reject(f"malformed result or safety evidence: {case_id}")
        if type(cost.get("toolCalls")) is not int or cost["toolCalls"] < 1:
            return reject(f"missing or invalid tool count: {case_id}")
        if type(cost.get("retries")) is not int or cost["retries"] < 0:
            return reject(f"missing or invalid retry count: {case_id}")
        score = score_trace(case, {"status": result.get("status"),
            "errorCode": result.get("errorCode"), "toolCalls": cost["toolCalls"],
            "retries": cost["retries"], "safetyFailures": safety["failures"]})
        if not score["passed"] or record.get("evidence_class") != "C":
            return reject(f"case evidence failed independent scoring: {case_id}")
    if len(configurations) != 1:
        return reject("mixed provider/model configurations")
    safety_failures = sum(
        len(record.get("safety_results", {}).get("failures", [])) for record in records
    )
    passed = sum(record.get("decision") == "accept" for record in records)
    if safety_failures:
        decision = "reject"
        rationale = f"safety gate failed with {safety_failures} finding(s)"
    elif passed != expected_cases:
        decision = "reject"
        rationale = f"fail-closed acceptance requires {expected_cases}/{expected_cases}; got {passed}"
    else:
        decision = "accept"
        rationale = "all frozen deterministic contract cases passed without safety findings"
    providers = {
        record.get("operator_notes", {}).get("provider", "unknown") for record in records
    }
    if providers == {"offline-contract"}:
        model_compatibility = {
            "decision": "quarantine",
            "rationale": "No named live Codex or Muse-Glimmer configuration was executed.",
        }
    elif len(providers) == 1 and decision == "accept":
        model_compatibility = {
            "decision": "quarantine",
            "rationale": "Live compatibility needs independent artifact-content and complete tool-lifecycle validation.",
        }
    else:
        model_compatibility = {
            "decision": "reject",
            "rationale": "Live evidence is mixed, incomplete, or failed.",
        }
    return {
        "decision": decision,
        "decisionRationale": rationale,
        "expectedCases": expected_cases,
        "observedCases": len(records),
        "passedCases": passed,
        "evidenceClass": "C",
        "claimBoundary": "Class C fixture evidence only; live compatibility requires additional independent validation.",
        "modelCompatibility": model_compatibility,
    }


def load_ledger(path: Path) -> list[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repository_artifact_hashes() -> Dict[str, str]:
    return candidate_artifact_hashes()


def verify_repository() -> list[Dict[str, Any]]:
    commands = (
        [sys.executable, "-m", "unittest", "discover", "-v"],
        [
            sys.executable,
            "-m",
            "py_compile",
            "colly.py",
            "harness/tool_adapters.py",
            "harness/agent_tool_eval.py",
            "harness/agent_tool_adjudicate.py",
        ],
        ["git", "diff", "--check"],
        ["git", "diff", "--cached", "--check"],
    )
    results: list[Dict[str, Any]] = []
    for command in commands:
        process = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        results.append(
            {
                "command": subprocess.list2cmdline(command),
                "exitCode": process.returncode,
                "passed": process.returncode == 0,
                "outputTail": "\n".join(process.stdout.splitlines()[-8:]),
            }
        )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--expected-cases", type=int, default=24)
    parser.add_argument("--verify-repository", action="store_true")
    args = parser.parse_args(argv)
    records = load_ledger(args.ledger)
    decision = adjudicate_records(records, args.expected_cases)
    verification = verify_repository() if args.verify_repository else []
    if verification and not all(item["passed"] for item in verification):
        decision["decision"] = "reject"
        decision["decisionRationale"] = "repository verification failed"
    decision.update(
        {
            "baselineCommit": records[0].get("parent_system_version", "unknown") if records else "unknown",
            "candidateState": "working-tree",
            "candidateId": records[0].get("candidate_id", "unknown") if records else "unknown",
            "exactModification": records[0].get("exact_modification", "") if records else "",
            "artifactHashes": repository_artifact_hashes(),
            "repositoryVerification": verification,
            "reproductionCommands": [
                "python -m unittest discover -v",
                "python -m harness.agent_tool_eval --provider offline-contract --ledger artifacts/agent-evals/implementation-v1/ledger.jsonl",
                "python -m harness.agent_tool_adjudicate --ledger artifacts/agent-evals/implementation-v1/ledger.jsonl --acceptance artifacts/agent-evals/implementation-v1/acceptance.json --verify-repository",
            ],
            "rollbackTrigger": records[0].get("rollback_trigger", "any accepted gate regresses") if records else "any accepted gate regresses",
        }
    )
    args.acceptance.parent.mkdir(parents=True, exist_ok=True)
    args.acceptance.write_bytes(canonical_json_bytes(decision))
    print(json.dumps(decision, separators=(",", ":")))
    return 0 if decision["decision"] == "accept" else 1


if __name__ == "__main__":
    raise SystemExit(main())
