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

try:
    import chardet
except ImportError:
    chardet = None
    logging.warning("chardet module not found. Encoding detection will be limited.")

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

def compile_exclusion_patterns(exclusions: List[str]) -> List[re.Pattern]:
    """Compile exclusion patterns into regex objects using fnmatch.translate."""
    return [re.compile(fnmatch.translate(pattern)) for pattern in exclusions]

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
        return result['encoding'] if result['encoding'] else default_encoding
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
    """Extract unique words from content using \\w+ pattern."""
    return set(re.findall(r'\w+', content))

def collect_unique_words(files: List[str], exclusion_patterns: List[re.Pattern], default_encoding: str) -> Set[str]:
    """Collect unique words from all files, excluding specified patterns."""
    words = set()
    for file_path in files:
        full_path = os.path.abspath(file_path)
        if not os.path.exists(full_path) or is_excluded(full_path, exclusion_patterns):
            continue
        if os.path.isfile(full_path):
            try:
                encoding = detect_encoding(full_path, default_encoding)
                with open(full_path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
                words.update(get_unique_words(content))
            except Exception as e:
                logging.error(f"Failed to read {full_path}: {e}")
        elif os.path.isdir(full_path):
            for root, dirs, files_in_dir in os.walk(full_path):
                dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), exclusion_patterns)]
                for file_name in files_in_dir:
                    file_full_path = os.path.join(root, file_name)
                    if not is_excluded(file_full_path, exclusion_patterns):
                        try:
                            encoding = detect_encoding(file_full_path, default_encoding)
                            with open(file_full_path, 'r', encoding=encoding, errors='replace') as f:
                                content = f.read()
                            words.update(get_unique_words(content))
                        except Exception as e:
                            logging.error(f"Failed to read {file_full_path}: {e}")
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

    chunks = [text[i:i + max_clip_length] for i in range(0, len(text), max_clip_length)]
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
                time.sleep(0.5)
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to copy chunk {idx} to clipboard: {e}")
            break
    logging.info(f"Copied {num_chunks} clipboard section(s)")

def build_verbose_output(truncate: bool, unique_words: Set[str], max_length: int,
                         overrides: List[Tuple[str, int]], follow_symlinks: bool,
                         default_encoding: str, minify_python: bool,
                         exclusion_patterns: List[re.Pattern]) -> List[str]:
    output = []
    # Introduction
    output.append("# Introduction")
    output.append("This output was created by colly.py. It gathers and processes files with optional transformations.")
    output.append("")
    # Determine truncation length if truncate is enabled
    if truncate:
        min_trunc_length = find_min_truncation_length(unique_words, max_length)
        if min_trunc_length is not None:
            truncation_length = min_trunc_length
            logging.info(f"Determined minimal truncation length X: {truncation_length}")
        else:
            truncation_length = None
            logging.warning("No suitable truncation length found within the possible range. Global truncation will not be applied. Overrides may still apply to specific files.")
    else:
        truncation_length = None

    # Truncation information
    if truncate:
        output.append("# Truncation Details")
        if truncation_length is not None:
            output.append(f"- A minimal truncation length of {truncation_length} was determined to preserve word uniqueness.")
            output.append(f"- Words longer than {truncation_length} characters may have been truncated in files without overrides.")
        else:
            output.append("- No suitable minimal truncation length was found within the allowed range -- meaning the truncation length was not set to a value that would preserve word uniqueness in the files.")
            output.append("- Global truncation was not applied to files without overrides.")
        if overrides:
            output.append("- Truncation overrides were applied to specific file patterns.")
        if max_length != 80:
            output.append(f"- The maximum allowed word length for truncation was set to {max_length}.")
        output.append("")
    else:
        output.append("")

    # Python Minification
    if minify_python:
        output.append("# Python Minification")
        output.append("- Python files were minified by removing comments and extra whitespace.")
        output.append("")

    # Symlink Following
    if follow_symlinks:
        output.append("# Symlink Following")
        output.append("- Symbolic links were followed during file traversal.")
        output.append("")

    # Exclusions
    if exclusion_patterns:
        output.append("# Exclusions")
        output.append("- Certain files or directories were excluded based on patterns.")
        output.append("")

    # Encoding
    if default_encoding != 'utf-8':
        output.append("# Encoding")
        output.append(f"- Default encoding set to: {default_encoding}")
        output.append("")

    # Script Run Parameters
    output.append("# Script Run Parameters")
    output.append("```")
    output.append(" ".join(sys.argv[1:]))
    output.append("```")
    output.append("")

    return output

def process_files(files: List[str], exclusion_patterns: List[re.Pattern], follow_symlinks: bool,
                  default_encoding: str, minify_python: bool, truncate: bool, max_length: int,
                  overrides: List[Tuple[str, int]], unique_words: Set[str], verbose: bool) -> str:
    output = []
    if verbose:
        output.extend(build_verbose_output(truncate, unique_words, max_length,
                                           overrides, follow_symlinks,
                                           default_encoding, minify_python,
                                           exclusion_patterns))

    if truncate:
        min_trunc_length = find_min_truncation_length(unique_words, max_length)
        if min_trunc_length is not None:
            truncation_length = min_trunc_length
            logging.info(f"Determined minimal truncation length X: {truncation_length}")
        else:
            truncation_length = None
            logging.warning("No suitable truncation length found within the possible range. Global truncation will not be applied. Overrides may still apply to specific files.")
    else:
        truncation_length = None

    # Process the files and append their content
    num_files_processed = 0
    for file_path in files:
        full_path = os.path.abspath(file_path)
        if not os.path.exists(full_path) or is_excluded(full_path, exclusion_patterns):
            continue
        if os.path.isfile(full_path):
            result = process_single_file(full_path, default_encoding, minify_python, truncate, truncation_length, overrides)
            if result:
                output.extend(result)
                num_files_processed += 1
        elif os.path.isdir(full_path):
            for root, dirs, files_in_dir in os.walk(full_path, followlinks=follow_symlinks):
                dirs[:] = [d for d in dirs if not is_excluded(os.path.join(root, d), exclusion_patterns)]
                for file_name in files_in_dir:
                    file_full_path = os.path.join(root, file_name)
                    if not is_excluded(file_full_path, exclusion_patterns):
                        result = process_single_file(file_full_path, default_encoding, minify_python, truncate, truncation_length, overrides)
                        if result:
                            output.extend(result)
                            num_files_processed += 1

    # Summary
    if verbose:
        output.append("# Summary")
        output.append(f"- Number of files processed: {num_files_processed}")
        output.append("")

    return '\n'.join(output)

def process_single_file(file_path: str, default_encoding: str, minify_python: bool,
                        truncate: bool, truncation_length: Optional[int],
                        overrides: List[Tuple[str, int]]) -> List[str]:
    """Process a single file with optional minification and truncation."""
    output = []
    extension = os.path.splitext(file_path)[1].lower()
    language = language_identifier.get(extension, "plaintext")
    encoding = detect_encoding(file_path, default_encoding)
    try:
        with open(file_path, 'r', encoding=encoding, errors='replace') as f:
            content = f.read()
    except Exception as e:
        logging.error(f"Failed to read {file_path}: {e}")
        return []

    if not content.strip():
        return []

    if minify_python and extension == '.py':
        content = minify_python_code(content)

    if truncate:
        override_max_length = match_override(file_path, overrides)
        trunc_length = override_max_length if override_max_length is not None else truncation_length
        if trunc_length is not None:
            content = truncate_content(content, trunc_length)

    try:
        relative_path = os.path.relpath(file_path)
    except ValueError:
        relative_path = file_path

    output.append(f"## {relative_path}")
    output.append(f"```{language}")
    output.append(content.rstrip())
    output.append("```")
    output.append("")
    return output

def main():
    parser = argparse.ArgumentParser(
        description="Process project files into markdown with optional transformations. Use parameters to control minification, truncation, and more.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="""
Examples of usage:

1) Basic processing with truncation and Python minification:
   python colly.py -f "src/**/*.py" --truncate --minify-python

2) Multiple files and extra exclusions with overrides:
   python colly.py -f "file1.py" "file2.py" --exclude "*.log" --override-max-length "*.py:50"

3) Follow symlinks and specify a different encoding:
   python colly.py -f "someDir/*" --follow-symlinks --encoding "latin-1"

4) Set a custom max word length:
   python colly.py -f "./**/*.md" --truncate --max-length 60
"""
    )
    parser.add_argument('-f', '--files', nargs='+', required=True, help="Files or directories to process (supports wildcards)")
    parser.add_argument('-e', '--exclude', nargs='*', default=[], help="Additional exclusion patterns")
    parser.add_argument('-x', '--max-clip-length', type=int, default=500000, help="Maximum clipboard chunk length")
    parser.add_argument('-s', '--follow-symlinks', action='store_true', help="Follow symbolic links")
    parser.add_argument('-c','--encoding', default='utf-8', help="Default encoding")
    parser.add_argument('-d','--debug', action='store_true', help="Enable debug logging")
    parser.add_argument('-m','--minify-python', action='store_true', help="Minify Python files")
    parser.add_argument('-t','--truncate', action='store_true', help="Enable dynamic truncation")
    parser.add_argument('-l','--max-length', type=int, default=80, help="Max word length for truncation")
    parser.add_argument('-o','--override-max-length', action='append', default=[], help="Pattern:length overrides (e.g., '*.py:50')")
    parser.add_argument('-v','--verbose', action='store_true', help="Show verbose output (info-level logs).")

    args = parser.parse_args()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    start_time = time.time()

    # Expand wildcards and handle patterns
    patterns = []
    for pattern in args.files:
        expanded = glob.glob(pattern, recursive=True)
        patterns.extend(expanded if expanded else [pattern])

    if not patterns:
        logging.error("No files matched the provided patterns.")
        sys.exit(1)

    combined_exclusions = default_exclusions + args.exclude
    exclusion_patterns = compile_exclusion_patterns(combined_exclusions)
    overrides = parse_override_max_length(args.override_max_length)

    unique_words = collect_unique_words(patterns, exclusion_patterns, args.encoding) if args.truncate else set()

    result = process_files(
        patterns, exclusion_patterns, args.follow_symlinks, args.encoding,
        args.minify_python, args.truncate, args.max_length, overrides,
        unique_words, args.verbose
    )

    if result.strip():
        # Pass in the user-supplied max_clip_length to the function
        copy_to_clipboard(result, args.max_clip_length)
    else:
        logging.info("No content generated after processing")

    logging.info(f"Completed in {time.time() - start_time:.2f} seconds")

if __name__ == "__main__":
    main()
