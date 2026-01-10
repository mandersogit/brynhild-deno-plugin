"""
Brynhild Deno Plugin - Sandboxed Python execution via Deno + Pyodide.

This plugin provides the python_sandbox tool for executing Python code
in a WebAssembly sandbox with no host filesystem or network access.
"""

import pathlib as _pathlib


def get_plugin_root() -> _pathlib.Path:
    """Get the root directory of this plugin package."""
    return _pathlib.Path(__file__).parent


def register():
    """
    Entry point for Brynhild plugin discovery.

    Returns a full Plugin instance with correct paths so tools can be loaded.
    We return Plugin (not just PluginManifest) because entry point plugins
    need real filesystem paths to find their tools/ directory.
    """
    # Import here to avoid circular imports and allow standalone use
    try:
        import brynhild.plugins.manifest as manifest
    except ImportError:
        raise RuntimeError(
            "brynhild must be installed to use this plugin via entry points. "
            "Install with: pip install brynhild"
        )

    plugin_root = get_plugin_root()
    manifest_path = plugin_root / "plugin.yaml"
    plugin_manifest = manifest.load_manifest(manifest_path)

    # Return full Plugin with real path (not synthetic <entry-point>)
    return manifest.Plugin(
        manifest=plugin_manifest,
        path=plugin_root,
    )


__version__ = "0.1.0"

