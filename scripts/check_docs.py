"""Check maintained docs, schema-reference drift and the executable quickstart."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/reference/agent-request.md"


def render_reference(schema):
    lines = [
        "# Agent request reference", "",
        "Generated from [the request schema](../../schemas/colly-agent-request.schema.json).",
        "Run `python scripts/check_docs.py --write` to refresh; do not edit manually.", "",
        "Required fields are marked below. " + ("Unknown fields are rejected." if schema.get("additionalProperties") is False else "Additional fields follow the schema policy.") + " See the",
        "[contract](../agent-tool-contract.md) for runtime semantics and the",
        "[example](../examples/collect_context.py) for a complete request.", "",
        "| Field | Type | Required | Schema constraints |", "|---|---|---|---|",
    ]
    for name, prop in schema["properties"].items():
        constraints = []
        for key in ("const", "enum", "minimum", "minLength", "minItems"):
            if key in prop:
                constraints.append(f"{key}: `{json.dumps(prop[key])}`")
        if "items" in prop:
            constraints.append(f"items: `{json.dumps(prop['items'], sort_keys=True)}`")
        required = "Yes" if name in schema.get("required", []) else "No"
        lines.append(f"| `{name}` | {prop['type']} | {required} | {'; '.join(constraints) or 'See runtime contract'} |")
    return "\n".join(lines) + "\n"


def local_link_errors(text, page, root):
    errors = []
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", line):
            target = target.strip().strip("<>")
            url = urlsplit(target)
            if url.scheme or url.netloc or not url.path:
                continue
            destination = (page.parent / unquote(url.path)).resolve()
            if not destination.is_relative_to(root.resolve()) or not destination.exists():
                errors.append(f"{page.relative_to(root)}:{number}: missing or external local target: {target}")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Refresh generated reference before checking")
    args = parser.parse_args(argv)
    schema = json.loads((ROOT / "schemas/colly-agent-request.schema.json").read_text(encoding="utf-8"))
    expected = render_reference(schema)
    if args.write:
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_text(expected, encoding="utf-8", newline="\n")
    errors = []
    if not REFERENCE.exists() or REFERENCE.read_bytes() != expected.encode("utf-8"):
        errors.append("Request reference drift: run python scripts/check_docs.py --write")
    pages = json.loads((ROOT / "docs/manifest.json").read_text(encoding="utf-8"))["pages"]
    for relative in pages:
        page = (ROOT / relative).resolve()
        if not page.is_relative_to(ROOT) or not page.is_file():
            errors.append(f"Missing or external documentation page: {relative}")
            continue
        errors.extend(local_link_errors(page.read_text(encoding="utf-8"), page, ROOT))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    subprocess.run([sys.executable, str(ROOT / "docs/examples/collect_context.py")], cwd=ROOT, check=True, timeout=30)
    print(f"Documentation checks passed: {len(pages)} pages, schema reference, executable example")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
