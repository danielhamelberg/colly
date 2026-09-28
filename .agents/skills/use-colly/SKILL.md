---
name: use-colly
description: Use when you need Colly to produce a bounded repository context bundle and provenance inventory, or when a human needs its legacy Markdown, graph, expansion, or annotation CLI.
---

# Use Colly

`colly` is the local artifact producer for bounded source context. Its primary agent interface creates Markdown plus a canonical JSON inventory; its broader legacy CLI remains useful for interactive analysis.

Use this skill when the task needs any of the following:

- a compact repo snapshot for another agent or model
- file content plus dependency-expanded neighbors
- an AI-native dependency graph
- a directory listing, stats report, or entanglement map
- post-hoc annotation of an already-generated dependency graph

## First Move

1. Read [.agents/tools/colly-tool.md](../../tools/colly-tool.md).
2. If `colly_collect_context` is registered, call it with the strict schema.
3. Otherwise use `python .\colly.py --agent --request-json -` with one LF-terminated JSON request on stdin.
4. Use the Human/legacy CLI only for interactive graph, stats, annotation, or clipboard behavior absent from agent v1.

## Agent Operating Rules

- Always provide an explicit absolute `root` and root-relative selectors and artifact paths.
- Prefer exact files. Use a narrow glob or directory only when the task genuinely needs scanning.
- Set bounds deliberately for unfamiliar repositories; never rely on truncation to hide an over-broad selection.
- Treat `status: "ok"` plus exit code 0 as success. Verify or consume the returned hashes when artifact identity matters.
- Treat structured validation, missing-file, binary, path, collision, and limit failures as deterministic. Change the request before retrying.
- Do not infer that a missing file was skipped: requested omissions fail closed.
- Remember that inventory `sourceSha256` identifies original bytes and `renderedSha256` identifies rendered content.

## Agent JSON example

```powershell
'{"schemaVersion":1,"root":"C:/repo","files":["src/*.py","requirements.lock"],"output":"audit/bundle.md","inventory":"audit/bundle.inventory.json","replace":false,"dependencyGraph":false,"braPreset":"none","autoTruncate":false,"encoding":"utf-8","maxFiles":256,"maxSourceBytes":16777216,"maxBundleBytes":393216,"maxScanEntries":10000,"deadlineSeconds":60}' |
  python .\colly.py --agent --request-json -
```

## Human/legacy CLI

### Operating Rules

- Treat `python .\colly.py -n ...` from the repo root as the default invocation.
- Use `--ignore-file` or `.collyignore` before piling on many `-e` exclusions.
- Use `--no-dependency-graph` when file bodies matter more than graph structure.
- Use `--show-stats` when you need a quick size/scope check before reading full output.
- Use `--bra` only when you intentionally want dependency expansion; keep it small first, usually `focused`.
- For graph overlays, generate or load a graph first, then apply one or more `--graph-annotation-file` bundles.

### Quick Routing

- Need a repo snapshot:
  `python .\colly.py -n -f <path>`
- Need a snapshot without the graph:
  `python .\colly.py -n --no-dependency-graph -f <path>`
- Need a graph you will inspect or annotate:
  `python .\colly.py -n -f <path> > graph.md`
- Need neighboring files around one entry file:
  `python .\colly.py -n --bra focused -f <file>`
- Need a review neighborhood with provenance:
  `python .\colly.py -n --bra review --bra-explain -f <file>`
- Need to annotate an existing graph:
  `python .\colly.py -n --graph-input graph.md --graph-annotation-file insights.json`

### Legacy Output Contract

Expect these sections:

- file bodies as `## <relative/path>` fenced code blocks
- dependency graph as `# AI-Native Dependency Graphs` followed by a JSON block
- optional BRA manifest as `# BRA Expansion Manifest` followed by a JSON block
- directory listing as `# AI-Optimized Directory Listing`
- optional stats and entanglement sections when requested

## Annotation Guidance

When annotating a graph after inspection, prefer graph-global selectors:

- node: `path`
- definition: `path` + `name`, optionally `type` or `signature`
- edge: `source_path` + `type`, plus `target_path` and/or `name`

Use annotations for judgments that are specific to the observed graph, such as:

- hubs
- entrypoints
- coupling hotspots
- measurement spines
- suspicious isolates
- policy or control boundaries

Do not pretend a live graph judgment is source truth. Keep bundle annotations clearly inferential and attach evidence where possible.

## References

- Tool card: [.agents/tools/colly-tool.md](../../tools/colly-tool.md)
- Agent contract: [docs/agent-tool-contract.md](../../../docs/agent-tool-contract.md)
- Graph annotation schema: [docs/ai-native-dependency-graph-annotations.md](../../../docs/ai-native-dependency-graph-annotations.md)
