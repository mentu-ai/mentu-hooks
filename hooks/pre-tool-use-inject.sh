#!/usr/bin/env bash
# Hook: pre-tool-use-inject — PreToolUse (matcher: Agent).
# Enriches a sub-agent prompt with read-only CIR ledger context via the
# mentu_policy claude adapter (supply tier). Resolves the package relative to
# its own location so a deployed copy works after a future installer run.
# Additive only; fails open to {}.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -d "$SCRIPT_DIR/../mentu_policy" ]; then PKG="$SCRIPT_DIR/.."; else PKG="$SCRIPT_DIR"; fi
python3 "$PKG/mentu_policy/adapters/shim.py" --agent claude --event inject || echo '{}'
