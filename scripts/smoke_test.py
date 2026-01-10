#!/usr/bin/env python3
"""Standalone smoke test for python_sandbox (no Brynhild required).

Prerequisites:
- deno installed and in PATH
- vendor/pyodide/ populated (run scripts/vendor-pyodide.sh)

Usage:
    python scripts/smoke_test.py
    # or
    ./scripts/smoke_test.py
"""

import asyncio as _asyncio
import sys as _sys
import pathlib as _pathlib

# Add project root to path for standalone testing
_project_root = _pathlib.Path(__file__).resolve().parent.parent
_sys.path.insert(0, str(_project_root))

import brynhild_deno_plugin.tools.python_sandbox as python_sandbox  # noqa: E402


async def main() -> int:
    """Run smoke tests and return exit code (0 = success)."""
    print("=== Smoke Test: python_sandbox ===\n")

    tool = python_sandbox.Tool()
    failures = 0

    # Test 1: Basic calculation
    print("Test 1: Basic calculation (2+2)")
    result = await tool.execute({"code": "2 + 2"})
    print(f"  Success: {result.success}")
    print(f"  Output: {result.output.strip()}")
    if result.success and "4" in result.output:
        print("  ✓ PASS\n")
    else:
        print(f"  ✗ FAIL (error: {result.error})\n")
        failures += 1

    # Test 2: Print + expression
    print("Test 2: Print + expression")
    result = await tool.execute({"code": "print('hello'); 'world'"})
    print(f"  Success: {result.success}")
    print(f"  Output: {result.output.strip()[:100]}")
    if result.success and "hello" in result.output and "world" in result.output:
        print("  ✓ PASS\n")
    else:
        print(f"  ✗ FAIL (error: {result.error})\n")
        failures += 1

    # Test 3: Syntax error
    print("Test 3: Syntax error handling")
    result = await tool.execute({"code": "def broken("})
    print(f"  Success: {result.success}")
    error_snippet = (result.error or "")[:60] + "..." if result.error else "None"
    print(f"  Error: {error_snippet}")
    if not result.success and result.error and "SyntaxError" in result.error:
        print("  ✓ PASS\n")
    else:
        print("  ✗ FAIL (expected SyntaxError)\n")
        failures += 1

    # Test 4: State persistence across calls
    print("Test 4: State persistence (variable defined in first call)")
    await tool.execute({"code": "x = 42"})
    result = await tool.execute({"code": "x * 2"})
    print(f"  Success: {result.success}")
    print(f"  Output: {result.output.strip()}")
    if result.success and "84" in result.output:
        print("  ✓ PASS\n")
    else:
        print(f"  ✗ FAIL (error: {result.error})\n")
        failures += 1

    # Test 5: Reset clears state
    print("Test 5: Reset clears state")
    await tool.execute({"code": "y = 100"})
    result = await tool.execute({"code": "y", "reset": True})
    print(f"  Success: {result.success}")
    if not result.success and "NameError" in (result.error or ""):
        print("  ✓ PASS (NameError as expected after reset)\n")
    else:
        print(f"  ✗ FAIL (expected NameError, got: {result.error})\n")
        failures += 1

    # Test 6: Files in sandbox
    print("Test 6: Files in sandbox")
    result = await tool.execute({
        "code": "open('data.txt').read()",
        "files": {"data.txt": "file contents here"},
        "reset": True,
    })
    print(f"  Success: {result.success}")
    print(f"  Output: {result.output.strip()[:60]}")
    if result.success and "file contents here" in result.output:
        print("  ✓ PASS\n")
    else:
        print(f"  ✗ FAIL (error: {result.error})\n")
        failures += 1

    # Summary
    print("=" * 40)
    if failures == 0:
        print("=== All Tests Passed ===")
        return 0
    else:
        print(f"=== {failures} Test(s) Failed ===")
        return 1


if __name__ == "__main__":
    exit_code = _asyncio.run(main())
    _sys.exit(exit_code)

