#!/usr/bin/env bash
# Mentu CIR Hook — Gemini CLI Adapter (thin shim).
# Maps Gemini events to CIR signals via the mentu_policy gemini adapter. The
# shim parses stdin correctly (fixing the legacy ${INPUT:-{}} parse bug). Gemini
# is observe-only (post-hoc events); a deny/ask degrades to a logged
# capability_degraded signal. Resolves the package relative to its own location.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -d "$SCRIPT_DIR/../mentu_policy" ]; then PKG="$SCRIPT_DIR/.."; else PKG="$SCRIPT_DIR"; fi
python3 "$PKG/mentu_policy/adapters/shim.py" --agent gemini || echo '{}'
