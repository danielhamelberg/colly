# Documentation as code

Documentation lives beside implementation and is reviewed in the same branch and pull request. A behavior change must update its contract, example, and relevant tests together.

## Local workflow

1. Edit the relevant Markdown source or canonical JSON Schema.
2. If the request schema changed, regenerate its reference:

   ```powershell
   python scripts/check_docs.py --write
   ```

3. Run the documentation gate:

   ```powershell
   python scripts/check_docs.py
   ```

4. Run the full tests and frozen evaluation described in the [README](../README.md).
5. Review the diff and commit the source, generated reference, and verification evidence together.

## Checks and scope

[The manifest](manifest.json) lists maintained documentation. The checker validates that every listed file exists, verifies repository-local inline Markdown link targets outside fenced code, checks the generated request reference for drift, and executes the [agent example](examples/collect_context.py) in a temporary workspace. The same gate runs in [CI](../.github/workflows/verify.yml).

Use relative inline Markdown links for local references. External URLs and heading fragments are not validated; this is a local file-link check, not a full Markdown parser or website crawler. Add new maintained pages to the manifest. Historical plans and dated verification records remain versioned but are outside the maintained-page link gate because they can refer to planned or historical paths.

`--write` only refreshes the generated reference; it then runs the same checks. Do not edit the generated reference manually. Do not execute arbitrary Markdown fences in CI: executable documentation is limited to the reviewed Python example named by the checker.

## Evidence and review

Label empirical claims with their scope. Local fixture checks are Class C evidence; live compatibility remains quarantined. Dated acceptance files are historical snapshots, and must not be reused to certify code with different recorded hashes. Changes to schemas, tests, scoring or frozen cases need explicit review; improving wording is not evidence of improved capability.
