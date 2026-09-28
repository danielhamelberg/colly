# Colly Capability Lift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for additive slices and `superpowers:systematic-debugging` followed by `superpowers:test-driven-development` for defect-style slices. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve up to six existing Colly capabilities in the order most likely to produce a meaningful quality, scale, and usability gain without loosening verification standards.

**Architecture:** Keep `colly.py` as the main delivery surface for now, but treat the work as six bounded capability slices with separate acceptance checks. Use future-state projection only to reveal the target shape; every projected gain must be earned by a concrete test, benchmark, or reproducible CLI check before adoption.

**Tech Stack:** Python CLI, `argparse`, AST-based Python analysis, regex-based JS/TS analysis, `pytest`/`unittest` CLI tests

---

## Route

### Skill Chain

1. `using-superpowers`
2. `envisage-future-state-projection`
3. `artifact-contract-engineering`
4. `residual-burden-diagnostics`
5. `benchmark-eval-design`
6. `writing-plans`
7. `systematic-debugging` or `executing-plans` per slice
8. `verification-before-completion`

### Missing Skill

- `envisage-governor` is not installed in this session.
- Substitute governance function:
  - `artifact-contract-engineering` freezes the artifact and acceptance shape.
  - `residual-burden-diagnostics` ranks what to improve first.
  - `benchmark-eval-design` defines proof requirements before counting any gain.

## Evidence vs Projection

### Directly Evidenced Current State

- File discovery and repository scoping are handled by raw glob and `os.walk` flows in [colly.py](/C:/colly/colly.py#L1384) and [colly.py](/C:/colly/colly.py#L1409).
- Dependency analysis exists, but the main forward graph path is still centralized in [colly.py](/C:/colly/colly.py#L1092) and script import analysis in [colly.py](/C:/colly/colly.py#L1039).
- Directory listing, stats, and entanglement features are rendered independently in [colly.py](/C:/colly/colly.py#L1615), [colly.py](/C:/colly/colly.py#L1623), and [colly.py](/C:/colly/colly.py#L1627), while normal content output is suppressed when any of those modes is active at [colly.py](/C:/colly/colly.py#L1631).
- Truncation has explicit support for plain text plus JSON/JSONL/CSV/YAML in [colly.py](/C:/colly/colly.py#L585), [colly.py](/C:/colly/colly.py#L606), [colly.py](/C:/colly/colly.py#L615), [colly.py](/C:/colly/colly.py#L634), and [colly.py](/C:/colly/colly.py#L754).
- Current tests validate ignore behavior, truncation, JS/TS dependency graphs, and JS/TS dependency expansion in [test_colly.py](/C:/colly/test_colly.py#L47), [test_colly.py](/C:/colly/test_colly.py#L72), [test_colly.py](/C:/colly/test_colly.py#L116), [test_colly.py](/C:/colly/test_colly.py#L232), and [test_colly.py](/C:/colly/test_colly.py#L304).

### Projected Future-Complete State

- Colly can scope repositories using both Colly-native ignores and VCS-aware rules without drowning in vendored or generated trees.
- Output modes compose predictably instead of acting like mutually exclusive “screens.”
- Python dependency graph quality reaches parity with the stronger JS/TS regression surface.
- Large-repo behavior is explicitly budgeted and benchmarked rather than emerging accidentally from full-tree scans.
- Truncation is safer and more format-aware for common text formats beyond the currently covered set.
- Verbose output explains what Colly actually selected, excluded, expanded, and truncated so failures are diagnosable without reading the source.

### Speculative Assumptions

- `.gitignore` support will matter in user workflows more than additional bespoke exclusion flags.
- Output-mode composability is desirable rather than intentionally excluded.
- Performance bottlenecks will be materially improved by scoping and dependency-graph narrowing rather than only low-level micro-optimizations.

## Artifact Contract

This plan is complete only if it satisfies all of the following:

- Names six or fewer capability slices.
- Provides a rationale for the ordering.
- Assigns each slice to either `executing-plans` or `systematic-debugging`.
- Defines a falsifiable claim and a verification gate for each slice.
- Avoids denominator hacks such as weakening output checks or shrinking test scope after failures appear.

Failure conditions:

- Treating a projected gain as current evidence.
- Bundling multiple slices into one unverifiable change.
- Claiming “performance improvement” without a before/after command or reproducible workload.
- Claiming “better graph quality” without additional regression tests.

## Residual Ranking

| Rank | Capability Slice | Residual Score | Dominant Driver | Class | Recommended Route |
|---|---|---:|---|---|---|
| 1 | Git-aware repository scoping | 9 | Coverage gap | reducible | `executing-plans` |
| 2 | Composable output modes | 8 | Contract gap | reducible | `systematic-debugging` |
| 3 | Python dependency graph parity | 8 | Evidence gap | reducible | `systematic-debugging` |
| 4 | Large-repo performance budget | 7 | Validation gap | reducible | `systematic-debugging` |
| 5 | Format-aware truncation expansion | 6 | Coverage gap | reducible | `executing-plans` |
| 6 | Selection and transformation provenance | 5 | Granularity mismatch | reducible | `executing-plans` |

Operational meaning:

- `9-8`: high-leverage blocker that changes whether Colly is usable on realistic repositories.
- `7-6`: meaningful quality or scale gain that should be batched only after a clear harness is defined.
- `5`: valuable UX and observability lift that becomes more important as the other slices land.

## Evaluation Ladder

### Global Baseline

- Run: `python -m pytest -q test_colly.py`
- Run: `python .\colly.py --help`
- For any performance slice, record one stable repo-local command before and after the change.

### Global Failure Rule

- Reject a slice if it adds a mainline test regression, weakens existing assertions, or only “improves” by narrowing the input surface without an explicit user-controlled rule.

### Global Release Gate

- No slice counts as accepted until its focused tests pass, the broader `python -m pytest -q test_colly.py` suite passes, and the exact CLI behavior claimed in the slice is reproduced in a fresh command invocation.

## Task 1: Git-Aware Repository Scoping

**Files:**
- Modify: [colly.py](/C:/colly/colly.py)
- Modify: [test_colly.py](/C:/colly/test_colly.py)

**Claim:** Colly should support repository-aware selection so tracked workflows do not require manual exclusion curation for ordinary use.

**Suggested route:** `executing-plans`

- [ ] Add a failing test for `.gitignore` alignment or tracked-file selection.
- [ ] Verify the test fails for the intended missing capability.
- [ ] Add the smallest CLI surface that makes the rule explicit, likely one of:
  - `--gitignore`
  - `--tracked-only`
  - `--untracked-only`
- [ ] Route discovery through Git only when the user opts into it, so behavior stays explicit.
- [ ] Re-run the focused test, then `python -m pytest -q test_colly.py`.

**Verification:**

- `python -m pytest -q test_colly.py -k "gitignore or tracked"`
- `python -m pytest -q test_colly.py`

## Task 2: Composable Output Modes

**Files:**
- Modify: [colly.py](/C:/colly/colly.py#L1615)
- Modify: [test_colly.py](/C:/colly/test_colly.py)

**Claim:** Directory listing, stats, entanglement map, dependency graph, and main file content should compose in a predictable order instead of suppressing one another implicitly.

**Suggested route:** `systematic-debugging`

- [ ] Reproduce the current suppression behavior with a focused CLI regression test.
- [ ] Decide the intended composition contract before changing code.
- [ ] Add failing tests for at least one supported combination such as:
  - `--directory-listing --show-stats -f .`
  - `--show-stats --no-dependency-graph -f small.txt`
- [ ] Implement the minimum render-order change that satisfies the contract.
- [ ] Re-run focused tests and the full Colly suite.

**Verification:**

- `python -m pytest -q test_colly.py -k "directory_listing or show_stats or entanglement"`
- `python -m pytest -q test_colly.py`

## Task 3: Python Dependency Graph Parity

**Files:**
- Modify: [colly.py](/C:/colly/colly.py#L285)
- Modify: [colly.py](/C:/colly/colly.py#L1092)
- Modify: [test_colly.py](/C:/colly/test_colly.py)

**Claim:** Python cross-file imports, symbol uses, and calls should have regression coverage similar to the existing JS/TS graph checks.

**Suggested route:** `systematic-debugging`

- [ ] Add a Python fixture pair similar to the existing JS/TS graph tests.
- [ ] Verify whether current behavior already passes part of the claim.
- [ ] Isolate the exact missing edge type if the graph is incomplete.
- [ ] Fix only the missing graph behavior and keep JS/TS coverage green.
- [ ] Add at least one `--bra` Python regression if missing.

**Verification:**

- `python -m pytest -q test_colly.py -k "python and dependency"`
- `python -m pytest -q test_colly.py`

## Task 4: Large-Repo Performance Budget

**Files:**
- Modify: [colly.py](/C:/colly/colly.py#L1409)
- Modify: [colly.py](/C:/colly/colly.py#L1602)
- Modify: [test_colly.py](/C:/colly/test_colly.py)

**Claim:** Selected-file workflows should not require a full supported-file scan across the entire inferred project root when that scan is unnecessary for the chosen feature set.

**Suggested route:** `systematic-debugging`

- [ ] Reproduce the hot path with a synthetic large directory tree fixture or a timing-sensitive harness that checks bounded traversal behavior.
- [ ] Identify which features truly require `all_supported_files`.
- [ ] Narrow dependency-graph and expansion precomputation to the feature paths that need it.
- [ ] Keep default behavior intact for graph-heavy modes.
- [ ] Re-run both correctness tests and the before/after workload.

**Verification:**

- `python -m pytest -q test_colly.py -k "performance or bra or dependency_graph"`
- Record before/after timings for one fixed command against the same synthetic repo fixture.

## Task 5: Format-Aware Truncation Expansion

**Files:**
- Modify: [colly.py](/C:/colly/colly.py#L585)
- Modify: [test_colly.py](/C:/colly/test_colly.py)

**Claim:** Existing truncation behavior should safely handle more real-world text formats without destroying the structural signals users rely on.

**Suggested route:** `executing-plans`

- [ ] Choose one or two high-value text formats adjacent to the current surface, likely Markdown and XML/HTML.
- [ ] Write failing tests that preserve headers, tags, or key structural anchors while shrinking oversized values.
- [ ] Add the smallest format-specific branch to the truncation strategy.
- [ ] Ensure existing JSON/CSV/YAML tests still pass unchanged.

**Verification:**

- `python -m pytest -q test_colly.py -k "truncate"`
- `python -m pytest -q test_colly.py`

## Task 6: Selection and Transformation Provenance

**Files:**
- Modify: [colly.py](/C:/colly/colly.py#L1225)
- Modify: [test_colly.py](/C:/colly/test_colly.py)

**Claim:** Verbose mode should explain what Colly selected and why, including loaded ignore files, explicit include overrides, and whether truncation or dependency expansion changed the final file set.

**Suggested route:** `executing-plans`

- [ ] Add focused verbose-mode tests for provenance lines.
- [ ] Extend verbose output with counts and sources rather than vague prose.
- [ ] Include loaded ignore file paths, exclusion counts, forced-include counts, and dependency-expansion counts where applicable.
- [ ] Keep output deterministic for testability.

**Verification:**

- `python -m pytest -q test_colly.py -k "verbose or exclusion or ignore"`
- `python -m pytest -q test_colly.py`

## Recommended Execution Order

1. Task 2: Composable output modes
2. Task 3: Python dependency graph parity
3. Task 4: Large-repo performance budget
4. Task 1: Git-aware repository scoping
5. Task 5: Format-aware truncation expansion
6. Task 6: Selection and transformation provenance

Rationale:

- Tasks 2-4 remove the highest current ambiguity and scale risk in the core execution path.
- Task 1 becomes safer once the repository-selection contract is clearer.
- Tasks 5-6 extend existing capability without destabilizing the core traversal model.

## Contradictions and Open Questions

- It is not yet proven that output-mode suppression is a bug rather than an intentional product choice.
- Git-aware scoping may overlap awkwardly with explicit `.collyignore` semantics if precedence rules are not made explicit.
- Performance work is high leverage, but the gain mechanism must be demonstrated with a stable workload rather than intuition.

## Verification and Approval Status

- Verification completed for the current baseline only: `python -m pytest -q test_colly.py` is green.
- No new capability slice has been implemented or validated yet.
- Approval gate remains open on slice order and on whether to execute inline or per-slice.

## Next Action

- Preferred next route: `systematic-debugging` for Task 2, then `test-driven-development`, then `verification-before-completion`.
- If the user prefers batch execution instead of bug-first sequencing, use `executing-plans` in the order listed above.
