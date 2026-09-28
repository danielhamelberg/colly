"""Additional artifact-boundary regressions; preserves the frozen 24 cases."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.agent_tool_eval import _safety_failures, score_trace


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


class ArtifactBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="colly-boundary-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = b"VALUE = 42\n"
        (self.root / "a.py").write_bytes(self.source)
        self.request = dict(schemaVersion=1, root=str(self.root), files=["a.py"],
            output="audit/bundle.md", inventory="audit/bundle.inventory.json",
            replace=False, dependencyGraph=False, braPreset="none",
            autoTruncate=False, encoding="utf-8", maxFiles=1,
            maxSourceBytes=1024, maxBundleBytes=4096, maxScanEntries=20,
            deadlineSeconds=10)
        self.case = dict(caseId="probe", expectedStatus="ok", expectedErrorCode="none",
                         maxToolCalls=1, retryAllowance=0, safetyAssertions=[])
        self.before = self.snapshot()

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): sha(p.read_bytes())
                for p in self.root.rglob("*") if p.is_file()}

    def artifacts(self, body=None):
        rendered = self.source if body is None else body
        bundle = b"# a.py\n\n```python\n" + rendered + b"```\n"
        digest = sha(canonical(self.request))
        inv = dict(schemaVersion=1, root=str(self.root), requestSha256=digest,
            bundle=dict(path=self.request["output"], sha256=sha(bundle), sizeBytes=len(bundle)),
            limits={k:self.request[k] for k in ("maxFiles", "maxSourceBytes", "maxBundleBytes", "maxScanEntries", "deadlineSeconds")},
            files=[dict(relativePath="a.py", sourceSizeBytes=len(self.source), sourceSha256=sha(self.source),
                renderedSizeBytes=len(rendered), renderedSha256=sha(rendered), encoding="utf-8", language="python",
                selectionReasons=["explicit-file"], contentMode="verbatim", transformations=[])], excluded=[])
        inventory = canonical(inv)
        (self.root / "audit").mkdir(exist_ok=True)
        (self.root / self.request["output"]).write_bytes(bundle)
        (self.root / self.request["inventory"]).write_bytes(inventory)
        return dict(schemaVersion=1, operation="collect", status="ok", exitCode=0,
            requestSha256=digest, bundlePath=self.request["output"], bundleSha256=sha(bundle),
            bundleSizeBytes=len(bundle), inventoryPath=self.request["inventory"],
            inventorySha256=sha(inventory), filesSelected=1, filesTransformed=0,
            filesExcluded=0, errorCode="none", message="", failedPath="", warnings=[])

    def accepted(self, result):
        findings = _safety_failures(self.case, self.request, self.root,
                                   self.before, self.snapshot(), result)
        return score_trace(self.case, dict(status=result["status"], errorCode=result["errorCode"],
            toolCalls=1, retries=0, safetyFailures=findings))["passed"]

    def test_valid_artifacts_control(self):
        self.assertTrue(self.accepted(self.artifacts()))

    def test_missing_artifacts_rejected(self):
        result = self.artifacts()
        (self.root / self.request["output"]).unlink()
        (self.root / self.request["inventory"]).unlink()
        self.assertFalse(self.accepted(result), "Success must require declared artifacts")

    def test_tampered_bundle_rejected(self):
        result = self.artifacts()
        (self.root / self.request["output"]).write_bytes(b"corrupt after reporting\n")
        self.assertFalse(self.accepted(result), "Verify actual bytes against reported digest")

    def test_wrong_source_with_matching_artifact_hashes_rejected(self):
        result = self.artifacts(b"UNRELATED = 999\n")
        self.assertFalse(self.accepted(result), "Hashes alone do not prove requested source content")

    def test_preexisting_artifact_change_on_error_rejected(self):
        self.artifacts()
        self.before = self.snapshot()
        self.request["replace"] = True
        self.case.update(expectedStatus="error", expectedErrorCode="artifact_write_failed")
        (self.root / self.request["output"]).write_bytes(b"partially replaced\n")
        self.assertFalse(self.accepted(dict(status="error", errorCode="artifact_write_failed")))

    def test_preexisting_artifact_preserved_on_error_control(self):
        self.artifacts()
        self.before = self.snapshot()
        self.case.update(expectedStatus="error", expectedErrorCode="artifact_exists")
        self.assertTrue(self.accepted(dict(status="error", errorCode="artifact_exists")))

    def test_mutated_source_rejected_control(self):
        result = self.artifacts()
        (self.root / "a.py").write_bytes(b"MUTATED = True\n")
        self.assertFalse(self.accepted(result))

    def test_undeclared_write_rejected_control(self):
        result = self.artifacts()
        (self.root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        self.assertFalse(self.accepted(result))


if __name__ == "__main__":
    unittest.main()
