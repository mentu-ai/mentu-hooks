#!/usr/bin/env bash
# Mentu CIR Hook — Cursor Agent Adapter (thin shim).
# Maps Cursor events (passed as $1) to CIR signals AND wires the real
# permission verdict via the mentu_policy cursor adapter — the hardcoded
# {"continue":true} is gone; the verdict now comes from evaluate(). Resolves the
# package relative to its own location. Fails open to {}.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -d "$SCRIPT_DIR/../mentu_policy" ]; then PKG="$SCRIPT_DIR/.."; else PKG="$SCRIPT_DIR"; fi
python3 "$PKG/mentu_policy/adapters/shim.py" --agent cursor "${1:-unknown}" || echo '{}'
