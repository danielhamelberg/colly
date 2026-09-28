# Colly Agent Tool Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, bounded, machine-readable Colly tool contract that works through shell, OpenAI Codex function/programmatic tool calling, and Muse Glimmer OpenAI-compatible or Hermes-style scaffolds while preserving the existing human CLI.

**Architecture:** Keep `colly.py` distributable as one script, but introduce explicit request, selection, collection, rendering, inventory, artifact-transaction, and completion stages. Both agent flag mode and JSON-stdin mode normalize into the same `AgentRequest`. Provider adapters live only in the evaluation harness and derive from one portable schema.

**Tech Stack:** Python standard library, `argparse`, `dataclasses`, `pathlib`, `hashlib`, `json`, `tempfile`, `unittest`, optional HTTP calls from the evaluation harness; no production provider SDK or MCP dependency.

---

## Planning basis

Design specification: `C:\colly\docs\superpowers\specs\2026-08-11-colly-agent-tool-contract-design.md`

Baseline established on 2026-08-11:

```text
python -m unittest -v
Ran 30 tests in 4.229s
OK
```

The working tree already contains and tests these behaviors. Do not reimplement or revert them:

- exact non-glob `-f` accepts an existing text file regardless of extension;
- directory/glob scans still apply supported-extension filtering;
- directly selected oversized files remain unchanged by default;
- `--auto-truncate` explicitly re-enables the size guard.

Implementation claims are subject to the recursive self-improvement protocol supplied for this workspace: keep evaluator changes versioned, preserve the denominator, distinguish evidence classes, and do not claim model compatibility until the named model/runtime configuration passes the frozen suite.

## Contract constants

Use these version-1 defaults in one place in `C:\colly\colly.py` and mirror them in the request schema examples:

```python
AGENT_SCHEMA_VERSION = 1
AGENT_DEFAULT_MAX_FILES = 256
AGENT_DEFAULT_MAX_SOURCE_BYTES = 16 * 1024 * 1024
AGENT_DEFAULT_MAX_BUNDLE_BYTES = 384 * 1024
AGENT_DEFAULT_MAX_SCAN_ENTRIES = 10_000
AGENT_DEFAULT_DEADLINE_SECONDS = 60
AGENT_MAX_REQUEST_BYTES = 64 * 1024
```

The bundle limit is a lowest-common-denominator default for the target model families. Limit exhaustion must fail with `limit_exceeded`; it must never silently activate truncation.

## Task 1: Add agent records and canonical JSON primitives

**Files:**

- Modify: `C:\colly\colly.py`
- Create: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Write failing canonicalization and record tests**

Create `test_colly_agent.py` with a focused unit-test class. The first tests should import production functions directly rather than spawning a process:

```python
import json
import unittest

from colly import AgentFailure, canonical_json_bytes, sha256_hex


class AgentPrimitiveTests(unittest.TestCase):
    def test_canonical_json_is_compact_sorted_utf8_with_final_lf(self):
        value = {"z": 1, "accent": "é", "nested": {"b": 2, "a": 1}}
        encoded = canonical_json_bytes(value)
        self.assertEqual(
            encoded,
            '{"accent":"é","nested":{"a":1,"b":2},"z":1}\n'.encode("utf-8"),
        )

    def test_sha256_hex_hashes_bytes(self):
        self.assertEqual(
            sha256_hex(b"colly"),
            "527d0e2e569e52587ade87bea8793a2ec49e76d8b38a2fa3915039725a808553",
        )

    def test_agent_failure_carries_stable_machine_fields(self):
        failure = AgentFailure("decode_failed", 4, "not utf-8", "bad.txt")
        self.assertEqual(failure.code, "decode_failed")
        self.assertEqual(failure.exit_code, 4)
        self.assertEqual(failure.failed_path, "bad.txt")
```

- [ ] **Step 2: Run the tests and verify they fail for missing symbols**

Run:

```powershell
python -m unittest -v test_colly_agent.AgentPrimitiveTests
```

Expected: import failure naming `AgentFailure`, `canonical_json_bytes`, or `sha256_hex`.

- [ ] **Step 3: Add immutable records and helpers near the existing constants**

Add these record shapes. Use tuples in immutable records so ordering cannot drift after selection:

```python
@dataclass(frozen=True)
class AgentRequest:
    schema_version: int
    root: str
    files: Tuple[str, ...]
    output: str
    inventory: str
    replace: bool
    dependency_graph: bool
    bra_preset: str
    auto_truncate: bool
    encoding: str
    max_files: int
    max_source_bytes: int
    max_bundle_bytes: int
    max_scan_entries: int
    deadline_seconds: int


@dataclass(frozen=True)
class SelectionRecord:
    absolute_path: str
    relative_path: str
    selection_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class ExclusionRecord:
    relative_path: str
    reason: str
    requested: bool


@dataclass(frozen=True)
class TransformationRecord:
    kind: str
    parameters: Dict[str, Any]
    before_sha256: str
    after_sha256: str


@dataclass(frozen=True)
class CollectedFile:
    absolute_path: str
    relative_path: str
    selection_reasons: Tuple[str, ...]
    source_size_bytes: int
    source_sha256: str
    encoding: str
    language: str
    rendered_text: str
    rendered_size_bytes: int
    rendered_sha256: str
    content_mode: str
    transformations: Tuple[TransformationRecord, ...]


@dataclass(frozen=True)
class SelectionResult:
    files: Tuple[SelectionRecord, ...]
    excluded: Tuple[ExclusionRecord, ...]
    scanned_entries: int


@dataclass(frozen=True)
class ArtifactSet:
    bundle_path: str
    bundle_sha256: str
    bundle_size_bytes: int
    inventory_path: str
    inventory_sha256: str


class AgentFailure(Exception):
    def __init__(self, code: str, exit_code: int, message: str, failed_path: str = ""):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.failed_path = failed_path
```

Add:

```python
def canonical_json_bytes(value: Any) -> bytes:
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
```

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
python -m unittest -v test_colly_agent.AgentPrimitiveTests
python -m unittest -v
```

Expected: primitive tests pass; the existing 30 tests remain green.

- [ ] **Step 5: Commit the isolated primitive change**

```powershell
git add -- colly.py test_colly_agent.py
git commit -m "feat: add agent contract primitives"
```

## Task 2: Define and validate the portable request/result schemas

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`
- Create: `C:\colly\schemas\colly-agent-request.schema.json`
- Create: `C:\colly\schemas\colly-agent-result.schema.json`
- Create: `C:\colly\schemas\colly-inventory.schema.json`

- [ ] **Step 1: Add failing request-validation tests**

Use one complete valid document as a test fixture:

```python
VALID_REQUEST = {
    "schemaVersion": 1,
    "root": "C:/repo",
    "files": ["src/*.py", "requirements.lock"],
    "output": "audit/bundle.md",
    "inventory": "audit/bundle.inventory.json",
    "replace": False,
    "dependencyGraph": False,
    "braPreset": "none",
    "autoTruncate": False,
    "encoding": "utf-8",
    "maxFiles": 256,
    "maxSourceBytes": 16777216,
    "maxBundleBytes": 393216,
    "maxScanEntries": 10000,
    "deadlineSeconds": 60,
}
```

Test these cases:

```python
def test_parse_agent_request_accepts_complete_v1_document(self):
    request = parse_agent_request_document(VALID_REQUEST)
    self.assertEqual(request.files, ("src/*.py", "requirements.lock"))
    self.assertEqual(request.bra_preset, "none")

def test_parse_agent_request_rejects_unknown_field(self):
    document = dict(VALID_REQUEST, surprise=True)
    with self.assertRaisesRegex(AgentFailure, "unknown field"):
        parse_agent_request_document(document)

def test_parse_agent_request_rejects_bool_where_integer_is_required(self):
    document = dict(VALID_REQUEST, maxFiles=True)
    with self.assertRaises(AgentFailure) as caught:
        parse_agent_request_document(document)
    self.assertEqual(caught.exception.code, "invalid_request")

def test_parse_agent_request_rejects_empty_files(self):
    document = dict(VALID_REQUEST, files=[])
    with self.assertRaises(AgentFailure):
        parse_agent_request_document(document)
```

- [ ] **Step 2: Run and observe missing parser failure**

```powershell
python -m unittest -v test_colly_agent.AgentRequestTests
```

- [ ] **Step 3: Implement strict manual validation without adding a runtime dependency**

Add `AGENT_REQUEST_FIELDS`, exact type checks, integer ranges, the `braPreset` enum (`none`, `focused`, `review`, `architecture`), nonempty strings, nonempty `files`, and duplicate artifact-path rejection. Reject unknown fields. Do not coerce strings, booleans, numbers, or path values.

Expose:

```python
def parse_agent_request_document(document: Any) -> AgentRequest:
    if type(document) is not dict:
        raise AgentFailure("invalid_request", 2, "request must be a JSON object")
    unknown = sorted(set(document) - AGENT_REQUEST_FIELDS)
    missing = sorted(AGENT_REQUEST_FIELDS - set(document))
    if unknown:
        raise AgentFailure("invalid_request", 2, f"unknown field: {unknown[0]}")
    if missing:
        raise AgentFailure("invalid_request", 2, f"missing field: {missing[0]}")
    return _build_validated_agent_request(document)
```

- [ ] **Step 4: Add strict JSON Schema files**

Each schema must declare Draft 2020-12, set `additionalProperties: false`, list every field in `required`, avoid nullable unions and conditional `oneOf`, and constrain numeric fields to positive integers. The request schema must use the exact camelCase document fields in `VALID_REQUEST`.

The result schema must require these fields on both success and failure:

```text
schemaVersion, operation, status, exitCode, requestSha256,
bundlePath, bundleSha256, bundleSizeBytes,
inventoryPath, inventorySha256,
filesSelected, filesTransformed, filesExcluded,
errorCode, message, failedPath, warnings
```

The inventory schema must define `bundle`, `limits`, `files`, and `excluded`, including both source and rendered hashes and an array of transformation records.

- [ ] **Step 5: Add schema drift tests using only `json`**

Load all three files and assert:

- every schema parses;
- request `required` equals `AGENT_REQUEST_FIELDS`;
- `additionalProperties` is false at the root and per-record object levels;
- the result required set is stable;
- the inventory file item requires `sourceSha256`, `renderedSha256`, `selectionReasons`, `contentMode`, and `transformations`.

- [ ] **Step 6: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentRequestTests test_colly_agent.AgentSchemaTests
python -m unittest -v
git add -- colly.py test_colly_agent.py schemas
git commit -m "feat: define portable agent schemas"
```

## Task 3: Implement explicit-root, deterministic, bounded selection

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`
- Create fixture files under temporary directories only; do not add generated case directories to the repository.

- [ ] **Step 1: Add failing selection tests**

Cover these exact behaviors:

1. Exact `requirements.lock` is selected with `("explicit-file",)`.
2. Directory scan includes `src/app.py` but records `requirements.lock` as `unsupported-extension`.
3. Glob results are sorted by normalized POSIX relative path.
4. Missing exact selector raises `requested_file_missing` and exit code 3.
5. Zero-match glob raises `selector_no_match`.
6. `../outside.py` and an absolute path outside root raise `path_outside_root`.
7. Every symlink is excluded in agent schema version 1, and a legacy `--follow-symlinks` flag is rejected in agent mode.
8. `maxScanEntries`, `maxFiles`, `maxSourceBytes`, and `deadlineSeconds` each raise `limit_exceeded` independently.
9. Exact files bypass `.collyignore` and default exclusions but not root, binary, decode, or budget checks.
10. Output and inventory paths selected as inputs raise `artifact_source_conflict`.

Use a helper that always constructs `AgentRequest` from a complete document so tests do not bypass validation.

- [ ] **Step 2: Verify selection tests fail**

```powershell
python -m unittest -v test_colly_agent.AgentSelectionTests
```

- [ ] **Step 3: Add root/path helpers**

Implement:

```python
def normalized_relative_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise AgentFailure(
            "path_outside_root", 3, "path resolves outside root", str(path)
        ) from exc
    return relative.as_posix()


def resolve_agent_path(root: Path, value: str, *, must_exist: bool) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=must_exist)
    normalized_relative_path(root, resolved)
    return resolved


def check_agent_deadline(deadline_at: float, failed_path: str = "") -> None:
    if time.monotonic() > deadline_at:
        raise AgentFailure(
            "limit_exceeded", 5, "agent deadline exceeded", failed_path
        )
```

Add `normalize_agent_request(request)` after path validation. It must replace the root with its resolved absolute POSIX form, convert exact absolute selectors inside the root to relative POSIX selectors, normalize relative selector separators, require glob selectors to be root-relative, and normalize output/inventory to root-relative POSIX paths. Use this effective request for hashing and every downstream stage.

Resolve the root first with `strict=True`, require a directory, and never use string-prefix containment checks.

- [ ] **Step 4: Implement `select_files(request, exclusion_patterns, deadline_at)` as a pure selection stage**

Required algorithm:

1. Load `.collyignore` and configured exclusions from the declared root.
2. Classify each selector as exact file, exact directory, or glob using `glob.has_magic`.
3. Anchor every relative selector at the root.
4. Sort directory names, file names, and glob matches before evaluation.
5. Increment `scanned_entries` for every directory entry actually inspected and call `check_agent_deadline(deadline_at)` between entries.
6. For directory/glob candidates, apply symlink, exclusion, and supported-extension rules and record exclusions.
7. For exact files, bypass extension and exclusion filters and record `explicit-file`.
8. Merge duplicate paths by unioning and sorting their reasons.
9. Reject case-normalized path collisions on Windows.
10. Apply the file-count and source-byte limits to the final seed set.

Sort external paths with:

```python
key=lambda item: item.relative_path.encode("utf-8")
```

Do not read or decode file content in this stage beyond `stat().st_size`.

- [ ] **Step 5: Run focused/full tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentSelectionTests
python -m unittest -v
git add -- colly.py test_colly_agent.py
git commit -m "feat: add bounded agent file selection"
```

## Task 4: Integrate bounded dependency expansion without restoring implicit graph output

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add failing expansion tests**

Create a root containing `main.py -> dep.py -> deep.py` and assert:

- `braPreset: none` selects only `main.py`;
- `focused` adds `dep.py` with `dependency-expansion` in `selectionReasons`;
- final order remains deterministic;
- final `maxFiles` and `maxSourceBytes` include expanded files;
- graph generation is not invoked by selection or expansion, regardless of the `dependencyGraph` field.

- [ ] **Step 2: Add an agent adapter around the existing `DependencyExpander`**

Expose:

```python
def expand_agent_selection(
    request: AgentRequest,
    selection: SelectionResult,
    exclusion_patterns: List[re.Pattern],
    deadline_at: float,
    progress: Optional[ProgressReporter] = None,
) -> SelectionResult:
```

Construct the existing expander with the declared root and sorted supported-file index. Map `none` to no expansion and the three named presets directly. Merge reasons rather than replacing them. Apply agent global limits after expansion even if the BRA preset has its own tighter budget, and check the monotonic deadline between indexed files and traversal layers.

Do not enable `symbol` expansion implicitly. Do not infer a root from AST files. Do not render dependency graphs in this function.

- [ ] **Step 3: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentExpansionTests
python -m unittest -v
git add -- colly.py test_colly_agent.py
git commit -m "feat: bind dependency expansion to agent limits"
```

## Task 5: Collect bytes strictly and record every transformation

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add failing strict-collection tests**

Test:

- UTF-8 text produces matching source/render hashes and `contentMode: verbatim`.
- UTF-8 BOM, UTF-16 LE BOM, and UTF-16 BE BOM are decoded and report their actual encoding.
- invalid UTF-8 raises `decode_failed`; no U+FFFD reaches the collected text.
- NUL-bearing exact binary raises `binary_input`.
- a missing or unreadable selected file raises a stable failure rather than disappearing.
- a dependency-expanded file that disappears before collection raises the same stable failure.
- empty text is collected and inventoried rather than omitted.
- unknown exact text uses `plaintext`.
- `autoTruncate: false` preserves oversized text.
- `autoTruncate: true` records `auto-truncate` with before/after hashes and parameters.
- unsafe structured truncation leaves content verbatim and lets the later bundle budget decide whether to fail.

- [ ] **Step 2: Implement deterministic decoding**

Add a pre-decode binary check for non-BOM data, then strict decoding:

```python
ENCODING_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decode_agent_source(data: bytes, default_encoding: str, relative_path: str) -> Tuple[str, str]:
    has_known_bom = any(data.startswith(marker) for marker, _ in ENCODING_BOMS)
    sample = data[:8192]
    disallowed_controls = sum(
        byte < 32 and byte not in (9, 10, 12, 13) for byte in sample
    )
    if not has_known_bom and sample and (
        0 in sample or disallowed_controls / len(sample) > 0.10
    ):
        raise AgentFailure("binary_input", 4, "content appears binary", relative_path)
    encoding = next(
        (name for marker, name in ENCODING_BOMS if data.startswith(marker)),
        default_encoding,
    )
    try:
        text = data.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise AgentFailure("decode_failed", 4, str(exc), relative_path) from exc
    if "\x00" in text:
        raise AgentFailure("binary_input", 4, "decoded content contains NUL", relative_path)
    return text, encoding
```

When decoding explicit UTF-16/32 endianness, strip exactly one decoded BOM character if present and document that BOM handling through the `encoding` field; do not label it a content transformation.

- [ ] **Step 3: Implement `collect_files(request, selection, deadline_at)`**

Read bytes once, reapply the source-byte budget to actual bytes in case files changed after selection, hash source bytes, strict-decode, apply only the explicitly requested auto-truncation strategy, and hash rendered UTF-8 text. Reuse the current structure-aware truncation function, but create a `TransformationRecord` only when output differs. Check the monotonic deadline before and after each file read and transformation.

Do not call legacy `process_single_file()` because it uses replacement decoding and silent error returns. Do not expose `--minify-python` through the JSON contract.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentCollectionTests
python -m unittest -v
git add -- colly.py test_colly_agent.py
git commit -m "feat: collect agent inputs without decoding loss"
```

## Task 6: Render a deterministic bundle and canonical inventory

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add failing render/inventory tests**

Test these properties:

1. Two runs over unchanged inputs return byte-identical bundle and inventory bytes.
2. Source content retains trailing whitespace and its final newline state inside the body.
3. A file containing triple backticks uses a longer outer fence.
4. A path containing control characters cannot create a new Markdown heading; the heading uses JSON path quoting.
5. Paths use `/` regardless of host separators.
6. `maxBundleBytes` fails before artifacts are written.
7. Inventory has separate source and rendered hashes, sorted unique reasons, and ordered transformations.
8. Inventory has no timestamp, PID, hostname, elapsed time, or temporary path.
9. Inventory bundle hash and size match the exact rendered bytes.
10. Dependency graph output, when requested, is deterministic and included before file sections.

- [ ] **Step 2: Add a dynamic fence helper and agent renderer**

Use JSON quoting for the heading and a fence longer than any run in the content:

```python
def markdown_fence_for(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_agent_file(file: CollectedFile) -> bytes:
    fence = markdown_fence_for(file.rendered_text)
    heading_path = json.dumps(file.relative_path, ensure_ascii=False)
    pieces = [f"## File: {heading_path}\n", f"{fence}{file.language}\n"]
    pieces.append(file.rendered_text)
    if not file.rendered_text.endswith(("\n", "\r")):
        pieces.append("\n")
    pieces.append(f"{fence}\n")
    return "".join(pieces).encode("utf-8")
```

`render_bundle()` must concatenate pre-rendered feature sections and file sections in a fixed order, check the byte ceiling incrementally, check the monotonic deadline between sections, and return bytes.

- [ ] **Step 3: Render the dependency graph only on explicit request**

Implement:

```python
def render_agent_dependency_graph(
    request: AgentRequest,
    files: Sequence[CollectedFile],
    exclusion_patterns: List[re.Pattern],
    deadline_at: float,
) -> bytes:
```

Return `b""` when `request.dependency_graph` is false. Otherwise reuse the existing graph builder with the declared root and only the supported, already-collected paths; check the deadline before/after graph construction and encode its Markdown as UTF-8. Do not infer a root, rescan unrelated directories, or enable graph output merely because BRA expansion ran.

- [ ] **Step 4: Build inventory from records rather than reparsing Markdown**

Expose:

```python
def build_agent_inventory(
    request: AgentRequest,
    request_sha256: str,
    bundle_bytes: bytes,
    files: Sequence[CollectedFile],
    excluded: Sequence[ExclusionRecord],
) -> bytes:
```

Use `canonical_json_bytes()`. Sort files by UTF-8 relative path, sort exclusions by `(relativePath, reason, requested)`, and serialize transformations in application order.

- [ ] **Step 5: Validate generated documents against schema invariants**

Because production has no `jsonschema` dependency, call the same exact-field/type validators used for requests for result and inventory documents before serialization. Tests should also load the JSON schema files and compare required field sets to runtime constants.

- [ ] **Step 6: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentRenderingTests test_colly_agent.AgentInventoryTests
python -m unittest -v
git add -- colly.py test_colly_agent.py
git commit -m "feat: render deterministic agent artifacts"
```

## Task 7: Write the bundle and inventory as one recoverable artifact transaction

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add failing transaction tests**

Test:

- missing parent directories are created inside root;
- existing targets fail with `artifact_exists` unless `replace` is true;
- temporary files are created in each target's directory;
- successful writes match expected hashes and leave no temp/backup files;
- a simulated second `os.replace` failure removes a newly created bundle;
- the same failure in replace mode restores both old artifacts;
- targets outside root fail before any directory or temporary file is created;
- output and inventory must be distinct and cannot be selected sources;
- completion success is impossible when either final hash verification fails.

Use `unittest.mock.patch` to fail the inventory commit deterministically. Use the same approach for unreadable-file tests; do not simulate failures by changing global filesystem permissions.

- [ ] **Step 2: Implement staging helpers**

Use `tempfile.NamedTemporaryFile(delete=False, dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")`, close before `os.replace` for Windows compatibility, flush and `os.fsync`, then reopen to verify bytes and hash.

- [ ] **Step 3: Implement `write_agent_artifacts()` rollback semantics**

Required signature:

```python
def write_agent_artifacts(
    root: Path,
    output_value: str,
    inventory_value: str,
    bundle_bytes: bytes,
    inventory_bytes: bytes,
    replace: bool,
) -> ArtifactSet:
```

Stage both files before committing either. In replace mode, move existing files to unique same-directory backups, commit bundle then inventory, and restore on failure. In create mode, delete a newly committed bundle if inventory commit fails. Always clean temporary and backup paths in `finally` blocks.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentArtifactTransactionTests
python -m unittest -v
git add -- colly.py test_colly_agent.py
git commit -m "feat: write recoverable agent artifact sets"
```

## Task 8: Add the `--agent` flag profile and stable completion/error records

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add failing end-to-end CLI tests**

Add a subprocess helper that does not inject `-n`:

```python
def run_agent(self, *args, input_text=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--agent", *args],
        cwd=self.root,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
```

Test:

- `--agent` requires `--root`, `-f`, `--output`, and `--inventory` in flag mode;
- success stdout is exactly one newline-terminated JSON object and contains no Markdown/progress text;
- the bundle and inventory paths, hashes, sizes, and counts match disk;
- diagnostics, if any, are stderr-only;
- no clipboard function is called;
- graph output is off by default and enabled only with `--dependency-graph`;
- truncation/minification stay off unless explicit, and minification is rejected in agent v1;
- missing/unreadable/binary/budget/write errors emit a JSON result and matching nonzero exit code;
- human mode still emits Markdown/clipboard behavior exactly as before.

- [ ] **Step 2: Add parser options without changing human defaults**

Add:

```text
--agent
--root PATH
--output PATH
--inventory PATH
--replace
--dependency-graph
--max-files N
--max-source-bytes N
--max-bundle-bytes N
--max-scan-entries N
--deadline-seconds N
```

Do not reuse `-o`; it already means `--override-max-length`. Put `--dependency-graph` and `--no-dependency-graph` in a mutually exclusive group. Resolve the effective graph default after parsing: true for human mode, false for agent mode.

- [ ] **Step 3: Create one normalized request builder for flag mode**

`build_agent_request_from_args(args, argv)` must:

- require selectors instead of accepting the human `['**/*']` default;
- apply the constants at the top of this plan;
- reject `--minify-python` in agent v1;
- reject `--follow-symlinks` in agent v1;
- map existing named `--bra` values into `braPreset` and reject legacy aliases in agent mode;
- set `autoTruncate` only when explicitly passed;
- map the existing `--encoding` value into the required `encoding` request field;
- serialize to the same document shape used by JSON request mode and call `parse_agent_request_document()`.

- [ ] **Step 4: Give `main()` exclusive ownership of stdout**

Add:

```python
def agent_error_result(failure: AgentFailure, request_sha256: str = "") -> Dict[str, Any]:
    return {
        "schemaVersion": 1,
        "operation": "collect",
        "status": "error",
        "exitCode": failure.exit_code,
        "requestSha256": request_sha256,
        "bundlePath": "",
        "bundleSha256": "",
        "bundleSizeBytes": 0,
        "inventoryPath": "",
        "inventorySha256": "",
        "filesSelected": 0,
        "filesTransformed": 0,
        "filesExcluded": 0,
        "errorCode": failure.code,
        "message": str(failure),
        "failedPath": failure.failed_path,
        "warnings": [],
    }
```

Use a bootstrap check for `--agent` and a parser subclass that raises `AgentFailure` rather than exiting before JSON can be written. The outermost agent handler catches expected failures and unexpected exceptions. Unexpected exceptions emit `internal_error`/70 on stdout and a traceback only when `--debug` is explicitly set, always on stderr.

- [ ] **Step 5: Run end-to-end and regression tests**

```powershell
python -m unittest -v test_colly_agent.AgentCliTests
python -m unittest -v
```

- [ ] **Step 6: Manually inspect stdout separation**

```powershell
$case = Join-Path $env:TEMP 'colly-agent-smoke'
New-Item -ItemType Directory -Force -Path $case | Out-Null
Set-Content -LiteralPath (Join-Path $case 'a.py') -Value "print('ok')" -Encoding utf8
python .\colly.py --agent --root $case -f a.py --output audit\bundle.md --inventory audit\bundle.inventory.json
```

Expected: one compact JSON line on stdout; both artifacts exist below `$case\audit`.

- [ ] **Step 7: Commit**

```powershell
git add -- colly.py test_colly_agent.py
git commit -m "feat: add deterministic agent cli profile"
```

## Task 9: Add JSON file/stdin request transport with flag-mode equivalence

**Files:**

- Modify: `C:\colly\colly.py`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add failing JSON transport tests**

Test:

- `--agent --request-json request.json` succeeds;
- `--agent --request-json -` reads one LF-terminated UTF-8 JSON object without waiting for EOF;
- empty, over-64-KiB, malformed, array-valued, duplicated-key, and same-record trailing-value JSON fail as `invalid_request`;
- UTF-8 BOM on a request file is accepted, but non-UTF-8 request data fails;
- selection/artifact/transform flags cannot be mixed with `--request-json`;
- semantically identical flag and JSON requests have the same canonical request hash and byte-identical artifacts after resetting the artifact targets between runs;
- a complete newline-terminated stdin record begins execution even if the producing pipe remains open;
- all failures still produce exactly one JSON stdout value.

Reject duplicate JSON keys with an `object_pairs_hook` rather than allowing last-key-wins behavior.

- [ ] **Step 2: Add `--request-json PATH_OR_DASH` and strict loader**

Implement:

```python
def reject_duplicate_json_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AgentFailure("invalid_request", 2, f"duplicate field: {key}")
        value[key] = item
    return value
```

For request files, reject a size above `AGENT_MAX_REQUEST_BYTES`, read bytes once, and allow multiline JSON. For stdin, call `sys.stdin.buffer.readline(AGENT_MAX_REQUEST_BYTES + 1)`, require a terminating LF, and reject an oversized record; do not wait for pipe EOF or read a second record. Decode strict UTF-8/UTF-8-SIG, parse with the hook and `parse_constant` rejection, validate, and compute `requestSha256` from the canonical normalized request document rather than the caller's formatting.

- [ ] **Step 3: Make both transports call the same orchestrator**

Required boundary:

```python
def execute_agent_request(request: AgentRequest) -> Tuple[Dict[str, Any], int]:
    effective_request = normalize_agent_request(request)
    request_document = agent_request_to_document(effective_request)
    request_sha256 = sha256_hex(canonical_json_bytes(request_document))
    deadline_at = time.monotonic() + effective_request.deadline_seconds
    exclusion_patterns = load_agent_exclusion_patterns(Path(effective_request.root))
    selection = select_files(effective_request, exclusion_patterns, deadline_at)
    selection = expand_agent_selection(
        effective_request, selection, exclusion_patterns, deadline_at, progress=None
    )
    collected = collect_files(effective_request, selection, deadline_at)
    graph_bytes = render_agent_dependency_graph(
        effective_request, collected, exclusion_patterns, deadline_at
    ) if effective_request.dependency_graph else b""
    bundle = render_bundle(effective_request, collected, graph_bytes, deadline_at)
    inventory = build_agent_inventory(
        effective_request,
        request_sha256,
        bundle,
        collected,
        selection.excluded,
    )
    artifacts = write_agent_artifacts(
        Path(effective_request.root),
        effective_request.output,
        effective_request.inventory,
        bundle,
        inventory,
        effective_request.replace,
    )
    result = build_agent_success_result(
        request_sha256, artifacts, collected, selection.excluded
    )
    return result, 0
```

There must be no transport-specific selection or rendering branches below this function.

- [ ] **Step 4: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentJsonTransportTests
python -m unittest -v
git add -- colly.py test_colly_agent.py
git commit -m "feat: accept canonical agent requests as json"
```

## Task 10: Publish one provider-neutral tool descriptor and tested adapters

**Files:**

- Create: `C:\colly\.agents\tools\colly-collect.tool.json`
- Create: `C:\colly\harness\tool_adapters.py`
- Create: `C:\colly\test_tool_adapters.py`

- [ ] **Step 1: Add failing adapter tests**

Test that both wrappers derive from the same request schema and preserve every constraint:

```python
def test_openai_responses_wrapper_uses_strict_function_schema(self):
    tool = build_openai_responses_tool()
    self.assertEqual(tool["type"], "function")
    self.assertEqual(tool["name"], "colly_collect_context")
    self.assertTrue(tool["strict"])
    self.assertFalse(tool["parameters"]["additionalProperties"])

def test_hermes_wrapper_only_changes_outer_envelope(self):
    responses = build_openai_responses_tool()
    hermes = build_hermes_tool()
    self.assertEqual(hermes["type"], "function")
    self.assertEqual(hermes["function"]["name"], responses["name"])
    self.assertEqual(hermes["function"]["parameters"], responses["parameters"])
```

Also assert the description is one sentence and under 240 characters, because redundant tool prose adds cost and can reduce routing clarity.

- [ ] **Step 2: Create the canonical descriptor**

Use this provider-neutral shape:

```json
{
  "schemaVersion": 1,
  "name": "colly_collect_context",
  "description": "Create a bounded local Markdown context bundle and canonical provenance inventory from explicitly selected files.",
  "inputSchema": "../../schemas/colly-agent-request.schema.json",
  "resultSchema": "../../schemas/colly-agent-result.schema.json",
  "transport": {
    "command": ["python", "colly.py", "--agent", "--request-json", "-"],
    "request": "stdin-json",
    "result": "stdout-json",
    "diagnostics": "stderr",
    "successExitCode": 0
  }
}
```

- [ ] **Step 3: Implement pure adapter builders**

`harness/tool_adapters.py` loads the descriptor and request schema and returns:

- OpenAI Responses function shape: `{type, name, description, parameters, strict}`;
- Hermes/OpenAI-compatible chat shape: `{type, function: {name, description, parameters}}`.

Do not add `call_id`, `caller`, model IDs, API keys, URLs, retry policies, or SDK objects to the canonical descriptor. Those belong to the host runtime.

- [ ] **Step 4: Add a PTC compatibility assertion**

Verify the Responses function descriptor can be copied unchanged into a host configuration with `allowed_callers` containing the programmatic tool runtime. The adapter test must state that the host, not Colly, preserves `call_id` and `caller` linkage.

- [ ] **Step 5: Run tests and commit**

```powershell
python -m unittest -v test_tool_adapters
python -m unittest -v
git add -- .agents/tools/colly-collect.tool.json harness/tool_adapters.py test_tool_adapters.py
git commit -m "feat: publish codex and hermes tool adapters"
```

## Task 11: Add a frozen compatibility evaluation and evidence ledger

**Files:**

- Create: `C:\colly\harness\agent_tool_eval.py`
- Create: `C:\colly\harness\agent_tool_adjudicate.py`
- Create: `C:\colly\harness\evals\agent-tool-cases-v1.jsonl`
- Create: `C:\colly\harness\evals\agent-tool-cases-v1.sha256`
- Create: `C:\colly\test_agent_tool_harness.py`
- Runtime output only: `C:\colly\artifacts\agent-evals\<iteration-id>\ledger.jsonl`

- [ ] **Step 1: Write the frozen closed-world case set before model trials**

Create 24 versioned cases:

- 8 main happy-path tasks: exact file, unknown extension, two files, glob, directory, explicit graph, focused expansion, replacement of known artifacts;
- 4 clarification/invalid-request tasks: absent root, missing selector, zero-match glob, conflicting artifact path;
- 6 adversarial tasks: traversal, symlink escape, binary file, invalid encoding, Markdown fence injection, filename/control-character injection;
- 4 budget/recovery tasks: scan, file, source-byte, and bundle-byte exhaustion followed by one corrected request;
- 2 stopping tasks: success must not be followed by another call; deterministic validation failure must not be retried.

Each line must contain `caseId`, user prompt, fixture recipe, expected normalized request fields, expected result status/error code, allowed call count, retry allowance, and safety assertions. Do not remove hard cases after seeing results; create `v2` for any justified evaluator revision.

Compute and commit the SHA-256 of the exact JSONL bytes.

- [ ] **Step 2: Add failing offline harness tests**

Test that:

- the case count and category counts are fixed;
- case IDs are unique;
- the stored SHA-256 matches;
- every expected request passes the production validator;
- simulated correct/incorrect traces score deterministically;
- a lowered denominator or missing case causes adjudication failure;
- the ledger schema contains all mandatory AGENTS.md fields.

- [ ] **Step 3: Implement provider runners behind explicit flags**

`agent_tool_eval.py` must support:

```text
--provider codex-responses
--provider codex-ptc
--provider muse-openai-compatible
--model MODEL_ID
--base-url URL
--reasoning-effort VALUE
--cases PATH
--ledger PATH
--seed INTEGER
--max-tool-calls 3
--max-retries 1
```

Default execution is offline fixture validation; no live call occurs without `--provider`. Read API keys from the provider's conventional environment variable and never write them to the ledger.

For Codex, send the strict Responses function descriptor and record the named model snapshot, reasoning effort, response IDs, tool-call count, tokens, latency, arguments, result record, and final assistant message. For PTC, separately record `program_output` and final message completeness.

For Muse Glimmer, use the OpenAI-compatible chat/Hermes wrapper, record the exact model ID, runtime name/version, quantization, reasoning-strength prompt, sampling parameters, and seed. Validate arguments locally even if the inference server claims schema enforcement.

- [ ] **Step 4: Implement a fixed adjudicator**

The adjudicator reads ledgers but never calls models. It must fail adoption unless:

- all 30 legacy tests pass;
- deterministic artifact/contract tests pass 100%;
- no critical safety assertion fails;
- each declared model/runtime configuration passes at least 22 of all 24 cases (91.7%);
- happy paths use exactly one Colly call;
- no trace exceeds three calls or one retry;
- corrected recovery calls fix only the failed field and do not broaden root/scope;
- final messages preserve required artifact paths and hashes;
- cost/latency improvements are ignored when correctness or evidence completeness falls.

Write one ledger record per case with:

```text
iteration_id, parent_system_version, candidate_id, hypothesis,
exact_modification, intended_mechanism, benchmarks_run,
main_results, regression_results, adversarial_results,
cost_results, safety_results, decision, decision_rationale,
reproducibility_notes, rollback_trigger, artifact_hashes,
operator_notes, evidence_class
```

- [ ] **Step 5: Run offline harness tests and a no-model baseline**

```powershell
python -m unittest -v test_agent_tool_harness
python harness\agent_tool_eval.py --cases harness\evals\agent-tool-cases-v1.jsonl --ledger artifacts\agent-evals\offline-v1\ledger.jsonl
python harness\agent_tool_adjudicate.py --ledger artifacts\agent-evals\offline-v1\ledger.jsonl
```

Expected: schema/fixture checks pass; the adjudicator labels this Class C contract evidence and does not claim live model compatibility.

- [ ] **Step 6: Run named live configurations only when infrastructure is available**

Codex example:

```powershell
python harness\agent_tool_eval.py --provider codex-responses --model gpt-5.6-sol --reasoning-effort medium --cases harness\evals\agent-tool-cases-v1.jsonl --ledger artifacts\agent-evals\codex-sol-v1\ledger.jsonl --seed 20260811
```

Muse example:

```powershell
python harness\agent_tool_eval.py --provider muse-openai-compatible --model meta-models/Muse-Glimmer-30B --base-url http://127.0.0.1:8000/v1 --reasoning-effort high --cases harness\evals\agent-tool-cases-v1.jsonl --ledger artifacts\agent-evals\muse-glimmer-v1\ledger.jsonl --seed 20260811
```

For a quantized Muse run, use a distinct configuration ID and ledger; do not merge it with BF16 results.

- [ ] **Step 7: Commit harness source and frozen cases, not generated live ledgers**

```powershell
git add -- harness/agent_tool_eval.py harness/agent_tool_adjudicate.py harness/evals test_agent_tool_harness.py
git commit -m "test: add agent tool compatibility evaluation"
```

## Task 12: Update agent-facing documentation and usage guidance

**Files:**

- Modify: `C:\colly\.agents\tools\colly-tool.md`
- Modify: `C:\colly\.agents\skills\use-colly\SKILL.md`
- Create: `C:\colly\docs\agent-tool-contract.md`
- Modify: `C:\colly\test_colly_agent.py`

- [ ] **Step 1: Add documentation contract tests**

Assert the docs contain:

- canonical flag and JSON-stdin examples;
- exact stdout/stderr/exit-code semantics;
- default limits;
- exact-file versus directory/glob semantics;
- strict binary/encoding failures;
- transformation provenance;
- Codex direct/PTC stopping rules;
- Muse Glimmer local-runtime validation rules;
- explicit statement that MCP is deferred;
- schema and descriptor paths.

- [ ] **Step 2: Rewrite the tool card's agent default**

The first recommended agent invocation must use `--agent`, explicit root, output, and inventory. Keep legacy `-n` recipes under a clearly labeled human/interactive section.

Add a host orchestration block:

```text
Call colly_collect_context once for a bounded context-collection stage.
Treat status=ok as terminal and do not repeat the call.
Do not retry invalid_request, path, selection, binary, decoding, or limit errors.
Retry artifact_write_failed at most once only when the host has evidence the failure is transient.
Never broaden root or selectors during recovery without user authorization.
```

- [ ] **Step 3: Update the skill routing**

The skill should prefer:

1. function tool/JSON request when available;
2. agent flag CLI when only a shell is available;
3. legacy `-n` mode only for human-readable ad hoc inspection.

Teach the skill to inspect completion status and hashes before reading bundle content, and to narrow selection on `limit_exceeded` instead of enabling truncation automatically.

- [ ] **Step 4: Document the compatibility matrix and evidence status**

In `docs/agent-tool-contract.md`, include:

| Route | Contract | Required status |
|---|---|---|
| Codex shell | agent flag CLI | supported |
| Codex Responses function | strict portable schema | supported after live gate |
| Codex programmatic calling | same function; host preserves call linkage | supported after PTC gate |
| Muse Glimmer OpenAI-compatible | Hermes wrapper over same schema | supported after live gate |
| Muse Glimmer direct shell | agent flag CLI | supported |
| MCP | none in v1 | deferred |

Label schema/unit results Class C and live/held-out claims accurately.

- [ ] **Step 5: Run tests and commit**

```powershell
python -m unittest -v test_colly_agent.AgentDocumentationTests
python -m unittest -v
git add -- .agents/tools/colly-tool.md .agents/skills/use-colly/SKILL.md docs/agent-tool-contract.md test_colly_agent.py
git commit -m "docs: define the colly agent tool contract"
```

## Task 13: Run final verification and write the acceptance record

**Files:**

- Create: `C:\colly\artifacts\agent-evals\implementation-v1\acceptance.json`
- Modify only if verification exposes a defect: files from prior tasks

- [ ] **Step 1: Run the complete deterministic suite twice**

```powershell
python -m unittest -v
python -m unittest -v
```

Expected: identical test count and all green both times.

- [ ] **Step 2: Run deterministic artifact comparison**

Use one fixed fixture root, run the same JSON request twice into separate empty artifact directories, and compare bundle and inventory bytes with `Get-FileHash` plus `Compare-Object`/byte reads. The only allowed difference is the explicitly different artifact path if the request differs; for a strict byte comparison, reset the destination between runs and reuse the identical request.

- [ ] **Step 3: Run adversarial and rollback-focused tests explicitly**

```powershell
python -m unittest -v test_colly_agent.AgentSelectionTests test_colly_agent.AgentCollectionTests test_colly_agent.AgentArtifactTransactionTests test_colly_agent.AgentCliTests
```

- [ ] **Step 4: Validate source hygiene**

```powershell
python -m py_compile colly.py harness\tool_adapters.py harness\agent_tool_eval.py harness\agent_tool_adjudicate.py
git diff --check
rg -n "errors=['\"]replace|print\(|copy_to_clipboard" colly.py
```

Review every match and verify none is reachable from agent collection/result paths except intentional stderr diagnostics. Legacy human paths may retain their existing behavior.

- [ ] **Step 5: Generate the implementation evidence ledger**

Run the frozen offline suite against the completed candidate:

```powershell
python harness\agent_tool_eval.py --cases harness\evals\agent-tool-cases-v1.jsonl --ledger artifacts\agent-evals\implementation-v1\ledger.jsonl
```

The ledger must include the deterministic test results from Steps 1–4 and one record per frozen case.

- [ ] **Step 6: Adjudicate and write the acceptance record**

```powershell
python harness\agent_tool_adjudicate.py --ledger artifacts\agent-evals\implementation-v1\ledger.jsonl --acceptance artifacts\agent-evals\implementation-v1\acceptance.json
```

The generated `acceptance.json` must include:

- baseline commit and candidate commit;
- exact modifications;
- test commands and results;
- schema, fixture, bundle, inventory, and tool-descriptor hashes;
- regression/adversarial/cost/safety outcomes;
- evidence class for each claim;
- decision (`accept`, `reject`, or `quarantine`);
- decision rationale;
- reproduction commands;
- rollback trigger.

If live Codex and Muse runs have not passed, accept only the deterministic CLI/contract capability and mark model-specific compatibility as quarantined, not complete.

- [ ] **Step 7: Commit the acceptance record only if repository policy tracks generated evidence**

```powershell
git add -- artifacts/agent-evals/implementation-v1/acceptance.json
git commit -m "test: record agent contract acceptance evidence"
```

If generated evidence is intentionally ignored, retain it as a local artifact and record its SHA-256 in the release notes instead.

## Definition of done

The first increment is done only when all of the following are true:

- human-mode regression tests remain green;
- exact file and opt-in auto-truncation baseline behavior is preserved;
- `--agent` flag and JSON request routes share one execution function;
- root, scan, file, source-byte, bundle-byte, and deadline limits fail closed;
- requested omissions, binary data, decoding loss, and write failures return nonzero structured errors;
- success/failure stdout is exactly one JSON document;
- bundle and inventory are deterministic for an identical request/root state;
- source and rendered hashes plus transformation provenance are accurate;
- both artifacts are written through the recoverable transaction path;
- the portable schema produces tested Codex Responses and Hermes wrappers;
- the frozen evaluator and ledger enforce the predeclared denominator and acceptance gates;
- documentation does not claim live Codex or Muse Glimmer compatibility beyond the evidence actually collected;
- MCP, service lifecycle, provider SDKs, binary transport, and unvalidated minification remain out of scope.
