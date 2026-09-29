# Colly agent tool contract v1

Colly agent mode is a deterministic local artifact operation: select bounded text inputs, optionally expand or graph them, render one Markdown bundle, and write a canonical provenance inventory. It is intentionally a single foreground operation, not an MCP server or background service.

## Interfaces

The canonical request schema is [`schemas/colly-agent-request.schema.json`](../schemas/colly-agent-request.schema.json). The registered function is `colly_collect_context`.

- OpenAI Codex models use a strict Responses function definition. For programmatic tool calling, the same definition may opt in with `allowed_callers: ["programmatic"]`; the host must preserve each `call_id` and caller linkage.
- Muse-Glimmer and other Hermes/OpenAI-compatible runtimes use the same schema under `tools[].function`.
- Any agent can use `python colly.py --agent --request-json -` and send exactly one LF-terminated UTF-8 JSON object on stdin.

The last two envelopes are generated from one descriptor, [`.agents/tools/colly-collect.tool.json`](../.agents/tools/colly-collect.tool.json), so provider wrappers do not fork the input contract.

## Behavioral guarantees

- Explicit root: all inputs and outputs must resolve beneath `root`; symlinks are rejected in v1.
- Exact selection: a non-glob exact file is included regardless of extension. Directory and glob scans use supported text extensions and exclusions.
- Fail closed: missing, unmatched, unreadable, binary, invalidly encoded, over-budget, or colliding requests return structured errors and a nonzero exit.
- Bounded execution: requests declare file, source-byte, bundle-byte, scan-entry, and deadline limits.
- No implicit transformation: dependency expansion, dependency graphs, and truncation are disabled unless requested.
- Declared writes only: only `output` and `inventory` may be created or replaced. Writes use same-directory temporary files; replacement requires `replace: true`; a failed two-artifact commit rolls back.
- Deterministic artifacts: normalized UTF-8 path ordering, dynamic Markdown fences, compact canonical JSON, and SHA-256 hashes make reruns comparable.

Agent-mode stdout contains exactly one compact result record conforming to [`schemas/colly-agent-result.schema.json`](../schemas/colly-agent-result.schema.json). Human-readable diagnostics belong on stderr. An agent should stop after `status: "ok"`; it should change the request, not blindly retry, after deterministic errors.

Version 1 defaults are 256 files, 16 MiB total source bytes, a 384 KiB rendered bundle, 10,000 scanned entries, a 60-second deadline, and a 64 KiB JSON request. Exceeding a limit returns `limit_exceeded`; it never silently enables truncation. Explicit binary input returns `binary_input`, and lossy decoding is forbidden (`decode_failed`).

## Provenance

The inventory conforms to [`schemas/colly-inventory.schema.json`](../schemas/colly-inventory.schema.json). Every selected file records normalized relative paths, selection reasons, encoding, source size and SHA-256, rendered size and SHA-256, content mode, and any transformations. The separate hashes prevent transformed or normalized content from being mistaken for verbatim source.

## Provider adapters

[`harness/tool_adapters.py`](../harness/tool_adapters.py) emits:

- a strict OpenAI Responses function envelope;
- an optional Codex programmatic-caller envelope; and
- a Hermes/OpenAI-compatible function envelope for Muse-Glimmer-style runtimes.

[`harness/agent_tool_eval.py`](../harness/agent_tool_eval.py) can run the frozen local contract suite or drive live Responses and OpenAI-compatible endpoints. Live runs require an explicit model and credentials. Provider output is validated by Colly itself; a model cannot relax the schema or execution bounds.

| Route | Contract | Evidence status |
|---|---|---|
| Codex shell | Agent CLI or JSON stdin | Supported by local contract tests |
| Codex Responses function | Strict portable function schema | Quarantined pending a named live model run |
| Codex programmatic calling | Same schema; host preserves caller/call linkage | Quarantined pending a named PTC run |
| Muse-Glimmer OpenAI-compatible | Hermes wrapper over the same schema | Quarantined pending a named runtime run |
| Muse-Glimmer direct shell | Agent CLI or JSON stdin | Supported by local contract tests |
| MCP | No v1 interface | Deferred |

For direct and programmatic Codex routes, `status: "ok"` is terminal and must not produce another Colly call. For Muse-Glimmer runtimes, locally validate every generated argument object even when the server advertises schema enforcement, and record the exact model/runtime configuration separately.

## Evidence and claim boundary

The frozen 24-case suite covers successful selection, invalid requests, adversarial paths/content, budgets, and stopping behavior. Its JSONL bytes are pinned by SHA-256, and the adjudicator rejects a lowered denominator or any safety finding. The local accepted ledger is Class C evidence: empirical evidence in a closed-world harness.

That evidence validates the executable transport and artifact contract within the tested fixtures. It does not prove live model compatibility, model selection quality, or open-world reliability. The experimental live runner now supplies an exact frozen request and refuses to execute altered model arguments. This is a transport check, not a test of autonomous request selection. Live compatibility stays quarantined even after a passing run until independent artifact-content and complete tool-lifecycle validation is implemented and reviewed.

The adjudicator independently checks case identity, uniqueness, suite hash, status/error codes, call/retry limits, and safety findings. The fixed denominator cannot be lowered with a CLI flag. Evaluator hardening is a separately tracked change; it does not change the frozen cases or establish improvement against the historical CLI.

Path checks assume a trusted workspace without concurrent filesystem mutation; they do not provide OS-level isolation. Deadlines are cooperative. If artifact rollback itself fails, the error is reported and remaining `.bak` recovery files are preserved for operator recovery.

## Artifact acceptance revision 2

The evaluation harness now retains bounded source/artifact bytes and independently
checks actual contents, artifact hashes, sizes, selection and error preservation.
The frozen v1 cases are unchanged. Old metadata-only ledgers must be rerun; they
cannot satisfy the new artifact acceptance checks. See the
[foreground handoff guide](selfinfer-handoff.md) for the verifier's supported
content modes, execution assumptions and comparison boundary. Live compatibility
remains quarantined pending named-runtime lifecycle validation.
