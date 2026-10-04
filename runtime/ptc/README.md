# M3b external PTC runtime

This directory is an isolated Python 3.12/`uv` runtime for the pinned external
PTC implementation. The main project remains Python 3.11 and does not depend
on Daytona or `open-ptc-agent`.

Pinned upstream:

```text
Chen-zexi/open-ptc-agent
425b9575bfdcaf949b0a4053e981cf8070c70e03
```

The runtime pins `mcp<2` because this upstream revision uses the MCP v1
`FastMCP` API.

The runner creates one frozen normalized snapshot per replay case, connects the
eight existing primitives through the thin `diagnostic_mcp.py` wrapper, and
records PTC-specific trace data. It never uploads the repository or credentials
to the sandbox.

## Environment

The real run requires runtime-only environment variables:

```bash
export DEEPSEEK_API_KEY=...
export DAYTONA_API_KEY=...
```

`DEEPSEEK_API_KEY` and `DAYTONA_API_KEY` are never written to traces or files.

## Setup

From the repository root:

```bash
uv python install 3.12
uv sync --project runtime/ptc
```

The runtime uses the same model configuration as the frozen DIRECT baseline:

```text
provider=deepseek
model=deepseek-v4-pro
base_url=https://api.deepseek.com
temperature=0
thinking=disabled
```

## Integration smoke

Run only `service_cascade_001` first:

```bash
uv run --project runtime/ptc python runtime/ptc/runner.py \
  --case service_cascade_001 \
  --trace-file /tmp/ptc-service-smoke.jsonl
```

Only after that succeeds, run each seed case once:

```bash
uv run --project runtime/ptc python runtime/ptc/runner.py \
  --trace-file /tmp/ptc-deepseek-v4-pro.jsonl
```

The ordinary GitHub CI does not run this runtime or require either key.
