#!/usr/bin/env bash
# Mentu CIR Hook — OpenAI Codex CLI Adapter (thin shim).
# Maps Codex events to CIR signals via the mentu_policy codex adapter. The shim
# parses stdin correctly (fixing the legacy ${INPUT:-{}} parse bug). Resolves
# the package relative to its own location. Fire-and-forget; fails open to {}.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -d "$SCRIPT_DIR/../mentu_policy" ]; then PKG="$SCRIPT_DIR/.."; else PKG="$SCRIPT_DIR"; fi
python3 "$PKG/mentu_policy/adapters/shim.py" --agent codex || echo '{}'
