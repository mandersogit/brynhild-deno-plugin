#!/usr/bin/env bash
#
# vendor-pyodide.sh — Download and vendor Pyodide for air-gapped deployment
#
# Run this ONCE on a machine with network access, then commit vendor/ to the repo.
#
# Usage:
#   ./scripts/vendor-pyodide.sh
#   PYODIDE_VERSION=0.26.0 ./scripts/vendor-pyodide.sh  # specific version
#

set -euo pipefail

# Configuration
PYODIDE_VERSION="${PYODIDE_VERSION:-0.27.0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"
VENDOR_DIR="$PLUGIN_ROOT/vendor"
PYODIDE_DIR="$VENDOR_DIR/pyodide"
LICENSES_DIR="$VENDOR_DIR/LICENSES"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

echo "========================================"
echo " Vendoring Pyodide $PYODIDE_VERSION"
echo "========================================"
echo

# Check for curl
if ! command -v curl &> /dev/null; then
    error "curl is required but not installed"
    exit 1
fi

# Clean existing vendor/pyodide
if [[ -d "$PYODIDE_DIR" ]]; then
    warn "Removing existing $PYODIDE_DIR"
    rm -rf "$PYODIDE_DIR"
fi

# Create directories
mkdir -p "$PYODIDE_DIR" "$LICENSES_DIR"

# Download Pyodide core distribution
info "Downloading pyodide-core-$PYODIDE_VERSION.tar.bz2..."
TARBALL_URL="https://github.com/pyodide/pyodide/releases/download/$PYODIDE_VERSION/pyodide-core-$PYODIDE_VERSION.tar.bz2"

if ! curl -fL "$TARBALL_URL" | tar -xj -C "$VENDOR_DIR/"; then
    error "Failed to download Pyodide from: $TARBALL_URL"
    echo
    echo "Available versions: https://github.com/pyodide/pyodide/releases"
    exit 1
fi

# Move extracted files to standard location
# (tarball extracts to pyodide-X.Y.Z/ or pyodide/)
if [[ -d "$VENDOR_DIR/pyodide-$PYODIDE_VERSION" ]]; then
    mv "$VENDOR_DIR/pyodide-$PYODIDE_VERSION"/* "$PYODIDE_DIR/"
    rmdir "$VENDOR_DIR/pyodide-$PYODIDE_VERSION"
fi

success "Pyodide core downloaded"

# Create packages directory for optional wheels
mkdir -p "$PYODIDE_DIR/packages"

# Download license files
echo
info "Downloading license files..."

# Pyodide - MPL 2.0
curl -sfL "https://raw.githubusercontent.com/pyodide/pyodide/main/LICENSE" \
    -o "$LICENSES_DIR/PYODIDE-MPL2.txt" \
    && success "Pyodide license (MPL 2.0)" \
    || warn "Could not download Pyodide license"

# Deno - MIT
curl -sfL "https://raw.githubusercontent.com/denoland/deno/main/LICENSE.md" \
    -o "$LICENSES_DIR/DENO-MIT.txt" \
    && success "Deno license (MIT)" \
    || warn "Could not download Deno license"

# CPython - PSF
curl -sfL "https://raw.githubusercontent.com/python/cpython/main/LICENSE" \
    -o "$LICENSES_DIR/PYTHON-PSF.txt" \
    && success "Python license (PSF)" \
    || warn "Could not download Python license"

# Create third-party summary
cat > "$LICENSES_DIR/THIRD-PARTY.txt" << 'EOF'
Third-Party Licenses for brynhild-deno-plugin
=============================================

This plugin vendors the following third-party components:

1. Pyodide (vendor/pyodide/)
   - License: Mozilla Public License 2.0 (MPL-2.0)
   - Source: https://github.com/pyodide/pyodide
   - Full license: LICENSES/PYODIDE-MPL2.txt

2. Deno (runtime dependency, not vendored)
   - License: MIT
   - Source: https://github.com/denoland/deno
   - Full license: LICENSES/DENO-MIT.txt

3. CPython/Python Standard Library (embedded in Pyodide)
   - License: Python Software Foundation License
   - Source: https://github.com/python/cpython
   - Full license: LICENSES/PYTHON-PSF.txt

The plugin code itself (tools/, deno/, scripts/) is licensed under MIT.
See the root LICENSE file.
EOF
success "Third-party summary created"

# Print summary
echo
echo "========================================"
echo " Vendoring Complete"
echo "========================================"
echo
info "Pyodide version: $PYODIDE_VERSION"
info "Location: $PYODIDE_DIR"
echo
echo "Contents:"
ls -la "$PYODIDE_DIR/"
echo
echo "Size:"
du -sh "$VENDOR_DIR"
echo
echo "Licenses:"
ls -la "$LICENSES_DIR/"
echo
success "Ready for offline use!"
echo
echo "Next steps:"
echo "  1. git add vendor/"
echo "  2. git commit -m 'Vendor Pyodide $PYODIDE_VERSION'"
echo "  3. python scripts/smoke_test.py"
echo

