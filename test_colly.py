import csv
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("colly.py")
AUTO_TRUNCATE_TARGET_BYTES = 6400


class CollyCliTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parent, prefix="case_")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_colly(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "-n", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def extract_file_body(self, output, relative_path):
        pattern = rf"## {re.escape(relative_path)}\n```[^\n]*\n(.*?)\n```"
        match = re.search(pattern, output, re.DOTALL)
        self.assertIsNotNone(match, output)
        return match.group(1)

    def extract_dependency_graph(self, output):
        pattern = r"# AI-Native Dependency Graphs\n```json\n(.*?)\n```"
        match = re.search(pattern, output, re.DOTALL)
        self.assertIsNotNone(match, output)
        return json.loads(match.group(1))

    def extract_bra_manifest(self, output):
        pattern = r"# BRA Expansion Manifest\n```json\n(.*?)\n```"
        match = re.search(pattern, output, re.DOTALL)
        self.assertIsNotNone(match, output)
        return json.loads(match.group(1))

    def utf8_size(self, text):
        return len(text.encode("utf-8"))

    def test_env_exclusion_does_not_filter_envisage_directory(self):
        target_dir = self.root / "envisage-research-object"
        target_dir.mkdir()
        (target_dir / "SKILL.md").write_text("# skill\n", encoding="utf-8")

        result = self.run_colly("-f", "envisage-research-object")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SKILL.md", result.stdout)

    def test_env_directory_is_still_excluded(self):
        included_dir = self.root / "project"
        included_dir.mkdir()
        (included_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")

        excluded_dir = self.root / "env"
        excluded_dir.mkdir()
        (excluded_dir / "skip.py").write_text("print('skip')\n", encoding="utf-8")

        result = self.run_colly("-f", ".")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project", result.stdout)
        self.assertNotIn("skip.py", result.stdout)

    def test_collyignore_excludes_matching_paths(self):
        included_dir = self.root / "project"
        included_dir.mkdir()
        (included_dir / "keep.py").write_text("print('keep')\n", encoding="utf-8")

        ignored_dir = self.root / "generated"
        ignored_dir.mkdir()
        (ignored_dir / "skip.py").write_text("print('skip')\n", encoding="utf-8")
        (self.root / ".collyignore").write_text("generated/\n", encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "-f", ".")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("keep.py", result.stdout)
        self.assertNotIn("skip.py", result.stdout)

    def test_ignore_file_argument_excludes_matching_paths(self):
        included_dir = self.root / "project"
        included_dir.mkdir()
        (included_dir / "keep.py").write_text("print('keep')\n", encoding="utf-8")

        ignored_dir = self.root / "dist"
        ignored_dir.mkdir()
        (ignored_dir / "skip.py").write_text("print('skip')\n", encoding="utf-8")
        (self.root / "custom.ignore").write_text("# generated output\n\ndist/\n", encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "--ignore-file", "custom.ignore", "-f", ".")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("keep.py", result.stdout)
        self.assertNotIn("skip.py", result.stdout)

    def test_explicit_file_argument_overrides_collyignore(self):
        ignored_dir = self.root / "generated"
        ignored_dir.mkdir()
        target_file = ignored_dir / "keep_anyway.py"
        target_file.write_text("print('keep')\n", encoding="utf-8")
        (self.root / ".collyignore").write_text("generated/\n", encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "-f", "generated/keep_anyway.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("keep_anyway.py", result.stdout)

    def test_explicit_file_argument_accepts_unknown_extension(self):
        source = self.root / "requirements.lock"
        original = '{"artifacts": [{"name": "fixture.whl"}]}\n'
        source.write_text(original, encoding="utf-8")

        result = self.run_colly(
            "--no-dependency-graph", "-f", "requirements.lock"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "requirements.lock")
        self.assertEqual(body, original.rstrip("\n"))

    def test_recursive_scan_still_excludes_unknown_extension(self):
        source = self.root / "requirements.lock"
        source.write_text('{"artifacts": []}\n', encoding="utf-8")
        (self.root / "included.py").write_text("print('ok')\n", encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "-f", ".")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("included.py", result.stdout)
        self.assertNotIn("requirements.lock", result.stdout)

    def test_direct_oversized_text_file_is_not_truncated_by_default(self):
        source = self.root / "oversized.txt"
        original = ("A" * 220 + "\n") * 60
        source.write_text(original, encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "-f", "oversized.txt")

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "oversized.txt")
        self.assertEqual(body, original.rstrip("\n"))

    def test_auto_truncate_truncates_direct_oversized_text_file(self):
        source = self.root / "oversized.txt"
        source.write_text(("A" * 220 + "\n") * 60, encoding="utf-8")

        result = self.run_colly(
            "--auto-truncate", "--no-dependency-graph", "-f", "oversized.txt"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "oversized.txt")
        self.assertLessEqual(self.utf8_size(body), AUTO_TRUNCATE_TARGET_BYTES)
        self.assertLess(len(max(re.findall(r"\w+", body), key=len)), 220)

    def test_direct_file_under_threshold_is_unchanged(self):
        original = "short line\n" * 100
        source = self.root / "small.txt"
        source.write_text(original, encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "-f", "small.txt")

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "small.txt")
        self.assertEqual(body, original.rstrip("\n"))

    def test_oversized_json_preserves_keys_and_truncates_values(self):
        long_key = "preserve_this_json_key_name_exactly"
        payload = {
            long_key: "J" * 7000,
            "nested": {"another_key": "K" * 2500},
        }
        source = self.root / "data.json"
        source.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        result = self.run_colly(
            "--auto-truncate", "--no-dependency-graph", "-f", "data.json"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "data.json")
        parsed = json.loads(body)
        self.assertLessEqual(self.utf8_size(body), AUTO_TRUNCATE_TARGET_BYTES)
        self.assertIn(long_key, parsed)
        self.assertLess(len(parsed[long_key]), len(payload[long_key]))
        self.assertEqual(parsed["nested"].keys(), payload["nested"].keys())

    def test_oversized_csv_preserves_headers_and_truncates_row_values(self):
        headers = [
            "PreserveThisHeaderExactly",
            "AndThisHeaderToo",
        ]
        rows = [headers]
        rows.extend([["V" * 180, "W" * 180] for _ in range(60)])
        source = self.root / "data.csv"
        with source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerows(rows)

        result = self.run_colly(
            "--auto-truncate", "--no-dependency-graph", "-f", "data.csv"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "data.csv")
        parsed_rows = list(csv.reader(body.splitlines()))
        self.assertLessEqual(self.utf8_size(body), AUTO_TRUNCATE_TARGET_BYTES)
        self.assertEqual(parsed_rows[0], headers)
        self.assertLess(len(parsed_rows[1][0]), len(rows[1][0]))

    def test_oversized_yaml_preserves_keys_and_truncates_scalar_values(self):
        key_name = "preserve_this_yaml_key_name_exactly"
        scalar_value = "ScalarToken_" + ("S" * 1200)
        block_value = "BlockToken_" + ("B" * 260)
        lines = [
            f"{key_name}: {scalar_value}",
            "notes: |",
        ]
        lines.extend([f"  {block_value}" for _ in range(40)])
        source = self.root / "data.yaml"
        source.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self.run_colly(
            "--auto-truncate", "--no-dependency-graph", "-f", "data.yaml"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "data.yaml")
        self.assertLessEqual(self.utf8_size(body), AUTO_TRUNCATE_TARGET_BYTES)
        self.assertIn(f"{key_name}:", body)
        self.assertNotIn(scalar_value, body)
        self.assertNotIn(block_value, body)

    def test_dependency_expanded_file_is_not_auto_truncated(self):
        main_file = self.root / "main.py"
        dep_file = self.root / "dep.py"
        long_value = "DependencyToken_" + ("D" * 7000)
        main_file.write_text("import dep\nprint('ok')\n", encoding="utf-8")
        dep_file.write_text(f'DATA = "{long_value}"\n', encoding="utf-8")

        result = self.run_colly(
            "--auto-truncate",
            "--no-dependency-graph",
            "--bra",
            "a",
            "-f",
            "main.py",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        dep_body = self.extract_file_body(result.stdout, "dep.py")
        self.assertIn(long_value, dep_body)

    def test_explicit_truncate_and_auto_size_guard_both_apply(self):
        source = self.root / "manual.txt"
        source.write_text(("M" * 320 + "\n") * 60, encoding="utf-8")

        result = self.run_colly(
            "--no-dependency-graph",
            "--truncate",
            "--auto-truncate",
            "--override-max-length",
            "*.txt:200",
            "-f",
            "manual.txt",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.extract_file_body(result.stdout, "manual.txt")
        token_lengths = [len(token) for token in re.findall(r"\w+", body)]
        self.assertLessEqual(self.utf8_size(body), AUTO_TRUNCATE_TARGET_BYTES)
        self.assertTrue(token_lengths)
        self.assertLess(max(token_lengths), 200)

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

    def test_bra_explain_emits_manifest_section(self):
        main_file = self.root / "main.py"
        dep_file = self.root / "dep.py"

        main_file.write_text("import dep\nprint(dep.VALUE)\n", encoding="utf-8")
        dep_file.write_text("VALUE = 'dep'\n", encoding="utf-8")

        result = self.run_colly("--bra", "focused", "--bra-explain", "-f", "main.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.extract_bra_manifest(result.stdout)
        self.assertEqual(manifest["seeds"], ["main.py"])
        self.assertEqual(manifest["included"][0]["path"], "dep.py")
        self.assertEqual(manifest["included"][0]["distance"], 1)
        self.assertEqual(manifest["included"][0]["edge_types"], ["import"])

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

    def test_typescript_ai_native_dependency_graph_contains_cross_file_edges(self):
        helper = self.root / "helper.ts"
        helper.write_text(
            "\n".join(
                [
                    "export interface Person {",
                    "  name: string;",
                    "}",
                    "",
                    "export const sharedValue = 'Ada';",
                    "",
                    "export function greet(name: string): string {",
                    "  return `Hello ${name}`;",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        main = self.root / "main.ts"
        main.write_text(
            "\n".join(
                [
                    "import { greet, sharedValue, Person } from './helper';",
                    "",
                    "const person: Person = { name: sharedValue };",
                    "console.log(greet(person.name));",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "main.ts", "helper.ts")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)

        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        self.assertIn("main.ts", node_ids)
        self.assertIn("helper.ts", node_ids)

        helper_defs = {
            (definition["name"], definition["type"])
            for definition in graph["nodes"][node_ids["helper.ts"]].get("definitions", [])
        }
        self.assertIn(("Person", "interface"), helper_defs)
        self.assertIn(("sharedValue", "variable"), helper_defs)
        self.assertIn(("greet", "function"), helper_defs)

        main_id = node_ids["main.ts"]
        helper_id = node_ids["helper.ts"]
        self.assertTrue(
            any(edge["source"] == main_id and edge["target"] == helper_id and edge["type"] == "imports" for edge in graph["edges"]),
            graph["edges"],
        )
        self.assertTrue(
            any(
                edge["source"] == main_id and edge["target"] == helper_id and edge["type"] == "calls" and edge["name"] == "greet"
                for edge in graph["edges"]
            ),
            graph["edges"],
        )
        self.assertTrue(
            any(
                edge["source"] == main_id and edge["target"] == helper_id and edge["type"] == "symbols" and edge["name"] in {"Person", "sharedValue"}
                for edge in graph["edges"]
            ),
            graph["edges"],
        )

    def test_python_ai_native_dependency_graph_emits_annotations(self):
        helper = self.root / "helper.py"
        helper.write_text(
            "\n".join(
                [
                    '# @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"role","summary":"Shared greeting utilities"}}',
                    '# @colly-graph {"target":{"kind":"definition","name":"greet","type":"function"},"annotation":{"kind":"contract","summary":"Returns greeting text","stability":"stable"}}',
                    "",
                    "def greet(name):",
                    '    return f"Hello {name}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        main = self.root / "main.py"
        main.write_text(
            "\n".join(
                [
                    '# @colly-graph {"target":{"kind":"edge","type":"imports","name":"helper"},"annotation":{"kind":"dataflow","summary":"Consumes helper greeting primitives"}}',
                    "import helper",
                    'print(helper.greet("Ada"))',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "main.py", "helper.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)

        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        helper_node = graph["nodes"][node_ids["helper.py"]]
        self.assertEqual(helper_node["annotations"][0]["kind"], "role")
        self.assertEqual(helper_node["annotations"][0]["summary"], "Shared greeting utilities")
        self.assertEqual(helper_node["annotations"][0]["_origin"]["line"], 1)

        greet_definition = next(
            definition
            for definition in helper_node.get("definitions", [])
            if definition["name"] == "greet" and definition["type"] == "function"
        )
        self.assertEqual(greet_definition["annotations"][0]["kind"], "contract")
        self.assertEqual(greet_definition["annotations"][0]["stability"], "stable")
        self.assertEqual(greet_definition["annotations"][0]["_origin"]["line"], 2)

        main_id = node_ids["main.py"]
        helper_id = node_ids["helper.py"]
        import_edge = next(
            edge
            for edge in graph["edges"]
            if edge["source"] == main_id
            and edge["target"] == helper_id
            and edge["type"] == "imports"
            and edge["name"] == "helper"
        )
        self.assertEqual(import_edge["annotations"][0]["kind"], "dataflow")
        self.assertEqual(import_edge["annotations"][0]["summary"], "Consumes helper greeting primitives")
        self.assertEqual(import_edge["annotations"][0]["_origin"]["line"], 1)

    def test_graph_annotation_bundle_applies_to_existing_graph_markdown(self):
        helper = self.root / "helper.py"
        helper.write_text(
            "\n".join(
                [
                    "def greet(name):",
                    '    return f"Hello {name}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        main = self.root / "main.py"
        main.write_text(
            "\n".join(
                [
                    "import helper",
                    'print(helper.greet("Ada"))',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        initial_result = self.run_colly("-f", "main.py", "helper.py")
        self.assertEqual(initial_result.returncode, 0, initial_result.stderr)

        graph_markdown = self.root / "graph.md"
        graph_markdown.write_text(initial_result.stdout, encoding="utf-8")

        annotation_bundle = {
            "version": 1,
            "annotations": [
                {
                    "selector": {"kind": "node", "path": "helper.py"},
                    "annotation": {"kind": "role", "summary": "Shared greeting utilities"},
                },
                {
                    "selector": {"kind": "definition", "path": "helper.py", "name": "greet", "type": "function"},
                    "annotation": {"kind": "contract", "summary": "Returns greeting text", "stability": "stable"},
                },
                {
                    "selector": {
                        "kind": "edge",
                        "source_path": "main.py",
                        "target_path": "helper.py",
                        "type": "imports",
                        "name": "helper",
                    },
                    "annotation": {"kind": "dataflow", "summary": "Consumes helper greeting primitives"},
                },
            ],
        }
        bundle_path = self.root / "insights.json"
        bundle_path.write_text(json.dumps(annotation_bundle, indent=2), encoding="utf-8")

        annotated_result = self.run_colly(
            "--graph-input",
            "graph.md",
            "--graph-annotation-file",
            "insights.json",
        )

        self.assertEqual(annotated_result.returncode, 0, annotated_result.stderr)
        graph = self.extract_dependency_graph(annotated_result.stdout)

        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        helper_node = graph["nodes"][node_ids["helper.py"]]
        self.assertEqual(helper_node["annotations"][0]["kind"], "role")
        self.assertEqual(helper_node["annotations"][0]["summary"], "Shared greeting utilities")
        self.assertEqual(helper_node["annotations"][0]["_origin"]["bundle"], "insights.json")

        greet_definition = next(
            definition
            for definition in helper_node.get("definitions", [])
            if definition["name"] == "greet" and definition["type"] == "function"
        )
        self.assertEqual(greet_definition["annotations"][0]["kind"], "contract")
        self.assertEqual(greet_definition["annotations"][0]["stability"], "stable")
        self.assertEqual(greet_definition["annotations"][0]["_origin"]["syntax"], "graph-annotation-bundle")

        main_id = node_ids["main.py"]
        helper_id = node_ids["helper.py"]
        import_edge = next(
            edge
            for edge in graph["edges"]
            if edge["source"] == main_id
            and edge["target"] == helper_id
            and edge["type"] == "imports"
            and edge["name"] == "helper"
        )
        self.assertEqual(import_edge["annotations"][0]["kind"], "dataflow")
        self.assertEqual(import_edge["annotations"][0]["summary"], "Consumes helper greeting primitives")
        self.assertEqual(import_edge["annotations"][0]["_origin"]["bundle"], "insights.json")

    def test_markdown_source_examples_do_not_emit_graph_annotations(self):
        helper = self.root / "helper.py"
        helper.write_text(
            "\n".join(
                [
                    "def greet():",
                    "    return 'hello'",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        docs = self.root / "notes.md"
        docs.write_text(
            "\n".join(
                [
                    "# Notes",
                    "",
                    "```python",
                    '# @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"role","summary":"Example only"}}',
                    "def sample():",
                    "    return True",
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "notes.md", "helper.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)
        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        docs_node = graph["nodes"][node_ids["notes.md"]]
        self.assertNotIn("annotations", docs_node)

    def test_markdown_html_comment_directive_annotates_markdown_node(self):
        docs = self.root / "notes.md"
        docs.write_text(
            "\n".join(
                [
                    "# Notes",
                    '<!-- @colly-graph {"target":{"kind":"node"},"annotation":{"kind":"role","summary":"Design note"}} -->',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "notes.md")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)
        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        docs_node = graph["nodes"][node_ids["notes.md"]]
        self.assertEqual(docs_node["annotations"][0]["kind"], "role")
        self.assertEqual(docs_node["annotations"][0]["summary"], "Design note")
        self.assertEqual(docs_node["annotations"][0]["_origin"]["line"], 2)

    def test_markdown_colly_graph_block_applies_global_annotations(self):
        helper = self.root / "helper.py"
        helper.write_text(
            "\n".join(
                [
                    "def greet(name):",
                    '    return f"Hello {name}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )

        docs = self.root / "architecture.md"
        docs.write_text(
            "\n".join(
                [
                    "# Architecture",
                    "",
                    "```colly-graph",
                    json.dumps(
                        {
                            "annotations": [
                                {
                                    "selector": {"kind": "node", "path": "helper.py"},
                                    "annotation": {"kind": "role", "summary": "Documented helper surface"},
                                },
                                {
                                    "selector": {
                                        "kind": "definition",
                                        "path": "helper.py",
                                        "name": "greet",
                                        "type": "function",
                                    },
                                    "annotation": {"kind": "contract", "summary": "Documented greeting contract"},
                                },
                            ]
                        },
                        indent=2,
                    ),
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "architecture.md", "helper.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)
        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        helper_node = graph["nodes"][node_ids["helper.py"]]
        self.assertEqual(helper_node["annotations"][0]["summary"], "Documented helper surface")
        self.assertEqual(helper_node["annotations"][0]["_origin"]["bundle"], "architecture.md")
        self.assertEqual(helper_node["annotations"][0]["_origin"]["syntax"], "markdown-colly-graph")
        self.assertEqual(helper_node["annotations"][0]["_origin"]["line"], 3)

        greet_definition = next(
            definition
            for definition in helper_node.get("definitions", [])
            if definition["name"] == "greet" and definition["type"] == "function"
        )
        self.assertEqual(greet_definition["annotations"][0]["summary"], "Documented greeting contract")

    def test_javascript_ai_native_dependency_graph_contains_cross_file_edges(self):
        helper = self.root / "helper.js"
        helper.write_text(
            "\n".join(
                [
                    "export class Greeter {",
                    "  constructor(name) {",
                    "    this.name = name;",
                    "  }",
                    "}",
                    "",
                    "export const sharedValue = 'Ada';",
                    "",
                    "export function greet(name) {",
                    "  return `Hello ${name}`;",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        main = self.root / "main.js"
        main.write_text(
            "\n".join(
                [
                    "import { greet, sharedValue, Greeter } from './helper.js';",
                    "",
                    "const greeter = new Greeter(sharedValue);",
                    "console.log(greet(greeter.name));",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "main.js", "helper.js")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)

        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        self.assertIn("main.js", node_ids)
        self.assertIn("helper.js", node_ids)

        helper_defs = {
            (definition["name"], definition["type"])
            for definition in graph["nodes"][node_ids["helper.js"]].get("definitions", [])
        }
        self.assertIn(("Greeter", "class"), helper_defs)
        self.assertIn(("sharedValue", "variable"), helper_defs)
        self.assertIn(("greet", "function"), helper_defs)

        main_id = node_ids["main.js"]
        helper_id = node_ids["helper.js"]
        self.assertTrue(
            any(edge["source"] == main_id and edge["target"] == helper_id and edge["type"] == "imports" for edge in graph["edges"]),
            graph["edges"],
        )
        self.assertTrue(
            any(
                edge["source"] == main_id and edge["target"] == helper_id and edge["type"] == "calls" and edge["name"] == "greet"
                for edge in graph["edges"]
            ),
            graph["edges"],
        )
        self.assertTrue(
            any(
                edge["source"] == main_id and edge["target"] == helper_id and edge["type"] == "symbols" and edge["name"] in {"Greeter", "sharedValue"}
                for edge in graph["edges"]
            ),
            graph["edges"],
        )

    def test_javascript_ai_native_dependency_graph_parses_comment_annotations(self):
        helper = self.root / "helper.js"
        helper.write_text(
            "\n".join(
                [
                    '// @colly-graph {"target":{"kind":"definition","name":"greet","type":"function"},"annotation":{"kind":"entrypoint","summary":"Primary greeting surface"}}',
                    "export function greet(name) {",
                    "  return `Hello ${name}`;",
                    "}",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        main = self.root / "main.js"
        main.write_text(
            "\n".join(
                [
                    "import { greet } from './helper.js';",
                    "console.log(greet('Ada'));",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        result = self.run_colly("-f", "main.js", "helper.js")

        self.assertEqual(result.returncode, 0, result.stderr)
        graph = self.extract_dependency_graph(result.stdout)

        node_ids = {node["path"]: idx for idx, node in enumerate(graph["nodes"])}
        helper_node = graph["nodes"][node_ids["helper.js"]]
        greet_definition = next(
            definition
            for definition in helper_node.get("definitions", [])
            if definition["name"] == "greet" and definition["type"] == "function"
        )
        self.assertEqual(greet_definition["annotations"][0]["kind"], "entrypoint")
        self.assertEqual(greet_definition["annotations"][0]["summary"], "Primary greeting surface")
        self.assertEqual(greet_definition["annotations"][0]["_origin"]["line"], 1)

    def test_bra_dependency_expansion_includes_typescript_imports(self):
        main_file = self.root / "main.ts"
        dep_file = self.root / "dep.ts"
        main_file.write_text("import { value } from './dep';\nconsole.log(value);\n", encoding="utf-8")
        dep_file.write_text("export const value = 'ts dependency';\n", encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "--bra", "a", "-f", "main.ts")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## dep.ts", result.stdout)
        dep_body = self.extract_file_body(result.stdout, "dep.ts")
        self.assertIn("ts dependency", dep_body)

    def test_bra_dependency_expansion_includes_javascript_imports(self):
        main_file = self.root / "main.js"
        dep_file = self.root / "dep.js"
        main_file.write_text("import { value } from './dep.js';\nconsole.log(value);\n", encoding="utf-8")
        dep_file.write_text("export const value = 'js dependency';\n", encoding="utf-8")

        result = self.run_colly("--no-dependency-graph", "--bra", "a", "-f", "main.js")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("## dep.js", result.stdout)
        dep_body = self.extract_file_body(result.stdout, "dep.js")
        self.assertIn("js dependency", dep_body)


if __name__ == "__main__":
    unittest.main()
