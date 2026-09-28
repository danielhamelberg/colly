# Agent request reference

Generated from [the request schema](../../schemas/colly-agent-request.schema.json).
Run `python scripts/check_docs.py --write` to refresh; do not edit manually.

Required fields are marked below. Unknown fields are rejected. See the
[contract](../agent-tool-contract.md) for runtime semantics and the
[example](../examples/collect_context.py) for a complete request.

| Field | Type | Required | Schema constraints |
|---|---|---|---|
| `schemaVersion` | integer | Yes | const: `1` |
| `root` | string | Yes | minLength: `1` |
| `files` | array | Yes | minItems: `1`; items: `{"minLength": 1, "type": "string"}` |
| `output` | string | Yes | minLength: `1` |
| `inventory` | string | Yes | minLength: `1` |
| `replace` | boolean | Yes | See runtime contract |
| `dependencyGraph` | boolean | Yes | See runtime contract |
| `braPreset` | string | Yes | enum: `["none", "focused", "review", "architecture"]` |
| `autoTruncate` | boolean | Yes | See runtime contract |
| `encoding` | string | Yes | minLength: `1` |
| `maxFiles` | integer | Yes | minimum: `1` |
| `maxSourceBytes` | integer | Yes | minimum: `1` |
| `maxBundleBytes` | integer | Yes | minimum: `1` |
| `maxScanEntries` | integer | Yes | minimum: `1` |
| `deadlineSeconds` | integer | Yes | minimum: `1` |
