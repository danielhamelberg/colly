# Colly Agent Tool Contract Design

**Status:** Proposed

**Date:** 2026-08-11

**Scope:** Make Colly a deterministic, bounded artifact-producing tool for OpenAI Codex models and Muse Glimmer models without changing the default human/clipboard workflow.

## Decision

Build an agent-native CLI profile and a provider-neutral JSON request/result envelope in one coherent increment. Keep the transport process-based and stateless. Do not add MCP, a daemon, provider SDK dependencies, or a general binary transport.

The preferred invocations are:

```powershell
python .\colly.py --agent `
  --root C:\repo `
  -f scripts\*.py requirements.lock `
  --output audit\bundle.md `
  --inventory audit\bundle.inventory.json
```

and, for function-tool hosts that already possess structured arguments:

```powershell
$request | python .\colly.py --agent --request-json -
```

Both routes execute the same internal request object and produce exactly one compact JSON completion record on stdout. Bundle content is written to disk, not returned through the tool channel.

## Evidence and assumptions

- **Class C — local empirical result:** `python -m unittest -v` passes all 30 existing tests on 2026-08-11.
- **Class C — repository observation:** exact non-glob `-f` files already bypass extension filtering, including `requirements.lock`.
- **Class C — repository observation:** automatic size-guard truncation is already opt-in through `--auto-truncate`.
- **Class C — repository observation:** the current processing path silently skips missing/unreadable files, decodes with replacement characters, infers the project root, renders to memory, and then copies or prints the bundle.
- **Class A — contract decision:** agent mode must fail closed on incomplete selection, decoding loss, budget exhaustion, and artifact-write failure.
- **Class E — interoperability inference:** a shallow strict schema, bounded execution, artifact references, and stable error codes should be more portable across frontier Codex models and smaller local Muse Glimmer runtimes than provider-specific wrappers or shell-command synthesis.
- **Class F — unvalidated hypothesis:** the JSON request route will reduce quoting and argument-construction failures compared with model-authored PowerShell commands. This must be measured in the compatibility harness before being claimed as an improvement.

Primary capability references:

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model) recommends the Responses API for tool calling, concise tool definitions, explicit return fields and error behavior, and bounded retry/stopping rules. It also distinguishes direct tool calls from programmatic tool calling and requires representative evaluation.
- [Muse Glimmer model card](https://huggingface.co/meta-models/Muse-Glimmer-30B) describes schema-based tool invocation, sequential tool use, failure recovery, OpenClaw/Hermes-style scaffold compatibility, and a 131,072+ token context window. The card also recommends application-specific evaluation and guardrails for agentic deployments.

## Current-state review

### Already implemented and retained

1. Exact non-glob files are treated as forced inputs regardless of extension.
2. Unknown explicit text files render as `plaintext`.
3. Directory and glob scans continue to filter by supported extensions and exclusions.
4. `--auto-truncate` is explicit and disabled by default.
5. Final file ordering is currently sorted and the existing CLI regression suite is green.

### Agentic blockers

1. **Silent incompleteness:** `process_files()` continues after missing, excluded, empty, or unreadable files and returns no structured omission record.
2. **Lossy decoding:** `process_single_file()` uses `errors='replace'`, so an agent cannot distinguish source bytes from repaired text.
3. **Implicit authority boundary:** the root is inferred from selected AST-bearing files and the current working directory rather than explicitly declared.
4. **No artifact contract:** bundle output, hashes, inventory, transaction status, and stable error codes are absent.
5. **No execution bounds:** broad globs and directory scans have no agent-profile cap on scanned entries, selected files, source bytes, bundle bytes, or wall-clock duration.
6. **Mixed interaction semantics:** human clipboard behavior, Markdown stdout, progress, logging, and future tool results share one orchestration path.
7. **Unreported transformation:** the current renderer can minify or truncate content without a per-file source/rendered hash pair or transformation ledger.
8. **Non-portable invocation:** a model must currently construct shell arguments and redirection correctly, including Windows quoting and path rules.

## Options considered

### Option 1 — Minimal CLI repair

Only retain the exact-file and opt-in truncation repairs.

**Benefit:** almost no added surface.

**Rejection reason:** these repairs are already present and do not provide bounded execution, machine-readable provenance, atomic artifacts, or a stable tool result.

### Option 2 — Agent CLI profile only

Add `--agent`, `--root`, `--output`, and `--inventory`, but require every tool host to construct shell arguments.

**Benefit:** small implementation and easy manual use.

**Limitation:** every Codex, Hermes, OpenClaw, llama.cpp, or other scaffold must independently solve quoting, argument precedence, and result parsing. This leaves an avoidable compatibility gap for local models.

### Option 3 — Agent CLI plus portable JSON envelope

Add the agent CLI profile and a single `--request-json <path|->` route backed by the same internal request object.

**Benefit:** preserves direct CLI use, gives function-calling hosts a natural JSON boundary, and keeps provider adapters outside Colly.

**Decision:** recommended. It is the smallest design that materially targets both Codex and Muse Glimmer tool calling.

### Deferred — MCP or resident service

MCP can be layered over the JSON contract later. It is not needed to establish selection, rendering, provenance, error, and artifact semantics. Adding it now would expand lifecycle, transport, authentication, and compatibility work before the core contract is validated.

## Goals

1. Produce a deterministic Markdown bundle and canonical inventory from explicit, bounded inputs.
2. Make every requested file either present in the inventory or represented by a structured failure.
3. Keep stdout parseable as exactly one JSON value in agent mode.
4. Preserve source identity with byte-level source hashes and rendered-content hashes.
5. Support direct shell tools, OpenAI function tools, OpenAI programmatic tool calling, and OpenAI-compatible/Hermes function scaffolds from one provider-neutral schema.
6. Preserve existing human defaults and CLI behavior outside `--agent`.
7. Leave an evaluation ledger that distinguishes local contract evidence from live-model compatibility evidence.

## Non-goals

- MCP server, daemon, background service, or plugin API.
- Remote file retrieval or a general binary-content transport.
- Automatic prompt generation or model routing inside Colly.
- Provider SDK dependencies in the production CLI.
- Automatic retries inside Colly; the tool returns a stable failure and the host owns a bounded retry policy.
- Claiming open-world model compatibility from schema/unit tests alone.
- Repairing all existing dependency-graph or minifier weaknesses as part of this increment.

## Agent-mode contract

### CLI profile

`--agent` changes orchestration defaults only for that invocation:

- require an explicit `--root`;
- require `--output` and `--inventory` in flag mode;
- disable clipboard, progress, verbose Markdown, stats, entanglement output, and dependency-graph output unless explicitly requested;
- disable minification and truncation unless explicitly requested;
- interpret relative selectors and artifact paths relative to `--root`, not the process working directory;
- never follow symlinks in agent schema version 1, and reject the legacy `--follow-symlinks` flag in agent mode;
- reject resolved source or artifact paths outside the root;
- use deterministic relative POSIX paths and ordering;
- fail rather than truncate when an execution limit is exceeded;
- write exactly one compact completion record to stdout and diagnostics to stderr;
- return a stable nonzero exit code on failure.

For `--request-json -`, stdin is a one-line protocol: one UTF-8 JSON object terminated by LF, capped at 64 KiB. This lets the process begin work after one complete record instead of waiting for pipe EOF. JSON request files may be formatted across lines but have the same 64 KiB cap.

Human mode retains its current clipboard and stdout behavior.

### Preferred JSON request

Keep the request deliberately shallow and avoid `oneOf`, nullable fields, external `$ref` requirements, and provider-specific metadata:

```json
{
  "schemaVersion": 1,
  "root": "C:/repo",
  "files": ["scripts/*.py", "requirements.lock"],
  "output": "audit/bundle.md",
  "inventory": "audit/bundle.inventory.json",
  "replace": false,
  "dependencyGraph": false,
  "braPreset": "none",
  "autoTruncate": false,
  "encoding": "utf-8",
  "maxFiles": 256,
  "maxSourceBytes": 16777216,
  "maxBundleBytes": 393216,
  "maxScanEntries": 10000,
  "deadlineSeconds": 60
}
```

All fields are required in schema version 1. Required booleans and explicit `"none"` enums are intentional: they avoid hidden defaults when a tool host serializes arguments. The CLI flag route fills the same object from documented defaults before validation.

The default bundle ceiling is chosen for the smaller target context class. Exceeding it is a structured failure, not a reason to modify source content. A caller may explicitly raise the limit or opt into `autoTruncate` and will see that transformation in the inventory.

### Selector semantics

- A string with no glob metacharacters that resolves to a file is `explicit-file` and bypasses extension/exclusion filtering.
- A string with glob metacharacters is `glob-match`; matched files use supported-extension and exclusion rules.
- A string with no glob metacharacters that resolves to a directory is `directory-scan`; descendants use supported-extension and exclusion rules.
- A missing exact selector is an error, not an empty match.
- A glob with zero matches is an error because the caller explicitly requested it.
- Exact binary input fails with `binary_input`; there is no replacement decoding.
- UTF BOMs are honored. Otherwise agent mode uses the explicitly declared encoding, which the flag route defaults to strict UTF-8.
- Symlinks are excluded in agent schema version 1. A later schema must re-evaluate root containment and cycle behavior before enabling them.
- Output and inventory targets cannot also be selected as source files.

### Completion record

The result is flat enough for broad function-tool compatibility and has the same fields on success and failure:

```json
{
  "schemaVersion": 1,
  "operation": "collect",
  "status": "ok",
  "exitCode": 0,
  "requestSha256": "...",
  "bundlePath": "audit/bundle.md",
  "bundleSha256": "...",
  "bundleSizeBytes": 123,
  "inventoryPath": "audit/bundle.inventory.json",
  "inventorySha256": "...",
  "filesSelected": 2,
  "filesTransformed": 0,
  "filesExcluded": 4,
  "errorCode": "none",
  "message": "",
  "failedPath": "",
  "warnings": []
}
```

On failure, artifact fields that do not exist are empty strings or zero, `status` is `error`, and `errorCode`, `message`, and `failedPath` explain the failure. No traceback is emitted on stdout.

Initial stable error codes:

- `none`
- `invalid_request`
- `root_not_found`
- `path_outside_root`
- `path_collision`
- `requested_file_missing`
- `selector_no_match`
- `binary_input`
- `decode_failed`
- `limit_exceeded`
- `artifact_exists`
- `artifact_source_conflict`
- `artifact_write_failed`
- `internal_error`

Use exit code `2` for request/argument errors, `3` for selection errors, `4` for content/decoding errors, `5` for limits, `6` for artifact errors, and `70` for unexpected internal errors.

## Internal boundary

Keep the one-file distribution for the first increment, but enforce three testable stages in `colly.py`:

```text
parse_agent_request()
        |
        v
select_files() -> SelectionResult
        |
        v
collect_files() -> list[CollectedFile]
        |
        v
render_bundle() -> bytes
        |
        v
build_inventory() -> canonical JSON bytes
        |
        v
write_artifacts() -> ArtifactSet
        |
        v
render_completion() -> one-line JSON
```

No stage writes to stdout. `main()` is the only stdout owner.

### Core records

`CollectedFile` carries:

- absolute resolved source path internally;
- normalized relative POSIX path externally;
- sorted, unique selection reasons;
- source byte size and SHA-256;
- strict decoded encoding;
- Markdown language tag;
- rendered body byte size and SHA-256;
- content mode: `verbatim` or `transformed`;
- ordered transformation records.

Use `selectionReasons` rather than a singular reason because the same file may be both explicit and dependency-expanded. Use distinct `sourceSha256` and `renderedSha256` values so an agent never confuses transformed or normalized output with source bytes.

### Rendering rules

- Sort by normalized relative path using UTF-8 byte order.
- Preserve decoded file content; do not use `.rstrip()` in the agent renderer.
- Choose a Markdown fence longer than any backtick run in the file.
- Use `plaintext` for exact text files with unknown extensions.
- Make dependency graph output opt-in in agent mode because it can materially change size and cost.
- Record every transformation, including auto-truncation, with its parameters and before/after hashes.
- Do not expose Python minification in the JSON contract until its semantic-preservation defects have a dedicated fix and regression suite. The legacy CLI flag remains outside this scope.

### Canonical inventory

Inventory JSON bytes use UTF-8 without BOM, sorted keys, compact separators, `ensure_ascii=False`, and one final LF. This is a Colly-defined canonical form, not a claim of RFC 8785 conformance. `requestSha256` is computed from the effective request after resolving the root, converting external paths to normalized POSIX form, and materializing flag-mode defaults; JSON whitespace and caller path separators therefore do not affect the hash.

```json
{
  "schemaVersion": 1,
  "root": "C:/repo",
  "requestSha256": "...",
  "bundle": {
    "path": "audit/bundle.md",
    "sha256": "...",
    "sizeBytes": 123
  },
  "limits": {
    "maxFiles": 256,
    "maxSourceBytes": 16777216,
    "maxBundleBytes": 393216,
    "maxScanEntries": 10000,
    "deadlineSeconds": 60
  },
  "files": [
    {
      "relativePath": "requirements.lock",
      "sourceSizeBytes": 4120,
      "sourceSha256": "...",
      "renderedSizeBytes": 4120,
      "renderedSha256": "...",
      "encoding": "utf-8",
      "language": "plaintext",
      "selectionReasons": ["explicit-file"],
      "contentMode": "verbatim",
      "transformations": []
    }
  ],
  "excluded": [
    {
      "relativePath": "vendor/example.whl",
      "reason": "default-exclusion",
      "requested": false
    }
  ]
}
```

Do not put wall-clock timestamps, absolute temporary paths, elapsed time, process IDs, or hostnames in canonical artifacts. Those belong in the evaluation ledger or stderr diagnostics because they break reproducibility.

### Artifact transaction

True atomic replacement across two independent files is not provided by common filesystems. Implement the strongest practical local transaction and document the boundary:

1. Validate both final paths and reject source conflicts.
2. Refuse existing artifacts unless `replace` is true.
3. Create both temporary files in their respective destination directories.
4. Write, flush, and `fsync` both temporary files.
5. Re-read and verify their hashes.
6. If replacing, move existing targets to same-directory backups.
7. Replace bundle, then inventory.
8. On any commit failure, restore backups or delete newly created targets.
9. Remove all temporary/backup files after success or rollback.

The completion record reports success only after both final artifacts exist and their hashes match. Crash consistency between the two replace operations remains a documented limitation; the inventory is authoritative only when its bundle hash matches the bundle.

## Model compatibility profile

### OpenAI Codex models

Required routes:

1. **Hosted/local shell:** invoke the flag CLI and parse the one-line stdout result.
2. **Responses function tool:** expose `colly_collect_context` with strict JSON parameters and pass the arguments to `--request-json -`.
3. **Programmatic tool calling:** allow the same tool only for a bounded context-collection stage. Preserve OpenAI `call_id` and `caller` in the host; these are transport metadata and do not enter Colly artifacts.

The host instruction should state the exact return fields, permit no retries for deterministic request errors, allow at most one retry for transient process/filesystem errors, and stop after the first `status: ok` result.

### Muse Glimmer models

Required routes:

1. **OpenAI-compatible local endpoint:** wrap the same canonical input schema in the endpoint's function-tool envelope.
2. **Hermes/OpenClaw-style scaffold:** translate only the outer tool declaration; invoke `--request-json -` with the model arguments unchanged.
3. **Direct local shell:** use the same CLI profile when the scaffold exposes a terminal rather than function tools.

The shared schema deliberately avoids deep nesting, conditional schemas, nullable unions, and provider-only fields. Runtime validation remains mandatory because local inference servers may not enforce strict structured outputs even when they accept a tool schema.

### Provider-neutral descriptor

Store a canonical descriptor with:

- tool name `colly_collect_context`;
- one-sentence description focused on deterministic local context artifacts;
- input schema path and result schema path;
- process transport command;
- stdout/stderr and exit-code semantics.

Compatibility adapters are generated or tested in `harness/`; they do not become production dependencies of `colly.py`.

## Safety and bounded execution

- Root containment is mandatory in agent mode.
- Source files are read-only.
- Artifact writes are limited to the two declared paths.
- Existing artifacts require explicit `replace: true`.
- Broad selection stops at `maxScanEntries`; selection stops at `maxFiles` and `maxSourceBytes`; rendering stops at `maxBundleBytes`; monotonic checks enforce `deadlineSeconds` between units of work.
- Limit failures do not produce partial successful artifacts.
- Exact binary input fails closed.
- No automatic dependency graph or expansion in agent mode.
- No internal retry loop.
- Filenames and file contents are data, never instructions to Colly.
- Completion messages avoid embedding source content or secrets.

## Evaluation and adjudication

The evaluation roles remain logically separate even if implemented in one repository:

- **Proposer:** implementation tasks and model-specific adapter hypotheses.
- **Executor:** unit/CLI tests and live compatibility runner.
- **Adjudicator:** fixed acceptance function that reads result ledgers and cannot change task denominators.

Evaluation bundle:

- `E_main`: exact-file, glob, directory, dependency-expansion, bundle, inventory, and result correctness.
- `E_reg`: all existing human-mode tests.
- `E_adv`: path traversal, symlink escape, binary input, invalid encoding, filename injection, fence injection, target/source collision, stdout pollution, partial write, and budget exhaustion.
- `E_cost`: tool calls, retries, wall time, selected bytes, bundle bytes, stdout bytes, and model tokens where available.
- `E_safety`: root containment, no source mutation, no undeclared writes, rollback integrity, and no success on incomplete requests.

Initial acceptance gates:

1. Existing 30-test baseline remains green.
2. Deterministic contract tests pass 100% across two runs with byte-identical bundle and inventory artifacts.
3. Every adversarial fixture returns the declared error code and leaves no partial artifact.
4. Success stdout is exactly one JSON line; failure stdout is exactly one JSON line; diagnostics never corrupt it.
5. Happy-path model tasks complete with exactly one Colly call; hard cap is three tool calls and one retry.
6. On the 24-case frozen local model-call suite, each declared Codex configuration and Muse Glimmer configuration passes at least 22 cases (91.7%) with zero critical safety failures and no human-mode regression.
7. Lower call count, latency, or token use counts as improvement only when correctness and evidence completeness are preserved.

The 22-of-24 live-model threshold is a predeclared adoption gate, not an assertion about current performance. Contract tests are Class C. Live runs against named model/runtime snapshots remain Class C until evaluated on held-out tasks not curated by the proposer. Open-world claims require a separately versioned external task set or independent replication.

## Rollout and rollback

1. Land the internal records and pure contract functions behind no CLI behavior change.
2. Add schemas and deterministic artifact tests.
3. Add `--agent` flag mode.
4. Add `--request-json` using the same request object.
5. Add compatibility adapters and the evidence ledger.
6. Mark the profile experimental until both target model families pass the frozen suite.

Rollback trigger: any human-mode regression, silent requested-file omission, root escape, partial-success artifact set, schema drift, or critical compatibility safety failure. Rollback removes the agent entry routes while retaining independently validated exact-file and opt-in truncation repairs.

## Deferred follow-ups

- `--dry-run` selection explanations after real debugging evidence shows it is needed.
- Content-addressed artifact directories if multi-file crash consistency becomes a practical problem.
- Chunked retrieval operation for bundles that exceed the Muse-compatible context ceiling.
- MCP wrapper generated from the stable request/result schemas.
- Repair and re-admit Python minification behind semantic-equivalence tests.
- Split `colly.py` into installable modules only after the contract stabilizes; do not combine packaging churn with the first agent-profile trial.
