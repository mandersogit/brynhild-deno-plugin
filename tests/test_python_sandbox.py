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

