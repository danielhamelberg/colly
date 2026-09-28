"""Executable agent quickstart; writes only inside a temporary project."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory(prefix="colly-docs-") as directory:
        root = Path(directory)
        original = b"VALUE = 42\n"
        (root / "example.py").write_bytes(original)
        request = {
            "schemaVersion": 1,
            "root": str(root),
            "files": ["example.py"],
            "output": "context.md",
            "inventory": "context.inventory.json",
            "replace": False,
            "dependencyGraph": False,
            "braPreset": "none",
            "autoTruncate": False,
            "encoding": "utf-8",
            "maxFiles": 1,
            "maxSourceBytes": 1024,
            "maxBundleBytes": 4096,
            "maxScanEntries": 10,
            "deadlineSeconds": 10,
        }
        process = subprocess.run(
            [sys.executable, str(REPO_ROOT / "colly.py"), "--agent", "--request-json", "-"],
            input=(json.dumps(request) + "\n").encode("utf-8"),
            capture_output=True, timeout=20, check=True,
        )
        result = json.loads(process.stdout)
        if result["status"] != "ok":
            raise RuntimeError(f"Unexpected result: {result}")
        for name, field in (("context.md", "bundleSha256"), ("context.inventory.json", "inventorySha256")):
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            if actual != result[field]:
                raise RuntimeError(f"Artifact hash mismatch: {name}")
        if (root / "example.py").read_bytes() != original:
            raise RuntimeError("Source file was changed")
        if original not in (root / "context.md").read_bytes():
            raise RuntimeError("Bundle does not contain the requested source")
        json.loads((root / "context.inventory.json").read_text(encoding="utf-8"))
    print("Agent example passed")


if __name__ == "__main__":
    main()
