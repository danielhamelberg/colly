# Quickstart

Run these commands from the repository root with Python 3.10 or newer. Python 3.12 is the version used by the Windows CI workflow.

## Print a context bundle

```powershell
python colly.py -n --no-dependency-graph -f colly.py
```

The command prints Markdown. `-n` prevents clipboard use. For dependency graphs and neighboring files, see the [tool guide](../.agents/tools/colly-tool.md).

## Run the verified agent example

```powershell
python docs/examples/collect_context.py
```

The [example source](examples/collect_context.py) creates a temporary project with one Python file, submits a complete JSON request, checks the result and both artifact hashes, and confirms that the input was preserved. It prints `Agent example passed` after those checks. The temporary project is removed when the example exits.

For your own project, use an absolute `root`, root-relative selectors, and two distinct artifact paths. Save the complete request as UTF-8 JSON, then run:

```powershell
python colly.py --agent --request-json request.json
```

The example is the executable request template. The [generated reference](reference/agent-request.md) lists every required field. Keep `replace` false unless replacement is intentional. A successful call returns one JSON record on stdout; diagnostics use stderr. Treat both `status: "ok"` and exit code zero as required for success.

## Handle failures

Missing files, invalid paths, decoding errors, collisions, and exceeded budgets produce structured errors. Correct the request before retrying. Narrow selectors or explicitly revise a budget; do not treat a missing artifact as a successful partial result. See the [contract](agent-tool-contract.md) for limits and recovery boundaries.
