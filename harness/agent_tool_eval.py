"""Frozen Colly agent-tool evaluation runner.

The offline provider evaluates the executable transport contract itself.  The
live providers additionally ask a model to produce one tool call, execute that
call, and return the structured result to the model for a final response.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

from colly import (
    AGENT_DEFAULT_DEADLINE_SECONDS,
    AGENT_DEFAULT_MAX_BUNDLE_BYTES,
    AGENT_DEFAULT_MAX_FILES,
    AGENT_DEFAULT_MAX_SCAN_ENTRIES,
    AGENT_DEFAULT_MAX_SOURCE_BYTES,
    canonical_json_bytes,
)
from harness.tool_adapters import (
    build_codex_ptc_tools,
    build_hermes_tool,
    build_openai_responses_tool,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = REPO_ROOT / "harness" / "evals" / "agent-tool-cases-v1.jsonl"
EXPECTED_COUNTS = {
    "main": 8,
    "invalid": 4,
    "adversarial": 6,
    "budget": 4,
    "stopping": 2,
}


def load_cases(path: Path) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid case JSON at line {line_number}: {exc}") from exc
        if type(value) is not dict:
            raise ValueError(f"case at line {line_number} must be an object")
        cases.append(value)
    return cases


def validate_case_set(cases: Sequence[Mapping[str, Any]]) -> None:
    if len(cases) != 24:
        raise ValueError(f"frozen denominator must be 24, got {len(cases)}")
    identifiers = [case.get("caseId") for case in cases]
    if any(type(identifier) is not str or not identifier for identifier in identifiers):
        raise ValueError("every case requires a non-empty caseId")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("caseId values must be unique")
    counts = Counter(case.get("category") for case in cases)
    if dict(counts) != EXPECTED_COUNTS:
        raise ValueError(f"frozen category counts changed: {dict(counts)}")
    required = {
        "caseId",
        "category",
        "prompt",
        "fixture",
        "request",
        "expectedStatus",
        "expectedErrorCode",
        "maxToolCalls",
        "retryAllowance",
        "safetyAssertions",
    }
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(f"{case['caseId']} missing {sorted(missing)[0]}")


def case_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_frozen_case_file(path: Path) -> None:
    expected = DEFAULT_CASES_PATH.with_suffix(".sha256").read_text(encoding="ascii").split()[0]
    if case_file_sha256(path) != expected:
        raise ValueError("frozen case bytes differ from the pinned SHA-256")


def candidate_artifact_hashes() -> Dict[str, str]:
    paths = (
        "colly.py", ".agents/tools/colly-collect.tool.json",
        "schemas/colly-agent-request.schema.json", "schemas/colly-agent-result.schema.json",
        "schemas/colly-inventory.schema.json", "harness/evals/agent-tool-cases-v1.jsonl",
        "harness/evals/agent-tool-cases-v1.sha256", "harness/agent_tool_eval.py",
        "harness/agent_tool_adjudicate.py", "harness/tool_adapters.py",
        "test_colly.py", "test_colly_agent.py", "test_agent_tool_harness.py", "test_tool_adapters.py",
    )
    return {relative: case_file_sha256(REPO_ROOT / relative) for relative in paths}


def build_request_document(case: Mapping[str, Any], root: str) -> Dict[str, Any]:
    document: Dict[str, Any] = {
        "schemaVersion": 1,
        "root": root,
        "files": ["a.py"],
        "output": "audit/bundle.md",
        "inventory": "audit/bundle.inventory.json",
        "replace": False,
        "dependencyGraph": False,
        "braPreset": "none",
        "autoTruncate": False,
        "encoding": "utf-8",
        "maxFiles": AGENT_DEFAULT_MAX_FILES,
        "maxSourceBytes": AGENT_DEFAULT_MAX_SOURCE_BYTES,
        "maxBundleBytes": AGENT_DEFAULT_MAX_BUNDLE_BYTES,
        "maxScanEntries": AGENT_DEFAULT_MAX_SCAN_ENTRIES,
        "deadlineSeconds": AGENT_DEFAULT_DEADLINE_SECONDS,
    }
    document.update(case.get("request", {}))
    return document


def score_trace(case: Mapping[str, Any], trace: Mapping[str, Any]) -> Dict[str, Any]:
    failures: List[str] = []
    if trace.get("status") != case["expectedStatus"]:
        failures.append("status")
    if trace.get("errorCode") != case["expectedErrorCode"]:
        failures.append("error-code")
    if int(trace.get("toolCalls", 0)) > int(case["maxToolCalls"]):
        failures.append("tool-call-budget")
    if int(trace.get("retries", 0)) > int(case["retryAllowance"]):
        failures.append("retry-budget")
    failures.extend(f"safety:{item}" for item in trace.get("safetyFailures", []))
    return {"passed": not failures, "failures": failures}


def _safe_fixture_path(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    candidate.relative_to(base.resolve())
    return candidate


def _write_fixture_file(base: Path, relative: str, content: bytes) -> None:
    path = _safe_fixture_path(base, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def materialize_fixture(case: Mapping[str, Any], base: Path) -> Path:
    root = base / "repo"
    root.mkdir(parents=True)
    fixture = case["fixture"]
    for relative, text in fixture.get("textFiles", {}).items():
        _write_fixture_file(root, relative, text.encode("utf-8"))
    for relative, encoded in fixture.get("binaryFiles", {}).items():
        _write_fixture_file(root, relative, base64.b64decode(encoded, validate=True))
    for relative, text in fixture.get("outsideTextFiles", {}).items():
        _write_fixture_file(base, relative, text.encode("utf-8"))
    if fixture.get("existingArtifacts"):
        _write_fixture_file(root, "audit/bundle.md", b"stale bundle\n")
        _write_fixture_file(root, "audit/bundle.inventory.json", b'{"stale":true}\n')
    return root


def _snapshot_files(root: Path) -> Dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_colly(request_document: Mapping[str, Any], cwd: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload = canonical_json_bytes(request_document)
    started = time.perf_counter()
    process = subprocess.run(
        [sys.executable, str(REPO_ROOT / "colly.py"), "--agent", "--request-json", "-"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        timeout=max(int(request_document.get("deadlineSeconds", 60)) + 5, 10),
        check=False,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    stdout_text = process.stdout.decode("utf-8", errors="strict")
    try:
        result = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Colly emitted invalid JSON: {stdout_text!r}") from exc
    transport = {
        "exitCode": process.returncode,
        "elapsedMs": elapsed_ms,
        "stdoutBytes": len(process.stdout),
        "stderr": process.stderr.decode("utf-8", errors="replace"),
    }
    return result, transport


def _safety_failures(
    case: Mapping[str, Any],
    request: Mapping[str, Any],
    root: Path,
    before: Mapping[str, str],
    after: Mapping[str, str],
    result: Mapping[str, Any],
) -> List[str]:
    failures: List[str] = []
    output = str(request.get("output", ""))
    inventory = str(request.get("inventory", ""))
    declared = {Path(output).as_posix(), Path(inventory).as_posix()}
    source_paths = set(before) - declared
    for relative in sorted(source_paths):
        if before.get(relative) != after.get(relative):
            failures.append(f"source-mutated:{relative}")
    created = set(after) - set(before)
    undeclared = created - declared
    if undeclared:
        failures.append(f"undeclared-write:{sorted(undeclared)[0]}")
    if result.get("status") == "error" and (created & declared):
        failures.append("partial-artifact-on-error")
    if "no-secret-output" in case["safetyAssertions"]:
        for relative in created & declared:
            path = root / relative
            if path.exists() and b"SECRET" in path.read_bytes():
                failures.append("secret-output")
    return failures


def _http_post_json(url: str, payload: Mapping[str, Any], api_key: str) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"provider HTTP {exc.code}: {detail}") from exc


def responses_output_text(response: Mapping[str, Any]) -> str:
    """Extract assistant text from a raw Responses REST document."""
    parts: List[str] = []
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text" and type(content.get("text")) is str:
                parts.append(content["text"])
    return "".join(parts)


def _codex_tool_request(args: argparse.Namespace, prompt: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for codex-responses")
    programmatic = args.provider == "codex-ptc"
    payload: Dict[str, Any] = {
        "model": args.model,
        "input": prompt,
        "tools": build_codex_ptc_tools() if programmatic else [build_openai_responses_tool()],
    }
    if not programmatic:
        payload["tool_choice"] = {"type": "function", "name": "colly_collect_context"}
    if args.reasoning_effort:
        payload["reasoning"] = {"effort": args.reasoning_effort}
    response = _http_post_json(args.base_url.rstrip("/") + "/responses", payload, api_key)
    calls = [item for item in response.get("output", []) if item.get("type") == "function_call"]
    if len(calls) != 1:
        raise RuntimeError(f"expected one Codex function call, got {len(calls)}")
    if calls[0].get("name") != "colly_collect_context":
        raise RuntimeError("unexpected Codex tool name")
    return json.loads(calls[0]["arguments"]), {"response": response, "call": calls[0]}


def _codex_continue(args: argparse.Namespace, state: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    call = state["call"]
    payload = {
        "model": args.model,
        "previous_response_id": state["response"]["id"],
        "input": [{
            "type": "function_call_output",
            "call_id": call["call_id"],
            "output": json.dumps(result, separators=(",", ":")),
        }],
        "tools": (
            build_codex_ptc_tools()
            if args.provider == "codex-ptc"
            else [build_openai_responses_tool()]
        ),
    }
    response = _http_post_json(
        args.base_url.rstrip("/") + "/responses", payload, os.environ["OPENAI_API_KEY"]
    )
    if any(item.get("type") == "function_call" for item in response.get("output", [])):
        raise RuntimeError("model did not stop after the tool result")
    return responses_output_text(response)


def build_muse_request_payload(
    model: str,
    prompt: str,
    reasoning_effort: str,
    seed: int,
) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = []
    if reasoning_effort:
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Use {reasoning_effort} reasoning effort. Produce bounded tool arguments, "
                    "honor the declared root, and stop after a successful result."
                ),
            }
        )
    messages.append({"role": "user", "content": prompt})
    return {
        "model": model,
        "messages": messages,
        "tools": [build_hermes_tool()],
        "seed": seed,
    }


def _muse_tool_request(args: argparse.Namespace, prompt: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    api_key = os.environ.get("MUSE_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("MUSE_API_KEY or OPENAI_API_KEY is required for muse-openai-compatible")
    payload = build_muse_request_payload(
        args.model, prompt, args.reasoning_effort, args.seed
    )
    messages = payload["messages"]
    response = _http_post_json(args.base_url.rstrip("/") + "/chat/completions", payload, api_key)
    message = response["choices"][0]["message"]
    calls = message.get("tool_calls", [])
    if len(calls) != 1:
        raise RuntimeError(f"expected one Hermes tool call, got {len(calls)}")
    if calls[0].get("function", {}).get("name") != "colly_collect_context":
        raise RuntimeError("unexpected Hermes tool name")
    return json.loads(calls[0]["function"]["arguments"]), {
        "messages": messages,
        "assistant": message,
        "call": calls[0],
        "apiKey": api_key,
    }


def _muse_continue(args: argparse.Namespace, state: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    messages = list(state["messages"])
    messages.append(state["assistant"])
    messages.append({
        "role": "tool",
        "tool_call_id": state["call"]["id"],
        "content": json.dumps(result, separators=(",", ":")),
    })
    response = _http_post_json(
        args.base_url.rstrip("/") + "/chat/completions",
        {
            "model": args.model,
            "messages": messages,
            "tools": [build_hermes_tool()],
            "seed": args.seed,
        },
        state["apiKey"],
    )
    message = response["choices"][0]["message"]
    if message.get("tool_calls"):
        raise RuntimeError("model did not stop after the tool result")
    return str(message.get("content") or "")


def _git_version() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _ledger_record(
    case: Mapping[str, Any],
    args: argparse.Namespace,
    request: Mapping[str, Any],
    provider_metadata: Mapping[str, Any],
    result: Mapping[str, Any],
    transport: Mapping[str, Any],
    trace: Mapping[str, Any],
    score: Mapping[str, Any],
    final_message: str,
) -> Dict[str, Any]:
    category = case["category"]
    case_result = {"caseId": case["caseId"], "passed": score["passed"], "failures": score["failures"]}
    return {
        "iteration_id": "implementation-v1",
        "parent_system_version": _git_version(),
        "candidate_id": "colly-agent-tool-contract-v1",
        "hypothesis": "A strict bounded artifact contract improves reliable agent tool use.",
        "exact_modification": "Agent JSON transport, bounded collector, canonical inventory, and provider adapters.",
        "intended_mechanism": "Make selection, execution bounds, writes, and provenance explicit and machine-checkable.",
        "benchmarks_run": ["agent-tool-cases-v1"],
        "main_results": case_result if category == "main" else {},
        "regression_results": case_result if category in {"invalid", "stopping"} else {},
        "adversarial_results": case_result if category == "adversarial" else {},
        "cost_results": {
            "elapsedMs": transport["elapsedMs"],
            "stdoutBytes": transport["stdoutBytes"],
            "toolCalls": trace["toolCalls"],
            "retries": trace["retries"],
        },
        "safety_results": {"failures": trace["safetyFailures"]},
        "decision": "accept" if score["passed"] else "reject",
        "decision_rationale": "frozen case passed" if score["passed"] else ";".join(score["failures"]),
        "reproducibility_notes": "Isolated fixture; canonical request; frozen JSONL case and SHA-256.",
        "rollback_trigger": "Any deterministic contract, safety, or regression case fails.",
        "artifact_hashes": {
            "candidate": getattr(args, "candidate_hashes", None) or candidate_artifact_hashes(),
            "request": result.get("requestSha256", ""),
            "bundle": result.get("bundleSha256", ""),
            "inventory": result.get("inventorySha256", ""),
        },
        "operator_notes": {
            "provider": args.provider,
            "model": args.model,
            "baseUrl": args.base_url if args.provider != "offline-contract" else "",
            "reasoningEffort": args.reasoning_effort,
            "seed": args.seed,
            "maxToolCalls": args.max_tool_calls,
            "maxRetries": args.max_retries,
            "caseId": case["caseId"],
            "category": category,
            "toolArguments": dict(request),
            "result": dict(result),
            "finalMessage": final_message,
            "providerMetadata": dict(provider_metadata),
            "caseSetSha256": case_file_sha256(args.cases),
        },
        "evidence_class": "C",
    }


def run_case(case: Mapping[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="colly-agent-eval-") as temp_name:
        root = materialize_fixture(case, Path(temp_name))
        canonical_request = build_request_document(case, str(root))
        prompt = case["prompt"] + "\nUse exactly this frozen request: " + json.dumps(canonical_request)
        provider_state: Dict[str, Any] = {}
        if args.provider == "offline-contract":
            request = canonical_request
        elif args.provider in {"codex-responses", "codex-ptc"}:
            request, provider_state = _codex_tool_request(
                args, prompt
            )
        else:
            request, provider_state = _muse_tool_request(
                args, prompt
            )
        if request != canonical_request:
            raise RuntimeError("model arguments differ from the frozen request; execution refused")
        before = _snapshot_files(root)
        result, transport = _run_colly(request, root)
        after = _snapshot_files(root)
        safety_failures = _safety_failures(case, request, root, before, after, result)
        trace = {
            "status": result.get("status"),
            "errorCode": result.get("errorCode"),
            "toolCalls": 1,
            "retries": 0,
            "safetyFailures": safety_failures,
        }
        score = score_trace(case, trace)
        final_message = ""
        if args.provider in {"codex-responses", "codex-ptc"}:
            final_message = _codex_continue(args, provider_state, result)
        elif args.provider == "muse-openai-compatible":
            final_message = _muse_continue(args, provider_state, result)
        if args.provider != "offline-contract" and not final_message.strip():
            score = {"passed": False, "failures": list(score["failures"]) + ["empty-final-message"]}
        provider_metadata: Dict[str, Any] = {}
        if args.provider in {"codex-responses", "codex-ptc"}:
            response = provider_state.get("response", {})
            call = provider_state.get("call", {})
            provider_metadata = {
                "responseId": response.get("id", ""),
                "usage": response.get("usage", {}),
                "toolCallId": call.get("call_id", ""),
                "caller": call.get("caller", {}),
            }
        elif args.provider == "muse-openai-compatible":
            provider_metadata = {"toolCallId": provider_state.get("call", {}).get("id", "")}
        return _ledger_record(
            case,
            args,
            request,
            provider_metadata,
            result,
            transport,
            trace,
            score,
            final_message,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("offline-contract", "codex-responses", "codex-ptc", "muse-openai-compatible"),
        default="offline-contract",
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--reasoning-effort", default="")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-tool-calls", type=int, default=2)
    parser.add_argument("--max-retries", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.provider != "offline-contract" and not args.model:
        raise SystemExit("--model is required for live providers")
    validate_frozen_case_file(args.cases)
    cases = load_cases(args.cases)
    validate_case_set(cases)
    args.candidate_hashes = candidate_artifact_hashes()
    args.ledger.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with args.ledger.open("wb") as ledger:
        for case in cases:
            try:
                record = run_case(case, args)
            except Exception as exc:
                # Provider refusals and harness errors are rejected evidence, not absent trials.
                result = {"status": "error", "errorCode": "harness_error"}
                trace = {"toolCalls": 0, "retries": 0, "safetyFailures": ["trial-incomplete"]}
                record = _ledger_record(case, args, {}, {}, result,
                    {"elapsedMs": 0, "stdoutBytes": 0}, trace,
                    {"passed": False, "failures": [type(exc).__name__]}, "")
                record["operator_notes"]["harnessErrorType"] = type(exc).__name__
                # Do not persist provider exception bodies: they can contain credentials or prompt data.
            records.append(record)
            ledger.write(canonical_json_bytes(record))
            ledger.flush()
            os.fsync(ledger.fileno())
    failures = sum(record["decision"] != "accept" for record in records)
    print(json.dumps({"cases": len(records), "failed": failures, "ledger": str(args.ledger)}, separators=(",", ":")))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
