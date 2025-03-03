# Colly.py

Colly.py is a Python script that processes specified files and directories, applies optional transformations such as Python code minification and dynamic word truncation, and generates a Markdown-formatted output that is automatically copied to the clipboard.

## Features

- **File and Directory Processing**: Process multiple files and directories, with support for wildcard patterns (e.g., `src/**/*.py`).
- **Exclusions**: Exclude specific files or directories using patterns (e.g., `*.log`, `node_modules`), with built-in defaults for common temporary or build directories.
- **Symbolic Link Support**: Optionally follow symbolic links during directory traversal.
- **Encoding Detection**: Automatically detect file encodings, with a fallback to a user-specified default (e.g., `utf-8`).
- **Python Minification**: Minify Python files by removing comments and excess whitespace.
- **Dynamic Word Truncation**: Truncate words to a minimal length that preserves uniqueness across all files, with optional pattern-based overrides (e.g., `*.py:50`).
- **Clipboard Output**: Copy the Markdown output to the clipboard, splitting large outputs into manageable chunks with instructional comments.

## Usage

Run the script from the command line using Python 3.x. The script accepts various arguments to customize its behavior.

### Command-Line Options

- **`-f`, `--files` (required)**  
  Specify files or directories to process. Supports wildcard patterns.  
  *Example*:  
  ```bash
  python colly.py -f "src/**/*.py" "docs/*.md"
  ```

- **`-e`, `--exclude`**  
  Additional patterns to exclude files or directories (combined with default exclusions like `node_modules`, `*.pyc`, etc.).  
  *Example*:  
  ```bash
  python colly.py -f "src/**/*.py" --exclude "*.log" "build"
  ```

- **`-x`, `--max-clip-length`**  
  Maximum length of each clipboard chunk (in characters).  
  **Default**: 500000  
  *Example*:  
  ```bash
  python colly.py -x 100000
  ```

- **`-s`, `--follow-symlinks`**  
  Follow symbolic links when traversing directories.  
  *Example*:  
  ```bash
  python colly.py -s
  ```

- **`-c`, `--encoding`**  
  Default encoding to use if detection fails.  
  **Default**: `utf-8`  
  *Example*:  
  ```bash
  python colly.py -c "latin-1"
  ```

- **`-d`, `--debug`**  
  Enable debug-level logging for troubleshooting.  
  *Example*:  
  ```bash
  python colly.py -d
  ```

- **`-m`, `--minify-python`**  
  Minify Python files by removing comments and excess whitespace.  
  *Example*:  
  ```bash
  python colly.py -m
  ```

- **`-t`, `--truncate`**  
  Enable dynamic truncation of words to preserve uniqueness.  
  *Example*:  
  ```bash
  python colly.py -t
  ```

- **`-l`, `--max-length`**  
  Maximum word length for truncation.  
  **Default**: 80  
  *Example*:  
  ```bash
  python colly.py -l 60
  ```

- **`-o`, `--override-max-length`**  
  Specify truncation length overrides for specific file patterns (format: `PATTERN:LENGTH`). Can be used multiple times.  
  *Example*:  
  ```bash
  python colly.py -o "*.py:50" -o "*.md:40"
  ```

- **`-v`, `--verbose`**  
  Include additional details in the output, such as truncation information and script parameters.  
  *Example*:  
  ```bash
  python colly.py -v
  ```

### Examples

- **Process Python files with truncation and minification:**
  ```bash
  python colly.py -f "src/**/*.py" --truncate --minify-python
  ```

- **Process multiple files with exclusions and overrides:**
  ```bash
  python colly.py -f "file1.py" "file2.py" --exclude "*.log" --override-max-length "*.py:50"
  ```

- **Follow symbolic links with a custom encoding:**
  ```bash
  python colly.py -f "someDir/*" --follow-symlinks --encoding "latin-1"
  ```

- **Set a custom truncation length for Markdown files:**
  ```bash
  python colly.py -f "./**/*.md" --truncate --max-length 60
  ```

## Dependencies

- **Python 3.x**: Required to run the script.
- **chardet (optional)**: Enhances encoding detection. Install with:
  ```bash
  pip install chardet
  ```
  Without chardet, encoding detection falls back to the default (`utf-8` or user-specified).

## Clipboard Support

The script uses platform-specific tools to copy to the clipboard:
- **Windows**: `clip` (built-in).
- **macOS**: `pbcopy` (built-in).
- **Linux**: `xclip` (must be installed, e.g., `sudo apt install xclip`).

## Limitations and Known Issues

- **File Access Errors**: Files that cannot be read (due to permissions or encoding issues) are skipped with an error logged.
- **Truncation Limitations**: If no minimal truncation length preserves word uniqueness within `--max-length`, global truncation is skipped, though pattern-specific overrides still apply.
- **Pattern Matching**: Exclusions and overrides use shell-style wildcards (via `fnmatch`), not full regex.
- **Unsupported Platforms**: Clipboard copying is not supported on platforms other than Windows, macOS, and Linux with `xclip`.
