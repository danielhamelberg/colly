# Colly foreground handoff and measured adoption

Colly remains the collector. `harness.task_handoff` is a foreground host adapter:
it passes verified bundle content and provenance to a solver, validates the
returned patch, and runs an operator-specified test command in a disposable copy.
The original source repository is never patched. No new server or Navigator is
required, and no workspace permissions are changed.

## Artifact acceptance v2

`harness.artifact_verifier` checks actual artifact bytes independently of the
collector. It validates result and inventory shapes, exit/status agreement,
request binding, selected-source hashes, rendered content, sizes, bounds,
dynamic Markdown fences, and preservation of pre-existing files on failure.

The frozen 24 fixtures are unchanged. Their exact expected selections are
independently enumerated in the verifier. Evaluation ledgers retain bounded,
base64-encoded before/after bytes; adjudication rechecks them against the frozen
fixtures even after temporary directories have gone. An old metadata-only ledger
is insufficient for v2 acceptance. Regenerate it with the current runner.

The verifier supports faithfully decoded, untruncated source text, including BOM
and newline handling. Lossy transformed content requires a separate verifier.
Graph envelopes are checked, but this does not certify graph-edge semantics.
The collector CLI itself and its transformation features remain unchanged.

Evidence assumes a trusted capturer and nonconcurrent workspace. Hashes bind
content; they do not authenticate an untrusted machine. Retained evidence can
contain source text, so store it with the repository's access controls. Live
provider compatibility remains quarantined.

## A reusable task

Save a JSON task with exactly these fields, using paths relative to the target
Git repository. Choose test commands and allowed edits yourself, before the solver
runs. Test commands are executable host instructions, not model suggestions.

```json
{
  "taskId": "maintenance-001",
  "instruction": "Describe the maintenance change and required behavior here.",
  "files": ["src/example.py", "tests/test_example.py"],
  "allowedEdits": ["src/example.py"],
  "testCommand": ["{python}", "-m", "unittest", "discover", "-v"]
}
```

From the Colly checkout, prepare a new run directory **outside** the target
repository. Replace the example paths with the actual target repository and task:

```powershell
python -m harness.task_handoff prepare --repo C:\projects\target --task-json C:\tasks\maintenance-001.json --run C:\brik\runs\maintenance-001 --mode colly-none
```

`solver-input.json` contains the actual source bundle, inventory, task and patch
contract. Its context and packet digests are recorded in `prepared.json`.
`contract.json` pins the source bytes, executor, edit authority and resource limits.
`colly-focused` uses Colly's existing focused dependency expansion. `direct` is a
matched-seed direct-reading baseline, **not** a complete autonomous search agent.

A solver receives the packet as one JSON document on stdin and emits one JSON
proposal on stdout. The proposal fields are `schemaVersion`, `taskId`,
`contextSha256`, `edits`, and `usage`. Each edit has `path`, `sourceSha256`, and
`newText`. Existing files require an exact source preimage and must have appeared
in the supplied context. An explicitly allowed new file uses
`sourceSha256: null`. Unexpected paths, duplicates, stale preimages, oversized
patches and changed contracts are rejected before application.

Submit a proposal produced by an existing SelfInfer/ChatGPT/Codex session:

```powershell
python -m harness.task_handoff complete --run C:\brik\runs\maintenance-001 --proposal C:\tasks\maintenance-001.proposal.json --solver-label "My configured SelfInfer session"
```

Alternatively, supply a JSON command array with `--solver-command-json` instead
of `--proposal`. The trusted adapter starts a fresh foreground process, passes the
packet through stdin, and records its output, errors, timing and completion.
The command must already be installed and authorized. This package does not
configure an API, discover credentials, or register a new local model runtime.

The receipt describes only the isolated checkout. A passing task does not modify
the original repository, merge a pull request, or activate a BRIK policy. Failed
attempts are retained and cannot be retried under the same run ID.

Solver and test processes are operator-trusted. Bounded I/O and deadlines are not
an OS sandbox; an empty solver working directory does not prevent host access.
A production untrusted-code deployment needs a separately enforced execution
boundary. Provider-reported usage is retained but not treated as verified billing.
Unknown cost remains unknown rather than being replaced by zero.

## Comparison

`harness.value_experiment` runs each task exactly once under each of `direct`,
`colly-none` and `colly-focused`, with common budgets, unchanged source hashes,
a fixed randomized mode order, and a fresh solver process. It records failures
as well as successes. A tasks file is a JSON array of task objects above.

```powershell
python -m harness.value_experiment --repo C:\projects\target --tasks C:\tasks\held-out.json --output C:\brik\runs\comparison-001 --solver-command-json '["C:\\path\\to\\python.exe","C:\\path\\to\\solver.py"]' --solver-label "Exact model and runtime configuration"
```

The included check is deliberately synthetic and deterministic:

```powershell
python scripts/run_value_smoke.py --output C:\brik\runs\handoff-smoke-001
```

It exercises three repair fixtures across three modes. Two fixtures are exposed
by their seed files; the third needs an imported file. A fixed worker emits
predefined repairs. This checks plumbing and missing-context rejection, **not**
independent maintenance performance or LLM competence.

Automatic adoption is disabled in this increment. A reviewed live comparison
needs independently chosen tasks, a named fresh-process solver, complete total
resource accounting, an ordinary search-agent baseline, and a fixed statistical
adoption contract. Until those exist, the decision is to retain the current
policy. This adapter does not replace or extend BRIK's restricted bit-policy
reference kernel with unsupported general-purpose claims.
