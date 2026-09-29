# Colly documentation

Start with the [quickstart](quickstart.md), then choose a reference:

| Task | Documentation |
|---|---|
| Collect context from an agent | [Agent contract](agent-tool-contract.md) |
| Inspect request fields | [Generated request reference](reference/agent-request.md) |
| Expand a file's code neighborhood | [Bounded context expansion](bra-context-expansion.md) |
| Add graph metadata | [Graph annotations](ai-native-dependency-graph-annotations.md) |
| Integrate an agent host | [Tool guide](../.agents/tools/colly-tool.md) |
| Change or verify documentation | [Documentation workflow](contributing.md) |

The source of truth for request structure is the [JSON Schema](../schemas/colly-agent-request.schema.json); the runtime enforces execution behavior. The generated reference is checked against that schema.

Plans under `docs/superpowers/` describe historical intentions. Dated records under `docs/verification/` describe the exact tested candidate identified by their hashes. Neither overrides the current contract or proves live model compatibility.

- [Foreground SelfInfer handoff and comparison](selfinfer-handoff.md)
