import os
import re
import time
import argparse
import glob
import sys
import fnmatch
import logging
import subprocess
from typing import List, Optional, Set, Tuple
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
    ".js": "javascript", ".ts": "typescript", ".mjs": "javascript", ".cjs": "javascript",
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
        self.bra_map = {
            'aa': 0, 'a': 1, 'b': 2, 'c': 3, 'd': 5, 'dd': 10, 'e': 20,
            '0': 0, '1': 1, '2': 2, '3': 3, '5': 5, '10': 10, '20': 20
        }

    def expand(self, initial_files):
        bra_before, bra_after = self._parse_bra_args()
        # Use legacy args only if --bra is not used
        before_expansion = bra_before if self.args.bra is not None else self.args.before
        after_expansion = bra_after if self.args.bra is not None else self.args.after

        if before_expansion == 0 and after_expansion == 0:
            return initial_files

        dep_types = self.args.dep_type
        merged_forward_graph = defaultdict(set)
        merged_reverse_graph = defaultdict(set)

        for dep_type in dep_types:
            is_reverse = dep_type.endswith(('_by', '_of'))
            base_type = dep_type.replace('_by', '').replace('_of', '')
            if base_type not in ['import', 'symbol', 'call']:
                base_type = 'import'

            graph = build_forward_graph(self.all_supported_files, base_type, self.project_root)
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

    def _parse_bra_args(self):
        if self.args.bra is None:
            return 0, 0
        
        vals = self.args.bra
        def parse_bra_val(val):
            v = val.lower()
            if v in self.bra_map:
                return self.bra_map[v]
            try:
                return int(v)
            except ValueError:
                raise ValueError(f"Invalid --bra value: {val}")

        if not vals: # Handles --bra with no arguments
            return 0, 0
        if len(vals) == 1:
            val = parse_bra_val(vals[0])
            return val, val
        elif len(vals) >= 2:
            return parse_bra_val(vals[0]), parse_bra_val(vals[1])
        return 0, 0

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
        for file_path in files:
            if not os.path.exists(file_path) or is_excluded(file_path, compile_exclusion_patterns(default_exclusions + self.args.exclude)):
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
        dep_graph = build_forward_graph(files, 'import', self.project_root)
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
        graph, definitions = self._analyze_files(files)
        if not graph and not definitions:
            return ""
        return self._render_ai_native_dependency_graph(files, graph, definitions)

    def _analyze_files(self, files):
        """
        Performs a two-pass analysis on the provided files to build a graph of
        dependencies and a catalog of definitions.
        """
        graph = defaultdict(lambda: defaultdict(list))
        definitions = defaultdict(list)
        symbol_locations = defaultdict(lambda: defaultdict(list))

        # Pass 1: Find all definitions and their locations
        for file_path in files:
            try:
                with open(file_path, 'r', encoding=self.args.encoding, errors='replace') as f:
                    tree = ast.parse(f.read(), filename=file_path)
                
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
            except Exception as e:
                logging.debug(f"Failed to parse definitions in {file_path}: {e}")

        # Pass 2: Find usages and build graph
        for file_path in files:
            try:
                with open(file_path, 'r', encoding=self.args.encoding, errors='replace') as f:
                    tree = ast.parse(f.read(), filename=file_path)

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
            except Exception as e:
                logging.debug(f"Failed to parse usages in {file_path}: {e}")
        
        return graph, definitions

    def _render_ai_native_dependency_graph(self, files, graph, definitions):
        """
        Renders the analyzed graph and definitions into a compact JSON format.
        """
        file_list = sorted(list(files))
        if not file_list:
            return ""

        node_ids = {file_path: idx for idx, file_path in enumerate(file_list)}
        
        # Build nodes with definitions
        nodes = []
        for file_path in file_list:
            try:
                rel_path = os.path.relpath(file_path, self.project_root)
            except ValueError:
                rel_path = os.path.basename(file_path)
            
            node_obj = {"path": rel_path}
            if file_path in definitions and definitions[file_path]:
                sorted_defs = sorted(definitions[file_path], key=lambda x: x['name'])
                node_obj["definitions"] = sorted_defs
            nodes.append(node_obj)

        # Build edges
        edges = []
        for src_path, typed_deps in graph.items():
            if src_path not in node_ids:
                continue
            src_id = node_ids[src_path]
            
            for dep_type, dep_list in typed_deps.items():
                # Use a tuple of items to make the dict hashable for deduplication
                unique_deps = {tuple(sorted(d.items())) for d in dep_list}
                for dep_tuple in unique_deps:
                    dep_info = dict(dep_tuple)
                    target_path = dep_info.get('target')
                    target_id = node_ids.get(target_path) if target_path else None
                    
                    if src_id == target_id:
                        continue

                    edges.append({
                        "source": src_id,
                        "target": target_id,
                        "type": dep_type,
                        "name": dep_info.get('name')
                    })

        # Sort edges for deterministic output
        edges.sort(key=lambda x: (x['source'], x['target'] if x['target'] is not None else -1, x['type'], x['name']))

        graph_data = {"nodes": nodes, "edges": edges}
        output = [
            "# AI-Native Dependency Graphs", "```json",
            json.dumps(graph_data, separators=(',', ':')),
            "```", ""
        ]
        return "\n".join(output)

def compile_exclusion_patterns(exclusions: List[str]) -> List[re.Pattern]:
    """Compile exclusion patterns into regex objects using fnmatch.translate."""
    normalized = []
    for pattern in exclusions:
        pattern = re.sub(r'^(\.\\|\./|\.)', '', pattern)
        if not any(c in pattern for c in '*?[]'):
            normalized.append(f'*{os.path.sep}{pattern}*')
            normalized.append(f'*{pattern}{os.path.sep}*')
            normalized.append(pattern)
        else:
            normalized.append(pattern)
    return [re.compile(fnmatch.translate(p)) for p in normalized]

def is_excluded(path: str, exclusion_patterns: List[re.Pattern]) -> bool:
    """Check if the path matches any exclusion pattern (search anywhere in the path)."""
    for pattern in exclusion_patterns:
        if pattern.search(path):
            return True
    return False

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

def collect_unique_words(files: List[str], exclusion_patterns: List[re.Pattern], default_encoding: str) -> Set[str]:
    """Collect unique words from all files, excluding specified patterns."""
    words = set()
    for file_path in files:
        if not os.path.exists(file_path) or is_excluded(file_path, exclusion_patterns):
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

def build_forward_graph(all_py_files: Set[str], dep_type: str, project_root: str) -> defaultdict[str, Set[str]]:
    graph = defaultdict(set)
    if dep_type == 'import':
        for file_path in all_py_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    tree = ast.parse(f.read(), filename=file_path)
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
            except Exception:
                pass
    elif dep_type in ['symbol', 'call']:
        defs = defaultdict(list)
        for file_path in all_py_files:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    tree = ast.parse(f.read(), filename=file_path)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                        defs[node.name].append(file_path)
                    elif dep_type == 'symbol' and isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                defs[target.id].append(file_path)
            except Exception:
                pass
        for file_path in all_py_files:
            try:
                tree = ast.parse(open(file_path, 'r', encoding='utf-8').read(), filename=file_path)
                used = set()
                if dep_type == 'symbol':
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                            used.add(node.id)
                else:  # 'call'
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                            used.add(node.func.id)
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
                 overrides: List[Tuple[str, int]], unique_words: Set[str], verbose: bool, show_stats: bool) -> tuple[str, Stats]:
    output = []
    stats = Stats()

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

    for file_path in files:
        if not os.path.exists(file_path) or is_excluded(file_path, exclusion_patterns):
            continue
        result, stat = process_single_file(file_path, default_encoding, minify_python, truncate, truncation_length, overrides)
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
                       overrides: List[Tuple[str, int]]) -> Tuple[List[str], Optional[dict]]:
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
            content = truncate_content(content, trunc_length)
            stat['lines'] = len(content.splitlines())
            stat['chars'] = len(content)

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

def get_input_files(args_patterns: List[str], exclusion_patterns: List[re.Pattern], follow_symlinks: bool) -> Set[str]:
    expanded_paths = set()
    for pattern in args_patterns:
        expanded = glob.glob(pattern, recursive=True)
        expanded_paths.update(os.path.abspath(p) for p in expanded if os.path.exists(p))
    input_files = set()
    for path in expanded_paths:
        if is_excluded(path, exclusion_patterns):
            continue
        if os.path.isfile(path):
            input_files.add(path)
        elif os.path.isdir(path):
            for root, dirs, files_in_dir in os.walk(path, followlinks=follow_symlinks):
                dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), exclusion_patterns)]
                for file_name in files_in_dir:
                    file_full_path = os.path.join(root, file_name)
                    if not is_excluded(file_full_path, exclusion_patterns):
                        input_files.add(file_full_path)
    return input_files

def get_all_py_files(project_root: str, exclusion_patterns: List[re.Pattern], follow_symlinks: bool) -> Set[str]:
    supported_exts = set(language_identifier.keys())
    supported_exts = {e for e in supported_exts if e.startswith('.')}

    all_supported = set()
    for root, dirs, files in os.walk(project_root, followlinks=follow_symlinks):
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

def main():
    parser = argparse.ArgumentParser(
        description="Process project files into markdown with optional transformations and dependency expansion, or generate an AI-optimized directory listing.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="""
Examples of usage:

1) Basic processing with truncation and Python minification:
   python colly.py --truncate --minify-python

2) With dependency expansion:
   python colly.py -f "main.py" --bra c

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
    parser.add_argument('-f', '--files', nargs='*', default=['**/*'], help="Files or directories to process (supports wildcards; defaults to all files in cwd recursively)")
    parser.add_argument('-e', '--exclude', nargs='*', default=[], help="Additional exclusion patterns")
    parser.add_argument('-s', '--follow-symlinks', action='store_true', help="Follow symbolic links")
    parser.add_argument('-c', '--encoding', default='utf-8', help="Default encoding")
    parser.add_argument('-d', '--debug', action='store_true', help="Enable debug logging")
    parser.add_argument('-n', '--no-clip', action='store_true', help="Do not copy output to clipboard")
    parser.add_argument('-x', '--max-clip-length', type=int, default=500000, help="Maximum clipboard chunk length")

    # Transformation arguments
    parser.add_argument('-m', '--minify-python', action='store_true', help="Minify Python files")
    parser.add_argument('-t', '--truncate', action='store_true', help="Enable dynamic truncation")
    parser.add_argument('-l', '--max-length', type=int, default=80, help="Max word length for truncation")
    parser.add_argument('-o', '--override-max-length', action='append', default=[], help="Pattern:length overrides (e.g., '*.py:50')")
    
    # Feature arguments
    parser.add_argument('--directory-listing', action='store_true', help="Generate AI-optimized directory listing.")
    parser.add_argument('--show-stats', action='store_true', help="Output processing statistics.")
    parser.add_argument('--entanglement-map', action='store_true', help="Render a low-resolution entanglement map.")
    parser.add_argument('--no-dependency-graph', action='store_true', help="Do not include the AI-native dependency graph.")

    # Dependency expansion arguments
    parser.add_argument('-A', '--after', type=int, default=0, help="[DEPRECATED] Number of dependency levels to include (downstream)")
    parser.add_argument('-B', '--before', type=int, default=0, help="[DEPRECATED] Number of dependent levels to include (upstream)")
    parser.add_argument('-T', '--dep-type', nargs='*', default=['import'], choices=['import', 'symbol', 'call', 'imported_by', 'symbol_used_by', 'called_by'], help="Type of dependency to trace for expansion")
    parser.add_argument('--bra', nargs='*', metavar='CUP',
        help=(
            "Intuitive dependency expansion. Sizes: aa=0, a=1, b=2, c=3, d=5, dd=10, e=20. "
            "Syntax: --bra [size|number] [size|number]. "
            "If one value: applies to both before/after. If two: first is before, second is after."
        )
    )
    
    # Verbosity and meta arguments
    parser.add_argument('-v', '--verbose', action='store_true', help="Show verbose output (info-level logs)")
    parser.add_argument('-q', '--show-min-truncation-length', action='store_true', help="Only output the minimal truncation length and exit")

    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    start_time = time.time()

    combined_exclusions = default_exclusions + args.exclude
    exclusion_patterns = compile_exclusion_patterns(combined_exclusions)
    overrides = parse_override_max_length(args.override_max_length)

    input_files = get_input_files(args.files, exclusion_patterns, args.follow_symlinks)

    if not input_files:
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
    supported_files = {f for f in input_files if os.path.splitext(f)[1].lower() in supported_exts}
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
    all_supported_files = get_all_py_files(project_root, exclusion_patterns, args.follow_symlinks)
    
    expander = DependencyExpander(args, project_root, all_supported_files)
    final_files = expander.expand(supported_files)
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
        output_parts.append(grapher.render(final_supported_files))

    if args.entanglement_map:
        entangler = EntanglementMap(args, project_root, all_supported_files)
        output_parts.append(entangler.render(final_supported_files))

    if args.show_stats:
        stats_gen = StatsGenerator(args, project_root, all_supported_files)
        output_parts.append(stats_gen.render(final_files_list))

    # --- Main File Content Processing ---
    # This part is now separate from stats generation
    if not (args.directory_listing or args.show_stats or args.entanglement_map):
        unique_words = collect_unique_words(final_files_list, exclusion_patterns, args.encoding) if (args.truncate or args.show_min_truncation_length) else set()

        if args.show_min_truncation_length:
            min_trunc_length = find_min_truncation_length(unique_words, args.max_length)
            print(f"Minimal truncation length: {min_trunc_length}" if min_trunc_length else "No suitable minimal truncation length found.")
            sys.exit(0)

        file_content_output, _ = process_files(
            final_files_list, exclusion_patterns, args.follow_symlinks, args.encoding,
            args.minify_python, args.truncate, args.max_length, overrides,
            unique_words, args.verbose, False # show_stats is handled separately
        )
        if file_content_output.strip():
            output_parts.append(file_content_output)

    result = "\n".join(output_parts)

    if not args.no_clip:
        copy_to_clipboard(result, args.max_clip_length)
        logging.info("Output copied to clipboard.")
    else:
        print(result)

    end_time = time.time()
    logging.info(f"Processing completed in {end_time - start_time:.2f} seconds")
    sys.exit(0)


if __name__ == "__main__":
    main()
