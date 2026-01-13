"""
Brynhild Deno Plugin - Sandboxed Python execution via Deno + Pyodide.

This plugin provides the python_sandbox tool for executing Python code
in a WebAssembly sandbox with no host filesystem or network access.
"""

import pathlib as _pathlib

__version__ = "0.1.0"


def get_plugin_root() -> _pathlib.Path:
    """Get the root directory of this plugin package."""
    return _pathlib.Path(__file__).parent


def register():
    """
    Entry point for Brynhild plugin discovery.

    Returns PluginManifest describing the plugin. Brynhild wraps this
    automatically with a synthetic path for entry-point plugins.

    Tools are discovered via the brynhild.tools entry point in pyproject.toml,
    not from a tools/ directory scan.
    """
    # Import here to avoid circular imports during discovery
    try:
        import brynhild.plugins.manifest as manifest
    except ImportError:
        raise RuntimeError(
            "brynhild must be installed to use this plugin via entry points. "
            "Install with: pip install brynhild"
        )

    return manifest.PluginManifest(
        name="deno-sandbox",
        version=__version__,
        description="Sandboxed Python execution using Deno + Pyodide (WebAssembly)",
        # Tools are registered via brynhild.tools entry point
        tools=["python_sandbox"],
    )

