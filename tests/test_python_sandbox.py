"""Tests for python_sandbox tool capabilities.

These tests verify the claims made in the capabilities documentation.
"""

import asyncio as _asyncio
import json as _json
import pytest as _pytest

import brynhild_deno_plugin.tools.python_sandbox as python_sandbox


@_pytest.fixture
def tool():
    """Create a fresh tool instance for each test."""
    return python_sandbox.Tool()


@_pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = _asyncio.new_event_loop()
    yield loop
    loop.close()


def run_async(coro):
    """Helper to run async code in sync tests."""
    return _asyncio.get_event_loop().run_until_complete(coro)


class TestBasicExecution:
    """Test basic code execution capabilities."""

    def test_simple_expression(self, tool):
        """Basic expression evaluation returns result."""
        result = run_async(tool.execute({"code": "2 + 2"}))
        assert result.success is True
        assert "4" in result.output

    def test_print_statement(self, tool):
        """Print statements are captured in stdout."""
        result = run_async(tool.execute({"code": "print('hello world')"}))
        assert result.success is True
        assert "hello world" in result.output

    def test_multiline_code(self, tool):
        """Multi-line code with functions works."""
        code = """
def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)

factorial(5)
"""
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "120" in result.output

    def test_print_and_expression(self, tool):
        """Both print output and expression result are captured."""
        result = run_async(tool.execute({"code": "print('side effect'); 42"}))
        assert result.success is True
        assert "side effect" in result.output
        assert "42" in result.output


class TestStatePersistence:
    """Test state persistence across calls."""

    def test_variable_persists(self, tool):
        """Variables defined in one call persist to the next."""
        run_async(tool.execute({"code": "x = 42"}))
        result = run_async(tool.execute({"code": "x * 2"}))
        assert result.success is True
        assert "84" in result.output

    def test_function_persists(self, tool):
        """Functions defined in one call can be called later."""
        run_async(tool.execute({"code": "def double(n): return n * 2"}))
        result = run_async(tool.execute({"code": "double(21)"}))
        assert result.success is True
        assert "42" in result.output

    def test_import_persists(self, tool):
        """Imports persist across calls."""
        run_async(tool.execute({"code": "import math"}))
        result = run_async(tool.execute({"code": "math.pi"}))
        assert result.success is True
        assert "3.14" in result.output

    def test_reset_clears_state(self, tool):
        """Reset clears all state."""
        run_async(tool.execute({"code": "persistent_var = 123"}))
        result = run_async(tool.execute({"code": "persistent_var", "reset": True}))
        assert result.success is False
        assert "NameError" in (result.error or result.output)


class TestErrorHandling:
    """Test error handling capabilities."""

    def test_syntax_error(self, tool):
        """Syntax errors are reported."""
        result = run_async(tool.execute({"code": "def broken("}))
        assert result.success is False
        assert "SyntaxError" in (result.error or result.output)

    def test_runtime_error(self, tool):
        """Runtime errors are reported with traceback."""
        result = run_async(tool.execute({"code": "1 / 0"}))
        assert result.success is False
        assert "ZeroDivisionError" in (result.error or result.output)

    def test_undefined_variable(self, tool):
        """NameError for undefined variables."""
        result = run_async(tool.execute({"code": "undefined_var"}))
        assert result.success is False
        assert "NameError" in (result.error or result.output)


class TestFileSystem:
    """Test virtual filesystem capabilities."""

    def test_file_injection(self, tool):
        """Files can be injected and read."""
        result = run_async(tool.execute({
            "code": "open('test.txt').read()",
            "files": {"test.txt": "hello from file"}
        }))
        assert result.success is True
        assert "hello from file" in result.output

    def test_file_write_and_read(self, tool):
        """Files written in sandbox can be read back."""
        run_async(tool.execute({
            "code": "open('output.txt', 'w').write('written data')"
        }))
        result = run_async(tool.execute({
            "code": "open('output.txt').read()"
        }))
        assert result.success is True
        assert "written data" in result.output

    def test_nested_directory(self, tool):
        """Files can be injected into nested directories."""
        result = run_async(tool.execute({
            "code": "open('subdir/nested/file.txt').read()",
            "files": {"subdir/nested/file.txt": "nested content"}
        }))
        assert result.success is True
        assert "nested content" in result.output


class TestStdlibModules:
    """Test that standard library modules work."""

    def test_json(self, tool):
        """json module works."""
        code = 'import json; json.dumps({"key": "value"})'
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "key" in result.output

    def test_math(self, tool):
        """math module works."""
        result = run_async(tool.execute({"code": "import math; math.sqrt(16)"}))
        assert result.success is True
        assert "4" in result.output

    def test_datetime(self, tool):
        """datetime module works."""
        code = "from datetime import date; date(2026, 1, 10).isoformat()"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "2026-01-10" in result.output

    def test_collections(self, tool):
        """collections module works."""
        code = "from collections import Counter; Counter('abracadabra').most_common(1)"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "a" in result.output

    def test_itertools(self, tool):
        """itertools module works."""
        code = "from itertools import permutations; list(permutations([1,2], 2))"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "(1, 2)" in result.output

    def test_re(self, tool):
        """re module works."""
        code = "import re; re.findall(r'\\d+', 'a1b2c3')"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "1" in result.output

    def test_pathlib(self, tool):
        """pathlib module works with virtual filesystem."""
        code = "from pathlib import Path; Path('/work').exists()"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "True" in result.output

    def test_csv(self, tool):
        """csv module works."""
        code = """
import csv
import io
data = 'a,b,c\\n1,2,3'
list(csv.reader(io.StringIO(data)))
"""
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "['a', 'b', 'c']" in result.output

    def test_hashlib(self, tool):
        """hashlib module works."""
        code = "import hashlib; hashlib.md5(b'test').hexdigest()[:8]"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "098f6bcd" in result.output

    def test_base64(self, tool):
        """base64 module works."""
        code = "import base64; base64.b64encode(b'hello').decode()"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "aGVsbG8=" in result.output

    def test_ast(self, tool):
        """ast module works."""
        code = "import ast; ast.parse('x = 1').body[0].__class__.__name__"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "Assign" in result.output

    def test_sqlite3_not_available(self, tool):
        """sqlite3 is NOT available in Pyodide core (would need vendoring)."""
        result = run_async(tool.execute({"code": "import sqlite3"}))
        assert result.success is False
        assert "ModuleNotFoundError" in (result.error or result.output)


class TestOutputFormats:
    """Test output format options."""

    def test_text_format_default(self, tool):
        """Default text format has labeled sections."""
        result = run_async(tool.execute({"code": "print('out'); 42"}))
        assert result.success is True
        assert "stdout:" in result.output or "result:" in result.output

    def test_json_format(self, tool):
        """JSON format returns parseable JSON."""
        result = run_async(tool.execute({
            "code": "print('hello'); 42",
            "format": "json"
        }))
        assert result.success is True
        # Output should be valid JSON
        data = _json.loads(result.output)
        assert data["ok"] is True
        assert "hello" in data["stdout"]
        assert "42" in data["result"]


class TestResourceLimits:
    """Test resource control capabilities."""

    def test_timeout_parameter_accepted(self, tool):
        """timeout_ms parameter is accepted."""
        result = run_async(tool.execute({
            "code": "1 + 1",
            "timeout_ms": 5000
        }))
        assert result.success is True

    def test_memory_parameter_accepted(self, tool):
        """memory_mb parameter is accepted."""
        result = run_async(tool.execute({
            "code": "1 + 1",
            "memory_mb": 256
        }))
        assert result.success is True


class TestSecurityLimitations:
    """Test that security limitations are enforced."""

    def test_no_os_system(self, tool):
        """os.system is not available or fails."""
        result = run_async(tool.execute({"code": "import os; os.system('ls')"}))
        # Should either fail or return non-zero/error
        # In Pyodide, os.system raises OSError or returns error
        assert result.success is False or "Error" in result.output or "error" in str(result.error).lower()

    def test_no_subprocess(self, tool):
        """subprocess module is not functional."""
        result = run_async(tool.execute({
            "code": "import subprocess; subprocess.run(['ls'])"
        }))
        # Should fail - subprocess doesn't work in WASM
        assert result.success is False or "Error" in (result.error or result.output)

    def test_no_host_filesystem(self, tool):
        """Cannot access host filesystem."""
        result = run_async(tool.execute({"code": "open('/etc/passwd').read()"}))
        assert result.success is False
        # Should get FileNotFoundError or PermissionError
        assert "Error" in (result.error or result.output)


class TestP0Fixes:
    """Tests for P0 audit fixes (critical security/correctness issues)."""

    def test_triple_quotes_in_code(self, tool):
        # P0-B: Code containing triple quotes should work (base64 encoding).
        # Using string concatenation to avoid syntax issues in the test itself.
        code = (
            "def greet(name):\n"
            '    """A function with a docstring."""\n'
            '    return f"Hello, {name}!"\n'
            "\n"
            'greet("World")'
        )
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "Hello, World" in result.output

    def test_triple_single_quotes_in_string(self, tool):
        # P0-B: String containing ''' should work.
        code = "x = \"This string has triple single quotes: '''\"; x"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "'''" in result.output

    def test_triple_single_quote_string_literal(self, tool):
        # P0-B: Actual ''' as Python string delimiters - this is what broke before.
        # The old r'''${json}''' wrapper would terminate early on this.
        code = "x = '''multi\nline\nstring'''; x"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "multi" in result.output

    def test_unicode_in_code(self, tool):
        """P0-B: Unicode characters in code should work."""
        code = '''
# Comment with émojis: 🐍🎉
greeting = "Héllo Wörld! 你好世界 🌍"
greeting
'''
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "Héllo" in result.output or "Hello" in result.output  # May normalize

    def test_backslashes_in_code(self, tool):
        """P0-B: Backslashes should be preserved correctly."""
        code = r'''
import re
pattern = r"\d+\.\d+"
re.findall(pattern, "3.14 and 2.71")
'''
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        assert "3.14" in result.output

    def test_timeout_recovery(self, tool):
        """P0-A: After timeout, subsequent calls should work (process recovered)."""
        import time
        
        # First call: infinite loop that will timeout
        start = time.time()
        result1 = run_async(tool.execute({
            "code": "while True: pass",
            "timeout_ms": 1000  # 1 second timeout
        }))
        elapsed = time.time() - start
        
        assert result1.success is False
        assert "timed out" in (result1.error or "").lower()
        assert "1000ms" in (result1.error or "")  # Should mention the timeout value
        # Should have taken at least ~1 second but not much more
        assert 0.8 < elapsed < 5.0, f"Timeout took unexpected time: {elapsed}s"

        # Second call: should work because process was killed and respawned
        result2 = run_async(tool.execute({"code": "2 + 2"}))
        assert result2.success is True
        assert "4" in result2.output


class TestP1Fixes:
    """Tests for P1 audit fixes (important security improvements)."""

    def test_output_truncation_at_source(self, tool):
        """P1-2.1: Large output should be truncated at source by _LimitedStringIO."""
        # Generate output larger than 10000 chars (LimitedStringIO limit)
        # LimitedStringIO limit (10000) is less than python_sandbox._truncate (12000)
        # so the truncation message remains visible.
        code = "print('x' * 20000)"
        result = run_async(tool.execute({"code": code}))
        assert result.success is True
        # The key assertion: LimitedStringIO adds this SPECIFIC message
        # If this message is present, we KNOW truncation happened in the wrapper
        assert "truncated at source" in result.output
        # Double-check: output should be bounded
        assert len(result.output) < 12500  # 10000 + message + formatting overhead

    def test_file_count_limit(self, tool):
        """P1-2.5: Too many files should be rejected."""
        # Create 101 files (limit is 100)
        files = {f"file{i}.txt": "content" for i in range(101)}
        result = run_async(tool.execute({
            "code": "1",
            "files": files
        }))
        assert result.success is False
        assert "too many files" in (result.error or "").lower()

    def test_file_size_limit(self, tool):
        """P1-2.5: File larger than 1MB should be rejected."""
        # Create a file larger than 1MB
        large_content = "x" * (1_000_001)
        result = run_async(tool.execute({
            "code": "1",
            "files": {"large.txt": large_content}
        }))
        assert result.success is False
        assert "too large" in (result.error or "").lower()


class TestDenoBoundary:
    """P1-2.6: Tests that verify Deno permission boundaries."""

    def test_deno_read_outside_allowed_blocked(self, tool):
        """Deno should not be able to read files outside allowed paths."""
        # Try to read /etc/passwd via Deno's JS API
        code = """
try:
    from js import Deno
    result = Deno.readTextFileSync('/etc/passwd')
    print(f"FAIL: read succeeded, got {len(result)} bytes")
except Exception as e:
    print(f"OK: {type(e).__name__}: {str(e)[:100]}")
"""
        result = run_async(tool.execute({"code": code}))
        # Should either fail or print OK (permission denied)
        assert "FAIL" not in result.output
        # Either prints OK or the whole thing fails
        assert result.success is False or "OK:" in result.output

    def test_deno_write_blocked(self, tool):
        """Deno should not be able to write to host filesystem."""
        code = """
try:
    from js import Deno
    Deno.writeTextFileSync('/tmp/brynhild_test.txt', 'test')
    print("FAIL: write succeeded")
except Exception as e:
    print(f"OK: {type(e).__name__}")
"""
        result = run_async(tool.execute({"code": code}))
        assert "FAIL" not in result.output

    def test_network_blocked_by_default(self, tool):
        """Network access should be blocked by default."""
        # fetch may be available as a symbol but should fail when actually called
        code = """
import asyncio
async def try_fetch():
    try:
        from js import fetch
        # Actually try to fetch - this should fail
        resp = await fetch('https://example.com')
        return f"FAIL: fetch succeeded with status {resp.status}"
    except Exception as e:
        return f"OK: {type(e).__name__}: {str(e)[:50]}"

print(asyncio.get_event_loop().run_until_complete(try_fetch()))
"""
        result = run_async(tool.execute({"code": code}))
        # Either the fetch call fails or we get a permission error
        assert "FAIL" not in result.output or result.success is False

