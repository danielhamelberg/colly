# BRA Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current hop-count-only `--bra` expansion with a policy-driven, budgeted, explainable context-expansion system while preserving legacy cup aliases for compatibility.

**Architecture:** Keep `colly.py` as the main delivery surface. Introduce a small internal BRA policy/manifest model, build a typed dependency index once, expand from seed files with deterministic scoring and explicit budgets, then optionally emit a machine-readable expansion manifest. Preserve `--bra a|b|c...` as aliases to named presets instead of letting legacy cup values define the underlying model.

**Tech Stack:** Python CLI, `argparse`, `dataclasses`, AST-based Python analysis, regex-based JS/TS analysis, `unittest` / `pytest`

---

## File Structure

- Modify: `colly.py`
  Responsibility: CLI contract, BRA policy parsing, typed graph index, traversal engine, manifest rendering, main-program integration.
- Modify: `test_colly.py`
  Responsibility: black-box CLI regression coverage for presets, budgets, edge selection, explain mode, and legacy compatibility.
- Create: `docs/bra-context-expansion.md`
  Responsibility: define named presets, scoring/budget semantics, manifest shape, and migration from legacy cup aliases.
- Modify: `.agents/tools/colly-tool.md`
  Responsibility: update agent-facing recipes to the new `--bra` contract.

## Assumptions Locked Before Implementation

- `focused` means outgoing import expansion only, one hop, small default file budget.
- `review` means one hop in both directions over imports, moderate file budget.
- `architecture` means broader two-hop expansion over imports and calls, larger file budget.
- `symbol` edges are opt-in and are not part of the default preset surface because they are noisier than imports.
- `--bra-explain` emits a new markdown section named `# BRA Expansion Manifest`.
- `--bra a|b|c...` remains supported, but maps onto named presets and explicit depths instead of controlling raw BFS directly.

## Task 1: Lock The New CLI Contract

**Files:**
- Modify: `test_colly.py`
- Modify: `colly.py`

- [ ] **Step 1: Write the failing focused-preset regression test**

```python
def test_bra_focused_preset_includes_one_hop_imports_only(self):
    main_file = self.root / "main.py"
    dep_file = self.root / "dep.py"
    deep_file = self.root / "deep.py"
    reverse_file = self.root / "reverse.py"

    main_file.write_text("import dep\nprint(dep.VALUE)\n", encoding="utf-8")
    dep_file.write_text("import deep\nVALUE = deep.VALUE\n", encoding="utf-8")
    deep_file.write_text("VALUE = 'deep'\n", encoding="utf-8")
    reverse_file.write_text("import main\nprint(main)\n", encoding="utf-8")

    result = self.run_colly("--no-dependency-graph", "--bra", "focused", "-f", "main.py")

    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("## dep.py", result.stdout)
    self.assertNotIn("## deep.py", result.stdout)
    self.assertNotIn("## reverse.py", result.stdout)
```

- [ ] **Step 2: Run the focused-preset regression to verify the missing contract**

Run: `python -m pytest -q test_colly.py -k "focused_preset"`

Expected: FAIL because `--bra focused` is not currently a valid contract and no preset semantics exist.

- [ ] **Step 3: Add the BRA policy model and preset table in `colly.py` near `DependencyExpander`**

```python
from dataclasses import dataclass, field

LEGACY_BRA_ALIASES = {
    "aa": ("focused", 0, 0),
    "a": ("focused", 0, 1),
    "b": ("review", 1, 1),
    "c": ("architecture", 1, 2),
    "d": ("architecture", 2, 3),
    "dd": ("architecture", 3, 5),
    "e": ("architecture", 5, 8),
}

BRA_PRESETS = {
    "focused": {"inward": 0, "outward": 1, "edge_types": ("import",), "budget_files": 8, "budget_bytes": None},
    "review": {"inward": 1, "outward": 1, "edge_types": ("import",), "budget_files": 20, "budget_bytes": None},
    "architecture": {"inward": 2, "outward": 2, "edge_types": ("import", "call"), "budget_files": 40, "budget_bytes": None},
}

EDGE_WEIGHTS = {
    "import": 100,
    "call": 60,
    "symbol": 20,
}

@dataclass(frozen=True)
class BRAExpansionPolicy:
    preset: str
    inward: int
    outward: int
    edge_types: tuple[str, ...]
    budget_files: int
    budget_bytes: int | None
    explain: bool = False

@dataclass
class BRAExpansionManifest:
    seeds: list[str]
    included: list[dict] = field(default_factory=list)
    pruned: list[dict] = field(default_factory=list)
```

- [ ] **Step 4: Add explicit BRA CLI flags in `main()`**

```python
parser.add_argument("--bra-in", type=int, help="Override inward dependency depth for BRA expansion.")
parser.add_argument("--bra-out", type=int, help="Override outward dependency depth for BRA expansion.")
parser.add_argument("--bra-edge", nargs="*", choices=["import", "call", "symbol"], help="Edge types used by BRA expansion.")
parser.add_argument("--bra-budget-files", type=int, help="Maximum number of files BRA may add.")
parser.add_argument("--bra-budget-bytes", type=int, help="Maximum total bytes BRA may add.")
parser.add_argument("--bra-explain", action="store_true", help="Emit a BRA expansion manifest section.")
```

- [ ] **Step 5: Replace `_parse_bra_args()` with policy parsing**

```python
def parse_bra_policy(args) -> BRAExpansionPolicy | None:
    if args.bra is None and args.bra_in is None and args.bra_out is None:
        return None

    raw = args.bra[0].lower() if args.bra else "focused"
    if raw in LEGACY_BRA_ALIASES:
        preset_name, inward, outward = LEGACY_BRA_ALIASES[raw]
    elif raw in BRA_PRESETS:
        preset_name = raw
        inward = BRA_PRESETS[preset_name]["inward"]
        outward = BRA_PRESETS[preset_name]["outward"]
    else:
        raise ValueError(f"Invalid --bra value: {raw}")

    inward = args.bra_in if args.bra_in is not None else inward
    outward = args.bra_out if args.bra_out is not None else outward
    edge_types = tuple(args.bra_edge or BRA_PRESETS[preset_name]["edge_types"])
    budget_files = args.bra_budget_files or BRA_PRESETS[preset_name]["budget_files"]
    budget_bytes = args.bra_budget_bytes if args.bra_budget_bytes is not None else BRA_PRESETS[preset_name]["budget_bytes"]

    return BRAExpansionPolicy(
        preset=preset_name,
        inward=inward,
        outward=outward,
        edge_types=edge_types,
        budget_files=budget_files,
        budget_bytes=budget_bytes,
        explain=args.bra_explain,
    )
```

- [ ] **Step 6: Run the focused-preset regression again**

Run: `python -m pytest -q test_colly.py -k "focused_preset"`

Expected: still FAIL, but now because traversal semantics are not yet implemented rather than because the CLI contract is missing.

- [ ] **Step 7: Commit the contract-only slice**

```bash
git add colly.py test_colly.py
git commit -m "refactor: add bra policy contract and preset parsing"
```

## Task 2: Replace Raw BFS With Typed, Scored Expansion

**Files:**
- Modify: `colly.py`
- Modify: `test_colly.py`

- [ ] **Step 1: Write the deterministic-budget regression test**

```python
def test_bra_budget_files_limits_candidates_deterministically(self):
    main_file = self.root / "main.py"
    dep_a = self.root / "dep_a.py"
    dep_b = self.root / "dep_b.py"
    dep_c = self.root / "dep_c.py"

    main_file.write_text("import dep_b\nimport dep_c\nimport dep_a\n", encoding="utf-8")
    dep_a.write_text("VALUE = 'a'\n", encoding="utf-8")
    dep_b.write_text("VALUE = 'b'\n", encoding="utf-8")
    dep_c.write_text("VALUE = 'c'\n", encoding="utf-8")

    result = self.run_colly(
        "--no-dependency-graph",
        "--bra",
        "focused",
        "--bra-budget-files",
        "2",
        "-f",
        "main.py",
    )

    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("## dep_a.py", result.stdout)
    self.assertIn("## dep_b.py", result.stdout)
    self.assertNotIn("## dep_c.py", result.stdout)
```

- [ ] **Step 2: Run the deterministic-budget regression**

Run: `python -m pytest -q test_colly.py -k "budget_files_limits_candidates"`

Expected: FAIL because the current expansion logic is count-based BFS without deterministic scoring.

- [ ] **Step 3: Build a typed graph index once instead of merging anonymous sets**

```python
def build_bra_graph_index(all_supported_files: Set[str], project_root: str) -> dict[str, dict[str, defaultdict[str, Set[str]]]]:
    forward = {}
    reverse = {}

    for edge_type in ("import", "call", "symbol"):
        typed_forward = build_forward_graph(all_supported_files, edge_type, project_root)
        typed_reverse = defaultdict(set)
        for src, deps in typed_forward.items():
            for dep in deps:
                typed_reverse[dep].add(src)
        forward[edge_type] = typed_forward
        reverse[edge_type] = typed_reverse

    return {"forward": forward, "reverse": reverse}
```

- [ ] **Step 4: Add scored candidate collection helpers**

```python
def score_bra_candidate(edge_type: str, distance: int, path: str, hit_count: int) -> tuple[int, int, str]:
    return (EDGE_WEIGHTS[edge_type] + (hit_count * 5), -distance, path)

def estimate_file_bytes(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
```

- [ ] **Step 5: Replace `collect_additional_files()` with policy-driven traversal**

```python
def expand_bra_candidates(
    start_files: Set[str],
    adjacency: defaultdict[str, Set[str]],
    edge_type: str,
    max_depth: int,
) -> dict[str, dict]:
    frontier = deque((start, 0) for start in sorted(start_files))
    visited = set(start_files)
    candidates = {}

    while frontier:
        current, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for neighbor in sorted(adjacency[current]):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            frontier.append((neighbor, depth + 1))
            entry = candidates.setdefault(neighbor, {"distance": depth + 1, "edge_types": set(), "reasons": []})
            entry["distance"] = min(entry["distance"], depth + 1)
            entry["edge_types"].add(edge_type)
            entry["reasons"].append({"from": current, "type": edge_type})

    return candidates
```

- [ ] **Step 6: Add a budgeted selector that respects score, file-count, and byte budgets**

```python
def select_bra_files(
    candidates: dict[str, dict],
    input_files: Set[str],
    budget_files: int,
    budget_bytes: int | None,
    manifest: BRAExpansionManifest,
) -> Set[str]:
    selected = set()
    consumed_bytes = 0

    ranked = []
    for path, meta in candidates.items():
        if path in input_files:
            continue
        hit_count = len(meta["edge_types"])
        edge_type = sorted(meta["edge_types"], key=lambda name: (-EDGE_WEIGHTS[name], name))[0]
        ranked.append((score_bra_candidate(edge_type, meta["distance"], path, hit_count), path, meta, edge_type))

    for _, path, meta, edge_type in sorted(ranked, reverse=True):
        file_bytes = estimate_file_bytes(path)
        if len(selected) >= budget_files:
            manifest.pruned.append({"path": path, "reason": "budget_files"})
            continue
        if budget_bytes is not None and consumed_bytes + file_bytes > budget_bytes:
            manifest.pruned.append({"path": path, "reason": "budget_bytes"})
            continue

        selected.add(path)
        consumed_bytes += file_bytes
        manifest.included.append({
            "path": path,
            "score": score_bra_candidate(edge_type, meta["distance"], path, len(meta["edge_types"])),
            "distance": meta["distance"],
            "edge_types": sorted(meta["edge_types"]),
            "bytes": file_bytes,
            "reasons": meta["reasons"],
        })

    return selected
```

- [ ] **Step 7: Wire `DependencyExpander.expand()` to the policy engine**

```python
def expand(self, initial_files):
    policy = parse_bra_policy(self.args)
    if policy is None:
        return initial_files, None

    graph_index = build_bra_graph_index(self.all_supported_files, self.project_root)
    manifest = BRAExpansionManifest(seeds=sorted(initial_files))
    candidates = {}

    for edge_type in policy.edge_types:
        if policy.outward > 0:
            outward_candidates = expand_bra_candidates(initial_files, graph_index["forward"][edge_type], edge_type, policy.outward)
            for path, meta in outward_candidates.items():
                candidates.setdefault(path, {"distance": meta["distance"], "edge_types": set(), "reasons": []})
                candidates[path]["distance"] = min(candidates[path]["distance"], meta["distance"])
                candidates[path]["edge_types"].update(meta["edge_types"])
                candidates[path]["reasons"].extend(meta["reasons"])

        if policy.inward > 0:
            inward_candidates = expand_bra_candidates(initial_files, graph_index["reverse"][edge_type], edge_type, policy.inward)
            for path, meta in inward_candidates.items():
                candidates.setdefault(path, {"distance": meta["distance"], "edge_types": set(), "reasons": []})
                candidates[path]["distance"] = min(candidates[path]["distance"], meta["distance"])
                candidates[path]["edge_types"].update(meta["edge_types"])
                candidates[path]["reasons"].extend(meta["reasons"])

    selected = select_bra_files(candidates, initial_files, policy.budget_files, policy.budget_bytes, manifest)
    return initial_files.union(selected), manifest
```

- [ ] **Step 8: Run the focused and deterministic-budget regressions**

Run: `python -m pytest -q test_colly.py -k "focused_preset or budget_files_limits_candidates"`

Expected: PASS

- [ ] **Step 9: Commit the traversal engine slice**

```bash
git add colly.py test_colly.py
git commit -m "feat: add budgeted typed bra traversal"
```

## Task 3: Add Explain Mode And Manifest Output

**Files:**
- Modify: `test_colly.py`
- Modify: `colly.py`

- [ ] **Step 1: Write the explain-mode regression test**

```python
def test_bra_explain_emits_manifest_section(self):
    main_file = self.root / "main.py"
    dep_file = self.root / "dep.py"

    main_file.write_text("import dep\nprint(dep.VALUE)\n", encoding="utf-8")
    dep_file.write_text("VALUE = 'dep'\n", encoding="utf-8")

    result = self.run_colly("--bra", "focused", "--bra-explain", "-f", "main.py")

    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("# BRA Expansion Manifest", result.stdout)
    self.assertIn('"seeds":["main.py"]', result.stdout)
    self.assertIn('"path":"dep.py"', result.stdout)
```

- [ ] **Step 2: Run the explain-mode regression**

Run: `python -m pytest -q test_colly.py -k "bra_explain_emits_manifest_section"`

Expected: FAIL because BRA currently has no explain output path.

- [ ] **Step 3: Add a manifest renderer**

```python
def render_bra_expansion_manifest(manifest: BRAExpansionManifest, project_root: str) -> str:
    payload = {
        "seeds": [os.path.relpath(path, project_root) for path in manifest.seeds],
        "included": [
            {
                **item,
                "path": os.path.relpath(item["path"], project_root),
                "reasons": [
                    {**reason, "from": os.path.relpath(reason["from"], project_root)}
                    for reason in item["reasons"]
                ],
            }
            for item in manifest.included
        ],
        "pruned": [
            {**item, "path": os.path.relpath(item["path"], project_root)}
            for item in manifest.pruned
        ],
    }
    return "\n".join([
        "# BRA Expansion Manifest",
        "```json",
        json.dumps(payload, separators=(",", ":")),
        "```",
        "",
    ])
```

- [ ] **Step 4: Thread the manifest through `main()`**

```python
expander = DependencyExpander(args, project_root, all_supported_files)
final_files, bra_manifest = expander.expand(supported_files)
final_files_list = sorted(list(final_files))

if bra_manifest and args.bra_explain:
    output_parts.append(render_bra_expansion_manifest(bra_manifest, project_root))
```

- [ ] **Step 5: Run the explain-mode regression and the broader BRA subset**

Run: `python -m pytest -q test_colly.py -k "bra_explain_emits_manifest_section or bra"`

Expected: PASS

- [ ] **Step 6: Commit the manifest slice**

```bash
git add colly.py test_colly.py
git commit -m "feat: add bra explain manifest output"
```

## Task 4: Add Edge-Type And Compatibility Coverage

**Files:**
- Modify: `test_colly.py`
- Modify: `colly.py`

- [ ] **Step 1: Write the symbol-opt-in regression test**

```python
def test_bra_symbols_are_opt_in(self):
    main_file = self.root / "main.py"
    dep_file = self.root / "dep.py"
    symbol_file = self.root / "symbol_owner.py"

    main_file.write_text("import dep\nprint(SHARED)\n", encoding="utf-8")
    dep_file.write_text("VALUE = 'dep'\n", encoding="utf-8")
    symbol_file.write_text("SHARED = 'value'\n", encoding="utf-8")

    result = self.run_colly("--no-dependency-graph", "--bra", "focused", "-f", "main.py")

    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("## dep.py", result.stdout)
    self.assertNotIn("## symbol_owner.py", result.stdout)
```

- [ ] **Step 2: Write the legacy-alias compatibility regression test**

```python
def test_bra_legacy_alias_a_matches_focused_preset(self):
    main_file = self.root / "main.py"
    dep_file = self.root / "dep.py"

    main_file.write_text("import dep\nprint(dep.VALUE)\n", encoding="utf-8")
    dep_file.write_text("VALUE = 'dep'\n", encoding="utf-8")

    legacy = self.run_colly("--no-dependency-graph", "--bra", "a", "-f", "main.py")
    focused = self.run_colly("--no-dependency-graph", "--bra", "focused", "-f", "main.py")

    self.assertEqual(legacy.returncode, 0, legacy.stderr)
    self.assertEqual(focused.returncode, 0, focused.stderr)
    self.assertEqual(legacy.stdout, focused.stdout)
```

- [ ] **Step 3: Run the compatibility regressions**

Run: `python -m pytest -q test_colly.py -k "symbols_are_opt_in or legacy_alias_a_matches_focused_preset"`

Expected: PASS after the preset parsing and default edge semantics are wired correctly.

- [ ] **Step 4: Tighten help text and epilog examples**

```python
parser.add_argument(
    "--bra",
    nargs="*",
    metavar="MODE",
    help=(
        "Context expansion preset or legacy alias. "
        "Named presets: focused, review, architecture. "
        "Legacy aliases: aa, a, b, c, d, dd, e."
    ),
)
```

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest -q test_colly.py`

Expected: PASS

- [ ] **Step 6: Commit the compatibility slice**

```bash
git add colly.py test_colly.py
git commit -m "test: lock bra compatibility and edge defaults"
```

## Task 5: Document The New BRA Model For Humans And Agents

**Files:**
- Create: `docs/bra-context-expansion.md`
- Modify: `.agents/tools/colly-tool.md`

- [ ] **Step 1: Write the new BRA design note**

```markdown
# BRA Context Expansion

## Named Presets

- `focused`: outward imports only, 1 hop, 8-file budget
- `review`: inward + outward imports, 1 hop each, 20-file budget
- `architecture`: inward + outward imports and calls, 2 hops each, 40-file budget

## Explicit Overrides

- `--bra-in`
- `--bra-out`
- `--bra-edge`
- `--bra-budget-files`
- `--bra-budget-bytes`
- `--bra-explain`

## Manifest

`--bra-explain` emits `# BRA Expansion Manifest` with:

- `seeds`
- `included`
- `pruned`
```

- [ ] **Step 2: Update the agent-facing tool card recipes**

```markdown
Single file plus focused code neighborhood:

```powershell
python .\colly.py -n --bra focused -f .\main.py
```

Review neighborhood with manifest:

```powershell
python .\colly.py -n --bra review --bra-explain -f .\main.py
```
```

- [ ] **Step 3: Verify docs mention only the supported contract**

Run: `Get-Content .\docs\bra-context-expansion.md`

Expected: named presets, explicit overrides, manifest contract, and legacy alias note all present.

- [ ] **Step 4: Commit the docs slice**

```bash
git add docs/bra-context-expansion.md .agents/tools/colly-tool.md
git commit -m "docs: document bra preset and manifest model"
```

## Spec Coverage

- Replace raw hop-count expansion with policy-driven expansion: covered by Task 1 and Task 2.
- Make budgets explicit and deterministic: covered by Task 2.
- Make expansion explainable: covered by Task 3.
- Preserve legacy cup compatibility: covered by Task 4.
- Make the new contract clear to agents and humans: covered by Task 5.

## Contradictions And Open Questions

- The current codebase still centralizes logic in `colly.py`; this plan respects that instead of forcing a module split.
- `symbol` edges may still be too noisy even when opt-in. If tests show unstable expansion, remove them from preset coverage and keep them explicit-only.
- Byte-budget semantics depend on file-size estimates, not token counts. If that proves too blunt, keep byte budgets but document them as approximate rather than exact token guards.

## Verification And Approval Status

- Evidence gathered:
  - Current `--bra` is a two-number hop-count parser in `DependencyExpander` and uses merged BFS over anonymous sets.
  - Current traversal is implemented via `build_forward_graph()` and `collect_additional_files()` with no scoring or manifest output.
  - Current CLI exposes legacy `--before`, `--after`, `--dep-type`, and `--bra`, but not policy or budget controls.
- Verification status:
  - Planning only. No implementation or tests have been run for this redesign.
- Approval status:
  - Awaiting approval to execute the plan.
- Recommended next skill:
  - `subagent-driven-development` if you want isolated task-by-task execution.
  - `executing-plans` if you want inline implementation in this session.

Plan complete and saved to `docs/superpowers/plans/2026-04-11-bra-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
