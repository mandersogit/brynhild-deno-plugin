# brynhild-deno-plugin

Sandboxed Python execution for [Brynhild](https://github.com/brynhild/brynhild) using Deno + Pyodide (WebAssembly).

## Features

- **Sandboxed execution** — Python runs in WebAssembly, isolated from the host OS
- **Air-gapped ready** — All dependencies vendored locally (no network required)
- **REPL-like output** — Captures stdout/stderr and returns final expression value
- **State persistence** — Variables persist across calls (optional reset)
- **File support** — Inject files into the sandbox for code to read/write

## Prerequisites

- **Deno** ≥2.0 ([install](https://deno.land/manual/getting_started/installation))
- **Python** ≥3.11 (for running the tool wrapper)

## Installation

### Option A: Pip install (recommended)

The plugin registers as a Brynhild entry point:

```bash
# Install alongside brynhild
pip install brynhild git+https://github.com/mandersogit/brynhild-deno-plugin.git

# Or for development
git clone https://github.com/mandersogit/brynhild-deno-plugin.git
pip install -e ./brynhild-deno-plugin
```

Brynhild will automatically discover the plugin via entry points.

### Option B: Clone only

```bash
git clone https://github.com/mandersogit/brynhild-deno-plugin.git
cd brynhild-deno-plugin
```

The `brynhild_deno_plugin/vendor/pyodide/` directory should already contain the vendored Pyodide distribution. If not:

```bash
./scripts/vendor-pyodide.sh
```

### Verify installation

```bash
python scripts/smoke_test.py
```

Expected output: `=== All Tests Passed ===`

## Usage

### Standalone (no Brynhild)

```python
import asyncio
from brynhild_deno_plugin.tools.python_sandbox import Tool

async def main():
    tool = Tool()
    result = await tool.execute({"code": "2 + 2"})
    print(result.output)  # "result:\n4\n"

asyncio.run(main())
```

### With Brynhild

```bash
# If installed via pip, the plugin is auto-discovered
brynhild chat "Use python_sandbox to compute factorial(10)"

# Or use env var for non-installed plugins
export BRYNHILD_PLUGIN_PATH="/path/to/brynhild-deno-plugin/brynhild_deno_plugin"
brynhild chat "Use python_sandbox to compute factorial(10)"
```

## Tool API

### Input Parameters

| Parameter    | Type    | Default    | Description                                   |
|--------------|---------|------------|-----------------------------------------------|
| `code`       | string  | (required) | Python code to execute                        |
| `files`      | object  | `{}`       | Map of path → content to inject into `/work/` |
| `packages`   | array   | `[]`       | Reserved (no packages vendored in v0.1)       |
| `pythonpath` | array   | `[]`       | Paths to add to `sys.path`                    |
| `timeout_ms` | integer | `30000`    | Execution timeout (ms)                        |
| `memory_mb`  | integer | `512`      | V8 heap limit (MB)                            |
| `reset`      | boolean | `false`    | Clear sandbox state before execution          |
| `format`     | string  | `"text"`   | Output format: `"text"` or `"json"`           |

### Output

- **text format** (default): Human-readable blocks for stdout, stderr, result
- **json format**: `{"ok": bool, "stdout": str, "stderr": str, "result": str, "error": str}`

### Examples

```python
# Basic expression
await tool.execute({"code": "sum(range(100))"})

# Print and return
await tool.execute({"code": "print('hello'); 42"})

# Use files
await tool.execute({
    "code": "import json; json.load(open('data.json'))",
    "files": {"data.json": '{"key": "value"}'}
})

# Fresh sandbox (clear state)
await tool.execute({"code": "x = 1", "reset": True})
```

## Security Model

Defense-in-depth approach:

1. **WebAssembly isolation** — Pyodide runs Python in WASM, sandboxed by design
2. **Deno permissions** — Process runs with:
   - `--no-remote` — No network for module loading
   - `--allow-read` — Limited to runner.ts + vendor/pyodide only
   - No `--allow-write`, `--allow-env`, `--allow-run`
3. **Resource limits** — Built-in protection against abuse:
   - Output truncation at 10,000 characters (at source)
   - Max 100 files per request, 1MB per file, 10MB total
   - Request size limit of 1MB
3. **Tool-side timeout** — Kills runaway processes
4. **Memory limits** — V8 heap cap (best-effort)

## Environment Variables

| Variable                              | Default | Description             |
|---------------------------------------|---------|-------------------------|
| `BRYNHILD_PYODIDE_DENO`               | `deno`  | Path to Deno executable |
| `BRYNHILD_PYODIDE_TIMEOUT_MS`         | `30000` | Default timeout         |
| `BRYNHILD_PYODIDE_MEMORY_MB`          | `512`   | Default memory limit    |
| `BRYNHILD_PYODIDE_ALLOW_NET`          | `false` | Enable network access   |
| `BRYNHILD_PYODIDE_REQUIRE_PERMISSION` | `false` | Prompt before execution |

## Project Structure

```
brynhild-deno-plugin/
├── pyproject.toml                 # Python package config + entry points
├── brynhild_deno_plugin/          # Main package (installed via pip)
│   ├── __init__.py                # Entry point registration + manifest
│   ├── deno/
│   │   └── runner.ts              # Deno/Pyodide runner (stdin/stdout JSON)
│   ├── tools/
│   │   └── python_sandbox.py      # Brynhild tool wrapper
│   └── vendor/
│       ├── pyodide/               # Vendored Pyodide (~15MB)
│       └── LICENSES/              # Third-party licenses
├── scripts/
│   ├── vendor-pyodide.sh          # Download Pyodide
│   ├── smoke_test.py              # Integration test
│   └── commit-helper.py           # Git commit workflow
└── workflow/                      # Development docs
```

## License

- **Plugin code**: MIT License
- **Vendored Pyodide**: Mozilla Public License 2.0
- **Python stdlib**: Python Software Foundation License

See `vendor/LICENSES/` for full license texts.

## Development

### Running tests

```bash
python scripts/smoke_test.py
```

### Manual runner test

```bash
echo '{"code": "2+2"}' | deno run --no-remote --unstable-detect-cjs \
  --allow-read="$(pwd)" brynhild_deno_plugin/deno/runner.ts
```

### Updating Pyodide

Edit `PYODIDE_VERSION` in `scripts/vendor-pyodide.sh` and re-run.

