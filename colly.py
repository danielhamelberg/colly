import os
import re
import time
import argparse
import glob
import sys
import fnmatch
import logging
import subprocess
import csv
import io
import codecs
import hashlib
import tempfile
import traceback
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import json
import ast
from collections import defaultdict, deque

try:
    import chardet
except ImportError:
    chardet = None
    logging.warning("chardet module not found. Encoding detection will be limited.")

CAN_UNPARSE = sys.version_info >= (3, 9)

# Set default encoding for stdout and stderr
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format='%(levelname)s: %(message)s'
)

# Mapping of file extensions to markdown languages
language_identifier = {
    ".ps1": "powershell", ".txt": "plaintext", ".py": "python", ".json": "json",
    ".jsonl": "json", ".csv": "csv",
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript", ".cjs": "javascript",
    ".html": "html", ".css": "css", ".scss": "scss", ".less": "less",
    ".xml": "xml", ".yml": "yaml", ".yaml": "yaml", ".md": "markdown",
    ".markdown": "markdown", ".mdx": "mdx", ".sh": "shell", ".bash": "shell",
    ".zsh": "shell", ".bat": "batch", ".cmd": "batch", ".c": "c",
    ".cpp": "cpp", ".h": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".go": "go",
    ".rs": "rust", ".swift": "swift", ".rb": "ruby", ".php": "php",
    ".r": "r", ".jl": "julia", ".pl": "perl", ".pm": "perl",
    ".lua": "lua", ".sql": "sql", ".ini": "ini", ".toml": "toml",
    ".cfg": "ini", ".conf": "ini", ".dockerfile": "dockerfile", ".makefile": "makefile",
    ".mk": "makefile", ".cmake": "cmake", ".asm": "asm", ".s": "asm",
    ".v": "verilog", ".sv": "systemverilog", ".vhdl": "vhdl", ".hdl": "vhdl",
    ".tex": "latex", ".bib": "bibtex", ".rmd": "rmarkdown", ".ipynb": "json",
    ".bicep": "bicep", ".azcli": "azurecli", ".psd1": "powershell",
    ".tf": "terraform", ".tfvars": "terraform", ".hcl": "hcl",
    ".rs": "rust", ".dart": "dart", ".scala": "scala", ".groovy": "groovy",
    ".clj": "clojure", ".cljs": "clojure", ".el": "emacs-lisp", ".hs": "haskell",
    ".lisp": "lisp", ".cl": "common-lisp", ".scm": "scheme", ".rkt": "racket",
    "Dockerfile": "dockerfile", "Makefile": "makefile", "CMakeLists.txt": "cmake",
}

# Default exclusions
default_exclusions = [
    "node_modules", "*.zip", "*.pkl", ".git", ".vscode", ".venv", "venv", "env",
    "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache", ".tox", ".coverage",
    ".cache", ".vs", ".idea", ".history", ".next", ".gradle", ".ipynb_checkpoints",
    "build", "dist", "bin", "obj", "packages", "lib", "include", "target", "out",
    "backup", "temp", "tmp", "logs", "test", "downloads", "releases", ".exe", "*.dll",
    "*.so", "*.dylib", "*.whl", "*.egg", "*.egg-info", "*.lock", "*.log", "*.bak",
    "*.tmp", "*.swp", "*.swo", "*.swn", "*.swo", "*.swn", "*.swo", "*.swn", "*.swo",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".bak", ".old",
]

AUTO_TRUNCATE_THRESHOLD_BYTES = 5 * 1024
AUTO_TRUNCATE_TARGET_BYTES = AUTO_TRUNCATE_THRESHOLD_BYTES + (AUTO_TRUNCATE_THRESHOLD_BYTES // 4)
VALUE_ONLY_STRUCTURED_EXTENSIONS = {".json", ".jsonl", ".csv", ".yaml", ".yml"}
SCRIPT_ANALYZABLE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
SCRIPT_MODULE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
DEFAULT_IGNORE_FILENAME = ".collyignore"
AGENT_SCHEMA_VERSION = 1
AGENT_DEFAULT_MAX_FILES = 256
AGENT_DEFAULT_MAX_SOURCE_BYTES = 16 * 1024 * 1024
AGENT_DEFAULT_MAX_BUNDLE_BYTES = 384 * 1024
AGENT_DEFAULT_MAX_SCAN_ENTRIES = 10_000
AGENT_DEFAULT_DEADLINE_SECONDS = 60
AGENT_MAX_REQUEST_BYTES = 64 * 1024
SCRIPT_CALL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "typeof", "new",
    "delete", "void", "await", "yield", "import", "export", "function", "class",
}
GRAPH_ANNOTATION_MARKER = "@colly-graph"
GRAPH_ANNOTATION_TARGET_KINDS = {"node", "definition", "edge"}
GRAPH_ANNOTATION_BUNDLE_SYNTAX = "graph-annotation-bundle"
MARKDOWN_GRAPH_ANNOTATION_EXTENSIONS = {".md", ".markdown", ".mdx", ".rmd"}
MARKDOWN_GRAPH_ANNOTATION_BUNDLE_SYNTAX = "markdown-colly-graph"
MARKDOWN_GRAPH_FENCE_INFOS = {"colly-graph", "colly-graph-json", "graph-annotations"}


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
class AgentExclusionPolicy:
    default_patterns: Tuple[str, ...]
    user_patterns: Tuple[str, ...]


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


AGENT_REQUEST_FIELDS = {
    "schemaVersion",
    "root",
    "files",
    "output",
    "inventory",
    "replace",
    "dependencyGraph",
    "braPreset",
    "autoTruncate",
    "encoding",
    "maxFiles",
    "maxSourceBytes",
    "maxBundleBytes",
    "maxScanEntries",
    "deadlineSeconds",
}
AGENT_BRA_PRESETS = {"none", "focused", "review", "architecture"}


def _require_agent_string(document: Dict[str, Any], name: str) -> str:
    value = document[name]
    if type(value) is not str or not value.strip():
        raise AgentFailure("invalid_request", 2, f"{name} must be a non-empty string")
    return value


def _require_agent_bool(document: Dict[str, Any], name: str) -> bool:
    value = document[name]
    if type(value) is not bool:
        raise AgentFailure("invalid_request", 2, f"{name} must be a boolean")
    return value


def _require_agent_positive_int(document: Dict[str, Any], name: str) -> int:
    value = document[name]
    if type(value) is not int or value <= 0:
        raise AgentFailure("invalid_request", 2, f"{name} must be a positive integer")
    return value


def parse_agent_request_document(document: Any) -> AgentRequest:
    if type(document) is not dict:
        raise AgentFailure("invalid_request", 2, "request must be a JSON object")
    unknown = sorted(set(document) - AGENT_REQUEST_FIELDS)
    missing = sorted(AGENT_REQUEST_FIELDS - set(document))
    if unknown:
        raise AgentFailure("invalid_request", 2, f"unknown field: {unknown[0]}")
    if missing:
        raise AgentFailure("invalid_request", 2, f"missing field: {missing[0]}")
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != AGENT_SCHEMA_VERSION:
        raise AgentFailure(
            "invalid_request", 2, f"schemaVersion must be {AGENT_SCHEMA_VERSION}"
        )
    files_value = document["files"]
    if type(files_value) is not list or not files_value:
        raise AgentFailure("invalid_request", 2, "files must be a non-empty array")
    if any(type(item) is not str or not item.strip() for item in files_value):
        raise AgentFailure(
            "invalid_request", 2, "files must contain non-empty strings"
        )
    bra_preset = _require_agent_string(document, "braPreset")
    if bra_preset not in AGENT_BRA_PRESETS:
        allowed = ", ".join(sorted(AGENT_BRA_PRESETS))
        raise AgentFailure(
            "invalid_request", 2, f"braPreset must be one of: {allowed}"
        )
    encoding = _require_agent_string(document, "encoding")
    try:
        codecs.lookup(encoding)
    except LookupError as exc:
        raise AgentFailure("invalid_request", 2, f"unknown encoding: {encoding}") from exc
    root = _require_agent_string(document, "root")
    output = _require_agent_string(document, "output")
    inventory = _require_agent_string(document, "inventory")
    if os.path.normcase(os.path.normpath(output)) == os.path.normcase(os.path.normpath(inventory)):
        raise AgentFailure(
            "invalid_request", 2, "output and inventory must be different paths"
        )
    return AgentRequest(
        schema_version=AGENT_SCHEMA_VERSION,
        root=root,
        files=tuple(files_value),
        output=output,
        inventory=inventory,
        replace=_require_agent_bool(document, "replace"),
        dependency_graph=_require_agent_bool(document, "dependencyGraph"),
        bra_preset=bra_preset,
        auto_truncate=_require_agent_bool(document, "autoTruncate"),
        encoding=encoding,
        max_files=_require_agent_positive_int(document, "maxFiles"),
        max_source_bytes=_require_agent_positive_int(document, "maxSourceBytes"),
        max_bundle_bytes=_require_agent_positive_int(document, "maxBundleBytes"),
        max_scan_entries=_require_agent_positive_int(document, "maxScanEntries"),
        deadline_seconds=_require_agent_positive_int(document, "deadlineSeconds"),
    )


def agent_request_to_document(request: AgentRequest) -> Dict[str, Any]:
    return {
        "schemaVersion": request.schema_version,
        "root": request.root,
        "files": list(request.files),
        "output": request.output,
        "inventory": request.inventory,
        "replace": request.replace,
        "dependencyGraph": request.dependency_graph,
        "braPreset": request.bra_preset,
        "autoTruncate": request.auto_truncate,
        "encoding": request.encoding,
        "maxFiles": request.max_files,
        "maxSourceBytes": request.max_source_bytes,
        "maxBundleBytes": request.max_bundle_bytes,
        "maxScanEntries": request.max_scan_entries,
        "deadlineSeconds": request.deadline_seconds,
    }

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


class ProgressReporter:
    def __init__(self, enabled: bool = False, stream=None, min_interval: float = 0.2):
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.min_interval = min_interval
        self._last_emit = 0.0
        self._last_line_length = 0
        self._active = False
        self._supports_inline = bool(getattr(self.stream, "isatty", lambda: False)())

    def update(self, stage: str, current: Optional[int] = None, total: Optional[int] = None, force: bool = False):
        if not self.enabled:
            return

        now = time.time()
        if not force and (now - self._last_emit) < self.min_interval:
            return

        parts = [stage]
        if current is not None and total is not None and total > 0:
            percent = (current / total) * 100
            parts.append(f"{current}/{total} ({percent:.1f}%)")
        elif current is not None:
            parts.append(str(current))

        line = " | ".join(parts)
        if self._supports_inline:
            padded = line.ljust(self._last_line_length)
            self.stream.write("\r" + padded)
            self.stream.flush()
            self._last_line_length = max(self._last_line_length, len(line))
        else:
            self.stream.write(line + "\n")
            self.stream.flush()
            self._last_line_length = len(line)

        self._last_emit = now
        self._active = True

    def finish(self, message: Optional[str] = None):
        if not self.enabled or not self._active:
            return

        if self._supports_inline:
            if message:
                padded = message.ljust(self._last_line_length)
                self.stream.write("\r" + padded + "\n")
            else:
                self.stream.write("\r" + (" " * self._last_line_length) + "\r")
            self.stream.flush()
        elif message:
            self.stream.write(message + "\n")
            self.stream.flush()

        self._active = False
        self._last_line_length = 0


def should_enable_progress(args) -> bool:
    return not args.no_progress and bool(getattr(sys.stderr, "isatty", lambda: False)())


@dataclass(frozen=True)
class BRAExpansionPolicy:
    preset: str
    inward: int
    outward: int
    edge_types: tuple[str, ...]
    budget_files: int
    budget_bytes: Optional[int]
    explain: bool = False


@dataclass
class BRAExpansionManifest:
    seeds: List[str]
    included: List[dict] = field(default_factory=list)
    pruned: List[dict] = field(default_factory=list)


def parse_bra_policy(args) -> Optional[BRAExpansionPolicy]:
    has_new_bra_contract = any(
        value is not None for value in (
            args.bra,
            args.bra_in,
            args.bra_out,
            args.bra_edge,
            args.bra_budget_files,
            args.bra_budget_bytes,
        )
    ) or args.bra_explain

    if not has_new_bra_contract:
        return None

    bra_values = args.bra or []
    if len(bra_values) > 1:
        raise ValueError("Use at most one --bra preset or legacy alias. Override depths with --bra-in/--bra-out.")

    raw = bra_values[0].lower() if bra_values else "focused"
    if raw in LEGACY_BRA_ALIASES:
        preset_name, inward, outward = LEGACY_BRA_ALIASES[raw]
    elif raw in BRA_PRESETS:
        preset_name = raw
        preset = BRA_PRESETS[preset_name]
        inward = preset["inward"]
        outward = preset["outward"]
    else:
        raise ValueError(f"Invalid --bra value: {raw}")

    preset = BRA_PRESETS[preset_name]
    inward = args.bra_in if args.bra_in is not None else inward
    outward = args.bra_out if args.bra_out is not None else outward
    edge_types = tuple(args.bra_edge or preset["edge_types"])
    budget_files = args.bra_budget_files if args.bra_budget_files is not None else preset["budget_files"]
    budget_bytes = args.bra_budget_bytes if args.bra_budget_bytes is not None else preset["budget_bytes"]

    return BRAExpansionPolicy(
        preset=preset_name,
        inward=max(0, inward),
        outward=max(0, outward),
        edge_types=edge_types,
        budget_files=max(0, budget_files),
        budget_bytes=budget_bytes,
        explain=args.bra_explain,
    )


def build_bra_graph_index(all_supported_files: Set[str], project_root: str,
                          progress: Optional[ProgressReporter] = None) -> dict:
    forward = {}
    reverse = {}

    for edge_type in ("import", "call", "symbol"):
        typed_forward = build_forward_graph(all_supported_files, edge_type, project_root, progress=progress)
        typed_reverse = defaultdict(set)
        for src, deps in typed_forward.items():
            for dep in deps:
                typed_reverse[dep].add(src)
        forward[edge_type] = typed_forward
        reverse[edge_type] = typed_reverse

    return {"forward": forward, "reverse": reverse}


def score_bra_candidate(edge_type: str, distance: int, path: str, hit_count: int) -> dict:
    return {
        "weight": EDGE_WEIGHTS[edge_type] + (hit_count * 5),
        "distance": distance,
        "path": path,
    }


def estimate_file_bytes(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def expand_bra_candidates(start_files: Set[str], adjacency: defaultdict, edge_type: str, max_depth: int) -> dict:
    frontier = deque((start, 0) for start in sorted(start_files))
    visited = set(start_files)
    candidates = {}

    while frontier:
        current, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for neighbor in sorted(adjacency[current]):
            next_depth = depth + 1
            entry = candidates.setdefault(
                neighbor,
                {"distance": next_depth, "edge_types": set(), "reasons": []}
            )
            entry["distance"] = min(entry["distance"], next_depth)
            entry["edge_types"].add(edge_type)
            entry["reasons"].append({"from": current, "type": edge_type})
            if neighbor in visited:
                continue
            visited.add(neighbor)
            frontier.append((neighbor, next_depth))

    return candidates


def select_bra_files(candidates: dict, input_files: Set[str], budget_files: int,
                     budget_bytes: Optional[int], manifest: BRAExpansionManifest) -> Set[str]:
    selected = set()
    consumed_bytes = 0
    ranked = []

    for path, meta in candidates.items():
        if path in input_files:
            continue
        primary_edge_type = sorted(meta["edge_types"], key=lambda name: (-EDGE_WEIGHTS[name], name))[0]
        hit_count = len(meta["edge_types"])
        score = score_bra_candidate(primary_edge_type, meta["distance"], path, hit_count)
        ranked.append((path, meta, primary_edge_type, score))

    ranked.sort(key=lambda item: (-item[3]["weight"], item[3]["distance"], item[0]))

    for path, meta, primary_edge_type, score in ranked:
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
            "score": score,
            "distance": meta["distance"],
            "edge_types": sorted(meta["edge_types"]),
            "bytes": file_bytes,
            "reasons": meta["reasons"],
            "primary_edge_type": primary_edge_type,
        })

    return selected


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
        json.dumps(payload, indent=2, ensure_ascii=False),
        "```",
        "",
    ])

class Feature:
    def __init__(self, args, project_root, all_supported_files):
        self.args = args
        self.project_root = project_root
        self.all_supported_files = all_supported_files

    def render(self, files):
        raise NotImplementedError

class DependencyExpander(Feature):
    def __init__(self, args, project_root, all_supported_files):
        super().__init__(args, project_root, all_supported_files)
        self.progress = getattr(args, "progress_reporter", None)

    def expand(self, initial_files):
        policy = parse_bra_policy(self.args)
        if policy is not None:
            return self._expand_with_policy(initial_files, policy)
        return self._expand_with_legacy_args(initial_files), None

    def _expand_with_policy(self, initial_files, policy: BRAExpansionPolicy):
        if policy.inward == 0 and policy.outward == 0:
            manifest = BRAExpansionManifest(seeds=sorted(initial_files))
            return set(initial_files), manifest

        graph_index = build_bra_graph_index(self.all_supported_files, self.project_root, progress=self.progress)
        manifest = BRAExpansionManifest(seeds=sorted(initial_files))
        candidates = {}

        for edge_type in policy.edge_types:
            if policy.outward > 0:
                outward_candidates = expand_bra_candidates(
                    initial_files, graph_index["forward"][edge_type], edge_type, policy.outward
                )
                self._merge_candidates(candidates, outward_candidates)

            if policy.inward > 0:
                inward_candidates = expand_bra_candidates(
                    initial_files, graph_index["reverse"][edge_type], edge_type, policy.inward
                )
                self._merge_candidates(candidates, inward_candidates)

        selected = select_bra_files(
            candidates, initial_files, policy.budget_files, policy.budget_bytes, manifest
        )
        return set(initial_files).union(selected), manifest

    def _expand_with_legacy_args(self, initial_files):
        before_expansion = self.args.before
        after_expansion = self.args.after

        if before_expansion == 0 and after_expansion == 0:
            return set(initial_files)

        dep_types = self.args.dep_type
        merged_forward_graph = defaultdict(set)
        merged_reverse_graph = defaultdict(set)

        for dep_type in dep_types:
            is_reverse = dep_type.endswith(('_by', '_of'))
            base_type = dep_type.replace('_by', '').replace('_of', '')
            if base_type not in ['import', 'symbol', 'call']:
                base_type = 'import'

            graph = build_forward_graph(
                self.all_supported_files, base_type, self.project_root, progress=self.progress
            )
            if is_reverse:
                graph = self._swap_graph(graph)

            for src, deps in graph.items():
                merged_forward_graph[src].update(deps)
                for dep in deps:
                    merged_reverse_graph[dep].add(src)

        final_files = set(initial_files)
        if after_expansion > 0:
            additional_deps = self._collect_additional_files(initial_files, merged_forward_graph, after_expansion, initial_files)
            final_files.update(additional_deps)
        if before_expansion > 0:
            additional_dependents = self._collect_additional_files(initial_files, merged_reverse_graph, before_expansion, initial_files)
            final_files.update(additional_dependents)
        return final_files

    def _merge_candidates(self, target, updates):
        for path, meta in updates.items():
            entry = target.setdefault(path, {"distance": meta["distance"], "edge_types": set(), "reasons": []})
            entry["distance"] = min(entry["distance"], meta["distance"])
            entry["edge_types"].update(meta["edge_types"])
            entry["reasons"].extend(meta["reasons"])

    def _swap_graph(self, graph):
        swapped = defaultdict(set)
        for src, deps in graph.items():
            for dep in deps:
                swapped[dep].add(src)
        return swapped

    def _collect_additional_files(self, start_files, g, max_num, input_files):
        return collect_additional_files(start_files, g, max_num, input_files)

class DirectoryLister(Feature):
    def render(self, files):
        stats = self._get_directory_stats(files)
        return self._render_directory_stats(stats)

    def _get_directory_stats(self, input_files):
        class DirStatsDict(defaultdict):
            def __missing__(self, key):
                self[key] = {"files": [], "subdirs": set()}
                return self[key]
        dir_stats = DirStatsDict()
        project_root_abs = os.path.abspath(self.project_root)
        for file_path in sorted(input_files):
            abs_file_path = os.path.abspath(file_path)
            try:
                rel_file_path = os.path.relpath(abs_file_path, project_root_abs)
            except ValueError:
                rel_file_path = os.path.basename(abs_file_path)
            
            dir_path = os.path.dirname(rel_file_path)
            file_name = os.path.basename(rel_file_path)
            dir_stats[dir_path]["files"].append(file_name)
            
            current_dir = dir_path
            while current_dir and current_dir != ".":
                parent_dir = os.path.dirname(current_dir)
                if parent_dir == current_dir:
                    break
                dir_stats[parent_dir]["subdirs"].add(os.path.basename(current_dir))
                current_dir = parent_dir

        for dir_path in dir_stats:
            dir_stats[dir_path]["subdirs"] = sorted(dir_stats[dir_path]["subdirs"])
            dir_stats[dir_path]["files"] = sorted(dir_stats[dir_path]["files"])

        return {
            "directory_structure": {
                dir_path: {
                    "files": stats["files"],
                    "subdirectories": stats["subdirs"]
                } for dir_path, stats in sorted(dir_stats.items())
            },
            "total_files": len(input_files),
            "total_directories": len(dir_stats)
        }

    def _render_directory_stats(self, directory_stats):
        output = ["# AI-Optimized Directory Listing", ""]
        output.append("This directory listing is designed for AI interpretability and efficient AI-AI-AI communication.")
        output.append("It provides a structured overview of directories containing the specified files.")
        output.append("")
        output.append("## Directory Structure")
        output.append("```json")
        output.append(json.dumps(directory_stats["directory_structure"], indent=2, ensure_ascii=False))
        output.append("```")
        output.append("")
        output.append("## Summary")
        output.append(f"- Total Files: {directory_stats['total_files']}")
        output.append(f"- Total Directories: {directory_stats['total_directories']}")
        output.append("")

        return "\n".join(output)

class StatsGenerator(Feature):
    def render(self, files):
        # This feature needs to process files to generate stats.
        # We call a simplified version of process_files that only gathers stats.
        stats = Stats()
        forced_files = get_forced_includes(self.args.files)
        for file_path in files:
            if not os.path.exists(file_path):
                continue
            if file_path not in forced_files and is_excluded(file_path, compile_exclusion_patterns(default_exclusions + self.args.exclude)):
                continue
            try:
                encoding = detect_encoding(file_path, self.args.encoding)
                with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
                stats.add_file(file_path, len(content.splitlines()), len(content))
            except Exception:
                continue
        
        output = ["# Stats Summary", "```json", stats.render(), "```", ""]
        return "\n".join(output)


class EntanglementMap(Feature):
    def render(self, files):
        dep_graph = build_forward_graph(files, 'import', self.project_root, progress=getattr(self.args, "progress_reporter", None))
        return self._render_entanglement_map(files, dep_graph)

    def _render_entanglement_map(self, files, dep_graph):
        max_files = 30
        file_list = sorted(list(files))[:max_files]
        idx_map = {f: i for i, f in enumerate(file_list)}
        matrix = [[0] * len(file_list) for _ in range(len(file_list))]
        for src, deps in dep_graph.items():
            if src in idx_map:
                for dep in deps:
                    if dep in idx_map:
                        matrix[idx_map[src]][idx_map[dep]] = 1
        header = '|   |' + '|'.join([str(i + 1) for i in range(len(file_list))]) + '|'
        sep = '|---|' + '|'.join(['---'] * len(file_list)) + '|'
        rows = []
        for i, row in enumerate(matrix):
            rows.append('|' + str(i + 1).rjust(3) + '|' + ''.join([' X ' if v else '   ' for v in row]) + '|')
        legend = '\n'.join([f'{i + 1}: {os.path.basename(f)}' for i, f in enumerate(file_list)])
        return '\n'.join([
            '# Low-Resolution Entanglement Map', '',
            'Shows which files depend on which others (X = dependency).', '',
            header, sep, *rows, '', 'Legend:', legend, ''
        ])

class AINativeDependencyGraph(Feature):
    def render(self, files):
        """
        Analyzes the files and renders a detailed, AI-native dependency graph.
        """
        graph_data = self.build_graph_data(files)
        if not graph_data:
            return ""
        return render_ai_native_dependency_graph_markdown(graph_data)

    def build_graph_data(self, files):
        """Build the machine-readable AI-native dependency graph object."""
        graph, definitions, annotations, graph_annotation_entries = self._analyze_files(files)
        has_annotations = any(file_annotations for file_annotations in annotations.values())
        if not graph and not definitions and not has_annotations and not graph_annotation_entries:
            return None
        graph_data = self._build_ai_native_dependency_graph_data(files, graph, definitions, annotations)
        return apply_graph_annotation_entries(graph_data, graph_annotation_entries)

    def _analyze_files(self, files):
        """
        Performs a two-pass analysis on the provided files to build a graph of
        dependencies and a catalog of definitions.
        """
        graph = defaultdict(lambda: defaultdict(list))
        definitions = defaultdict(list)
        annotations = defaultdict(list)
        graph_annotation_entries = []
        symbol_locations = defaultdict(lambda: defaultdict(list))
        file_list = sorted(files)
        total = len(file_list)
        progress = getattr(self.args, "progress_reporter", None)

        # Pass 1: Find all definitions and their locations
        for index, file_path in enumerate(file_list, start=1):
            if progress:
                progress.update("Dependency graph pass 1/2", index, total)
            try:
                extension = os.path.splitext(file_path)[1].lower()
                with open(file_path, 'r', encoding=self.args.encoding, errors='replace') as f:
                    content = f.read()

                if extension in MARKDOWN_GRAPH_ANNOTATION_EXTENSIONS:
                    try:
                        source_name = os.path.relpath(file_path, self.project_root)
                    except ValueError:
                        source_name = os.path.basename(file_path)
                    file_annotations = extract_markdown_graph_annotation_directives(content)
                    graph_annotation_entries.extend(
                        extract_markdown_graph_annotation_entries(content, source_name)
                    )
                else:
                    file_annotations = extract_graph_annotation_directives(content)
                if file_annotations:
                    annotations[file_path].extend(file_annotations)

                if extension == '.py':
                    tree = ast.parse(content, filename=file_path)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            signature = ""
                            if CAN_UNPARSE:
                                try:
                                    signature_parts = []
                                    for arg in node.args.args:
                                        part = arg.arg
                                        if arg.annotation:
                                            part += f": {ast.unparse(arg.annotation)}"
                                        signature_parts.append(part)
                                    signature = f"({', '.join(signature_parts)})"
                                    if node.returns:
                                        signature += f" -> {ast.unparse(node.returns)}"
                                except Exception:
                                    signature = "(...)" # Fallback for complex annotations

                            definitions[file_path].append({'name': node.name, 'type': 'function', 'signature': signature})
                            symbol_locations[node.name]['call'].append(file_path)

                        elif isinstance(node, ast.ClassDef):
                            definitions[file_path].append({'name': node.name, 'type': 'class'})
                            symbol_locations[node.name]['symbol'].append(file_path)

                        elif isinstance(node, ast.Assign):
                            for target in node.targets:
                                if isinstance(target, ast.Name):
                                    definitions[file_path].append({'name': target.id, 'type': 'variable'})
                                    symbol_locations[target.id]['symbol'].append(file_path)
                elif extension in SCRIPT_ANALYZABLE_EXTENSIONS:
                    analyze_script_definitions(file_path, content, definitions, symbol_locations)
            except Exception as e:
                logging.debug(f"Failed to parse definitions in {file_path}: {e}")

        # Pass 2: Find usages and build graph
        for index, file_path in enumerate(file_list, start=1):
            if progress:
                progress.update("Dependency graph pass 2/2", index, total)
            try:
                extension = os.path.splitext(file_path)[1].lower()
                with open(file_path, 'r', encoding=self.args.encoding, errors='replace') as f:
                    content = f.read()

                if extension == '.py':
                    tree = ast.parse(content, filename=file_path)

                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                dep = resolve_import(file_path, alias.name, 0, self.project_root)
                                graph[file_path]['imports'].append({'name': alias.name, 'target': dep if dep and dep in files else None})

                        elif isinstance(node, ast.ImportFrom):
                            module_name = node.module or ''
                            dep = resolve_import(file_path, module_name, node.level, self.project_root)
                            for alias in node.names:
                                full_name = f"{module_name}.{alias.name}" if module_name else alias.name
                                graph[file_path]['imports'].append({'name': full_name, 'target': dep if dep and dep in files else None})

                        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                            func_name = node.func.id
                            if func_name in symbol_locations and 'call' in symbol_locations[func_name]:
                                for def_file in symbol_locations[func_name]['call']:
                                    if def_file != file_path:
                                        graph[file_path]['calls'].append({'name': func_name, 'target': def_file})

                        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                            symbol_name = node.id
                            if symbol_name in symbol_locations and 'symbol' in symbol_locations[symbol_name]:
                                for def_file in symbol_locations[symbol_name]['symbol']:
                                    if def_file != file_path:
                                        graph[file_path]['symbols'].append({'name': symbol_name, 'target': def_file})
                elif extension in SCRIPT_ANALYZABLE_EXTENSIONS:
                    analyze_script_usages(file_path, content, files, graph, symbol_locations, self.project_root)
            except Exception as e:
                logging.debug(f"Failed to parse usages in {file_path}: {e}")
        
        return graph, definitions, annotations, graph_annotation_entries

    def _build_ai_native_dependency_graph_data(self, files, graph, definitions, annotations):
        """
        Build the analyzed graph and definitions into a compact JSON object.
        """
        file_list = sorted(list(files))
        if not file_list:
            return None

        node_ids = {file_path: idx for idx, file_path in enumerate(file_list)}
        relative_paths = {}
        
        # Build nodes with definitions
        nodes = []
        for file_path in file_list:
            try:
                rel_path = os.path.relpath(file_path, self.project_root)
            except ValueError:
                rel_path = os.path.basename(file_path)
            relative_paths[file_path] = rel_path
            
            node_obj = {"path": rel_path}
            file_annotations = annotations.get(file_path, [])
            node_annotations = select_node_annotations(file_annotations, rel_path)
            if node_annotations:
                node_obj["annotations"] = node_annotations

            if file_path in definitions and definitions[file_path]:
                sorted_defs = []
                for definition in sorted(
                    definitions[file_path],
                    key=lambda x: (x['name'], x.get('type', ''), x.get('signature', ''))
                ):
                    definition_obj = dict(definition)
                    definition_annotations = select_definition_annotations(file_annotations, definition_obj)
                    if definition_annotations:
                        definition_obj["annotations"] = definition_annotations
                    sorted_defs.append(definition_obj)
                node_obj["definitions"] = sorted_defs
            nodes.append(node_obj)

        # Build edges
        edges = []
        for src_path, typed_deps in graph.items():
            if src_path not in node_ids:
                continue
            src_id = node_ids[src_path]
            source_annotations = annotations.get(src_path, [])
            
            for dep_type, dep_list in typed_deps.items():
                # Use a tuple of items to make the dict hashable for deduplication
                unique_deps = {tuple(sorted(d.items())) for d in dep_list}
                for dep_tuple in unique_deps:
                    dep_info = dict(dep_tuple)
                    target_path = dep_info.get('target')
                    target_id = node_ids.get(target_path) if target_path else None
                    
                    if src_id == target_id:
                        continue

                    edge_obj = {
                        "source": src_id,
                        "target": target_id,
                        "type": dep_type,
                        "name": dep_info.get('name')
                    }
                    target_rel_path = relative_paths.get(target_path) if target_path else None
                    edge_annotations = select_edge_annotations(source_annotations, edge_obj, target_rel_path)
                    if edge_annotations:
                        edge_obj["annotations"] = edge_annotations
                    edges.append(edge_obj)

        # Sort edges for deterministic output
        edges.sort(key=lambda x: (x['source'], x['target'] if x['target'] is not None else -1, x['type'], x['name']))

        return {"nodes": nodes, "edges": edges}

def compile_exclusion_patterns(exclusions: List[str]) -> List[str]:
    """Normalize exclusion patterns for segment-aware matching."""
    normalized = []
    for pattern in exclusions:
        cleaned = pattern.strip()
        cleaned = re.sub(r'^(\.\\|\./)', '', cleaned)
        cleaned = cleaned.replace('\\', '/')
        cleaned = cleaned.rstrip('/')
        if cleaned:
            normalized.append(cleaned)
    return normalized

def get_ignore_file_paths(cwd: str, explicit_ignore_files: List[str]) -> List[str]:
    """Collect ignore files, auto-loading the repo-local default when present."""
    paths = []
    default_path = os.path.join(cwd, DEFAULT_IGNORE_FILENAME)
    if os.path.isfile(default_path):
        paths.append(default_path)

    for ignore_file in explicit_ignore_files:
        candidate = os.path.abspath(ignore_file)
        if not os.path.isfile(candidate):
            raise FileNotFoundError(candidate)
        if candidate not in paths:
            paths.append(candidate)

    return paths

def load_ignore_patterns(ignore_file_paths: List[str]) -> List[str]:
    """Load ignore patterns from files, skipping comments and blank lines."""
    patterns = []
    for ignore_file_path in ignore_file_paths:
        with open(ignore_file_path, 'r', encoding='utf-8', errors='replace') as handle:
            for raw_line in handle:
                line = raw_line.lstrip('\ufeff').strip()
                if not line or line.startswith('#'):
                    continue
                patterns.append(line)
    return patterns

def is_excluded(path: str, exclusion_patterns: List[str]) -> bool:
    """Check if the path matches any exclusion pattern using segment-aware matching."""
    normalized_path = os.path.normpath(path).replace('\\', '/').rstrip('/')
    path_segments = [segment for segment in normalized_path.split('/') if segment]

    for pattern in exclusion_patterns:
        normalized_pattern = pattern.replace('\\', '/').rstrip('/')
        if not normalized_pattern:
            continue

        if '/' in normalized_pattern:
            if fnmatch.fnmatch(normalized_path, normalized_pattern):
                return True
            if fnmatch.fnmatch(normalized_path, f'*/{normalized_pattern}'):
                return True
            if fnmatch.fnmatch(normalized_path, f'{normalized_pattern}/*'):
                return True
            if fnmatch.fnmatch(normalized_path, f'*/{normalized_pattern}/*'):
                return True
            continue

        if any(fnmatch.fnmatch(segment, normalized_pattern) for segment in path_segments):
            return True

    return False

def get_forced_includes(args_patterns: List[str]) -> Set[str]:
    """Allow direct file arguments to bypass ignore rules."""
    forced_files = set()
    for pattern in args_patterns:
        if glob.has_magic(pattern):
            continue
        candidate = os.path.abspath(pattern)
        if os.path.isfile(candidate):
            forced_files.add(candidate)
    return forced_files


def normalized_relative_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise AgentFailure(
            "path_outside_root", 3, "path resolves outside root", str(path)
        ) from exc
    return relative.as_posix()


def _path_has_symlink(root: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            return True
    return False


def resolve_agent_path(root: Path, value: str, *, must_exist: bool) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    if _path_has_symlink(root, candidate):
        raise AgentFailure("invalid_request", 2, "symlinks are not allowed in agent mode", value)
    try:
        resolved = candidate.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise AgentFailure(
            "requested_file_missing", 3, "requested path does not exist", value
        ) from exc
    normalized_relative_path(root, resolved)
    return resolved


def check_agent_deadline(deadline_at: float, failed_path: str = "") -> None:
    if time.monotonic() > deadline_at:
        raise AgentFailure(
            "limit_exceeded", 5, "agent deadline exceeded", failed_path
        )


def _normalize_agent_selector(root: Path, selector: str) -> str:
    normalized = selector.replace("\\", "/")
    candidate = Path(normalized)
    if glob.has_magic(normalized) and candidate.is_absolute():
        raise AgentFailure(
            "path_outside_root", 3, "absolute glob selectors are not allowed", selector
        )
    lexical = candidate if candidate.is_absolute() else root / candidate
    if _path_has_symlink(root, lexical):
        raise AgentFailure(
            "invalid_request", 2, "symlinks are not allowed in agent mode", selector
        )
    try:
        resolved = lexical.resolve(strict=False)
    except OSError as exc:
        raise AgentFailure("invalid_request", 2, str(exc), selector) from exc
    relative = normalized_relative_path(root, resolved)
    return relative or "."


def normalize_agent_request(request: AgentRequest) -> AgentRequest:
    try:
        root = Path(request.root).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise AgentFailure(
            "root_not_found", 2, "root directory does not exist", request.root
        ) from exc
    if not root.is_dir():
        raise AgentFailure("root_not_found", 2, "root is not a directory", request.root)
    selectors = tuple(
        sorted(
            {_normalize_agent_selector(root, selector) for selector in request.files},
            key=lambda value: value.encode("utf-8"),
        )
    )
    output_path = resolve_agent_path(root, request.output, must_exist=False)
    inventory_path = resolve_agent_path(root, request.inventory, must_exist=False)
    output = normalized_relative_path(root, output_path)
    inventory = normalized_relative_path(root, inventory_path)
    if os.path.normcase(output) == os.path.normcase(inventory):
        raise AgentFailure(
            "invalid_request", 2, "output and inventory must be different paths"
        )
    return replace(
        request,
        root=root.as_posix(),
        files=selectors,
        output=output,
        inventory=inventory,
    )


def load_agent_exclusion_patterns(root: Path) -> AgentExclusionPolicy:
    user_patterns: List[str] = []
    ignore_path = resolve_agent_path(root, DEFAULT_IGNORE_FILENAME, must_exist=False)
    if ignore_path.is_file():
        try:
            ignore_text = ignore_path.read_text(encoding="utf-8-sig", errors="strict")
        except UnicodeError as exc:
            raise AgentFailure(
                "decode_failed", 4, "ignore file is not valid UTF-8", DEFAULT_IGNORE_FILENAME
            ) from exc
        for raw_line in ignore_text.splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#"):
                user_patterns.append(line)
    return AgentExclusionPolicy(
        default_patterns=tuple(compile_exclusion_patterns(default_exclusions)),
        user_patterns=tuple(compile_exclusion_patterns(user_patterns)),
    )


def _agent_exclusion_reason(relative_path: str, policy: AgentExclusionPolicy) -> str:
    if is_excluded(relative_path, list(policy.default_patterns)):
        return "default-exclusion"
    if is_excluded(relative_path, list(policy.user_patterns)):
        return "user-exclusion"
    return ""


def _agent_supported_path(path: Path) -> bool:
    return path.suffix.lower() in {
        item for item in language_identifier if item.startswith(".")
    } or path.name in language_identifier


def select_files(
    request: AgentRequest,
    exclusion_patterns: AgentExclusionPolicy,
    deadline_at: float,
) -> SelectionResult:
    root = Path(request.root)
    output_path = (root / request.output).resolve(strict=False)
    inventory_path = (root / request.inventory).resolve(strict=False)
    selected: Dict[str, Tuple[Path, Set[str]]] = {}
    excluded: List[ExclusionRecord] = []
    scanned_entries = 0

    def inspect_entry(relative_path: str = "") -> None:
        nonlocal scanned_entries
        scanned_entries += 1
        check_agent_deadline(deadline_at, relative_path)
        if scanned_entries > request.max_scan_entries:
            raise AgentFailure(
                "limit_exceeded", 5, "maxScanEntries exceeded", relative_path
            )

    def add_file(path: Path, reason: str, *, explicit: bool, inspected: bool = False) -> bool:
        if not inspected:
            inspect_entry(normalized_relative_path(root, path))
        lexical = path if path.is_absolute() else root / path
        if _path_has_symlink(root, lexical):
            if explicit:
                resolved = lexical.resolve(strict=False)
                normalized_relative_path(root, resolved)
                raise AgentFailure(
                    "invalid_request",
                    2,
                    "symlinks are not allowed in agent mode",
                    normalized_relative_path(root, lexical),
                )
            excluded.append(
                ExclusionRecord(normalized_relative_path(root, lexical), "symlink", False)
            )
            return False
        try:
            resolved = lexical.resolve(strict=True)
        except FileNotFoundError as exc:
            raise AgentFailure(
                "requested_file_missing", 3, "selected file disappeared", str(path)
            ) from exc
        relative = normalized_relative_path(root, resolved)
        if resolved in (output_path, inventory_path):
            raise AgentFailure(
                "artifact_source_conflict",
                3,
                "artifact path cannot be selected as source",
                relative,
            )
        if not explicit and not _agent_supported_path(resolved):
            excluded.append(ExclusionRecord(relative, "unsupported-extension", False))
            return False
        exclusion_reason = "" if explicit else _agent_exclusion_reason(relative, exclusion_patterns)
        if exclusion_reason:
            excluded.append(ExclusionRecord(relative, exclusion_reason, False))
            return False
        key = os.path.normcase(relative) if os.name == "nt" else relative
        if key in selected and selected[key][0] != resolved:
            raise AgentFailure("path_collision", 3, "normalized path collision", relative)
        if key not in selected:
            selected[key] = (resolved, set())
        selected[key][1].add(reason)
        return True

    def bounded_entries(directory: Path) -> List[Path]:
        # Count during enumeration, including nonmatches, before materializing or sorting.
        check_agent_deadline(deadline_at, normalized_relative_path(root, directory))
        entries = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    candidate = Path(entry.path)
                    relative = normalized_relative_path(root, candidate)
                    inspect_entry(relative)
                    if _path_has_symlink(root, candidate):
                        excluded.append(ExclusionRecord(relative, "symlink", False))
                        continue
                    reason = _agent_exclusion_reason(relative, exclusion_patterns) if candidate.is_dir() else ""
                    if reason:
                        excluded.append(ExclusionRecord(relative, reason, False))
                        continue
                    entries.append(candidate)
        except OSError as exc:
            raise AgentFailure("requested_file_missing", 3, "directory is unreadable", str(directory)) from exc
        return sorted(entries, key=lambda path: path.name.encode("utf-8"))

    def scan_directory(directory: Path, reason: str) -> int:
        added = 0
        pending = [directory]
        while pending:
            for candidate in bounded_entries(pending.pop()):
                if candidate.is_dir():
                    pending.append(candidate)
                elif candidate.is_file():
                    added += add_file(candidate, reason, explicit=False, inspected=True)
        return added

    def scan_glob(selector: str) -> int:
        added = 0
        pending = [(root, tuple(Path(selector).parts))]
        while pending:
            directory, parts = pending.pop()
            check_agent_deadline(deadline_at, selector)
            if not parts or parts == ("**",):
                added += scan_directory(directory, "glob-match")
                continue
            component, remainder = parts[0], parts[1:]
            if component == "**":
                pending.append((directory, remainder))
            for candidate in bounded_entries(directory):
                # Preserve glob's implicit-dotfile rule.
                if candidate.name.startswith(".") and not component.startswith("."):
                    continue
                if component == "**":
                    if candidate.is_dir():
                        pending.append((candidate, parts))
                    continue
                if not fnmatch.fnmatch(candidate.name, component):
                    continue
                if remainder:
                    if candidate.is_dir():
                        pending.append((candidate, remainder))
                elif candidate.is_dir():
                    added += scan_directory(candidate, "glob-match")
                elif candidate.is_file():
                    added += add_file(candidate, "glob-match", explicit=False, inspected=True)
        return added

    for selector in request.files:
        check_agent_deadline(deadline_at, selector)
        matched = 0
        if glob.has_magic(selector):
            matched = scan_glob(selector)
        else:
            candidate = root / Path(selector)
            if candidate.is_symlink():
                resolved = candidate.resolve(strict=False)
                normalized_relative_path(root, resolved)
                raise AgentFailure(
                    "invalid_request", 2, "symlinks are not allowed in agent mode", selector
                )
            if not candidate.exists():
                raise AgentFailure(
                    "requested_file_missing", 3, "requested path does not exist", selector
                )
            if candidate.is_file():
                matched = add_file(candidate, "explicit-file", explicit=True)
            elif candidate.is_dir():
                matched = scan_directory(candidate, "directory-scan")
        if not matched:
            raise AgentFailure(
                "selector_no_match", 3, "selector produced no supported files", selector
            )

    records = tuple(
        sorted(
            (
                SelectionRecord(
                    absolute_path=str(path),
                    relative_path=normalized_relative_path(root, path),
                    selection_reasons=tuple(sorted(reasons)),
                )
                for path, reasons in selected.values()
            ),
            key=lambda item: item.relative_path.encode("utf-8"),
        )
    )
    if len(records) > request.max_files:
        raise AgentFailure("limit_exceeded", 5, "maxFiles exceeded")
    source_bytes = 0
    for record in records:
        check_agent_deadline(deadline_at, record.relative_path)
        try:
            source_bytes += Path(record.absolute_path).stat().st_size
        except OSError as exc:
            raise AgentFailure(
                "requested_file_missing", 3, "selected file is unreadable", record.relative_path
            ) from exc
        if source_bytes > request.max_source_bytes:
            raise AgentFailure(
                "limit_exceeded", 5, "maxSourceBytes exceeded", record.relative_path
            )
    exclusions = tuple(
        sorted(
            excluded,
            key=lambda item: (
                item.relative_path.encode("utf-8"),
                item.reason,
                item.requested,
            ),
        )
    )
    return SelectionResult(records, exclusions, scanned_entries)


class _AgentDeadlineProgress:
    def __init__(self, deadline_at: float):
        self.deadline_at = deadline_at

    def update(
        self,
        stage: str,
        current: Optional[int] = None,
        total: Optional[int] = None,
        force: bool = False,
    ) -> None:
        check_agent_deadline(self.deadline_at, stage)


def _agent_supported_index(
    request: AgentRequest,
    policy: AgentExclusionPolicy,
    deadline_at: float,
    starting_scan_count: int,
) -> Tuple[Set[str], int]:
    root = Path(request.root)
    supported: Set[str] = set()
    scanned = starting_scan_count
    for current_root, dirs, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        kept_dirs = []
        for directory_name in sorted(dirs, key=lambda value: value.encode("utf-8")):
            scanned += 1
            relative = normalized_relative_path(root, (current / directory_name).resolve(strict=False))
            check_agent_deadline(deadline_at, relative)
            if scanned > request.max_scan_entries:
                raise AgentFailure("limit_exceeded", 5, "maxScanEntries exceeded", relative)
            candidate = current / directory_name
            if _path_has_symlink(root, candidate) or _agent_exclusion_reason(relative, policy):
                continue
            kept_dirs.append(directory_name)
        dirs[:] = kept_dirs
        for file_name in sorted(files, key=lambda value: value.encode("utf-8")):
            scanned += 1
            candidate = current / file_name
            relative = normalized_relative_path(root, candidate.resolve(strict=False))
            check_agent_deadline(deadline_at, relative)
            if scanned > request.max_scan_entries:
                raise AgentFailure("limit_exceeded", 5, "maxScanEntries exceeded", relative)
            if _path_has_symlink(root, candidate):
                continue
            if not _agent_supported_path(candidate):
                continue
            if _agent_exclusion_reason(relative, policy):
                continue
            supported.add(str(candidate.resolve(strict=True)))
    return supported, scanned


def expand_agent_selection(
    request: AgentRequest,
    selection: SelectionResult,
    exclusion_patterns: AgentExclusionPolicy,
    deadline_at: float,
    progress: Optional[ProgressReporter] = None,
) -> SelectionResult:
    if request.bra_preset == "none":
        return selection
    all_supported, scanned = _agent_supported_index(
        request,
        exclusion_patterns,
        deadline_at,
        selection.scanned_entries,
    )
    args = SimpleNamespace(
        bra=[request.bra_preset],
        bra_in=None,
        bra_out=None,
        bra_edge=None,
        bra_budget_files=None,
        bra_budget_bytes=None,
        bra_explain=False,
        before=0,
        after=0,
        dep_type=["import"],
        encoding=request.encoding,
        progress_reporter=progress or _AgentDeadlineProgress(deadline_at),
    )
    initial_paths = {record.absolute_path for record in selection.files}
    expander = DependencyExpander(args, request.root, all_supported)
    try:
        expanded_paths, _manifest = expander.expand(initial_paths)
    except ValueError as exc:
        raise AgentFailure("invalid_request", 2, str(exc)) from exc
    root = Path(request.root)
    records_by_key: Dict[str, Tuple[Path, Set[str]]] = {}
    for record in selection.files:
        path = Path(record.absolute_path)
        key = os.path.normcase(record.relative_path) if os.name == "nt" else record.relative_path
        records_by_key[key] = (path, set(record.selection_reasons))
    for raw_path in expanded_paths:
        check_agent_deadline(deadline_at, str(raw_path))
        path = Path(raw_path).resolve(strict=False)
        relative = normalized_relative_path(root, path)
        key = os.path.normcase(relative) if os.name == "nt" else relative
        if key not in records_by_key:
            records_by_key[key] = (path, {"dependency-expansion"})
    records = tuple(
        sorted(
            (
                SelectionRecord(
                    absolute_path=str(path),
                    relative_path=normalized_relative_path(root, path),
                    selection_reasons=tuple(sorted(reasons)),
                )
                for path, reasons in records_by_key.values()
            ),
            key=lambda item: item.relative_path.encode("utf-8"),
        )
    )
    if len(records) > request.max_files:
        raise AgentFailure("limit_exceeded", 5, "maxFiles exceeded after expansion")
    total_bytes = 0
    for record in records:
        check_agent_deadline(deadline_at, record.relative_path)
        try:
            total_bytes += Path(record.absolute_path).stat().st_size
        except OSError as exc:
            raise AgentFailure(
                "requested_file_missing", 3, "expanded file is unavailable", record.relative_path
            ) from exc
        if total_bytes > request.max_source_bytes:
            raise AgentFailure(
                "limit_exceeded",
                5,
                "maxSourceBytes exceeded after expansion",
                record.relative_path,
            )
    return SelectionResult(records, selection.excluded, scanned)


ENCODING_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


def decode_agent_source(
    data: bytes, default_encoding: str, relative_path: str
) -> Tuple[str, str]:
    has_known_bom = any(data.startswith(marker) for marker, _ in ENCODING_BOMS)
    sample = data[:8192]
    disallowed_controls = sum(
        byte < 32 and byte not in (9, 10, 12, 13) for byte in sample
    )
    if not has_known_bom and sample and (
        0 in sample or disallowed_controls / len(sample) > 0.10
    ):
        raise AgentFailure(
            "binary_input", 4, "content appears binary", relative_path
        )
    encoding = next(
        (name for marker, name in ENCODING_BOMS if data.startswith(marker)),
        codecs.lookup(default_encoding).name,
    )
    try:
        text = data.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise AgentFailure("decode_failed", 4, str(exc), relative_path) from exc
    if text.startswith("\ufeff"):
        text = text[1:]
    if "\x00" in text:
        raise AgentFailure(
            "binary_input", 4, "decoded content contains NUL", relative_path
        )
    return text, encoding


def collect_files(
    request: AgentRequest,
    selection: SelectionResult,
    deadline_at: float,
) -> Tuple[CollectedFile, ...]:
    collected: List[CollectedFile] = []
    actual_source_bytes = 0
    for record in selection.files:
        check_agent_deadline(deadline_at, record.relative_path)
        path = Path(record.absolute_path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise AgentFailure(
                "requested_file_missing" if isinstance(exc, FileNotFoundError) else "decode_failed",
                3 if isinstance(exc, FileNotFoundError) else 4,
                "selected file is unavailable" if isinstance(exc, FileNotFoundError) else "selected file is unreadable",
                record.relative_path,
            ) from exc
        actual_source_bytes += len(data)
        if actual_source_bytes > request.max_source_bytes:
            raise AgentFailure(
                "limit_exceeded", 5, "maxSourceBytes exceeded", record.relative_path
            )
        source_sha256 = sha256_hex(data)
        text, encoding = decode_agent_source(data, request.encoding, record.relative_path)
        rendered_text = text
        transformations: List[TransformationRecord] = []
        extension = path.suffix.lower()
        if request.auto_truncate and get_content_size_bytes(text) > AUTO_TRUNCATE_THRESHOLD_BYTES:
            check_agent_deadline(deadline_at, record.relative_path)
            best_length, best_content, _safe = find_best_truncation_for_target_size(
                text, extension, AUTO_TRUNCATE_TARGET_BYTES
            )
            if best_content is not None and best_content != text:
                before_sha256 = sha256_hex(text.encode("utf-8"))
                rendered_text = best_content
                after_sha256 = sha256_hex(rendered_text.encode("utf-8"))
                transformations.append(
                    TransformationRecord(
                        kind="auto-truncate",
                        parameters={
                            "maxLength": best_length,
                            "targetSizeBytes": AUTO_TRUNCATE_TARGET_BYTES,
                        },
                        before_sha256=before_sha256,
                        after_sha256=after_sha256,
                    )
                )
        rendered_bytes = rendered_text.encode("utf-8")
        language = language_identifier.get(
            extension,
            language_identifier.get(path.name, "plaintext"),
        )
        collected.append(
            CollectedFile(
                absolute_path=str(path),
                relative_path=record.relative_path,
                selection_reasons=record.selection_reasons,
                source_size_bytes=len(data),
                source_sha256=source_sha256,
                encoding=encoding,
                language=language,
                rendered_text=rendered_text,
                rendered_size_bytes=len(rendered_bytes),
                rendered_sha256=sha256_hex(rendered_bytes),
                content_mode="transformed" if transformations else "verbatim",
                transformations=tuple(transformations),
            )
        )
        check_agent_deadline(deadline_at, record.relative_path)
    return tuple(collected)


def markdown_fence_for(text: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", text)),
        default=0,
    )
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


def render_agent_dependency_graph(
    request: AgentRequest,
    files: Sequence[CollectedFile],
    exclusion_patterns: AgentExclusionPolicy,
    deadline_at: float,
) -> bytes:
    if not request.dependency_graph:
        return b""
    check_agent_deadline(deadline_at)
    default_encoding = codecs.lookup(request.encoding).name
    graph_files: Set[str] = set()
    for file in files:
        if not _agent_supported_path(Path(file.absolute_path)):
            continue
        if file.encoding not in {default_encoding, "utf-8-sig"}:
            raise AgentFailure(
                "decode_failed",
                4,
                "dependency graph requires files decodable with the request encoding",
                file.relative_path,
            )
        graph_files.add(file.absolute_path)
    if not graph_files:
        return b""
    args = SimpleNamespace(
        encoding=request.encoding,
        progress_reporter=_AgentDeadlineProgress(deadline_at),
    )
    grapher = AINativeDependencyGraph(args, request.root, graph_files)
    graph_data = grapher.build_graph_data(graph_files)
    check_agent_deadline(deadline_at)
    if not graph_data:
        return b""
    return render_ai_native_dependency_graph_markdown(graph_data).encode("utf-8")


def render_bundle(
    request: AgentRequest,
    files: Sequence[CollectedFile],
    graph_bytes: bytes,
    deadline_at: float,
) -> bytes:
    parts: List[bytes] = []
    size = 0

    def append_part(part: bytes, failed_path: str = "") -> None:
        nonlocal size
        check_agent_deadline(deadline_at, failed_path)
        separator = b"" if not parts or parts[-1].endswith(b"\n\n") else b"\n"
        added = separator + part
        if added and not added.endswith(b"\n"):
            added += b"\n"
        size += len(added)
        if size > request.max_bundle_bytes:
            raise AgentFailure(
                "limit_exceeded", 5, "maxBundleBytes exceeded", failed_path
            )
        parts.append(added)

    if graph_bytes:
        append_part(graph_bytes)
    for file in sorted(files, key=lambda item: item.relative_path.encode("utf-8")):
        append_part(render_agent_file(file), file.relative_path)
    return b"".join(parts)


def _transformation_document(record: TransformationRecord) -> Dict[str, Any]:
    return {
        "kind": record.kind,
        "parameters": record.parameters,
        "beforeSha256": record.before_sha256,
        "afterSha256": record.after_sha256,
    }


def build_agent_inventory(
    request: AgentRequest,
    request_sha256: str,
    bundle_bytes: bytes,
    files: Sequence[CollectedFile],
    excluded: Sequence[ExclusionRecord],
) -> bytes:
    document = {
        "schemaVersion": AGENT_SCHEMA_VERSION,
        "root": request.root,
        "requestSha256": request_sha256,
        "bundle": {
            "path": request.output,
            "sha256": sha256_hex(bundle_bytes),
            "sizeBytes": len(bundle_bytes),
        },
        "limits": {
            "maxFiles": request.max_files,
            "maxSourceBytes": request.max_source_bytes,
            "maxBundleBytes": request.max_bundle_bytes,
            "maxScanEntries": request.max_scan_entries,
            "deadlineSeconds": request.deadline_seconds,
        },
        "files": [
            {
                "relativePath": file.relative_path,
                "sourceSizeBytes": file.source_size_bytes,
                "sourceSha256": file.source_sha256,
                "renderedSizeBytes": file.rendered_size_bytes,
                "renderedSha256": file.rendered_sha256,
                "encoding": file.encoding,
                "language": file.language,
                "selectionReasons": list(file.selection_reasons),
                "contentMode": file.content_mode,
                "transformations": [
                    _transformation_document(item) for item in file.transformations
                ],
            }
            for file in sorted(
                files, key=lambda item: item.relative_path.encode("utf-8")
            )
        ],
        "excluded": [
            {
                "relativePath": item.relative_path,
                "reason": item.reason,
                "requested": item.requested,
            }
            for item in sorted(
                excluded,
                key=lambda item: (
                    item.relative_path.encode("utf-8"),
                    item.reason,
                    item.requested,
                ),
            )
        ],
    }
    return canonical_json_bytes(document)


def _stage_agent_artifact(target: Path, data: bytes) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        delete=False,
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        staged = temporary.read_bytes()
        if staged != data or sha256_hex(staged) != sha256_hex(data):
            raise OSError("staged artifact verification failed")
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _reserve_backup_path(target: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".bak",
    )
    os.close(descriptor)
    backup = Path(raw_path)
    backup.unlink(missing_ok=True)
    return backup


def write_agent_artifacts(
    root: Path,
    output_value: str,
    inventory_value: str,
    bundle_bytes: bytes,
    inventory_bytes: bytes,
    replace_existing: bool,
) -> ArtifactSet:
    root = root.resolve(strict=True)
    output_path = resolve_agent_path(root, output_value, must_exist=False)
    inventory_path = resolve_agent_path(root, inventory_value, must_exist=False)
    if output_path == inventory_path:
        raise AgentFailure(
            "invalid_request", 2, "output and inventory must be different paths"
        )
    for target in (output_path, inventory_path):
        if target.exists() and not target.is_file():
            raise AgentFailure("invalid_request", 2, "artifact target must be a regular file", normalized_relative_path(root, target))
        if target.exists() and not replace_existing:
            raise AgentFailure(
                "artifact_exists", 6, "artifact already exists", normalized_relative_path(root, target)
            )

    staged_output: Optional[Path] = None
    staged_inventory: Optional[Path] = None
    output_backup: Optional[Path] = None
    inventory_backup: Optional[Path] = None
    output_committed = False
    inventory_committed = False
    preserve_backups = False
    try:
        staged_output = _stage_agent_artifact(output_path, bundle_bytes)
        staged_inventory = _stage_agent_artifact(inventory_path, inventory_bytes)
        if replace_existing and output_path.exists():
            output_backup = _reserve_backup_path(output_path)
            os.replace(output_path, output_backup)
        if replace_existing and inventory_path.exists():
            inventory_backup = _reserve_backup_path(inventory_path)
            os.replace(inventory_path, inventory_backup)
        os.replace(staged_output, output_path)
        staged_output = None
        output_committed = True
        os.replace(staged_inventory, inventory_path)
        staged_inventory = None
        inventory_committed = True
        if output_path.read_bytes() != bundle_bytes:
            raise OSError("bundle verification failed after commit")
        if inventory_path.read_bytes() != inventory_bytes:
            raise OSError("inventory verification failed after commit")
    except Exception as exc:
        try:
            if output_committed:
                output_path.unlink(missing_ok=True)
            if inventory_committed:
                inventory_path.unlink(missing_ok=True)
            if output_backup is not None and output_backup.exists():
                os.replace(output_backup, output_path)
                output_backup = None
            if inventory_backup is not None and inventory_backup.exists():
                os.replace(inventory_backup, inventory_path)
                inventory_backup = None
        except Exception as rollback_exc:
            preserve_backups = True
            recovery_paths = [str(path) for path in (output_backup, inventory_backup) if path is not None]
            raise AgentFailure(
                "artifact_write_failed",
                6,
                f"artifact write failed and rollback failed: {rollback_exc}; recovery backups: {recovery_paths}",
            ) from exc
        if isinstance(exc, AgentFailure):
            raise
        raise AgentFailure("artifact_write_failed", 6, str(exc)) from exc
    finally:
        for temporary in (staged_output, staged_inventory):
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        for backup in (() if preserve_backups else (output_backup, inventory_backup)):
            if backup is not None:
                backup.unlink(missing_ok=True)

    return ArtifactSet(
        bundle_path=normalized_relative_path(root, output_path),
        bundle_sha256=sha256_hex(bundle_bytes),
        bundle_size_bytes=len(bundle_bytes),
        inventory_path=normalized_relative_path(root, inventory_path),
        inventory_sha256=sha256_hex(inventory_bytes),
    )

def detect_encoding(file_path: str, default_encoding: str) -> str:
    """Detect file encoding using chardet or fallback to default."""
    if chardet is None:
        return default_encoding
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(8192)
        result = chardet.detect(raw_data)
        encoding = result['encoding'] if result and result.get('encoding') else default_encoding
        return encoding if encoding is not None else default_encoding
    except Exception:
        return default_encoding

def minify_python_code(content: str) -> str:
    """Minify Python code by removing comments and excess whitespace."""
    content = re.sub(r'#.*$', '', content, flags=re.MULTILINE)
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return '\n'.join(lines)

def truncate_string(s: str, max_length: int) -> str:
    """Truncate a string to a maximum length without adding any suffix."""
    return s[:max_length] if len(s) > max_length else s

def get_unique_words(content: str) -> Set[str]:
    r"""Extract unique words from content using \w+ pattern."""
    return set(re.findall(r'\w+', content))

def collect_unique_words(files: List[str], exclusion_patterns: List[re.Pattern], default_encoding: str,
                         forced_files: Optional[Set[str]] = None) -> Set[str]:
    """Collect unique words from all files, excluding specified patterns."""
    words = set()
    forced_files = forced_files or set()
    for file_path in files:
        if not os.path.exists(file_path):
            continue
        if file_path not in forced_files and is_excluded(file_path, exclusion_patterns):
            continue
        try:
            encoding = detect_encoding(file_path, default_encoding)
            with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                content = f.read()
            words.update(get_unique_words(content))
        except Exception as e:
            logging.error(f"Failed to read {file_path}: {e}")
    return words

def find_min_truncation_length(words: Set[str], max_length: int) -> Optional[int]:
    """Find the minimal truncation length X to avoid new duplicates."""
    if not words:
        return None
    min_possible_length = 1
    max_possible_length = min(max(len(word) for word in words), max_length)
    for X in range(min_possible_length, max_possible_length + 1):
        truncated_words = {truncate_string(word, X) for word in words}
        if len(truncated_words) == len(words):
            return X
    return None

def truncate_content(content: str, X: int) -> str:
    """Truncate words longer than X in the content."""
    def truncate_match(match):
        word = match.group(0)
        return truncate_string(word, X)
    return re.sub(r'\w+', truncate_match, content)

def get_content_size_bytes(content: str) -> int:
    """Measure content size as UTF-8 bytes."""
    return len(content.encode('utf-8'))

def truncate_nested_string_values(value: Any, max_length: int) -> Any:
    """Recursively truncate string values while preserving object keys."""
    if isinstance(value, dict):
        return {key: truncate_nested_string_values(val, max_length) for key, val in value.items()}
    if isinstance(value, list):
        return [truncate_nested_string_values(item, max_length) for item in value]
    if isinstance(value, str):
        return truncate_content(value, max_length)
    return value

def truncate_json_content(content: str, max_length: int) -> Tuple[str, bool]:
    """Truncate only JSON string values and re-serialize compactly."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content, False
    truncated_payload = truncate_nested_string_values(payload, max_length)
    return json.dumps(truncated_payload, ensure_ascii=False, separators=(',', ':')), True

def truncate_jsonl_content(content: str, max_length: int) -> Tuple[str, bool]:
    """Truncate only JSONL string values and preserve blank lines."""
    trailing_newline = content.endswith('\n')
    output_lines = []
    for line in content.splitlines():
        if not line.strip():
            output_lines.append('')
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return content, False
        truncated_payload = truncate_nested_string_values(payload, max_length)
        output_lines.append(json.dumps(truncated_payload, ensure_ascii=False, separators=(',', ':')))
    result = '\n'.join(output_lines)
    if trailing_newline:
        result += '\n'
    return result, True

def truncate_csv_content(content: str, max_length: int) -> Tuple[str, bool]:
    """Truncate CSV row values while preserving the header row."""
    try:
        rows = list(csv.reader(io.StringIO(content)))
    except csv.Error:
        return content, False

    if not rows:
        return content, True

    truncated_rows = [rows[0]]
    for row in rows[1:]:
        truncated_rows.append([truncate_content(value, max_length) for value in row])

    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerows(truncated_rows)
    result = output.getvalue()
    if not content.endswith('\n') and result.endswith('\n'):
        result = result[:-1]
    return result, True

def split_yaml_comment(text: str) -> Tuple[str, str, str]:
    """Split a YAML value segment into body, preserved spacing, and trailing comment."""
    in_single = False
    in_double = False
    escaped = False
    for idx, char in enumerate(text):
        if char == '\\' and in_double:
            escaped = not escaped
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == '#' and not in_single and not in_double and (idx == 0 or text[idx - 1].isspace()):
            body_with_spacing = text[:idx]
            body = body_with_spacing.rstrip()
            spacing = body_with_spacing[len(body):]
            return body, spacing, text[idx:]
        escaped = False
    return text, '', ''

def find_yaml_mapping_separator(text: str) -> Optional[int]:
    """Find the first mapping colon outside quotes and flow collections."""
    in_single = False
    in_double = False
    escaped = False
    flow_depth = 0

    for idx, char in enumerate(text):
        if char == '\\' and in_double:
            escaped = not escaped
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif not in_single and not in_double:
            if char in '{[':
                flow_depth += 1
            elif char in '}]':
                flow_depth = max(0, flow_depth - 1)
            elif char == ':' and flow_depth == 0:
                next_char = text[idx + 1] if idx + 1 < len(text) else ''
                if next_char == '' or next_char.isspace() or next_char in '|>&*!':
                    return idx
        escaped = False
    return None

def truncate_yaml_value_segment(segment: str, max_length: int) -> Tuple[str, bool, bool]:
    """Truncate a YAML value segment without touching a key prefix."""
    leading_ws_len = len(segment) - len(segment.lstrip())
    leading_ws = segment[:leading_ws_len]
    body_with_comment = segment[leading_ws_len:]
    body, comment_spacing, comment = split_yaml_comment(body_with_comment)
    stripped_body = body.strip()

    if not stripped_body:
        return segment, False, True
    if stripped_body.startswith(('|', '>')):
        return f"{leading_ws}{body}{comment_spacing}{comment}", True, True
    if stripped_body.startswith(('{', '[')):
        return segment, False, False

    truncated_body = truncate_content(body, max_length)
    return f"{leading_ws}{truncated_body}{comment_spacing}{comment}", False, True

def truncate_yaml_scalar_line(line: str, max_length: int) -> Tuple[str, bool, bool]:
    """Truncate YAML scalar values while preserving key tokens."""
    stripped_line = line.strip()
    if not stripped_line or stripped_line.startswith('#') or stripped_line in {'---', '...'}:
        return line, False, True

    indent_len = len(line) - len(line.lstrip(' '))
    prefix = line[:indent_len]
    rest = line[indent_len:]

    if rest.startswith('- '):
        prefix += '- '
        rest = rest[2:]
        separator_idx = find_yaml_mapping_separator(rest)
        if separator_idx is not None:
            key_prefix = rest[:separator_idx + 1]
            value_segment = rest[separator_idx + 1:]
            truncated_segment, enters_block, safe = truncate_yaml_value_segment(value_segment, max_length)
            return f"{prefix}{key_prefix}{truncated_segment}", enters_block, safe
        truncated_segment, enters_block, safe = truncate_yaml_value_segment(rest, max_length)
        return f"{prefix}{truncated_segment}", enters_block, safe

    separator_idx = find_yaml_mapping_separator(rest)
    if separator_idx is None:
        truncated_segment, enters_block, safe = truncate_yaml_value_segment(rest, max_length)
        return f"{prefix}{truncated_segment}", enters_block, safe

    key_prefix = rest[:separator_idx + 1]
    value_segment = rest[separator_idx + 1:]
    truncated_segment, enters_block, safe = truncate_yaml_value_segment(value_segment, max_length)
    return f"{prefix}{key_prefix}{truncated_segment}", enters_block, safe

def truncate_yaml_content(content: str, max_length: int) -> Tuple[str, bool]:
    """Truncate YAML scalar values while preserving original layout where possible."""
    trailing_newline = content.endswith('\n')
    lines = content.splitlines()
    output_lines = []
    safe = True
    in_block_scalar = False
    block_indent = 0
    idx = 0

    while idx < len(lines):
        line = lines[idx]
        if in_block_scalar:
            stripped_line = line.strip()
            current_indent = len(line) - len(line.lstrip(' '))
            if stripped_line and current_indent <= block_indent:
                in_block_scalar = False
                continue
            if stripped_line:
                output_lines.append(f"{line[:current_indent]}{truncate_content(line[current_indent:], max_length)}")
            else:
                output_lines.append(line)
            idx += 1
            continue

        truncated_line, enters_block, line_safe = truncate_yaml_scalar_line(line, max_length)
        output_lines.append(truncated_line)
        safe = safe and line_safe
        if enters_block:
            in_block_scalar = True
            block_indent = len(line) - len(line.lstrip(' '))
        idx += 1

    result = '\n'.join(output_lines)
    if trailing_newline:
        result += '\n'
    return result, safe

def apply_truncation_strategy(content: str, extension: str, max_length: int) -> Tuple[str, bool]:
    """Apply the correct truncation strategy for the current file type."""
    if extension == '.json':
        return truncate_json_content(content, max_length)
    if extension == '.jsonl':
        return truncate_jsonl_content(content, max_length)
    if extension == '.csv':
        return truncate_csv_content(content, max_length)
    if extension in {'.yaml', '.yml'}:
        return truncate_yaml_content(content, max_length)
    return truncate_content(content, max_length), True

def find_best_truncation_for_target_size(content: str, extension: str, target_size_bytes: int) -> Tuple[Optional[int], Optional[str], bool]:
    """Find the least aggressive truncation length that satisfies the byte limit."""
    lower_bound = 1
    upper_bound = max(1, len(content))
    best_length = None
    best_content = None
    best_safe = True
    saw_unsafe = False

    while lower_bound <= upper_bound:
        candidate_length = (lower_bound + upper_bound) // 2
        candidate_content, safe = apply_truncation_strategy(content, extension, candidate_length)
        saw_unsafe = saw_unsafe or not safe
        candidate_size = get_content_size_bytes(candidate_content)
        if candidate_size <= target_size_bytes:
            best_length = candidate_length
            best_content = candidate_content
            best_safe = safe
            lower_bound = candidate_length + 1
        else:
            upper_bound = candidate_length - 1

    if best_length is not None:
        return best_length, best_content, best_safe
    return None, None, not saw_unsafe

def parse_override_max_length(overrides: List[str]) -> List[Tuple[str, int]]:
    """Parse override max-length specifications."""
    parsed = []
    for override in overrides:
        try:
            pattern, length = override.split(':', 1)
            length = int(length)
            if length <= 0:
                raise ValueError
            parsed.append((pattern, length))
        except ValueError:
            logging.error(f"Invalid override format: {override}. Expected PATTERN:INTEGER")
    return parsed

def match_override(file_path: str, overrides: List[Tuple[str, int]]) -> Optional[int]:
    """Check if the file matches any override patterns."""
    for pattern, length in overrides:
        if fnmatch.fnmatch(os.path.basename(file_path), pattern):
            return length
    return None

def copy_to_clipboard(text: str, max_clip_length: int) -> None:
    """Copy text to clipboard with instructional comments for large outputs."""
    platform_commands = {
        'win32': 'clip',
        'darwin': 'pbcopy',
        'linux': 'xclip -selection clipboard'
    }
    cmd = platform_commands.get(sys.platform)
    if not cmd:
        logging.warning(f"Clipboard copying not supported on {sys.platform}")
        return

    file_blocks = []
    current_block = []
    lines = text.splitlines(keepends=True)
    for line in lines:
        if line.startswith('## '):
            if current_block:
                file_blocks.append(''.join(current_block))
                current_block = []
        current_block.append(line)
    if current_block:
        file_blocks.append(''.join(current_block))

    chunks = []
    chunk = ''
    for block in file_blocks:
        if len(chunk) + len(block) > max_clip_length and chunk:
            chunks.append(chunk)
            chunk = block
        else:
            chunk += block
    if chunk:
        chunks.append(chunk)
    num_chunks = len(chunks)

    for idx, chunk in enumerate(chunks, 1):
        if idx < num_chunks:
            comment = f"# Clipboard section {idx} of {num_chunks}. It is imperative not to respond until all sections have been provided.\n"
        else:
            comment = f"# Clipboard section {idx} of {num_chunks}. All sections have been provided. You may proceed with the response.\n"
        chunk_with_comment = comment + chunk
        try:
            subprocess.run(cmd, shell=(sys.platform == 'linux'), input=chunk_with_comment.encode('utf-8'), check=True)
            if idx < num_chunks:
                time.sleep(2.5)
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to copy chunk {idx} to clipboard: {e}")
            break
    logging.info(f"Copied {num_chunks} clipboard section(s)")

def resolve_import(current_file: str, module: str, level: int, project_root: str) -> Optional[str]:
    """Resolve an import to a project-local file path."""
    if level == 0:
        parts = module.split('.')
        candidate = os.path.join(project_root, *parts) + '.py'
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
        candidate = os.path.join(project_root, *parts, '__init__.py')
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    else:
        cur_dir = os.path.dirname(current_file)
        for _ in range(level - 1):
            cur_dir = os.path.dirname(cur_dir)
            if not cur_dir:
                return None
        parts = module.split('.') if module else []
        candidate = os.path.join(cur_dir, *parts) + '.py'
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
        candidate = os.path.join(cur_dir, *parts, '__init__.py')
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return None

def resolve_script_import(current_file: str, module: str, project_root: str) -> Optional[str]:
    """Resolve a JS/TS-style import to a local project file when possible."""
    if not module:
        return None

    if module.startswith('.'):
        base_candidate = os.path.normpath(os.path.join(os.path.dirname(current_file), module))
    else:
        base_candidate = os.path.normpath(os.path.join(project_root, *module.split('/')))

    candidates = []
    raw_ext = os.path.splitext(base_candidate)[1].lower()
    if raw_ext:
        candidates.append(base_candidate)
        stem = base_candidate[:-len(raw_ext)]
        if raw_ext in {'.js', '.jsx', '.mjs', '.cjs'}:
            for ext in SCRIPT_MODULE_EXTENSIONS:
                candidates.append(f"{stem}{ext}")
    else:
        for ext in SCRIPT_MODULE_EXTENSIONS:
            candidates.append(f"{base_candidate}{ext}")

    for ext in SCRIPT_MODULE_EXTENSIONS:
        candidates.append(os.path.join(base_candidate, f"index{ext}"))

    seen_candidates = set()
    for candidate in candidates:
        normalized = os.path.abspath(candidate)
        if normalized in seen_candidates:
            continue
        seen_candidates.add(normalized)
        if os.path.exists(normalized):
            return normalized

    return None

def strip_script_comments(content: str) -> str:
    """Remove line and block comments from JS/TS-like source."""
    return re.sub(r'//.*?$|/\*.*?\*/', '', content, flags=re.MULTILINE | re.DOTALL)

def strip_script_strings(content: str) -> str:
    """Replace string literal contents so token scans focus on code identifiers."""
    string_pattern = r'`(?:\\.|[^`])*`|"(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\''
    return re.sub(string_pattern, '""', content, flags=re.DOTALL)

def parse_markdown_fence_start(line: str) -> Optional[Tuple[str, int, str]]:
    """Return markdown fence metadata for a possible fenced-code opener."""
    match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
    if not match:
        return None
    fence = match.group(1)
    info = match.group(2).strip()
    return fence[0], len(fence), info

def is_markdown_fence_close(line: str, fence_char: str, fence_len: int) -> bool:
    """Return True when a line closes the active markdown fence."""
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {fence_char} and len(stripped) >= fence_len

def is_markdown_graph_annotation_fence(info: str) -> bool:
    """Detect explicit markdown code fences that contain graph annotation JSON."""
    tokens = [
        token.strip().strip("{}").lstrip(".").lower()
        for token in re.split(r'[\s,]+', info)
        if token.strip()
    ]
    return any(token in MARKDOWN_GRAPH_FENCE_INFOS for token in tokens)

def extract_markdown_graph_annotation_directives(content: str) -> List[dict]:
    """Extract file-local graph annotations from markdown HTML comments outside fences."""
    directives = []
    active_fence = None
    comment_pattern = re.compile(r'<!--\s*' + re.escape(GRAPH_ANNOTATION_MARKER) + r'\s+(.+?)\s*-->')

    for line_number, line in enumerate(content.splitlines(), start=1):
        if active_fence:
            fence_char, fence_len = active_fence
            if is_markdown_fence_close(line, fence_char, fence_len):
                active_fence = None
            continue

        fence = parse_markdown_fence_start(line)
        if fence:
            active_fence = (fence[0], fence[1])
            continue

        for match in comment_pattern.finditer(line):
            payload = match.group(1).strip()
            if not payload:
                logging.debug("Ignoring empty markdown graph annotation directive at line %s", line_number)
                continue

            try:
                directive = json.loads(payload)
            except json.JSONDecodeError as exc:
                logging.debug("Invalid markdown graph annotation directive at line %s: %s", line_number, exc)
                continue

            normalized = normalize_graph_annotation_directive(directive, line_number)
            if normalized is not None:
                directives.append(normalized)

    return directives

def extract_markdown_graph_annotation_entries(content: str, source_name: str) -> List[dict]:
    """Extract graph-global annotation bundles from explicit markdown fences."""
    annotation_entries = []
    active_fence = None

    for line_number, line in enumerate(content.splitlines(), start=1):
        if active_fence:
            fence_char, fence_len, is_graph_fence, start_line, block_lines = active_fence
            if is_markdown_fence_close(line, fence_char, fence_len):
                if is_graph_fence:
                    payload = "\n".join(block_lines).strip()
                    if payload:
                        try:
                            bundle = json.loads(payload)
                        except json.JSONDecodeError as exc:
                            logging.debug(
                                "Invalid markdown graph annotation block in %s at line %s: %s",
                                source_name,
                                start_line,
                                exc,
                            )
                        else:
                            annotation_entries.extend(
                                normalize_graph_annotation_bundle(
                                    bundle,
                                    source_name,
                                    syntax=MARKDOWN_GRAPH_ANNOTATION_BUNDLE_SYNTAX,
                                    origin={"line": start_line},
                                )
                            )
                active_fence = None
                continue

            if is_graph_fence:
                block_lines.append(line)
            continue

        fence = parse_markdown_fence_start(line)
        if fence:
            fence_char, fence_len, info = fence
            active_fence = (
                fence_char,
                fence_len,
                is_markdown_graph_annotation_fence(info),
                line_number,
                [],
            )

    return annotation_entries

def extract_graph_annotation_directives(content: str) -> List[dict]:
    """Extract machine-readable AI-native graph annotations from source comments."""
    directives = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip()
        payload = None

        if stripped.startswith('#'):
            stripped = stripped[1:].lstrip()
        elif stripped.startswith('//'):
            stripped = stripped[2:].lstrip()
        elif stripped.startswith('/*'):
            stripped = stripped[2:].lstrip()
            if stripped.endswith('*/'):
                stripped = stripped[:-2].rstrip()
            else:
                continue
        else:
            continue

        if not stripped.startswith(GRAPH_ANNOTATION_MARKER):
            continue

        payload = stripped[len(GRAPH_ANNOTATION_MARKER):].strip()
        if not payload:
            logging.debug("Ignoring empty graph annotation directive at line %s", line_number)
            continue

        try:
            directive = json.loads(payload)
        except json.JSONDecodeError as exc:
            logging.debug("Invalid graph annotation directive at line %s: %s", line_number, exc)
            continue

        normalized = normalize_graph_annotation_directive(directive, line_number)
        if normalized is not None:
            directives.append(normalized)

    return directives

def normalize_graph_annotation_directive(directive: Any, line_number: int) -> Optional[dict]:
    """Validate and normalize a parsed @colly-graph directive."""
    if not isinstance(directive, dict):
        return None

    target = directive.get("target")
    annotation = directive.get("annotation")
    if not isinstance(target, dict) or not isinstance(annotation, dict) or not annotation:
        return None

    kind = target.get("kind")
    if kind not in GRAPH_ANNOTATION_TARGET_KINDS:
        return None

    if kind == "definition" and not isinstance(target.get("name"), str):
        return None

    if kind == "edge":
        if not isinstance(target.get("type"), str):
            return None
        if not isinstance(target.get("name"), str) and not isinstance(target.get("target"), str):
            return None

    normalized_annotation = dict(annotation)
    normalized_annotation["_origin"] = {
        "line": line_number,
        "syntax": GRAPH_ANNOTATION_MARKER,
    }
    return {
        "target": dict(target),
        "annotation": normalized_annotation,
    }

def sort_graph_annotations(annotations: List[dict]) -> List[dict]:
    """Sort annotations deterministically for stable graph output."""
    return sorted(
        annotations,
        key=lambda annotation: (
            annotation.get("_origin", {}).get("line", 0),
            annotation.get("_origin", {}).get("entry", 0),
            annotation.get("kind", ""),
            annotation.get("summary", ""),
            json.dumps(annotation, sort_keys=True),
        ),
    )

def select_node_annotations(file_annotations: List[dict], rel_path: str) -> List[dict]:
    """Select node annotations that apply to the current file node."""
    annotations = []
    for directive in file_annotations:
        target = directive["target"]
        if target.get("kind") != "node":
            continue
        if "path" in target and target["path"] != rel_path:
            continue
        annotations.append(directive["annotation"])
    return sort_graph_annotations(annotations)

def select_definition_annotations(file_annotations: List[dict], definition: dict) -> List[dict]:
    """Select definition annotations that match the emitted definition object."""
    annotations = []
    for directive in file_annotations:
        target = directive["target"]
        if target.get("kind") != "definition":
            continue
        if target.get("name") != definition.get("name"):
            continue
        if "type" in target and target["type"] != definition.get("type"):
            continue
        annotations.append(directive["annotation"])
    return sort_graph_annotations(annotations)

def select_edge_annotations(file_annotations: List[dict], edge: dict, target_rel_path: Optional[str]) -> List[dict]:
    """Select edge annotations that match the emitted edge object."""
    annotations = []
    for directive in file_annotations:
        target = directive["target"]
        if target.get("kind") != "edge":
            continue
        if target.get("type") != edge.get("type"):
            continue
        if "name" in target and target["name"] != edge.get("name"):
            continue
        if "target" in target and target["target"] != target_rel_path:
            continue
        annotations.append(directive["annotation"])
    return sort_graph_annotations(annotations)

def render_ai_native_dependency_graph_markdown(graph_data: dict) -> str:
    """Render AI-native dependency graph data as the standard markdown section."""
    output = [
        "# AI-Native Dependency Graphs", "```json",
        json.dumps(graph_data, separators=(',', ':')),
        "```", ""
    ]
    return "\n".join(output)

def parse_ai_native_dependency_graph_input(content: str) -> dict:
    """Parse AI-native dependency graph input from raw JSON or markdown output."""
    stripped = content.strip()
    payload = stripped

    if not stripped.startswith('{'):
        match = re.search(r"# AI-Native Dependency Graphs\n```json\n(.*?)\n```", content, re.DOTALL)
        if not match:
            raise ValueError("Could not find an AI-native dependency graph JSON block in the provided input.")
        payload = match.group(1)

    graph_data = json.loads(payload)
    if not isinstance(graph_data, dict):
        raise ValueError("Dependency graph payload must be a JSON object.")
    if not isinstance(graph_data.get("nodes"), list) or not isinstance(graph_data.get("edges"), list):
        raise ValueError("Dependency graph payload must contain 'nodes' and 'edges' arrays.")
    return graph_data

def clone_graph_annotation(annotation: dict) -> dict:
    """Deep-copy a JSON-safe annotation payload."""
    return json.loads(json.dumps(annotation))

def normalize_graph_annotation_bundle(
    bundle: Any,
    bundle_name: str,
    syntax: str = GRAPH_ANNOTATION_BUNDLE_SYNTAX,
    origin: Optional[dict] = None,
) -> List[dict]:
    """Validate and normalize a post-hoc graph annotation bundle."""
    if isinstance(bundle, dict) and isinstance(bundle.get("annotations"), list):
        entries = bundle["annotations"]
    elif isinstance(bundle, list):
        entries = bundle
    elif isinstance(bundle, dict) and isinstance(bundle.get("selector"), dict):
        entries = [bundle]
    else:
        return []

    normalized = []
    for entry_index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue

        selector = entry.get("selector")
        annotation = entry.get("annotation")
        if not isinstance(selector, dict) or not isinstance(annotation, dict) or not annotation:
            continue

        kind = selector.get("kind")
        if kind not in GRAPH_ANNOTATION_TARGET_KINDS:
            continue

        if kind == "node":
            if not isinstance(selector.get("path"), str):
                continue
        elif kind == "definition":
            if not isinstance(selector.get("path"), str) or not isinstance(selector.get("name"), str):
                continue
        elif kind == "edge":
            if not isinstance(selector.get("source_path"), str) or not isinstance(selector.get("type"), str):
                continue
            if not isinstance(selector.get("target_path"), str) and not isinstance(selector.get("name"), str):
                continue

        normalized_annotation = dict(annotation)
        origin_payload = dict(origin or {})
        origin_payload.setdefault("bundle", bundle_name)
        origin_payload.update({
            "bundle": bundle_name,
            "entry": entry_index,
            "syntax": syntax,
        })
        normalized_annotation["_origin"] = origin_payload
        normalized.append({
            "selector": dict(selector),
            "annotation": normalized_annotation,
        })

    return normalized

def load_graph_annotation_bundle(path: str) -> List[dict]:
    """Load a graph annotation bundle JSON file."""
    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        payload = json.load(handle)
    return normalize_graph_annotation_bundle(payload, os.path.basename(path))

def append_graph_annotation(element: dict, annotation: dict) -> None:
    """Append an annotation to a graph element and keep ordering deterministic."""
    element.setdefault("annotations", [])
    element["annotations"].append(clone_graph_annotation(annotation))
    element["annotations"] = sort_graph_annotations(element["annotations"])

def apply_graph_annotation_entries(graph_data: dict, annotation_entries: List[dict]) -> dict:
    """Apply post-hoc graph annotation bundle entries to graph data."""
    if not annotation_entries:
        return graph_data

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    node_paths = {index: node.get("path") for index, node in enumerate(nodes)}
    node_by_path = {node.get("path"): node for node in nodes if isinstance(node, dict) and isinstance(node.get("path"), str)}

    for entry in annotation_entries:
        selector = entry["selector"]
        annotation = entry["annotation"]
        kind = selector.get("kind")

        if kind == "node":
            node = node_by_path.get(selector.get("path"))
            if node is not None:
                append_graph_annotation(node, annotation)
            continue

        if kind == "definition":
            node = node_by_path.get(selector.get("path"))
            if node is None:
                continue
            for definition in node.get("definitions", []):
                if definition.get("name") != selector.get("name"):
                    continue
                if "type" in selector and definition.get("type") != selector.get("type"):
                    continue
                if "signature" in selector and definition.get("signature") != selector.get("signature"):
                    continue
                append_graph_annotation(definition, annotation)
            continue

        if kind == "edge":
            for edge in edges:
                source_path = node_paths.get(edge.get("source"))
                target_path = node_paths.get(edge.get("target")) if edge.get("target") is not None else None

                if source_path != selector.get("source_path"):
                    continue
                if edge.get("type") != selector.get("type"):
                    continue
                if "target_path" in selector and target_path != selector.get("target_path"):
                    continue
                if "name" in selector and edge.get("name") != selector.get("name"):
                    continue
                append_graph_annotation(edge, annotation)

    return graph_data

def add_definition(definitions, symbol_locations, file_path: str, name: str, definition_type: str,
                   seen_definitions: Set[Tuple[str, str, str]], signature: str = "") -> None:
    """Register a definition once and record where it can be referenced from."""
    definition_key = (name, definition_type, signature)
    if definition_key in seen_definitions:
        return
    seen_definitions.add(definition_key)

    definition = {'name': name, 'type': definition_type}
    if signature:
        definition['signature'] = signature
    definitions[file_path].append(definition)

    if definition_type == 'function':
        symbol_locations[name]['call'].append(file_path)
    else:
        symbol_locations[name]['symbol'].append(file_path)

def analyze_script_definitions(file_path: str, content: str, definitions, symbol_locations) -> None:
    """Collect definitions from JS/TS source using lightweight syntax heuristics."""
    source = strip_script_comments(content)
    seen_definitions = set()
    arrow_function_names = set()

    function_pattern = re.compile(
        r'(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+'
        r'([A-Za-z_$][\w$]*)\s*(?:<[^>\n]+>\s*)?(\([^)]*\)(?:\s*:\s*[^{=\n]+)?)?'
    )
    arrow_function_pattern = re.compile(
        r'(?m)^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)'
        r'\s*(?::[^=\n]+)?=\s*(?:async\s*)?(?:<[^>\n]+>\s*)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>'
    )
    class_pattern = re.compile(r'(?m)^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)\b')
    interface_pattern = re.compile(r'(?m)^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)\b')
    type_pattern = re.compile(r'(?m)^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\b')
    enum_pattern = re.compile(r'(?m)^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)\b')
    variable_pattern = re.compile(r'(?m)^\s*(?:export\s+)?(?:declare\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\b')

    for match in function_pattern.finditer(source):
        signature = match.group(2) or "(...)"
        add_definition(definitions, symbol_locations, file_path, match.group(1), 'function', seen_definitions, signature)

    for match in arrow_function_pattern.finditer(source):
        arrow_function_names.add(match.group(1))
        signature = match.group(2) or "(...)"
        if not signature.startswith('('):
            signature = f"({signature})"
        add_definition(definitions, symbol_locations, file_path, match.group(1), 'function', seen_definitions, signature)

    for match in class_pattern.finditer(source):
        add_definition(definitions, symbol_locations, file_path, match.group(1), 'class', seen_definitions)

    for match in interface_pattern.finditer(source):
        add_definition(definitions, symbol_locations, file_path, match.group(1), 'interface', seen_definitions)

    for match in type_pattern.finditer(source):
        add_definition(definitions, symbol_locations, file_path, match.group(1), 'type', seen_definitions)

    for match in enum_pattern.finditer(source):
        add_definition(definitions, symbol_locations, file_path, match.group(1), 'enum', seen_definitions)

    for match in variable_pattern.finditer(source):
        variable_name = match.group(1)
        if variable_name in arrow_function_names:
            continue
        add_definition(definitions, symbol_locations, file_path, variable_name, 'variable', seen_definitions)

def collect_script_import_edges(file_path: str, content: str, files: Set[str], project_root: str) -> List[dict]:
    """Collect JS/TS import edges for local dependency analysis."""
    source = strip_script_comments(content)

    import_patterns = [
        re.compile(r'(?ms)^\s*import\s+(?:type\s+)?(?:[\w*\s{},]+\s+from\s+)?[\'"]([^\'"]+)[\'"]\s*;?'),
        re.compile(r'(?ms)^\s*export\s+(?:type\s+)?(?:\*|{[\w*\s,\n]+})\s+from\s+[\'"]([^\'"]+)[\'"]\s*;?'),
        re.compile(r'require\(\s*[\'"]([^\'"]+)[\'"]\s*\)'),
        re.compile(r'(?<!\w)import\(\s*[\'"]([^\'"]+)[\'"]\s*\)'),
    ]

    edges = []
    seen_import_edges = set()
    for pattern in import_patterns:
        for match in pattern.finditer(source):
            module_name = match.group(1)
            if module_name in seen_import_edges:
                continue
            seen_import_edges.add(module_name)
            dep = resolve_script_import(file_path, module_name, project_root)
            edges.append({'name': module_name, 'target': dep if dep and dep in files else None})
    return edges

def collect_script_usage_names(content: str) -> Tuple[Set[str], Set[str]]:
    """Collect JS/TS call and symbol identifiers from source."""
    usage_source = strip_script_strings(strip_script_comments(content))
    call_names = {
        match.group(1) for match in re.finditer(r'\b([A-Za-z_$][\w$]*)\s*\(', usage_source)
        if match.group(1) not in SCRIPT_CALL_KEYWORDS
    }
    symbol_names = set(re.findall(r'\b[A-Za-z_$][\w$]*\b', usage_source)) - SCRIPT_CALL_KEYWORDS
    return call_names, symbol_names

def analyze_script_usages(file_path: str, content: str, files: Set[str], graph, symbol_locations,
                          project_root: str) -> None:
    """Collect JS/TS imports and cross-file symbol/call references."""
    for edge in collect_script_import_edges(file_path, content, files, project_root):
        graph[file_path]['imports'].append(edge)

    call_names, symbol_names = collect_script_usage_names(content)

    for func_name in call_names:
        if func_name in symbol_locations and 'call' in symbol_locations[func_name]:
            for def_file in symbol_locations[func_name]['call']:
                if def_file != file_path:
                    graph[file_path]['calls'].append({'name': func_name, 'target': def_file})

    for symbol_name in symbol_names:
        if symbol_name in symbol_locations and 'symbol' in symbol_locations[symbol_name]:
            for def_file in symbol_locations[symbol_name]['symbol']:
                if def_file != file_path:
                    graph[file_path]['symbols'].append({'name': symbol_name, 'target': def_file})

def build_forward_graph(all_py_files: Set[str], dep_type: str, project_root: str,
                        progress: Optional[ProgressReporter] = None) -> defaultdict[str, Set[str]]:
    graph = defaultdict(set)
    file_list = sorted(all_py_files)
    if dep_type == 'import':
        total = len(file_list)
        for index, file_path in enumerate(file_list, start=1):
            if progress:
                progress.update(f"Building {dep_type} graph", index, total)
            try:
                extension = os.path.splitext(file_path)[1].lower()
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                if extension == '.py':
                    tree = ast.parse(content, filename=file_path)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            for alias in node.names:
                                dep = resolve_import(file_path, alias.name, 0, project_root)
                                if dep and dep in all_py_files:
                                    graph[file_path].add(dep)
                        elif isinstance(node, ast.ImportFrom):
                            dep = resolve_import(file_path, node.module or '', node.level, project_root)
                            if dep and dep in all_py_files:
                                graph[file_path].add(dep)
                elif extension in SCRIPT_ANALYZABLE_EXTENSIONS:
                    for dep_info in collect_script_import_edges(file_path, content, all_py_files, project_root):
                        if dep_info['target'] and dep_info['target'] in all_py_files:
                            graph[file_path].add(dep_info['target'])
            except Exception:
                pass
    elif dep_type in ['symbol', 'call']:
        defs = defaultdict(list)
        total = len(file_list)
        for index, file_path in enumerate(file_list, start=1):
            if progress:
                progress.update(f"Indexing {dep_type} definitions", index, total)
            try:
                extension = os.path.splitext(file_path)[1].lower()
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                if extension == '.py':
                    tree = ast.parse(content, filename=file_path)
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                            defs[node.name].append(file_path)
                        elif dep_type == 'symbol' and isinstance(node, ast.Assign):
                            for target in node.targets:
                                if isinstance(target, ast.Name):
                                    defs[target.id].append(file_path)
                elif extension in SCRIPT_ANALYZABLE_EXTENSIONS:
                    temp_definitions = defaultdict(list)
                    temp_symbol_locations = defaultdict(lambda: defaultdict(list))
                    analyze_script_definitions(file_path, content, temp_definitions, temp_symbol_locations)
                    for symbol_name, locations in temp_symbol_locations.items():
                        if dep_type == 'call':
                            defs[symbol_name].extend(locations.get('call', []))
                        else:
                            defs[symbol_name].extend(locations.get('symbol', []))
            except Exception:
                pass
        for index, file_path in enumerate(file_list, start=1):
            if progress:
                progress.update(f"Linking {dep_type} graph", index, total)
            try:
                extension = os.path.splitext(file_path)[1].lower()
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                used = set()
                if extension == '.py':
                    tree = ast.parse(content, filename=file_path)
                    if dep_type == 'symbol':
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                                used.add(node.id)
                    else:  # 'call'
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                                used.add(node.func.id)
                elif extension in SCRIPT_ANALYZABLE_EXTENSIONS:
                    call_names, symbol_names = collect_script_usage_names(content)
                    used = symbol_names if dep_type == 'symbol' else call_names

                for sym in used:
                    for def_file in defs[sym]:
                        if def_file != file_path:
                            graph[file_path].add(def_file)
            except Exception:
                pass
    return graph

def collect_additional_files(start_files: Set[str], g: defaultdict, max_num: int, input_files: Set[str]) -> Set[str]:
    added = set()
    for start in start_files:
        queue = deque([start])
        visited = set([start])
        count = 0
        while queue and count < max_num:
            cur = queue.popleft()
            for neigh in g[cur]:
                if neigh not in visited and neigh not in input_files:
                    added.add(neigh)
                    visited.add(neigh)
                    queue.append(neigh)
                    count += 1
                    if count >= max_num:
                        break
    return added

def build_verbose_output(truncate: bool, unique_words: Set[str], max_length: int,
                        overrides: List[Tuple[str, int]], follow_symlinks: bool,
                        default_encoding: str, minify_python: bool,
                        exclusion_patterns: List[re.Pattern]) -> List[str]:
    output = []
    output.append("# Introduction")
    output.append("This output was created by colly.py. It gathers and processes files with optional transformations.")
    output.append("")
    if truncate:
        output.append("# Truncation Details")
        min_trunc_length = find_min_truncation_length(unique_words, max_length)
        if min_trunc_length is not None:
            output.append(f"- A minimal truncation length of {min_trunc_length} was determined to preserve word uniqueness.")
            output.append(f"- Words longer than {min_trunc_length} characters may have been truncated in files without overrides.")
        else:
            output.append("- No suitable minimal truncation length was found within the allowed range.")
            output.append("- Global truncation was not applied to files without overrides.")
        if overrides:
            output.append("- Truncation overrides were applied to specific file patterns.")
        if max_length != 80:
            output.append(f"- The maximum allowed word length for truncation was set to {max_length}.")
        output.append("")
    if minify_python:
        output.append("# Python Minification")
        output.append("- Python files were minified by removing comments and extra whitespace.")
        output.append("")
    if follow_symlinks:
        output.append("# Symlink Following")
        output.append("- Symbolic links were followed during file traversal.")
        output.append("")
    if exclusion_patterns:
        output.append("# Exclusions")
        output.append("- Certain files or directories were excluded based on patterns.")
        output.append("")
    if default_encoding != 'utf-8':
        output.append("# Encoding")
        output.append(f"- Default encoding set to: {default_encoding}")
        output.append("")
    output.append("# Script Run Parameters")
    output.append("```")
    output.append(" ".join(sys.argv[1:]))
    output.append("```")
    output.append("")
    return output

class Stats:
    def __init__(self):
        self.per_file = {}
        self.total_lines = 0
        self.total_chars = 0
        self.num_files_processed = 0

    def add_file(self, file_path, lines, chars):
        self.per_file[file_path] = {'lines': lines, 'chars': chars}
        self.total_lines += lines
        self.total_chars += chars
        self.num_files_processed += 1

    def render(self):
        stats_dict = {
            "num_files_processed": self.num_files_processed,
            "total_lines": self.total_lines,
            "total_chars": self.total_chars,
            "files": [
                {
                    "file": os.path.relpath(file),
                    "lines": stat['lines'],
                    "chars": stat['chars']
                }
                for file, stat in self.per_file.items()
            ]
        }
        return json.dumps(stats_dict, indent=2, ensure_ascii=False)

def process_files(files: List[str], exclusion_patterns: List[re.Pattern], follow_symlinks: bool,
                 default_encoding: str, minify_python: bool, truncate: bool, max_length: int,
                 overrides: List[Tuple[str, int]], unique_words: Set[str], verbose: bool,
                 show_stats: bool, direct_input_files: Optional[Set[str]] = None,
                 forced_files: Optional[Set[str]] = None,
                 auto_truncate: bool = False,
                 progress: Optional[ProgressReporter] = None) -> tuple[str, Stats]:
    output = []
    stats = Stats()
    direct_input_files = direct_input_files or set()
    forced_files = forced_files or set()

    if verbose:
        output.extend(build_verbose_output(truncate, unique_words, max_length,
                                          overrides, follow_symlinks,
                                          default_encoding, minify_python,
                                          exclusion_patterns))

    truncation_length = None
    if truncate:
        min_trunc_length = find_min_truncation_length(unique_words, max_length)
        if min_trunc_length is not None:
            truncation_length = min_trunc_length
            logging.info(f"Determined minimal truncation length X: {truncation_length}")
        else:
            logging.warning("No suitable truncation length found within the possible range. Global truncation will not be applied. Overrides may still apply to specific files.")

    total = len(files)
    for index, file_path in enumerate(files, start=1):
        if progress:
            progress.update("Rendering files", index, total)
        if not os.path.exists(file_path):
            continue
        if file_path not in forced_files and is_excluded(file_path, exclusion_patterns):
            continue
        result, stat = process_single_file(
            file_path, default_encoding, minify_python, truncate, truncation_length,
            overrides, auto_truncate and file_path in direct_input_files
        )
        if result:
            output.extend(result)
            if stat:
                stats.add_file(file_path, stat['lines'], stat['chars'])

    if show_stats:
        output.append("# Stats Summary")
        output.append("```json")
        output.append(stats.render())
        output.append("```")
        output.append("")

    if verbose:
        output.append("# Summary")
        output.append(f"- Number of files processed: {stats.num_files_processed}")
        output.append("")

    return '\n'.join(output), stats

def process_single_file(file_path: str, default_encoding: str, minify_python: bool,
                       truncate: bool, truncation_length: Optional[int],
                       overrides: List[Tuple[str, int]], auto_size_guard: bool) -> Tuple[List[str], Optional[dict]]:
    output = []
    extension = os.path.splitext(file_path)[1].lower()
    language = language_identifier.get(extension, "plaintext")
    encoding = detect_encoding(file_path, default_encoding)
    try:
        with open(file_path, 'r', encoding=encoding, errors='replace') as f:
            content = f.read()
    except Exception as e:
        logging.error(f"Failed to read {file_path}: {e}")
        return [], None

    if not content.strip():
        return [], None

    stat = {
        'lines': len(content.splitlines()),
        'chars': len(content),
    }

    if minify_python and extension == '.py':
        content = minify_python_code(content)
        stat['lines'] = len(content.splitlines())
        stat['chars'] = len(content)

    if truncate:
        override_max_length = match_override(file_path, overrides)
        trunc_length = override_max_length if override_max_length is not None else truncation_length
        if trunc_length is not None:
            content, safe = apply_truncation_strategy(content, extension, trunc_length)
            if extension in VALUE_ONLY_STRUCTURED_EXTENSIONS and not safe:
                logging.warning(f"Structured truncation could not be safely applied to {file_path}; preserving ambiguous content.")
            stat['lines'] = len(content.splitlines())
            stat['chars'] = len(content)

    if auto_size_guard and get_content_size_bytes(content) > AUTO_TRUNCATE_THRESHOLD_BYTES:
        best_length, best_content, safe = find_best_truncation_for_target_size(content, extension, AUTO_TRUNCATE_TARGET_BYTES)
        if best_content is not None and best_content != content:
            content = best_content
            stat['lines'] = len(content.splitlines())
            stat['chars'] = len(content)
            logging.info(
                f"Applied auto-size truncation to {file_path} at max length {best_length} to keep the file body within {AUTO_TRUNCATE_TARGET_BYTES} bytes."
            )
        elif extension in VALUE_ONLY_STRUCTURED_EXTENSIONS and not safe:
            logging.warning(
                f"Structured auto-size truncation could not safely reduce {file_path} to {AUTO_TRUNCATE_TARGET_BYTES} bytes; preserving ambiguous content."
            )

    try:
        relative_path = os.path.relpath(file_path)
    except ValueError:
        relative_path = file_path

    output.append(f"## {relative_path}")
    output.append(f"```{language}")
    output.append(content.rstrip())
    output.append("```")
    output.append("")
    return output, stat

def get_input_files(args_patterns: List[str], exclusion_patterns: List[re.Pattern], follow_symlinks: bool,
                    forced_files: Optional[Set[str]] = None,
                    progress: Optional[ProgressReporter] = None) -> Set[str]:
    forced_files = forced_files or set()
    expanded_paths = set()
    pattern_total = len(args_patterns)
    for pattern_index, pattern in enumerate(args_patterns, start=1):
        if progress:
            progress.update("Expanding input patterns", pattern_index, pattern_total)
        expanded = glob.glob(pattern, recursive=True)
        expanded_paths.update(os.path.abspath(p) for p in expanded if os.path.exists(p))
    input_files = set()
    path_list = sorted(expanded_paths)
    total_paths = len(path_list)
    for path_index, path in enumerate(path_list, start=1):
        if progress:
            progress.update("Scanning input paths", path_index, total_paths)
        if path in forced_files and os.path.isfile(path):
            input_files.add(path)
            continue
        if is_excluded(path, exclusion_patterns):
            continue
        if os.path.isfile(path):
            input_files.add(path)
        elif os.path.isdir(path):
            for root, dirs, files_in_dir in os.walk(path, followlinks=follow_symlinks):
                dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), exclusion_patterns)]
                for file_name in files_in_dir:
                    file_full_path = os.path.join(root, file_name)
                    if file_full_path in forced_files or not is_excluded(file_full_path, exclusion_patterns):
                        input_files.add(file_full_path)
    return input_files

def get_all_py_files(project_root: str, exclusion_patterns: List[re.Pattern], follow_symlinks: bool,
                     progress: Optional[ProgressReporter] = None) -> Set[str]:
    supported_exts = set(language_identifier.keys())
    supported_exts = {e for e in supported_exts if e.startswith('.')}

    all_supported = set()
    scanned_dirs = 0
    for root, dirs, files in os.walk(project_root, followlinks=follow_symlinks):
        scanned_dirs += 1
        if progress:
            progress.update("Indexing supported files", scanned_dirs)
        dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), exclusion_patterns)]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in supported_exts and not is_excluded(os.path.join(root, f), exclusion_patterns):
                all_supported.add(os.path.join(root, f))
    return all_supported

def get_directory_stats(input_files: Set[str], project_root: str) -> dict:
    """Generate a structured directory listing for input files, rooted at project_root."""
    class DirStatsDict(defaultdict):
        def __missing__(self, key):
            self[key] = {"files": [], "subdirs": set()}
            return self[key]
    dir_stats = DirStatsDict()
    project_root_abs = os.path.abspath(project_root)
    for file_path in sorted(input_files):
        abs_file_path = os.path.abspath(file_path)
        rel_file_path = os.path.relpath(abs_file_path, project_root_abs)
        dir_path = os.path.dirname(rel_file_path)
        file_name = os.path.basename(rel_file_path)
        dir_stats[dir_path]["files"].append(file_name)
        # Only include parent directories up to project_root
        current_dir = dir_path
        while current_dir and current_dir != ".":
            parent_dir = os.path.dirname(current_dir)
            if parent_dir == current_dir:
                break
            dir_stats[parent_dir]["subdirs"].add(os.path.basename(current_dir))
            current_dir = parent_dir

    # Convert sets to sorted lists for consistent output
    for dir_path in dir_stats:
        dir_stats[dir_path]["subdirs"] = sorted(dir_stats[dir_path]["subdirs"])
        dir_stats[dir_path]["files"] = sorted(dir_stats[dir_path]["files"])

    return {
        "directory_structure": {
            dir_path: {
                "files": stats["files"],
                "subdirectories": stats["subdirs"]
            } for dir_path, stats in sorted(dir_stats.items())
        },
        "total_files": len(input_files),
        "total_directories": len(dir_stats)
    }

def render_directory_stats(directory_stats: dict) -> str:
    """Render directory stats as a markdown-formatted string optimized for AI-AI-AI communication."""
    output = ["# AI-Optimized Directory Listing", ""]
    output.append("This directory listing is designed for AI interpretability and efficient AI-AI-AI communication.")
    output.append("It provides a structured overview of directories containing the specified files.")
    output.append("")

    output.append("## Directory Structure")
    output.append("```json")
    output.append(json.dumps(directory_stats["directory_structure"], indent=2, ensure_ascii=False))
    output.append("```")
    output.append("")

    output.append("## Summary")
    output.append(f"- Total Files: {directory_stats['total_files']}")
    output.append(f"- Total Directories: {directory_stats['total_directories']}")
    output.append("")

    return "\n".join(output)

AGENT_RESULT_FIELDS = {
    "schemaVersion",
    "operation",
    "status",
    "exitCode",
    "requestSha256",
    "bundlePath",
    "bundleSha256",
    "bundleSizeBytes",
    "inventoryPath",
    "inventorySha256",
    "filesSelected",
    "filesTransformed",
    "filesExcluded",
    "errorCode",
    "message",
    "failedPath",
    "warnings",
}


def agent_error_result(
    failure: AgentFailure, request_sha256: str = ""
) -> Dict[str, Any]:
    return {
        "schemaVersion": AGENT_SCHEMA_VERSION,
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


def build_agent_success_result(
    request_sha256: str,
    artifacts: ArtifactSet,
    files: Sequence[CollectedFile],
    excluded: Sequence[ExclusionRecord],
) -> Dict[str, Any]:
    return {
        "schemaVersion": AGENT_SCHEMA_VERSION,
        "operation": "collect",
        "status": "ok",
        "exitCode": 0,
        "requestSha256": request_sha256,
        "bundlePath": artifacts.bundle_path,
        "bundleSha256": artifacts.bundle_sha256,
        "bundleSizeBytes": artifacts.bundle_size_bytes,
        "inventoryPath": artifacts.inventory_path,
        "inventorySha256": artifacts.inventory_sha256,
        "filesSelected": len(files),
        "filesTransformed": sum(file.content_mode == "transformed" for file in files),
        "filesExcluded": len(excluded),
        "errorCode": "none",
        "message": "",
        "failedPath": "",
        "warnings": [],
    }


def validate_agent_result_document(document: Dict[str, Any]) -> None:
    if type(document) is not dict or set(document) != AGENT_RESULT_FIELDS:
        raise AgentFailure("internal_error", 70, "invalid agent result shape")
    if document["status"] not in {"ok", "error"}:
        raise AgentFailure("internal_error", 70, "invalid agent result status")
    if type(document["warnings"]) is not list:
        raise AgentFailure("internal_error", 70, "invalid agent result warnings")


def execute_agent_request(request: AgentRequest) -> Tuple[Dict[str, Any], int]:
    effective_request = normalize_agent_request(request)
    request_document = agent_request_to_document(effective_request)
    request_sha256 = sha256_hex(canonical_json_bytes(request_document))
    deadline_at = time.monotonic() + effective_request.deadline_seconds
    exclusion_patterns = load_agent_exclusion_patterns(Path(effective_request.root))
    selection = select_files(effective_request, exclusion_patterns, deadline_at)
    selection = expand_agent_selection(
        effective_request,
        selection,
        exclusion_patterns,
        deadline_at,
        progress=None,
    )
    collected = collect_files(effective_request, selection, deadline_at)
    graph_bytes = render_agent_dependency_graph(
        effective_request,
        collected,
        exclusion_patterns,
        deadline_at,
    )
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
    validate_agent_result_document(result)
    return result, 0


def _argument_present(argv: Sequence[str], names: Set[str]) -> bool:
    return any(
        argument in names
        or any(argument.startswith(f"{name}=") for name in names)
        for argument in argv
    )


def build_agent_request_from_args(args: argparse.Namespace, argv: Sequence[str]) -> AgentRequest:
    for name, value in (
        ("--root", args.root),
        ("--output", args.output),
        ("--inventory", args.inventory),
    ):
        if not value:
            raise AgentFailure("invalid_request", 2, f"{name} is required in agent mode")
    if not _argument_present(argv, {"-f", "--files"}) or not args.files:
        raise AgentFailure("invalid_request", 2, "-f/--files is required in agent mode")
    if args.minify_python:
        raise AgentFailure("invalid_request", 2, "--minify-python is not supported in agent v1")
    if args.truncate:
        raise AgentFailure("invalid_request", 2, "use --auto-truncate in agent v1")
    if args.follow_symlinks:
        raise AgentFailure("invalid_request", 2, "--follow-symlinks is not supported in agent v1")
    if args.exclude or args.ignore_file or args.override_max_length:
        raise AgentFailure(
            "invalid_request",
            2,
            "use the root .collyignore file; custom exclusion and override flags are not supported in agent v1",
        )
    if args.dependency_graph and args.no_dependency_graph:
        raise AgentFailure(
            "invalid_request", 2, "--dependency-graph conflicts with --no-dependency-graph"
        )
    unsupported_features = (
        args.directory_listing
        or args.show_stats
        or args.entanglement_map
        or args.graph_input
        or args.graph_annotation_file
        or args.bra_in is not None
        or args.bra_out is not None
        or args.bra_edge is not None
        or args.bra_budget_files is not None
        or args.bra_budget_bytes is not None
        or args.bra_explain
        or args.after
        or args.before
        or args.show_min_truncation_length
    )
    if unsupported_features:
        raise AgentFailure(
            "invalid_request", 2, "unsupported legacy feature flag in agent v1"
        )
    bra_preset = "none"
    if args.bra is not None:
        if len(args.bra) == 0:
            bra_preset = "focused"
        elif len(args.bra) == 1 and args.bra[0] in AGENT_BRA_PRESETS - {"none"}:
            bra_preset = args.bra[0]
        else:
            raise AgentFailure(
                "invalid_request", 2, "agent mode accepts one named --bra preset"
            )
    document = {
        "schemaVersion": AGENT_SCHEMA_VERSION,
        "root": args.root,
        "files": list(args.files),
        "output": args.output,
        "inventory": args.inventory,
        "replace": args.replace,
        "dependencyGraph": bool(args.dependency_graph),
        "braPreset": bra_preset,
        "autoTruncate": args.auto_truncate,
        "encoding": args.encoding,
        "maxFiles": args.max_files,
        "maxSourceBytes": args.max_source_bytes,
        "maxBundleBytes": args.max_bundle_bytes,
        "maxScanEntries": args.max_scan_entries,
        "deadlineSeconds": args.deadline_seconds,
    }
    return parse_agent_request_document(document)


def emit_agent_result(document: Dict[str, Any]) -> None:
    validate_agent_result_document(document)
    sys.stdout.write(canonical_json_bytes(document).decode("utf-8"))
    sys.stdout.flush()


def reject_duplicate_json_keys(
    pairs: Sequence[Tuple[str, Any]],
) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AgentFailure("invalid_request", 2, f"duplicate field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise AgentFailure("invalid_request", 2, f"invalid JSON constant: {value}")


def load_agent_request_json(value: str) -> AgentRequest:
    if value == "-":
        raw = sys.stdin.buffer.readline(AGENT_MAX_REQUEST_BYTES + 1)
        if len(raw) > AGENT_MAX_REQUEST_BYTES:
            raise AgentFailure("invalid_request", 2, "stdin request exceeds 64 KiB")
        if not raw.endswith(b"\n"):
            raise AgentFailure(
                "invalid_request", 2, "stdin request must be LF-terminated"
            )
    else:
        request_path = Path(value)
        try:
            if request_path.stat().st_size > AGENT_MAX_REQUEST_BYTES:
                raise AgentFailure(
                    "invalid_request", 2, "request file exceeds 64 KiB", value
                )
            raw = request_path.read_bytes()
        except AgentFailure:
            raise
        except OSError as exc:
            raise AgentFailure(
                "invalid_request", 2, "request file is unavailable", value
            ) from exc
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise AgentFailure("invalid_request", 2, "request must be UTF-8", value) from exc
    try:
        document = json.loads(
            text,
            object_pairs_hook=reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except AgentFailure:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise AgentFailure("invalid_request", 2, f"invalid request JSON: {exc}") from exc
    return parse_agent_request_document(document)


AGENT_REQUEST_JSON_CONFLICTS = {
    "-f",
    "--files",
    "--root",
    "--output",
    "--inventory",
    "--replace",
    "--dependency-graph",
    "--no-dependency-graph",
    "--auto-truncate",
    "--truncate",
    "--minify-python",
    "--follow-symlinks",
    "--encoding",
    "-c",
    "--bra",
    "--bra-in",
    "--bra-out",
    "--bra-edge",
    "--bra-budget-files",
    "--bra-budget-bytes",
    "--bra-explain",
    "--max-files",
    "--max-source-bytes",
    "--max-bundle-bytes",
    "--max-scan-entries",
    "--deadline-seconds",
    "--exclude",
    "-e",
    "--ignore-file",
}


def _validate_request_json_argv(argv: Sequence[str]) -> None:
    if _argument_present(argv, AGENT_REQUEST_JSON_CONFLICTS):
        raise AgentFailure(
            "invalid_request",
            2,
            "--request-json cannot be mixed with selection or execution flags",
        )


def run_agent_mode(args: argparse.Namespace, argv: Sequence[str]) -> int:
    request_sha256 = ""
    try:
        if args.request_json:
            _validate_request_json_argv(argv)
            request = load_agent_request_json(args.request_json)
        else:
            request = build_agent_request_from_args(args, argv)
        effective = normalize_agent_request(request)
        request_sha256 = sha256_hex(
            canonical_json_bytes(agent_request_to_document(effective))
        )
        result, exit_code = execute_agent_request(effective)
    except AgentFailure as failure:
        result = agent_error_result(failure, request_sha256)
        exit_code = failure.exit_code
    except Exception as exc:
        if args.debug:
            traceback.print_exc(file=sys.stderr)
        failure = AgentFailure("internal_error", 70, str(exc))
        result = agent_error_result(failure, request_sha256)
        exit_code = 70
    emit_agent_result(result)
    return exit_code


class CollyArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, agent_mode: bool = False, **kwargs):
        self.agent_mode = agent_mode
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        if self.agent_mode:
            raise AgentFailure("invalid_request", 2, message)
        super().error(message)


def main(argv: Optional[Sequence[str]] = None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    agent_requested = "--agent" in raw_argv
    parser = CollyArgumentParser(
        agent_mode=agent_requested,
        description="Process project files into markdown with optional transformations and dependency expansion, or generate an AI-optimized directory listing.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="""
Examples of usage:

1) Basic processing with truncation and Python minification:
   python colly.py --truncate --minify-python

2) With dependency expansion:
   python colly.py -f "main.py" --bra focused

3) Multiple files and extra exclusions with overrides:
   python colly.py -f "file1.py" "file2.py" --exclude "*.log" --override-max-length "*.py:50"

4) Generate directory listing:
   python colly.py --directory-listing

5) Show statistics:
   python colly.py --show-stats

6) Show entanglement map:
   python colly.py --entanglement-map
"""
    )
    # Core arguments
    parser.add_argument('--agent', action='store_true', help="Enable deterministic machine-readable artifact mode")
    parser.add_argument('--root', help="Explicit project root for agent mode")
    parser.add_argument('--output', help="Bundle artifact path, relative to --root in agent mode")
    parser.add_argument('--inventory', help="Inventory artifact path, relative to --root in agent mode")
    parser.add_argument('--request-json', help="Read an agent request from a JSON file or '-' for stdin")
    parser.add_argument('--replace', action='store_true', help="Replace existing agent artifacts transactionally")
    parser.add_argument('-f', '--files', nargs='*', default=['**/*'], help="Files or directories to process (supports wildcards; defaults to all files in cwd recursively)")
    parser.add_argument('-e', '--exclude', nargs='*', default=[], help="Additional exclusion patterns")
    parser.add_argument('--ignore-file', action='append', default=[], help="Load exclusion patterns from a file such as .collyignore")
    parser.add_argument('-s', '--follow-symlinks', action='store_true', help="Follow symbolic links")
    parser.add_argument('-c', '--encoding', default='utf-8', help="Default encoding")
    parser.add_argument('-d', '--debug', action='store_true', help="Enable debug logging")
    parser.add_argument('-n', '--no-clip', action='store_true', help="Do not copy output to clipboard")
    parser.add_argument('-x', '--max-clip-length', type=int, default=500000, help="Maximum clipboard chunk length")
    parser.add_argument('--no-progress', action='store_true', help="Disable stderr progress updates for long-running operations")

    # Transformation arguments
    parser.add_argument('-m', '--minify-python', action='store_true', help="Minify Python files")
    parser.add_argument('-t', '--truncate', action='store_true', help="Enable dynamic truncation")
    parser.add_argument('--auto-truncate', action='store_true', help="Automatically truncate directly selected files larger than the size guard")
    parser.add_argument('-l', '--max-length', type=int, default=80, help="Max word length for truncation")
    parser.add_argument('-o', '--override-max-length', action='append', default=[], help="Pattern:length overrides (e.g., '*.py:50')")
    
    # Feature arguments
    parser.add_argument('--directory-listing', action='store_true', help="Generate AI-optimized directory listing.")
    parser.add_argument('--show-stats', action='store_true', help="Output processing statistics.")
    parser.add_argument('--entanglement-map', action='store_true', help="Render a low-resolution entanglement map.")
    parser.add_argument('--dependency-graph', action='store_true', help="Explicitly include the dependency graph in agent mode")
    parser.add_argument('--no-dependency-graph', action='store_true', help="Do not include the AI-native dependency graph.")
    parser.add_argument('--graph-input', help="Read an existing AI-native dependency graph from JSON or prior colly markdown output.")
    parser.add_argument('--graph-annotation-file', action='append', default=[], help="Apply a post-hoc graph annotation bundle JSON file. Can be repeated.")

    # Dependency expansion arguments
    parser.add_argument('-A', '--after', type=int, default=0, help="[DEPRECATED] Number of dependency levels to include (downstream)")
    parser.add_argument('-B', '--before', type=int, default=0, help="[DEPRECATED] Number of dependent levels to include (upstream)")
    parser.add_argument('-T', '--dep-type', nargs='*', default=['import'], choices=['import', 'symbol', 'call', 'imported_by', 'symbol_used_by', 'called_by'], help="Type of dependency to trace for expansion")
    parser.add_argument('--bra', nargs='*', metavar='MODE',
        help=(
            "Context expansion preset or legacy alias. "
            "Named presets: focused, review, architecture. "
            "Legacy aliases: aa, a, b, c, d, dd, e."
        )
    )
    parser.add_argument('--bra-in', type=int, help="Override inward dependency depth for BRA expansion.")
    parser.add_argument('--bra-out', type=int, help="Override outward dependency depth for BRA expansion.")
    parser.add_argument('--bra-edge', nargs='*', choices=['import', 'call', 'symbol'], help="Edge types used by BRA expansion.")
    parser.add_argument('--bra-budget-files', type=int, help="Maximum number of files BRA may add.")
    parser.add_argument('--bra-budget-bytes', type=int, help="Maximum total bytes BRA may add.")
    parser.add_argument('--bra-explain', action='store_true', help="Emit a BRA expansion manifest section.")

    # Agent execution bounds
    parser.add_argument('--max-files', type=int, default=AGENT_DEFAULT_MAX_FILES, help="Maximum files selected in agent mode")
    parser.add_argument('--max-source-bytes', type=int, default=AGENT_DEFAULT_MAX_SOURCE_BYTES, help="Maximum aggregate source bytes in agent mode")
    parser.add_argument('--max-bundle-bytes', type=int, default=AGENT_DEFAULT_MAX_BUNDLE_BYTES, help="Maximum rendered bundle bytes in agent mode")
    parser.add_argument('--max-scan-entries', type=int, default=AGENT_DEFAULT_MAX_SCAN_ENTRIES, help="Maximum filesystem entries inspected in agent mode")
    parser.add_argument('--deadline-seconds', type=int, default=AGENT_DEFAULT_DEADLINE_SECONDS, help="Monotonic execution deadline in agent mode")
    
    # Verbosity and meta arguments
    parser.add_argument('-v', '--verbose', action='store_true', help="Show verbose output (info-level logs)")
    parser.add_argument('-q', '--show-min-truncation-length', action='store_true', help="Only output the minimal truncation length and exit")

    try:
        args = parser.parse_args(raw_argv)
    except AgentFailure as failure:
        emit_agent_result(agent_error_result(failure))
        return failure.exit_code
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    if args.agent:
        return run_agent_mode(args, raw_argv)

    args.progress_reporter = ProgressReporter(enabled=should_enable_progress(args))

    start_time = time.time()

    if args.graph_input:
        try:
            args.progress_reporter.update("Loading graph input", force=True)
            with open(args.graph_input, 'r', encoding='utf-8', errors='replace') as handle:
                graph_content = handle.read()
            graph_data = parse_ai_native_dependency_graph_input(graph_content)
        except FileNotFoundError:
            parser.error(f"Graph input not found: {args.graph_input}")
        except (json.JSONDecodeError, ValueError) as exc:
            parser.error(f"Invalid graph input: {exc}")

        annotation_entries = []
        for annotation_path in args.graph_annotation_file:
            try:
                annotation_entries.extend(load_graph_annotation_bundle(annotation_path))
            except FileNotFoundError:
                parser.error(f"Graph annotation bundle not found: {annotation_path}")
            except json.JSONDecodeError as exc:
                parser.error(f"Invalid graph annotation bundle JSON in {annotation_path}: {exc}")

        graph_data = apply_graph_annotation_entries(graph_data, annotation_entries)
        result = render_ai_native_dependency_graph_markdown(graph_data)

        if not args.no_clip:
            args.progress_reporter.update("Copying output to clipboard", force=True)
            copy_to_clipboard(result, args.max_clip_length)
            logging.info("Output copied to clipboard.")
        else:
            args.progress_reporter.update("Writing output", force=True)
            print(result)

        end_time = time.time()
        args.progress_reporter.finish(f"Completed in {end_time - start_time:.2f}s")
        logging.info(f"Processing completed in {end_time - start_time:.2f} seconds.")
        return

    try:
        ignore_file_paths = get_ignore_file_paths(os.getcwd(), args.ignore_file)
    except FileNotFoundError as exc:
        parser.error(f"Ignore file not found: {exc.filename or exc}")

    ignore_patterns = load_ignore_patterns(ignore_file_paths)
    args.exclude = ignore_patterns + args.exclude
    combined_exclusions = default_exclusions + args.exclude
    exclusion_patterns = compile_exclusion_patterns(combined_exclusions)
    overrides = parse_override_max_length(args.override_max_length)
    forced_files = get_forced_includes(args.files)
    graph_annotation_entries = []
    for annotation_path in args.graph_annotation_file:
        try:
            graph_annotation_entries.extend(load_graph_annotation_bundle(annotation_path))
        except FileNotFoundError:
            parser.error(f"Graph annotation bundle not found: {annotation_path}")
        except json.JSONDecodeError as exc:
            parser.error(f"Invalid graph annotation bundle JSON in {annotation_path}: {exc}")

    args.progress_reporter.update("Starting scan", force=True)
    input_files = get_input_files(
        args.files, exclusion_patterns, args.follow_symlinks, forced_files, progress=args.progress_reporter
    )

    if not input_files:
        args.progress_reporter.finish()
        logging.error("No files matched the provided patterns.")
        sys.exit(1)

    # --- Project Root Calculation ---
    def get_ast_roots(py_files):
        roots = set()
        for f in py_files:
            try:
                with open(f, 'r', encoding=args.encoding, errors='replace') as src:
                    tree = ast.parse(src.read(), filename=f)
                for node in tree.body:
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        roots.add(os.path.dirname(f))
                        break
            except Exception:
                continue
        return roots

    supported_exts = {e for e in language_identifier.keys() if e.startswith('.')}
    supported_files = {
        f for f in input_files
        if f in forced_files or os.path.splitext(f)[1].lower() in supported_exts
    }
    ast_roots = get_ast_roots({f for f in supported_files if f.endswith('.py')})

    cwd = os.path.abspath(os.getcwd())
    if ast_roots:
        candidate_root = os.path.commonpath(list(ast_roots)) if ast_roots else cwd
    else:
        candidate_root = os.path.commonpath(list(input_files)) if input_files else cwd
    
    candidate_root_abs = os.path.abspath(candidate_root)
    project_root = candidate_root_abs if candidate_root_abs.startswith(cwd) else cwd
    if not os.path.isdir(project_root):
        project_root = os.path.dirname(project_root)

    # --- File Set Calculation ---
    all_supported_files = get_all_py_files(
        project_root, exclusion_patterns, args.follow_symlinks, progress=args.progress_reporter
    )
    
    try:
        expander = DependencyExpander(args, project_root, all_supported_files)
        args.progress_reporter.update("Resolving dependency expansion", force=True)
        final_files, bra_manifest = expander.expand(supported_files)
    except ValueError as exc:
        args.progress_reporter.finish()
        parser.error(str(exc))
    final_files_list = sorted(list(final_files))
    final_supported_files = {f for f in final_files if os.path.splitext(f)[1].lower() in supported_exts}

    # --- Content Generation ---
    output_parts = []

    # --- Feature Rendering ---
    if args.directory_listing:
        lister = DirectoryLister(args, project_root, all_supported_files)
        output_parts.append(lister.render(final_files))

    if not args.no_dependency_graph:
        grapher = AINativeDependencyGraph(args, project_root, all_supported_files)
        graph_data = grapher.build_graph_data(final_supported_files)
        if graph_data:
            graph_data = apply_graph_annotation_entries(graph_data, graph_annotation_entries)
            output_parts.append(render_ai_native_dependency_graph_markdown(graph_data))

    if args.entanglement_map:
        entangler = EntanglementMap(args, project_root, all_supported_files)
        output_parts.append(entangler.render(final_supported_files))

    if args.show_stats:
        stats_gen = StatsGenerator(args, project_root, all_supported_files)
        output_parts.append(stats_gen.render(final_files_list))

    if bra_manifest and args.bra_explain:
        output_parts.append(render_bra_expansion_manifest(bra_manifest, project_root))

    # --- Main File Content Processing ---
    # This part is now separate from stats generation
    if not (args.directory_listing or args.show_stats or args.entanglement_map):
        unique_words = collect_unique_words(
            final_files_list, exclusion_patterns, args.encoding, forced_files
        ) if (args.truncate or args.show_min_truncation_length) else set()

        if args.show_min_truncation_length:
            min_trunc_length = find_min_truncation_length(unique_words, args.max_length)
            print(f"Minimal truncation length: {min_trunc_length}" if min_trunc_length else "No suitable minimal truncation length found.")
            sys.exit(0)

        file_content_output, _ = process_files(
            final_files_list, exclusion_patterns, args.follow_symlinks, args.encoding,
            args.minify_python, args.truncate, args.max_length, overrides,
            unique_words, args.verbose, False, supported_files, forced_files,
            args.auto_truncate, args.progress_reporter # show_stats is handled separately
        )
        if file_content_output.strip():
            output_parts.append(file_content_output)

    result = "\n".join(output_parts)

    if not args.no_clip:
        args.progress_reporter.update("Copying output to clipboard", force=True)
        copy_to_clipboard(result, args.max_clip_length)
        logging.info("Output copied to clipboard.")
    else:
        args.progress_reporter.update("Writing output", force=True)
        print(result)

    end_time = time.time()
    args.progress_reporter.finish(f"Completed in {end_time - start_time:.2f}s")
    logging.info(f"Processing completed in {end_time - start_time:.2f} seconds")
    sys.exit(0)


if __name__ == "__main__":
    sys.exit(main())
