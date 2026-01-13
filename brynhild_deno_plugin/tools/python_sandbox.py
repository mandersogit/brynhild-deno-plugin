"""Brynhild tool: sandboxed Python interpreter via Deno + Pyodide.

This tool spawns a locked-down Deno process that runs Pyodide (Python compiled to WebAssembly).
It accepts Python code, executes it inside Pyodide, and returns stdout/stderr plus the
final-expression result (REPL-like).

Security model (defense-in-depth):
- Pyodide executes Python in WebAssembly, isolated from the host OS by default.
- Deno runs with explicit permissions; this tool launches Deno with:
  - no network access (--no-remote)
  - no environment variable access
  - read access limited to plugin root (vendored Pyodide files)
  - optional memory limit via V8 flags
- Tool-side timeout kills the Deno process to recover from infinite loops.
- All dependencies are vendored locally - no network access required.

See README.md for setup instructions.
"""

from __future__ import annotations

import asyncio as _asyncio
import json as _json
import os as _os
import pathlib as _pathlib
import shutil as _shutil
import typing as _typing

# brynhild is a required dependency - import directly
import brynhild.tools.base as _base


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _clamp_int(value: int, *, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


class Tool(_base.Tool):
    """Sandboxed Python execution using Deno + Pyodide."""

    def __init__(self) -> None:
        self._lock = _asyncio.Lock()
        self._proc: _asyncio.subprocess.Process | None = None
        self._proc_memory_mb: int | None = None

        self._plugin_root = _pathlib.Path(__file__).resolve().parent.parent
        self._runner_path = self._plugin_root / "deno" / "runner.ts"
        self._vendor_pyodide = self._plugin_root / "vendor" / "pyodide"

        self._deno_bin = _os.environ.get("BRYNHILD_PYODIDE_DENO", "deno")

        self._default_timeout_ms = int(_os.environ.get("BRYNHILD_PYODIDE_TIMEOUT_MS", "30000"))
        self._default_memory_mb = int(_os.environ.get("BRYNHILD_PYODIDE_MEMORY_MB", "512"))
        self._max_output_chars = int(_os.environ.get("BRYNHILD_PYODIDE_MAX_OUTPUT_CHARS", "12000"))

        # Hard-disable network by default; enable only if you understand the implications.
        self._allow_net = _env_bool("BRYNHILD_PYODIDE_ALLOW_NET", default=False)

    @property
    def name(self) -> str:
        return "python_sandbox"

    @property
    def description(self) -> str:
        return (
            "Executes Python code in a sandbox using Deno + Pyodide (WebAssembly). "
            "Returns captured stdout/stderr and the value of the final expression."
        )

    @property
    def input_schema(self) -> dict[str, _typing.Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. The value of the final expression (if any) is returned.",
                },
                "files": {
                    "type": "object",
                    "description": (
                        "Optional mapping of file paths to contents. Files are written into the sandbox "
                        "under /work/<path> (path traversal is rejected)."
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "packages": {
                    "type": "array",
                    "description": (
                        "Optional Pyodide packages to load via pyodide.loadPackage(). "
                        "Only packages included in the vendored Pyodide distribution are available."
                    ),
                    "items": {"type": "string"},
                    "default": [],
                },
                "pythonpath": {
                    "type": "array",
                    "description": "Extra in-sandbox paths to prepend to sys.path (e.g. ['/work']).",
                    "items": {"type": "string"},
                    "default": [],
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Execution timeout in milliseconds. On timeout, the sandbox process is killed.",
                    "default": 30000,
                    "minimum": 1,
                    "maximum": 600000,
                },
                "memory_mb": {
                    "type": "integer",
                    "description": (
                        "V8 heap limit (MB) for the Deno process. Helps limit memory usage. "
                        "This is a best-effort guard, not a strict RSS cap."
                    ),
                    "default": 512,
                    "minimum": 16,
                    "maximum": 4096,
                },
                "reset": {
                    "type": "boolean",
                    "description": "If true, restarts the sandbox process before executing (clears all state).",
                    "default": False,
                },
                "format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "description": (
                        "Output format. 'text' returns a readable block. "
                        "'json' returns a JSON object string."
                    ),
                    "default": "text",
                },
            },
            "required": ["code"],
        }

    @property
    def requires_permission(self) -> bool:
        # The tool runs in a WASM sandbox with no host filesystem/network access,
        # so it's safe to run without permission prompts by default.
        #
        # Sandbox is safe by design (WebAssembly isolation), so no permission needed.
        # Set BRYNHILD_PYODIDE_REQUIRE_PERMISSION=true to enable prompts if desired.
        return _env_bool("BRYNHILD_PYODIDE_REQUIRE_PERMISSION", default=False)

    # Optional (new in Brynhild 0.2.0): classify risk and recovery behavior
    @property
    def risk_level(self) -> str:
        return "read_only"

    @property
    def recovery_policy(self) -> str:
        return "allow"

    async def execute(self, input: dict[str, _typing.Any]) -> _base.ToolResult:
        code = input.get("code")
        if not isinstance(code, str) or not code.strip():
            return _base.ToolResult(success=False, output="", error="code is required and must be a non-empty string")

        timeout_ms = input.get("timeout_ms", self._default_timeout_ms)
        if not isinstance(timeout_ms, int):
            return _base.ToolResult(success=False, output="", error="timeout_ms must be an integer")
        timeout_ms = _clamp_int(timeout_ms, lo=1, hi=600_000)

        memory_mb = input.get("memory_mb", self._default_memory_mb)
        if not isinstance(memory_mb, int):
            return _base.ToolResult(success=False, output="", error="memory_mb must be an integer")
        memory_mb = _clamp_int(memory_mb, lo=16, hi=4096)

        reset = bool(input.get("reset", False))
        fmt = input.get("format", "text")
        if fmt not in ("text", "json"):
            return _base.ToolResult(success=False, output="", error="format must be 'text' or 'json'")

        files = input.get("files") or {}
        if not isinstance(files, dict):
            return _base.ToolResult(success=False, output="", error="files must be an object (mapping path -> content)")
        # Ensure all file values are strings (runner will coerce, but be strict here).
        for k, v in files.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return _base.ToolResult(success=False, output="", error="files must map string paths to string contents")

        packages = input.get("packages") or []
        if not isinstance(packages, list) or any(not isinstance(p, str) for p in packages):
            return _base.ToolResult(success=False, output="", error="packages must be an array of strings")

        pythonpath = input.get("pythonpath") or []
        if not isinstance(pythonpath, list) or any(not isinstance(p, str) for p in pythonpath):
            return _base.ToolResult(success=False, output="", error="pythonpath must be an array of strings")

        payload = {
            "code": code,
            "files": files,
            "packages": packages,
            "pythonpath": pythonpath,
        }

        try:
            resp = await self._call_runner(
                payload,
                timeout_ms=timeout_ms,
                memory_mb=memory_mb,
                reset=reset,
            )
        except FileNotFoundError as e:
            return _base.ToolResult(
                success=False,
                output="",
                error=str(e),
            )
        except _asyncio.TimeoutError:
            return _base.ToolResult(
                success=False,
                output="",
                error=f"Execution timed out after {timeout_ms}ms (sandbox process was killed).",
            )
        except (RuntimeError, OSError) as e:
            return _base.ToolResult(
                success=False,
                output="",
                error=str(e),
            )

        # Truncate very large fields to keep tool outputs manageable.
        stdout = str(resp.get("stdout") or "")
        stderr = str(resp.get("stderr") or "")
        result = resp.get("result")
        error = resp.get("error")

        stdout = self._truncate(stdout)
        stderr = self._truncate(stderr)
        if isinstance(result, str):
            result = self._truncate(result)

        if fmt == "json":
            return _base.ToolResult(
                success=bool(resp.get("ok")),
                output=_json.dumps(
                    {
                        "ok": bool(resp.get("ok")),
                        "stdout": stdout,
                        "stderr": stderr,
                        "result": result,
                        "error": error,
                    },
                    ensure_ascii=False,
                ),
                error=(None if resp.get("ok") else (error or "Python execution failed")),
            )

        # Human-readable format
        blocks: list[str] = []
        if stdout:
            blocks.append("stdout:\n" + stdout.rstrip())
        if stderr:
            blocks.append("stderr:\n" + stderr.rstrip())
        if result is not None:
            blocks.append("result:\n" + str(result).rstrip())
        if not blocks:
            blocks.append("(no output)")

        combined = "\n\n".join(blocks).rstrip() + "\n"

        return _base.ToolResult(
            success=bool(resp.get("ok")),
            output=combined,
            error=(None if resp.get("ok") else (error or "Python execution failed")),
        )

    def _truncate(self, s: str) -> str:
        if len(s) <= self._max_output_chars:
            return s
        head = s[: self._max_output_chars]
        return head + f"\n… [truncated to {self._max_output_chars} chars]"

    async def _call_runner(
        self,
        payload: dict[str, _typing.Any],
        *,
        timeout_ms: int,
        memory_mb: int,
        reset: bool,
    ) -> dict[str, _typing.Any]:
        if not self._runner_path.exists():
            raise FileNotFoundError(f"Runner script not found: {self._runner_path}")

        if not self._vendor_pyodide.exists():
            raise FileNotFoundError(
                f"Vendored Pyodide not found at {self._vendor_pyodide}. "
                "Run scripts/vendor-pyodide.sh to download."
            )

        if _shutil.which(self._deno_bin) is None:
            raise FileNotFoundError(
                f"deno executable not found ({self._deno_bin}). Install Deno and/or set BRYNHILD_PYODIDE_DENO."
            )

        async with self._lock:
            if reset:
                await self._kill_proc_locked()

            if self._proc is None or self._proc.returncode is not None or self._proc_memory_mb != memory_mb:
                await self._kill_proc_locked()
                self._proc = await self._spawn_proc_locked(memory_mb=memory_mb)
                self._proc_memory_mb = memory_mb

            assert self._proc is not None
            proc = self._proc

            # Send request line
            line = _json.dumps(payload, ensure_ascii=False) + "\n"
            assert proc.stdin is not None
            proc.stdin.write(line.encode("utf-8"))
            await proc.stdin.drain()

            # Read one response line with timeout
            assert proc.stdout is not None
            try:
                raw = await _asyncio.wait_for(proc.stdout.readline(), timeout=timeout_ms / 1000.0)
            except _asyncio.TimeoutError:
                # P0-A: Force-kill on timeout to prevent wedged subprocess
                await self._force_kill_proc_locked()
                raise

            if not raw:
                # Process died unexpectedly; read stderr for details (bounded, with timeout).
                err = await self._read_stderr_bounded(proc)
                await self._kill_proc_locked()
                raise RuntimeError(f"Deno runner exited unexpectedly. stderr:\n{err.strip()}")

            text = raw.decode("utf-8", errors="replace").strip()
            try:
                return _json.loads(text)
            except _json.JSONDecodeError:
                # Try to read additional stderr context (bounded, with timeout)
                err = await self._read_stderr_bounded(proc)
                raise RuntimeError(f"Runner returned non-JSON output: {text[:200]}\n\nstderr:\n{err[:500]}")

    async def _spawn_proc_locked(self, *, memory_mb: int) -> _asyncio.subprocess.Process:
        # P1-2.3: Narrow --allow-read to minimum required paths
        # Only allow reading the runner script and vendored Pyodide files
        runner_path = str(self._runner_path.resolve())
        vendor_path = str(self._vendor_pyodide.resolve())

        args = [
            self._deno_bin,
            "run",
            "--quiet",
            "--no-prompt",
            "--no-lock",
            "--no-remote",  # No network for module loading
            "--unstable-detect-cjs",  # Required for Pyodide's CommonJS files
            f"--allow-read={runner_path},{vendor_path}",
            # No --allow-write: vendored mode doesn't need cache writes
        ]

        if self._allow_net:
            # If you enable this, strongly consider restricting domains:
            #   --allow-net=cdn.jsdelivr.net,files.pythonhosted.org,pypi.org
            args.append("--allow-net")

        # Best-effort memory cap for the JS side (V8 heap).
        args.append(f"--v8-flags=--max-old-space-size={memory_mb}")
        args.append(str(self._runner_path))

        env = dict(_os.environ)

        # Important: do NOT grant Deno env access permission. We pass env from parent, but
        # inside Deno the script cannot read it without --allow-env.
        proc = await _asyncio.create_subprocess_exec(
            *args,
            cwd=str(self._plugin_root),
            env=env,
            stdin=_asyncio.subprocess.PIPE,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
        )
        return proc

    async def _read_stderr_bounded(self, proc: _asyncio.subprocess.Process, max_bytes: int = 8192) -> str:
        """Read stderr with bounded size and timeout to prevent hangs (P0-C)."""
        if proc.stderr is None:
            return ""
        try:
            data = await _asyncio.wait_for(proc.stderr.read(max_bytes), timeout=0.5)
            return data.decode("utf-8", errors="replace")
        except _asyncio.TimeoutError:
            return "(stderr read timed out)"
        except Exception:
            return ""

    async def _force_kill_proc_locked(self) -> None:
        """Force-kill subprocess without risking hang (P0-A).
        
        This is called on timeout - we must not wait for graceful shutdown
        because the process may be wedged (e.g., infinite loop).
        """
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        self._proc_memory_mb = None

        # Immediately kill - no graceful shutdown attempt
        try:
            proc.kill()
        except ProcessLookupError:
            pass  # Already dead
        except Exception:
            pass

        # Brief wait for cleanup, then abandon
        try:
            await _asyncio.wait_for(proc.wait(), timeout=1.0)
        except _asyncio.TimeoutError:
            pass  # Orphaned but we've moved on
        except Exception:
            pass

    async def _kill_proc_locked(self) -> None:
        """Gracefully kill subprocess with timeout protection (P0-C)."""
        if self._proc is None:
            return
        proc = self._proc
        self._proc = None
        self._proc_memory_mb = None

        # Try graceful shutdown, but with timeout to prevent hang
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                try:
                    proc.stdin.write(b'{"shutdown": true}\n')
                    # P0-C: Add timeout to drain() to prevent hang if process not reading
                    await _asyncio.wait_for(proc.stdin.drain(), timeout=0.5)
                except _asyncio.TimeoutError:
                    pass  # Process not reading, proceed to kill
                except Exception:
                    pass
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if proc.returncode is None:
                proc.kill()
        except Exception:
            pass

        try:
            await _asyncio.wait_for(proc.wait(), timeout=1.0)
        except Exception:
            pass

