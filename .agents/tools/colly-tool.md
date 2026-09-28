# Tool: `colly`

## Purpose

`colly` creates a bounded Markdown context bundle plus a canonical JSON provenance inventory. Agents should use the structured function or JSON transport; the clipboard-oriented CLI remains available for humans.

## Agent contract (preferred)

Registered function name: `colly_collect_context`.

- Codex Responses: strict function tool with the request schema in `schemas/colly-agent-request.schema.json`.
- Codex programmatic tool calling: the same function schema with `allowed_callers: ["programmatic"]`.
- Muse/Hermes: OpenAI-compatible `tools[].function` envelope around the same schema.

Direct JSON transport from this repo root:

```powershell
'{"schemaVersion":1,"root":"C:/repo","files":["src/*.py","requirements.lock"],"output":"audit/bundle.md","inventory":"audit/bundle.inventory.json","replace":false,"dependencyGraph":false,"braPreset":"none","autoTruncate":false,"encoding":"utf-8","maxFiles":256,"maxSourceBytes":16777216,"maxBundleBytes":393216,"maxScanEntries":10000,"deadlineSeconds":60}' |
  python .\colly.py --agent --request-json -
```

In agent mode, stdout contains exactly one JSON completion record. Diagnostics go to stderr. A deterministic request failure returns a nonzero exit code and a structured error record; agents should not retry it unchanged.

The tool writes only the declared `output` and `inventory` paths, using same-directory temporary files and replace semantics only when `replace` is true. Exact file selectors bypass extension filters; directory and glob scans remain extension-filtered and bounded. Truncation, dependency expansion, and graph rendering are off unless explicitly requested.

See [docs/agent-tool-contract.md](../../docs/agent-tool-contract.md) for schemas, limits, result handling, and compatibility evidence.

Host orchestration rules:

1. Call `colly_collect_context` once for a bounded collection stage.
2. Treat `status: "ok"` as terminal; verify the returned paths/hashes and do not repeat the call.
3. Do not retry validation, path, selection, binary, decoding, collision, or limit errors unchanged.
4. Retry `artifact_write_failed` at most once and only with evidence of a transient host failure.
5. Never broaden the root, selectors, or budgets during recovery without user authorization.

## Human/interactive mode

Default interactive command:

```powershell
python .\colly.py -n ...
```

Use `-n` so the legacy command prints Markdown instead of copying it to the clipboard.

## Best Uses

- create a markdown snapshot of one file, a directory, or a repo slice
- generate an AI-native dependency graph
- expand context around one or more files through bounded import/call neighborhoods
- get quick stats before opening a large tree
- apply post-hoc graph insight bundles to an existing graph

## Fast Recipes

Basic repo slice:

```powershell
python .\colly.py -n -f .\src
```

Single file with no graph:

```powershell
python .\colly.py -n --no-dependency-graph -f .\main.py
```

Single file plus focused code neighborhood:

```powershell
python .\colly.py -n --bra focused -f .\main.py
```

Review neighborhood with manifest:

```powershell
python .\colly.py -n --bra review --bra-explain -f .\main.py
```

Stats before deeper inspection:

```powershell
python .\colly.py -n --show-stats -f .\src
```

Directory listing only:

```powershell
python .\colly.py -n --directory-listing -f .\src
```

Generate a graph artifact:

```powershell
python .\colly.py -n -f .\src > graph.md
```

Apply an annotation bundle to a saved graph:

```powershell
python .\colly.py -n --graph-input .\graph.md --graph-annotation-file .\insights.json
```

Apply multiple bundles:

```powershell
python .\colly.py -n --graph-input .\graph.md --graph-annotation-file .\baseline.json --graph-annotation-file .\review.json
```

## Decision Guide

If you want file bodies:

- use `-f <path>`
- add `--no-dependency-graph` if the graph is noise

If you want graph structure:

- keep the graph on
- narrow `-f` before widening the scope

If you want neighboring code:

- add `--bra`
- start with `focused`, then widen to `review` or `architecture` only if needed
- use `--bra-edge` only when you intentionally need non-default edge types

If you want graph-only post-processing:

- use `--graph-input`
- pass one or more `--graph-annotation-file` bundles

## Important Flags

- `-f`, `--files`
  Target files or directories. Be explicit; the default recursive scan is broad.
- `-n`, `--no-clip`
  Print output instead of copying it to the clipboard.
- `--ignore-file`
  Load ignore patterns from a file such as `.collyignore`.
- `-e`, `--exclude`
  Add extra exclusion patterns.
- `--no-dependency-graph`
  Skip the graph when you only want content.
- `--show-stats`
  Emit a stats report.
- `--directory-listing`
  Emit structured directory inventory.
- `--entanglement-map`
  Emit a low-resolution dependency heatmap.
- `--bra`
  Expand context through named presets. Start with `focused`.
- `--bra-in`, `--bra-out`
  Override inward and outward traversal depth for BRA.
- `--bra-edge`
  Override the edge types used by BRA. `symbol` is opt-in.
- `--bra-budget-files`, `--bra-budget-bytes`
  Put explicit bounds on BRA expansion.
- `--bra-explain`
  Emit a machine-readable `# BRA Expansion Manifest` section.
- `-T`, `--dep-type`
  Deprecated legacy dependency-expansion selector.
- `-t`, `--truncate`
  Enable truncation for oversized outputs.
- `-o`, `--override-max-length`
  Override truncation length by glob.
- `--graph-input`
  Read an existing dependency graph from raw JSON or prior colly markdown.
- `--graph-annotation-file`
  Apply a post-hoc annotation bundle.

## Output Shapes

### File Content

File bodies render like:

````markdown
## path/to/file.py
```python
...
```
````

### Dependency Graph

The graph renders as:

````markdown
# AI-Native Dependency Graphs
```json
{"nodes":[...],"edges":[...]}
```
````

`nodes` contain `path`, optional `definitions`, and optional `annotations`.

`edges` contain:

- `source`
- `target`
- `type`
- `name`
- optional `annotations`

### Markdown Graph Metadata

Markdown files can contribute graph annotations without turning prose into graph structure:

- HTML comments outside code fences can hold file-local `@colly-graph` directives for the Markdown node.
- Fenced JSON blocks with an info string containing `colly-graph` can hold graph-global annotation bundles.
- Ordinary Markdown prose, headings, links, and code examples are ignored by the graph annotation parser.

### BRA Expansion Manifest

When `--bra-explain` is enabled, colly also emits:

````markdown
# BRA Expansion Manifest
```json
{"seeds":["main.py"],"included":[...],"pruned":[...]}
```
````

Use this to see why context was included and what the budget pruned.

### Graph Annotation Bundles

Post-hoc bundles are JSON. Minimal shape:

```json
{
  "version": 1,
  "annotations": [
    {
      "selector": {
        "kind": "node",
        "path": "src/main.py"
      },
      "annotation": {
        "kind": "entrypoint",
        "summary": "Primary execution surface"
      }
    }
  ]
}
```

See [docs/ai-native-dependency-graph-annotations.md](../../docs/ai-native-dependency-graph-annotations.md) for the full selector rules.

## Common Failure Modes

- `No files matched the provided patterns`
  Usually a bad path, a wrong working directory, or exclusions that are too broad.
- Output is too large
  Narrow `-f`, reduce the BRA preset or budget, disable the graph, or enable truncation.
- Graph is noisy
  Scope the input more tightly before trying to interpret global structure.
- BRA pulled in the wrong files
  Drop to `focused`, keep `symbol` edges off unless explicitly needed, and inspect the manifest.
- Bundle appears to do nothing
  Check selector paths exactly; post-hoc bundles are graph-global and path-sensitive.

## Good Default Workflow For Agents

1. Run `python .\colly.py -n --show-stats -f <target>` to gauge scope.
2. Run the real content or graph command with a narrower path if needed.
3. Inspect the graph before making architectural claims.
4. If insights matter later, write a bundle and reapply it with `--graph-input`.
