#!/usr/bin/env bash
# Mentu Universal Agent Hook → CIR Signal Bridge (thin shim).
# Normalizes events from ANY AI tool into CIR signals via the mentu_policy
# universal adapter (one decision authority). Fire-and-forget; never blocks;
# always exits 0. Resolves the package relative to its own location so a
# deployed copy works after a future installer run.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -d "$SCRIPT_DIR/../mentu_policy" ]; then PKG="$SCRIPT_DIR/.."; else PKG="$SCRIPT_DIR"; fi
python3 "$PKG/mentu_policy/adapters/shim.py" --agent mentu || echo '{}'
