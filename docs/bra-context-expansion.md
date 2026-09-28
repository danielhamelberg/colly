# BRA Context Expansion

`--bra` now means bounded context expansion around one or more seed files.
The expansion model is preset-driven, deterministic, and optionally explainable.

## Named Presets

- `focused`: outward imports only, 1 hop, 8-file budget
- `review`: inward and outward imports, 1 hop each, 20-file budget
- `architecture`: inward and outward imports and calls, 2 hops each, 40-file budget

These presets are the default contract. Reach for explicit overrides only when the preset is close but not exact.

## Explicit Overrides

Use these to override preset behavior:

- `--bra-in <n>`: override inward traversal depth
- `--bra-out <n>`: override outward traversal depth
- `--bra-edge import call symbol`: select the edge types used during expansion
- `--bra-budget-files <n>`: cap the number of added files
- `--bra-budget-bytes <n>`: cap the total estimated bytes of added files
- `--bra-explain`: emit a machine-readable expansion manifest

`symbol` edges are opt-in. They are intentionally excluded from the default presets because they are noisier than imports.

## Manifest

`--bra-explain` emits a new markdown section:

````markdown
# BRA Expansion Manifest
```json
{
  "seeds": ["main.py"],
  "included": [
    {
      "path": "dep.py",
      "distance": 1,
      "edge_types": ["import"],
      "bytes": 42,
      "reasons": [{"from": "main.py", "type": "import"}]
    }
  ],
  "pruned": []
}
```
````

Fields:

- `seeds`: the original seed files
- `included`: files added by BRA after scoring and budget checks
- `pruned`: candidate files excluded by file-count or byte budget

The manifest is designed for agents. It answers why a file was included, which edge types led to it, and what was dropped by the budget.

## Selection Semantics

- Imports outrank calls, and calls outrank symbols.
- Shorter paths outrank longer ones.
- Ties are broken by path for deterministic output.
- File-count budget is enforced before byte budget pruning is recorded.

## Legacy Alias Compatibility

Legacy cup aliases still work:

- `aa` -> `focused` with `--bra-in 0 --bra-out 0`
- `a` -> `focused` with `--bra-in 0 --bra-out 1`
- `b` -> `review`
- `c`, `d`, `dd`, `e` -> progressively wider `architecture` expansions

Prefer named presets in new usage. Keep aliases only for compatibility with older commands or notes.

## Examples

Focused context around one file:

```powershell
python .\colly.py -n --bra focused -f .\main.py
```

Review context with provenance:

```powershell
python .\colly.py -n --bra review --bra-explain -f .\main.py
```

Architecture context with an explicit byte guard:

```powershell
python .\colly.py -n --bra architecture --bra-budget-bytes 120000 -f .\src
```
