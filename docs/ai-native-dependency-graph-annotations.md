# AI-Native Dependency Graph Annotations

`colly` supports two annotation paths for AI-native dependency graph elements:

- source-level directives embedded in code comments
- Markdown-level directives embedded in HTML comments or explicit `colly-graph` fences
- post-hoc annotation bundles applied after the graph already exists

This lets an author predeclare insight in source, or lets an AI inspect a finished graph and then attach fresh annotations based on that specific graph state.

## Source Syntax

Use a single-line comment containing the `@colly-graph` marker followed by a JSON object.

Python:

```python
# @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"role","summary":"CLI entrypoint"}}
# @colly-graph {"target":{"kind":"definition","name":"main","type":"function"},"annotation":{"kind":"entrypoint","summary":"Primary command dispatcher"}}
```

JavaScript / TypeScript:

```ts
// @colly-graph {"target":{"kind":"edge","type":"imports","name":"./schema"},"annotation":{"kind":"dataflow","summary":"Consumes schema contract"}}
// @colly-graph {"target":{"kind":"definition","name":"buildGraph","type":"function"},"annotation":{"kind":"hotspot","summary":"Graph synthesis stage"}}
```

Single-line block comments are also accepted:

```js
/* @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"domain","summary":"Billing workflow"}} */
```

## Markdown Syntax

Markdown files are parsed more conservatively than source files. Ordinary prose, headings, and fenced code samples are ignored so documentation examples cannot accidentally modify the graph.

For file-local annotations on the Markdown node itself, use a single-line HTML comment outside fenced code:

````markdown
<!-- @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"role","summary":"Architecture note"}} -->
````

For graph-global annotations from a Markdown file, use a fenced JSON block whose info string contains `colly-graph`:

````markdown
```colly-graph
{
  "annotations": [
    {
      "selector": {
        "kind": "node",
        "path": "helper.py"
      },
      "annotation": {
        "kind": "role",
        "summary": "Documented helper surface"
      }
    }
  ]
}
```
````

Markdown `colly-graph` fences use the same selector rules as post-hoc bundles, but their `_origin.syntax` is `markdown-colly-graph` and their `_origin.bundle` is the Markdown file path.

## Post-Hoc Bundle Syntax

Post-hoc bundles are JSON files intended for the workflow:

1. generate the AI-native dependency graph
2. inspect that graph
3. emit an annotation bundle from the inspection
4. apply the bundle back onto the graph

Bundle shape:

```json
{
  "version": 1,
  "annotations": [
    {
      "selector": {
        "kind": "node",
        "path": "helper.py"
      },
      "annotation": {
        "kind": "role",
        "summary": "Shared greeting utilities"
      }
    },
    {
      "selector": {
        "kind": "definition",
        "path": "helper.py",
        "name": "greet",
        "type": "function"
      },
      "annotation": {
        "kind": "contract",
        "summary": "Returns greeting text"
      }
    },
    {
      "selector": {
        "kind": "edge",
        "source_path": "main.py",
        "target_path": "helper.py",
        "type": "imports",
        "name": "helper"
      },
      "annotation": {
        "kind": "dataflow",
        "summary": "Consumes helper greeting primitives"
      }
    }
  ]
}
```

Bundle selectors are graph-global rather than file-local:

- `node`: requires `path`
- `definition`: requires `path` and `name`; may also include `type` and `signature`
- `edge`: requires `source_path` and `type`, plus at least one of `target_path` or `name`

## Directive Shape

Each directive is a JSON object with two required keys:

```json
{
  "target": {
    "kind": "node | definition | edge"
  },
  "annotation": {
    "kind": "role",
    "summary": "Human-readable insight"
  }
}
```

### Target Kinds

`node`

- Applies to the file that contains the directive.
- Optional selector field:
  - `path`: relative graph path for an extra match guard.

`definition`

- Applies to a definition emitted from the same file.
- Required selector fields:
  - `name`
- Optional selector fields:
  - `type`

`edge`

- Applies to a dependency edge emitted from the same file.
- Required selector fields:
  - `type`
- At least one of these must also be present:
  - `name`
  - `target`
- Optional selector fields:
  - `target`: relative target path from the emitted graph, useful when `name` alone is ambiguous.

### Annotation Object

`annotation` must be a non-empty JSON object. `colly` preserves user-provided keys verbatim and adds provenance metadata during emission.

Recommended fields:

- `kind`
- `summary`
- `priority`
- `confidence`
- `owners`
- `tags`

## Emitted Graph Shape

The existing graph shape remains intact. `colly` adds optional `annotations` arrays in three places:

- `nodes[i].annotations`
- `nodes[i].definitions[j].annotations`
- `edges[k].annotations`

Each emitted annotation includes the original payload plus reserved provenance in `_origin`:

```json
{
  "kind": "role",
  "summary": "CLI entrypoint",
  "_origin": {
    "line": 1,
    "syntax": "@colly-graph"
  }
}
```

For post-hoc bundles, `_origin` records bundle provenance:

```json
{
  "kind": "dataflow",
  "summary": "Consumes helper greeting primitives",
  "_origin": {
    "bundle": "insights.json",
    "entry": 2,
    "syntax": "graph-annotation-bundle"
  }
}
```

## Matching Rules

- Directives are file-local. A directive only annotates elements emitted from the file that contains it.
- Node directives annotate the file node for that file.
- Definition directives match by definition `name`, and optionally `type`.
- Edge directives match by edge `type`, plus `name` and/or `target` when supplied.
- Post-hoc bundles are graph-global. They match emitted graph elements by explicit selector fields.
- Markdown `colly-graph` fences are graph-global. They match emitted graph elements by explicit selector fields.
- Invalid JSON or invalid selectors are ignored rather than aborting graph generation.
- Markdown code fences without the explicit `colly-graph` info string are ignored, including examples that contain source-level `@colly-graph` comments.

## CLI Workflow

Apply a post-hoc bundle during graph generation:

```bash
python colly.py -f main.py helper.py --graph-annotation-file insights.json
```

Apply a post-hoc bundle to a graph that already exists:

```bash
python colly.py --graph-input graph.md --graph-annotation-file insights.json
```

`--graph-input` accepts either:

- raw graph JSON
- prior `colly` markdown output containing the `# AI-Native Dependency Graphs` block

## Example

Given:

```python
# @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"role","summary":"Shared greeting utilities"}}
# @colly-graph {"target":{"kind":"definition","name":"greet","type":"function"},"annotation":{"kind":"contract","summary":"Returns greeting text","stability":"stable"}}
def greet(name):
    return f"Hello {name}"
```

and:

```python
# @colly-graph {"target":{"kind":"edge","type":"imports","name":"helper"},"annotation":{"kind":"dataflow","summary":"Consumes helper greeting primitives"}}
import helper
print(helper.greet("Ada"))
```

the emitted graph will contain:

- a node annotation on `helper.py`
- a definition annotation on `greet`
- an edge annotation on the `imports` edge from `main.py` to `helper.py`
