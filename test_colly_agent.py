import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from colly import (
    AGENT_REQUEST_FIELDS,
    AgentFailure,
    build_agent_inventory,
    canonical_json_bytes,
    collect_files,
    expand_agent_selection,
    load_agent_exclusion_patterns,
    normalize_agent_request,
    parse_agent_request_document,
    render_agent_file,
    render_agent_dependency_graph,
    render_bundle,
    select_files,
    sha256_hex,
    write_agent_artifacts,
)


REPO_ROOT = Path(__file__).parent
SCRIPT_PATH = REPO_ROOT / "colly.py"


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


class AgentRequestTests(unittest.TestCase):
    def test_parse_agent_request_accepts_complete_v1_document(self):
        request = parse_agent_request_document(VALID_REQUEST)

        self.assertEqual(request.files, ("src/*.py", "requirements.lock"))
        self.assertEqual(request.bra_preset, "none")
        self.assertEqual(request.encoding, "utf-8")

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


class AgentSchemaTests(unittest.TestCase):
    def load_schema(self, name):
        path = REPO_ROOT / "schemas" / name
        return json.loads(path.read_text(encoding="utf-8"))

    def test_request_schema_matches_runtime_fields(self):
        schema = self.load_schema("colly-agent-request.schema.json")

        self.assertEqual(set(schema["required"]), AGENT_REQUEST_FIELDS)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], 1)

    def test_result_schema_has_stable_required_fields(self):
        schema = self.load_schema("colly-agent-result.schema.json")

        self.assertFalse(schema["additionalProperties"])
        self.assertIn("requestSha256", schema["required"])
        self.assertIn("errorCode", schema["required"])
        self.assertIn("warnings", schema["required"])

    def test_inventory_schema_requires_provenance_fields(self):
        schema = self.load_schema("colly-inventory.schema.json")
        item = schema["properties"]["files"]["items"]

        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(item["additionalProperties"])
        self.assertTrue(
            {
                "sourceSha256",
                "renderedSha256",
                "selectionReasons",
                "contentMode",
                "transformations",
            }.issubset(item["required"])
        )


class AgentFilesystemTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=REPO_ROOT, prefix="agent_case_")
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "repo"
        self.root.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def request(self, files, **overrides):
        document = dict(VALID_REQUEST)
        document.update(
            root=self.root.as_posix(),
            files=list(files),
            output="audit/bundle.md",
            inventory="audit/bundle.inventory.json",
        )
        document.update(overrides)
        return normalize_agent_request(parse_agent_request_document(document))

    def select(self, request):
        exclusions = load_agent_exclusion_patterns(Path(request.root))
        return select_files(request, exclusions, time.monotonic() + 30)


class AgentSelectionTests(AgentFilesystemTestCase):
    def test_overlapping_selectors_merge_selection_reasons(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")
        result = self.select(self.request(["*.py", "a.py"]))
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0].selection_reasons, ("explicit-file", "glob-match"))

    def test_glob_accounts_for_nonmatching_entries(self):
        for index in range(4):
            (self.root / f"item{index}.unknown").write_text("x", encoding="utf-8")
        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["*.py"], maxScanEntries=2))
        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_recursive_glob_does_not_enter_excluded_directories(self):
        ignored = self.root / "node_modules"
        ignored.mkdir()
        (ignored / "hidden.py").write_text("SECRET", encoding="utf-8")
        (self.root / "a.py").write_text("A = 1", encoding="utf-8")
        real_scandir = os.scandir

        def guarded_scandir(path):
            if Path(path) == ignored:
                self.fail("glob traversed an excluded directory")
            return real_scandir(path)

        with patch("colly.os.scandir", side_effect=guarded_scandir):
            result = self.select(self.request(["**/*.py"]))
        self.assertEqual([item.relative_path for item in result.files], ["a.py"])

    def test_ignore_file_symlink_is_rejected_before_reading(self):
        (self.root / ".collyignore").write_text("*.py", encoding="utf-8")
        real_is_symlink = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path.name == ".collyignore" or real_is_symlink(path)):
            with self.assertRaises(AgentFailure) as caught:
                load_agent_exclusion_patterns(self.root)
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_output_symlink_is_rejected_before_normalization(self):
        # Mock the lexical check so this gate also runs on Windows without symlink privileges.
        real_is_symlink = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path.name == "linked.md" or real_is_symlink(path)):
            with self.assertRaises(AgentFailure) as caught:
                self.request(["a.py"], output="linked.md")
        self.assertEqual(caught.exception.code, "invalid_request")

    def test_exact_unknown_extension_bypasses_extension_and_ignore_filters(self):
        (self.root / "requirements.lock").write_text("package==1\n", encoding="utf-8")
        (self.root / ".collyignore").write_text("*.lock\n", encoding="utf-8")

        result = self.select(self.request(["requirements.lock"]))

        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0].relative_path, "requirements.lock")
        self.assertEqual(result.files[0].selection_reasons, ("explicit-file",))

    def test_directory_scan_filters_unknown_extensions_and_records_exclusion(self):
        src = self.root / "src"
        src.mkdir()
        (src / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (src / "requirements.lock").write_text("package==1\n", encoding="utf-8")

        result = self.select(self.request(["src"]))

        self.assertEqual([item.relative_path for item in result.files], ["src/app.py"])
        self.assertIn(
            ("src/requirements.lock", "unsupported-extension", False),
            [(item.relative_path, item.reason, item.requested) for item in result.excluded],
        )

    def test_glob_results_are_sorted_by_utf8_relative_path(self):
        (self.root / "b.py").write_text("B = 1\n", encoding="utf-8")
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")

        result = self.select(self.request(["*.py"]))

        self.assertEqual([item.relative_path for item in result.files], ["a.py", "b.py"])
        self.assertTrue(all(item.selection_reasons == ("glob-match",) for item in result.files))

    def test_missing_exact_selector_fails(self):
        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["missing.py"]))

        self.assertEqual(caught.exception.code, "requested_file_missing")
        self.assertEqual(caught.exception.exit_code, 3)

    def test_zero_match_glob_fails(self):
        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["missing-*.py"]))

        self.assertEqual(caught.exception.code, "selector_no_match")

    def test_parent_traversal_fails_during_normalization(self):
        (self.base / "outside.py").write_text("OUTSIDE = True\n", encoding="utf-8")

        with self.assertRaises(AgentFailure) as caught:
            self.request(["../outside.py"])

        self.assertEqual(caught.exception.code, "path_outside_root")

    def test_file_count_limit_fails_closed(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")
        (self.root / "b.py").write_text("B = 1\n", encoding="utf-8")

        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["*.py"], maxFiles=1))

        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_source_byte_limit_fails_closed(self):
        (self.root / "a.py").write_text("A = 'too large'\n", encoding="utf-8")

        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["a.py"], maxSourceBytes=1))

        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_past_deadline_fails_closed(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")
        request = self.request(["a.py"])
        exclusions = load_agent_exclusion_patterns(Path(request.root))

        with self.assertRaises(AgentFailure) as caught:
            select_files(request, exclusions, time.monotonic() - 1)

        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_artifact_cannot_be_selected_as_source(self):
        audit = self.root / "audit"
        audit.mkdir()
        (audit / "bundle.md").write_text("old\n", encoding="utf-8")

        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["audit/bundle.md"]))

        self.assertEqual(caught.exception.code, "artifact_source_conflict")

    def test_symlink_is_not_allowed_in_agent_v1(self):
        outside = self.base / "outside.py"
        outside.write_text("OUTSIDE = True\n", encoding="utf-8")
        link = self.root / "link.py"
        try:
            os.symlink(outside, link)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        with self.assertRaises(AgentFailure) as caught:
            self.select(self.request(["link.py"]))

        self.assertIn(caught.exception.code, {"invalid_request", "path_outside_root"})


class AgentExpansionTests(AgentFilesystemTestCase):
    def setUp(self):
        super().setUp()
        (self.root / "main.py").write_text("import dep\nVALUE = dep.VALUE\n", encoding="utf-8")
        (self.root / "dep.py").write_text("import deep\nVALUE = deep.VALUE\n", encoding="utf-8")
        (self.root / "deep.py").write_text("VALUE = 'deep'\n", encoding="utf-8")

    def expanded(self, preset, **overrides):
        request = self.request(["main.py"], braPreset=preset, **overrides)
        exclusions = load_agent_exclusion_patterns(Path(request.root))
        selected = select_files(request, exclusions, time.monotonic() + 30)
        return expand_agent_selection(
            request, selected, exclusions, time.monotonic() + 30
        )

    def test_none_keeps_only_seed(self):
        result = self.expanded("none")

        self.assertEqual([item.relative_path for item in result.files], ["main.py"])

    def test_focused_adds_one_hop_import_with_reason(self):
        result = self.expanded("focused")
        by_path = {item.relative_path: item for item in result.files}

        self.assertEqual(list(by_path), ["dep.py", "main.py"])
        self.assertEqual(by_path["dep.py"].selection_reasons, ("dependency-expansion",))
        self.assertNotIn("deep.py", by_path)

    def test_expansion_obeys_global_file_limit(self):
        with self.assertRaises(AgentFailure) as caught:
            self.expanded("focused", maxFiles=1)

        self.assertEqual(caught.exception.code, "limit_exceeded")


class AgentCollectionTests(AgentFilesystemTestCase):
    def collected(self, relative_path, data, **overrides):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        request = self.request([relative_path], **overrides)
        selection = self.select(request)
        return request, selection, collect_files(
            request, selection, time.monotonic() + 30
        )

    def test_utf8_verbatim_has_matching_source_and_rendered_hashes(self):
        _request, _selection, files = self.collected("a.py", b"print('ok')\n")
        file = files[0]

        self.assertEqual(file.content_mode, "verbatim")
        self.assertEqual(file.source_sha256, file.rendered_sha256)
        self.assertEqual(file.rendered_text, "print('ok')\n")
        self.assertEqual(file.encoding, "utf-8")

    def test_utf8_bom_is_decoded_strictly(self):
        _request, _selection, files = self.collected(
            "bom.txt", b"\xef\xbb\xbfhello\n"
        )

        self.assertEqual(files[0].encoding, "utf-8-sig")
        self.assertEqual(files[0].rendered_text, "hello\n")

    def test_invalid_utf8_fails_without_replacement(self):
        path = self.root / "bad.txt"
        path.write_bytes(b"\x80not-utf8")
        request = self.request(["bad.txt"])
        selection = self.select(request)

        with self.assertRaises(AgentFailure) as caught:
            collect_files(request, selection, time.monotonic() + 30)

        self.assertEqual(caught.exception.code, "decode_failed")

    def test_binary_input_fails(self):
        path = self.root / "binary.dat"
        path.write_bytes(b"ABC\x00DEF")
        request = self.request(["binary.dat"])
        selection = self.select(request)

        with self.assertRaises(AgentFailure) as caught:
            collect_files(request, selection, time.monotonic() + 30)

        self.assertEqual(caught.exception.code, "binary_input")

    def test_empty_text_is_collected(self):
        _request, _selection, files = self.collected("empty.txt", b"")

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].rendered_text, "")

    def test_unknown_explicit_text_uses_plaintext(self):
        _request, _selection, files = self.collected("settings.custom", b"key=value\n")

        self.assertEqual(files[0].language, "plaintext")

    def test_auto_truncate_records_transformation(self):
        data = (("A" * 300 + "\n") * 40).encode("utf-8")
        _request, _selection, files = self.collected(
            "large.txt", data, autoTruncate=True
        )
        file = files[0]

        self.assertEqual(file.content_mode, "transformed")
        self.assertEqual(len(file.transformations), 1)
        self.assertEqual(file.transformations[0].kind, "auto-truncate")
        self.assertNotEqual(file.source_sha256, file.rendered_sha256)
        self.assertLessEqual(file.rendered_size_bytes, 6400)

    def test_file_growth_after_selection_reapplies_source_budget(self):
        path = self.root / "growing.txt"
        path.write_text("small\n", encoding="utf-8")
        request = self.request(["growing.txt"], maxSourceBytes=10)
        selection = self.select(request)
        path.write_text("now this is too large\n", encoding="utf-8")

        with self.assertRaises(AgentFailure) as caught:
            collect_files(request, selection, time.monotonic() + 30)

        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_disappearing_expanded_file_fails(self):
        (self.root / "main.py").write_text("import dep\n", encoding="utf-8")
        dependency = self.root / "dep.py"
        dependency.write_text("VALUE = 1\n", encoding="utf-8")
        request = self.request(["main.py"], braPreset="focused")
        exclusions = load_agent_exclusion_patterns(Path(request.root))
        selection = select_files(request, exclusions, time.monotonic() + 30)
        selection = expand_agent_selection(
            request, selection, exclusions, time.monotonic() + 30
        )
        dependency.unlink()

        with self.assertRaises(AgentFailure) as caught:
            collect_files(request, selection, time.monotonic() + 30)

        self.assertEqual(caught.exception.code, "requested_file_missing")

    def test_unreadable_file_fails(self):
        path = self.root / "blocked.txt"
        path.write_text("secret\n", encoding="utf-8")
        request = self.request(["blocked.txt"])
        selection = self.select(request)

        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            with self.assertRaises(AgentFailure) as caught:
                collect_files(request, selection, time.monotonic() + 30)

        self.assertEqual(caught.exception.code, "decode_failed")


class AgentRenderingTests(AgentFilesystemTestCase):
    def prepare(self, name="a.py", data=b"print('ok')\n", **overrides):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        request = self.request([name], **overrides)
        selection = self.select(request)
        files = collect_files(request, selection, time.monotonic() + 30)
        return request, selection, files

    def test_bundle_is_byte_deterministic(self):
        request, _selection, files = self.prepare()

        first = render_bundle(request, files, b"", time.monotonic() + 30)
        second = render_bundle(request, files, b"", time.monotonic() + 30)

        self.assertEqual(first, second)

    def test_dynamic_fence_is_longer_than_source_backticks(self):
        request, _selection, files = self.prepare("notes.md", b"before\n```\nafter\n")

        rendered = render_agent_file(files[0]).decode("utf-8")

        self.assertIn("````markdown\n", rendered)
        self.assertTrue(rendered.endswith("````\n"))

    def test_heading_json_quotes_control_characters(self):
        _request, _selection, files = self.prepare()
        synthetic = replace(files[0], relative_path="unsafe\n# injected.py")

        rendered = render_agent_file(synthetic).decode("utf-8")

        self.assertTrue(rendered.startswith('## File: "unsafe\\n# injected.py"\n'))
        self.assertNotIn("\n# injected.py\n", rendered)

    def test_bundle_limit_fails_before_writing(self):
        request, _selection, files = self.prepare(maxBundleBytes=10)

        with self.assertRaises(AgentFailure) as caught:
            render_bundle(request, files, b"", time.monotonic() + 30)

        self.assertEqual(caught.exception.code, "limit_exceeded")

    def test_explicit_graph_bytes_precede_file_sections(self):
        request, _selection, files = self.prepare()
        graph = b"# AI-Native Dependency Graphs\n{}\n"

        bundle = render_bundle(request, files, graph, time.monotonic() + 30)

        self.assertTrue(bundle.startswith(graph))
        self.assertGreater(bundle.find(b"## File:"), len(graph) - 1)

    def test_dependency_graph_is_explicit_and_deterministic(self):
        (self.root / "main.py").write_text("import dep\n", encoding="utf-8")
        (self.root / "dep.py").write_text("VALUE = 1\n", encoding="utf-8")
        request = self.request(
            ["main.py", "dep.py"], dependencyGraph=True
        )
        exclusions = load_agent_exclusion_patterns(Path(request.root))
        selection = select_files(request, exclusions, time.monotonic() + 30)
        files = collect_files(request, selection, time.monotonic() + 30)

        first = render_agent_dependency_graph(
            request, files, exclusions, time.monotonic() + 30
        )
        second = render_agent_dependency_graph(
            request, files, exclusions, time.monotonic() + 30
        )

        self.assertEqual(first, second)
        self.assertIn(b"# AI-Native Dependency Graphs", first)
        self.assertIn(b'"type":"imports"', first)


class AgentInventoryTests(AgentFilesystemTestCase):
    def test_inventory_contains_hashes_reasons_and_no_volatile_fields(self):
        path = self.root / "requirements.lock"
        path.write_text("package==1\n", encoding="utf-8")
        request = self.request(["requirements.lock"])
        selection = self.select(request)
        files = collect_files(request, selection, time.monotonic() + 30)
        bundle = render_bundle(request, files, b"", time.monotonic() + 30)
        request_sha256 = sha256_hex(canonical_json_bytes({
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
        }))

        encoded = build_agent_inventory(
            request, request_sha256, bundle, files, selection.excluded
        )
        inventory = json.loads(encoded)

        self.assertEqual(inventory["bundle"]["sha256"], sha256_hex(bundle))
        self.assertEqual(inventory["files"][0]["sourceSha256"], files[0].source_sha256)
        self.assertEqual(inventory["files"][0]["renderedSha256"], files[0].rendered_sha256)
        self.assertEqual(inventory["files"][0]["selectionReasons"], ["explicit-file"])
        serialized = encoded.decode("utf-8")
        for volatile in ("timestamp", "elapsed", "hostname", "temporary", "pid"):
            self.assertNotIn(volatile, serialized.lower())

    def test_inventory_is_canonical_and_byte_deterministic(self):
        path = self.root / "a.py"
        path.write_text("A = 1\n", encoding="utf-8")
        request = self.request(["a.py"])
        selection = self.select(request)
        files = collect_files(request, selection, time.monotonic() + 30)
        bundle = render_bundle(request, files, b"", time.monotonic() + 30)

        first = build_agent_inventory(request, "a" * 64, bundle, files, selection.excluded)
        second = build_agent_inventory(request, "a" * 64, bundle, files, selection.excluded)

        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        self.assertEqual(first.count(b"\n"), 1)


class AgentArtifactTransactionTests(AgentFilesystemTestCase):
    def test_creates_both_artifacts_and_parent_directories(self):
        bundle = b"bundle\n"
        inventory = b'{"inventory":true}\n'

        artifacts = write_agent_artifacts(
            self.root,
            "audit/bundle.md",
            "audit/bundle.inventory.json",
            bundle,
            inventory,
            False,
        )

        self.assertEqual((self.root / artifacts.bundle_path).read_bytes(), bundle)
        self.assertEqual((self.root / artifacts.inventory_path).read_bytes(), inventory)
        self.assertEqual(artifacts.bundle_sha256, sha256_hex(bundle))
        self.assertEqual(artifacts.inventory_sha256, sha256_hex(inventory))
        self.assertFalse(list((self.root / "audit").glob("*.tmp")))

    def test_existing_artifact_requires_replace(self):
        audit = self.root / "audit"
        audit.mkdir()
        (audit / "bundle.md").write_text("old\n", encoding="utf-8")

        with self.assertRaises(AgentFailure) as caught:
            write_agent_artifacts(
                self.root,
                "audit/bundle.md",
                "audit/bundle.inventory.json",
                b"new\n",
                b"{}\n",
                False,
            )

        self.assertEqual(caught.exception.code, "artifact_exists")
        self.assertEqual((audit / "bundle.md").read_text(encoding="utf-8"), "old\n")

    def test_replace_updates_both_artifacts(self):
        audit = self.root / "audit"
        audit.mkdir()
        (audit / "bundle.md").write_bytes(b"old bundle\n")
        (audit / "bundle.inventory.json").write_bytes(b"old inventory\n")

        write_agent_artifacts(
            self.root,
            "audit/bundle.md",
            "audit/bundle.inventory.json",
            b"new bundle\n",
            b"new inventory\n",
            True,
        )

        self.assertEqual((audit / "bundle.md").read_bytes(), b"new bundle\n")
        self.assertEqual(
            (audit / "bundle.inventory.json").read_bytes(), b"new inventory\n"
        )
        self.assertFalse(list(audit.glob("*.bak")))


class AgentCliTests(AgentFilesystemTestCase):
    def run_agent(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--agent", *args],
            cwd=self.root,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
        )

    def required_args(self, *selectors):
        return (
            "--root",
            str(self.root),
            "-f",
            *selectors,
            "--output",
            "audit/bundle.md",
            "--inventory",
            "audit/bundle.inventory.json",
        )

    def test_agent_success_writes_artifacts_and_only_json_stdout(self):
        (self.root / "requirements.lock").write_text("package==1\n", encoding="utf-8")

        result = self.run_agent(*self.required_args("requirements.lock"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        completion = json.loads(result.stdout)
        self.assertEqual(completion["status"], "ok")
        self.assertEqual(completion["errorCode"], "none")
        self.assertNotIn("## File:", result.stdout)
        bundle = self.root / completion["bundlePath"]
        inventory = self.root / completion["inventoryPath"]
        self.assertEqual(sha256_hex(bundle.read_bytes()), completion["bundleSha256"])
        self.assertEqual(sha256_hex(inventory.read_bytes()), completion["inventorySha256"])

    def test_agent_defaults_dependency_graph_off(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")

        result = self.run_agent(*self.required_args("a.py"))

        completion = json.loads(result.stdout)
        bundle = (self.root / completion["bundlePath"]).read_text(encoding="utf-8")
        self.assertNotIn("# AI-Native Dependency Graphs", bundle)

    def test_agent_missing_requested_file_returns_structured_nonzero_error(self):
        result = self.run_agent(*self.required_args("missing.py"))

        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        completion = json.loads(result.stdout)
        self.assertEqual(completion["status"], "error")
        self.assertEqual(completion["errorCode"], "requested_file_missing")
        self.assertFalse((self.root / "audit" / "bundle.md").exists())

    def test_agent_requires_explicit_root_output_inventory_and_files(self):
        result = self.run_agent("--root", str(self.root))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["errorCode"], "invalid_request")

    def test_agent_rejects_minification_and_symlink_flags(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")

        for forbidden in ("--minify-python", "--follow-symlinks"):
            with self.subTest(forbidden=forbidden):
                result = self.run_agent(
                    *self.required_args("a.py"), forbidden
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)["errorCode"], "invalid_request")

    def test_human_mode_still_prints_markdown(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "-n",
                "--no-dependency-graph",
                "-f",
                "a.py",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## a.py", result.stdout)


class AgentJsonTransportTests(AgentFilesystemTestCase):
    def run_request(self, document_text, *extra_args):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--agent",
                "--request-json",
                "-",
                *extra_args,
            ],
            cwd=self.root,
            input=document_text,
            capture_output=True,
            text=True,
            check=False,
        )

    def document(self, **overrides):
        value = dict(VALID_REQUEST)
        value.update(
            root=self.root.as_posix(),
            files=["a.py"],
            output="audit/bundle.md",
            inventory="audit/bundle.inventory.json",
        )
        value.update(overrides)
        return value

    def test_stdin_json_request_succeeds(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")
        payload = json.dumps(self.document(), separators=(",", ":")) + "\n"

        result = self.run_request(payload)

        self.assertEqual(result.returncode, 0, result.stderr)
        completion = json.loads(result.stdout)
        self.assertEqual(completion["status"], "ok")
        self.assertTrue((self.root / completion["bundlePath"]).is_file())

    def test_stdin_requires_one_newline_terminated_record(self):
        result = self.run_request(json.dumps(self.document()))

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["errorCode"], "invalid_request")

    def test_malformed_and_duplicate_json_are_rejected(self):
        payloads = (
            "{not json}\n",
            '{"schemaVersion":1,"schemaVersion":1}\n',
            "[]\n",
            "{} {}\n",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                result = self.run_request(payload)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(
                    json.loads(result.stdout)["errorCode"], "invalid_request"
                )

    def test_request_file_accepts_utf8_bom_and_multiline_json(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")
        request_path = self.root / "request.json"
        request_path.write_bytes(
            b"\xef\xbb\xbf" + json.dumps(self.document(), indent=2).encode("utf-8")
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--agent",
                "--request-json",
                str(request_path),
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ok")

    def test_request_json_rejects_mixed_selection_flags(self):
        payload = json.dumps(self.document()) + "\n"

        result = self.run_request(payload, "-f", "other.py")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["errorCode"], "invalid_request")

    def test_flag_and_json_routes_have_equivalent_hashes_and_artifacts(self):
        (self.root / "a.py").write_text("A = 1\n", encoding="utf-8")
        flag_result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--agent",
                "--root",
                str(self.root),
                "-f",
                "a.py",
                "--output",
                "audit/bundle.md",
                "--inventory",
                "audit/bundle.inventory.json",
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        flag_completion = json.loads(flag_result.stdout)
        flag_bundle = (self.root / flag_completion["bundlePath"]).read_bytes()
        flag_inventory = (self.root / flag_completion["inventoryPath"]).read_bytes()
        shutil.rmtree(self.root / "audit")
        json_result = self.run_request(json.dumps(self.document()) + "\n")
        json_completion = json.loads(json_result.stdout)

        self.assertEqual(flag_completion["requestSha256"], json_completion["requestSha256"])
        self.assertEqual(
            flag_bundle, (self.root / json_completion["bundlePath"]).read_bytes()
        )
        self.assertEqual(
            flag_inventory,
            (self.root / json_completion["inventoryPath"]).read_bytes(),
        )

class AgentArtifactRollbackTests(AgentFilesystemTestCase):
    def test_directory_artifact_target_is_rejected_without_displacing_contents(self):
        target = self.root / "bundle"
        target.mkdir()
        (target / "keep.txt").write_bytes(b"keep")
        with self.assertRaises(AgentFailure):
            write_agent_artifacts(self.root, "bundle", "inventory.json", b"new", b"{}", True)
        self.assertEqual((target / "keep.txt").read_bytes(), b"keep")
        self.assertFalse((self.root / "inventory.json").exists())

    def test_failed_rollback_preserves_original_backups(self):
        bundle = self.root / "bundle.md"
        inventory = self.root / "inventory.json"
        bundle.write_bytes(b"original bundle")
        inventory.write_bytes(b"original inventory")
        real_replace = os.replace

        def fail_commit_and_restore(source, destination):
            source_path = Path(source)
            if source_path.suffix == ".bak" or (source_path.suffix == ".tmp" and Path(destination) == inventory):
                raise OSError("injected commit or restore failure")
            return real_replace(source, destination)

        with patch("colly.os.replace", side_effect=fail_commit_and_restore):
            with self.assertRaises(AgentFailure):
                write_agent_artifacts(self.root, "bundle.md", "inventory.json", b"new", b"{}", True)
        backups = {path.read_bytes() for path in self.root.glob("*.bak")}
        self.assertEqual(backups, {b"original bundle", b"original inventory"})

    def test_target_outside_root_fails_before_write(self):
        with self.assertRaises(AgentFailure) as caught:
            write_agent_artifacts(
                self.root,
                "../bundle.md",
                "audit/inventory.json",
                b"bundle\n",
                b"{}\n",
                False,
            )

        self.assertEqual(caught.exception.code, "path_outside_root")
        self.assertFalse((self.base / "bundle.md").exists())

    def test_second_commit_failure_removes_new_bundle(self):
        real_replace = os.replace
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated inventory commit failure")
            return real_replace(source, destination)

        with patch("colly.os.replace", side_effect=fail_second):
            with self.assertRaises(AgentFailure) as caught:
                write_agent_artifacts(
                    self.root,
                    "audit/bundle.md",
                    "audit/bundle.inventory.json",
                    b"bundle\n",
                    b"{}\n",
                    False,
                )

        self.assertEqual(caught.exception.code, "artifact_write_failed")
        self.assertFalse((self.root / "audit" / "bundle.md").exists())
        self.assertFalse((self.root / "audit" / "bundle.inventory.json").exists())

    def test_replace_failure_restores_both_old_artifacts(self):
        audit = self.root / "audit"
        audit.mkdir()
        bundle_path = audit / "bundle.md"
        inventory_path = audit / "bundle.inventory.json"
        bundle_path.write_bytes(b"old bundle\n")
        inventory_path.write_bytes(b"old inventory\n")
        real_replace = os.replace
        failed = False

        def fail_inventory_commit(source, destination):
            nonlocal failed
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                not failed
                and source_path.suffix == ".tmp"
                and destination_path == inventory_path
            ):
                failed = True
                raise OSError("simulated inventory commit failure")
            return real_replace(source, destination)

        with patch("colly.os.replace", side_effect=fail_inventory_commit):
            with self.assertRaises(AgentFailure):
                write_agent_artifacts(
                    self.root,
                    "audit/bundle.md",
                    "audit/bundle.inventory.json",
                    b"new bundle\n",
                    b"new inventory\n",
                    True,
                )

        self.assertEqual(bundle_path.read_bytes(), b"old bundle\n")
        self.assertEqual(inventory_path.read_bytes(), b"old inventory\n")
        self.assertFalse(list(audit.glob("*.tmp")))
        self.assertFalse(list(audit.glob("*.bak")))


class AgentDocumentationTests(unittest.TestCase):
    def test_tool_card_leads_with_machine_contract(self):
        text = (REPO_ROOT / ".agents" / "tools" / "colly-tool.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("--agent --request-json -", text)
        self.assertIn("stdout contains exactly one JSON completion record", text)
        self.assertIn("Codex Responses", text)
        self.assertIn("Muse/Hermes", text)
        self.assertIn("Human/interactive mode", text)

    def test_skill_prefers_structured_tool_and_agent_cli(self):
        text = (REPO_ROOT / ".agents" / "skills" / "use-colly" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        structured = text.index("colly_collect_context")
        json_transport = text.index("--agent --request-json -")
        legacy = text.index("Human/legacy CLI")
        self.assertLess(structured, json_transport)
        self.assertLess(json_transport, legacy)

    def test_contract_doc_states_bounded_write_and_claim_boundary(self):
        text = (REPO_ROOT / "docs" / "agent-tool-contract.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Declared writes only", text)
        self.assertIn("384 KiB", text)
        self.assertIn("binary_input", text)
        self.assertIn("Muse-Glimmer", text)
        self.assertIn("MCP", text)
        self.assertIn("Class C", text)
        self.assertIn("does not prove live model compatibility", text)

if __name__ == "__main__":
    unittest.main()
