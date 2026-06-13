#!/usr/bin/env bash
# Hook: pre-tool-use-permission — PreToolUse for ALL tool calls.
# Trust-banded allow/deny via the mentu_policy claude adapter (one decision
# authority; golden-parity gated). Resolves the package relative to its own
# location so a deployed copy works after a future installer run. Fails open.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [ -d "$SCRIPT_DIR/../mentu_policy" ]; then PKG="$SCRIPT_DIR/.."; else PKG="$SCRIPT_DIR"; fi
python3 "$PKG/mentu_policy/adapters/shim.py" --agent claude --event pre_tool || echo '{}'
