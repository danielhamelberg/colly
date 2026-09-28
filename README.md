# Colly

Colly collects source files into Markdown context bundles, with optional dependency graphs, bounded context expansion, and graph annotations. Agent mode writes a bundle and a canonical JSON provenance inventory through an explicit, bounded request contract.

## Requirements

Python 3.10 or newer. The core CLI and test suite use the standard library. Installing `chardet` optionally improves encoding detection in the legacy CLI; agent mode uses the requested encoding without lossy decoding.

## Usage

See the [documentation index](docs/index.md) and [executable quickstart](docs/quickstart.md).

```powershell
python colly.py -n --no-dependency-graph -f src
python colly.py --agent --request-json request.json
```

`-n` prints legacy output instead of using the clipboard. Agent request examples, schemas, budgets, and error behavior are in [the contract](docs/agent-tool-contract.md). The [tool guide](.agents/tools/colly-tool.md) covers graphs and context expansion. `colly.py` replaces the duplicate `colly3.py` entry point.

## Verification

From the repository root:

```powershell
python -m unittest discover -v
python scripts/check_docs.py
python -m harness.agent_tool_eval --provider offline-contract --ledger artifacts/verification/ledger.jsonl
python -m harness.agent_tool_adjudicate --ledger artifacts/verification/ledger.jsonl --acceptance artifacts/verification/acceptance.json --verify-repository
```

The 24-case suite is pinned by exact SHA-256 bytes; changing its contents requires a separately reviewed evaluator version. The adjudicator validates unique case identities, suite hashes, result status, call/retry limits, and safety findings. Evidence remains Class C (local synthetic fixtures). Passing these checks does not establish a comparative capability gain or live model compatibility. Live adapters are experimental, and their compatibility decisions remain quarantined pending independent content and lifecycle validation.

Filesystem safety assumes a trusted, nonconcurrent local workspace. Cooperative deadlines and path checks do not provide an operating-system sandbox against another process changing files during execution. Windows may skip real symlink tests when the account lacks symlink privileges; mocked boundary tests still run.
