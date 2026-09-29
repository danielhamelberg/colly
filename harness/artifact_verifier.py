"""Independent, bounded verification of Colly artifacts and retained evidence.

This module never imports the collector. It checks concrete bytes rather than
trusting a successful status or self-reported hashes. Evidence is portable data,
not executable paths. The host/capturer and nonconcurrent workspace are trusted.
Graph semantics and lossy transformations are not certified by this verifier.
"""
from __future__ import annotations

import base64
import codecs
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

VERSION = "colly-artifacts-v2"
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_INVENTORY_BYTES = 2 * 1024 * 1024
LIMIT_KEYS = ("maxFiles", "maxSourceBytes", "maxBundleBytes", "maxScanEntries", "deadlineSeconds")
SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def load_json(data: bytes) -> Any:
    def bad_constant(value):
        raise ValueError("nonfinite JSON number")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                      parse_constant=bad_constant)


def relative_path(value: Any) -> str:
    """Validate canonical relative paths independently of the host OS."""
    if type(value) is not str or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("noncanonical relative path")
    return value


def _schema(value: Any, schema: Mapping[str, Any], label: str) -> None:
    types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    kind = schema.get("type")
    if kind not in types or type(value) is not types[kind]:
        raise ValueError(f"{label}:type")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{label}:const")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label}:enum")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{label}:required")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(f"{label}:additional-field")
        for key in value.keys() & props.keys():
            _schema(value[key], props[key], f"{label}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{label}:minItems")
        for item in value:
            _schema(item, schema["items"], label + "[]")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10**9):
            raise ValueError(f"{label}:length")
    elif kind == "integer" and value < schema.get("minimum", -10**20):
        raise ValueError(f"{label}:minimum")


def validate_schema(value: Any, name: str) -> None:
    _schema(value, load_json((SCHEMAS / name).read_bytes()), name)


def decoded_source(data: bytes, default: str) -> tuple[bytes, str]:
    choices = ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
               (codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16-le"),
               (codecs.BOM_UTF16_BE, "utf-16-be"))
    encoding = next((enc for marker, enc in choices if data.startswith(marker)), codecs.lookup(default).name)
    text = data.decode(encoding, errors="strict")
    if text.startswith("\ufeff"):
        text = text[1:]
    if "\x00" in text:
        raise ValueError("binary source")
    return text.encode("utf-8"), encoding


def _file_section(path: str, body: bytes, language: str) -> bytes:
    text = body.decode("utf-8")
    longest = max((len(x) for x in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    ending = "" if text.endswith(("\n", "\r")) else "\n"
    return (f"## File: {json.dumps(path, ensure_ascii=False)}\n{fence}{language}\n" + text + ending + fence + "\n").encode("utf-8")


def normalized_receipt_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the documented portable request form without filesystem access.

    The host supplies an absolute canonical root and root-relative selectors.
    General collector path resolution is intentionally not duplicated here.
    """
    value = dict(request)
    value["root"] = value["root"].replace("\\", "/")
    for key in ("output", "inventory"):
        value[key] = value[key].replace("\\", "/")
        relative_path(value[key])
    value["files"] = sorted(set(item.replace("\\", "/") for item in value["files"]),
                             key=lambda item: item.encode("utf-8"))
    return value


def verify_artifact_bytes(request: Mapping[str, Any], result: Mapping[str, Any],
                          sources: Mapping[str, bytes], artifacts: Mapping[str, bytes],
                          expected_paths: Sequence[str] | None = None) -> list[str]:
    """Validate successful results. Caller supplies source bytes from trusted capture."""
    try:
        validate_schema(dict(request), "colly-agent-request.schema.json")
        request = normalized_receipt_request(request)
        validate_schema(dict(result), "colly-agent-result.schema.json")
        if result["status"] != "ok" or result["exitCode"] != 0 or result["errorCode"] != "none":
            raise ValueError("success-status")
        output, inventory_path = (relative_path(request[k]) for k in ("output", "inventory"))
        if output == inventory_path or result["bundlePath"] != output or result["inventoryPath"] != inventory_path:
            raise ValueError("artifact-path-binding")
        bundle, raw_inventory = artifacts[output], artifacts[inventory_path]
        if len(bundle) > request["maxBundleBytes"] or len(raw_inventory) > MAX_INVENTORY_BYTES:
            raise ValueError("artifact-budget")
        request_hash = sha(canonical(dict(request)))
        if result["requestSha256"] != request_hash:
            raise ValueError("request-binding")
        if (result["bundleSha256"] != sha(bundle) or result["bundleSizeBytes"] != len(bundle)
                or result["inventorySha256"] != sha(raw_inventory)):
            raise ValueError("artifact-digest-or-size")
        inventory = load_json(raw_inventory)
        validate_schema(inventory, "colly-inventory.schema.json")
        if canonical(inventory) != raw_inventory:
            raise ValueError("inventory-not-canonical")
        if inventory["root"] != request["root"] or inventory["requestSha256"] != request_hash:
            raise ValueError("inventory-request-binding")
        if inventory["bundle"] != {"path": output, "sha256": sha(bundle), "sizeBytes": len(bundle)}:
            raise ValueError("inventory-bundle-binding")
        if inventory["limits"] != {k: request[k] for k in LIMIT_KEYS}:
            raise ValueError("inventory-limits")
        records = inventory["files"]
        paths = [relative_path(item["relativePath"]) for item in records]
        if not paths or len(set(paths)) != len(paths) or paths != sorted(paths, key=lambda p: p.encode("utf-8")):
            raise ValueError("inventory-file-identity-order")
        if len(paths) > request["maxFiles"] or result["filesSelected"] != len(paths):
            raise ValueError("file-count")
        if expected_paths is not None and set(paths) != set(expected_paths):
            raise ValueError("selection-does-not-match-expected")
        # An exact file which existed before execution must never disappear from selection.
        for selector in request["files"]:
            if selector in sources and selector not in paths:
                raise ValueError("requested-source-missing")
        if result["filesExcluded"] != len(inventory["excluded"]):
            raise ValueError("excluded-count")
        sections = []
        source_bytes = 0
        for item in records:
            path = item["relativePath"]
            if path in (output, inventory_path):
                raise ValueError("artifact-source-collision")
            data = sources[path]
            source_bytes += len(data)
            if item["sourceSha256"] != sha(data) or item["sourceSizeBytes"] != len(data):
                raise ValueError("source-digest-or-size")
            body, encoding = decoded_source(data, request["encoding"])
            if item["encoding"] != encoding:
                raise ValueError("source-encoding")
            if item["contentMode"] != "verbatim" or item["transformations"]:
                raise ValueError("transformed-content-needs-separate-verifier")
            if item["renderedSha256"] != sha(body) or item["renderedSizeBytes"] != len(body):
                raise ValueError("rendered-content-does-not-match-source")
            if not re.fullmatch(r"[A-Za-z0-9_+-]+", item["language"]):
                raise ValueError("invalid-language")
            sections.append(_file_section(path, body, item["language"]))
        if source_bytes > request["maxSourceBytes"] or result["filesTransformed"] != 0:
            raise ValueError("source-budget-or-transformed-count")
        expected = b"\n".join(sections)
        if request["dependencyGraph"]:
            # Validate graph envelope; do not certify its semantic edges as causal truth.
            marker = b"# AI-Native Dependency Graphs\n```json\n"
            if bundle.startswith(marker):
                end = bundle.find(b"\n```\n", len(marker))
                if end == -1 or type(load_json(bundle[len(marker):end])) is not dict:
                    raise ValueError("graph-envelope")
                bundle = bundle[end + len(b"\n```\n"):]
                if bundle.startswith(b"\n"):
                    bundle = bundle[1:]
        if bundle != expected:
            raise ValueError("bundle-source-content-or-structure")
        return []
    except (ValueError, TypeError, KeyError, LookupError, UnicodeError, OSError, OverflowError) as exc:
        return [f"artifact-invalid:{exc.args[0] if exc.args else type(exc).__name__}"]


def verify_transition(request: Mapping[str, Any], result: Mapping[str, Any],
                      before: Mapping[str, bytes], after: Mapping[str, bytes],
                      exit_code: int, expected_paths: Sequence[str] | None = None) -> list[str]:
    """Check exit/status agreement, all file changes and artifact bytes independently."""
    failures = []
    try:
        validate_schema(dict(result), "colly-agent-result.schema.json")
    except (ValueError, TypeError, KeyError) as exc:
        return [f"result-invalid:{exc}"]
    if type(exit_code) is not int or exit_code != result["exitCode"]:
        failures.append("process-exit-mismatch")
    if (result["status"] == "ok") != (exit_code == 0):
        failures.append("process-status-mismatch")
    if result["status"] == "error":
        if result["errorCode"] == "none":
            failures.append("error-without-code")
        if dict(before) != dict(after):
            failures.append("workspace-changed-on-error")
        return failures
    try:
        declared = {relative_path(request[k]) for k in ("output", "inventory")}
    except (ValueError, KeyError, TypeError):
        return failures + ["declared-path-invalid"]
    for path in set(before) | set(after):
        if path not in declared and before.get(path) != after.get(path):
            failures.append(f"undeclared-change:{path}")
    if not request.get("replace") and declared & set(before):
        failures.append("replacement-not-authorized")
    failures.extend(verify_artifact_bytes(request, result, before, after, expected_paths))
    return failures


def snapshot_bytes(root: Path, max_bytes: int = MAX_EVIDENCE_BYTES,
                   max_entries: int = 10000) -> dict[str, bytes]:
    """Bounded no-symlink capture for a trusted, nonconcurrent fixture workspace."""
    root = root.resolve(strict=True)
    result, total, seen = {}, 0, 0
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            seen += 1
            if seen > max_entries:
                raise ValueError("evidence-entry-budget")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError("evidence-symlink")
            if stat.S_ISDIR(mode):
                pending.append(path)
            elif stat.S_ISREG(mode):
                if total + path.stat().st_size > max_bytes:
                    raise ValueError("evidence-byte-budget")
                with path.open("rb") as stream:
                    data = stream.read(max_bytes - total + 1)
                total += len(data)
                if total > max_bytes:
                    raise ValueError("evidence-byte-budget")
                result[path.relative_to(root).as_posix()] = data
            else:
                raise ValueError("evidence-special-file")
    return result


def make_evidence(request: Mapping[str, Any], result: Mapping[str, Any],
                  before: Mapping[str, bytes], after: Mapping[str, bytes],
                  exit_code: int) -> dict[str, Any]:
    if sum(map(len, before.values())) + sum(map(len, after.values())) > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence-byte-budget")
    def encode(files):
        return {relative_path(path): {"sha256": sha(data), "bytesBase64": base64.b64encode(data).decode("ascii")}
                for path, data in sorted(files.items())}
    return {"version": VERSION, "request": dict(request), "result": dict(result),
            "exitCode": exit_code, "before": encode(before), "after": encode(after)}


def unpack_evidence(evidence: Any) -> tuple[dict[str, bytes], dict[str, bytes]]:
    if type(evidence) is not dict or set(evidence) != {"version", "request", "result", "exitCode", "before", "after"}:
        raise ValueError("evidence-shape")
    if evidence["version"] != VERSION:
        raise ValueError("evidence-version")
    total = 0
    decoded = []
    for name in ("before", "after"):
        value = evidence[name]
        if type(value) is not dict or len(value) > 10000:
            raise ValueError("evidence-files")
        files = {}
        for path, item in value.items():
            relative_path(path)
            if type(item) is not dict or set(item) != {"sha256", "bytesBase64"} or type(item["bytesBase64"]) is not str:
                raise ValueError("evidence-blob-shape")
            if len(item["bytesBase64"]) > 4 * (MAX_EVIDENCE_BYTES - total + 2) // 3:
                raise ValueError("evidence-byte-budget")
            data = base64.b64decode(item["bytesBase64"], validate=True)
            total += len(data)
            if total > MAX_EVIDENCE_BYTES or item["sha256"] != sha(data):
                raise ValueError("evidence-digest-or-budget")
            files[path] = data
        decoded.append(files)
    return decoded[0], decoded[1]


def expected_fixture_before(case: Mapping[str, Any]) -> dict[str, bytes]:
    fixture = case["fixture"]
    expected = {p: t.encode("utf-8") for p, t in fixture.get("textFiles", {}).items()}
    expected.update({p: base64.b64decode(t, validate=True) for p, t in fixture.get("binaryFiles", {}).items()})
    if fixture.get("existingArtifacts"):
        expected.update({"audit/bundle.md": b"stale bundle\n", "audit/bundle.inventory.json": b'{"stale":true}\n'})
    return expected


# Independently enumerated outcomes for the unchanged frozen v1 fixtures.
# Changes to this oracle, like verifier changes, require separate review.
EXPECTED_PATHS = {
    "main-exact": ["a.py"], "main-unknown-extension": ["requirements.lock"],
    "main-two-files": ["a.py", "b.py"], "main-glob": ["src/a.py", "src/b.py"],
    "main-directory": ["src/a.py", "src/readme.md"], "main-graph": ["dep.py", "main.py"],
    "main-focused-expansion": ["dep.py", "main.py"], "main-replace-authorized": ["a.py"],
    "adv-markdown-fence": ["notes.md"], "adv-content-instruction": ["prompt.txt"],
    "stopping-success": ["a.py"],
}
