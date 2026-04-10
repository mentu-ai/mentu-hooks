#!/usr/bin/env bash
# Hook: instructions-loaded-cir
# Fires: InstructionsLoaded — when CLAUDE.md loads
# Input (stdin JSON): base hook input + file_path, memory_type, load_reason
# Purpose: Emit CIR lifecycle signal for context tracking

set -euo pipefail
INPUT=$(cat)

FILE_PATH=$(echo "$INPUT" | jq -r '.file_path // "unknown"')
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Ensure signals directory exists
SIGNAL_DIR=".mentu/cir/signals"
mkdir -p "$SIGNAL_DIR"

# Write CIR lifecycle signal
SIGNAL_ID="instrload-$(date +%s)-$$"
jq -n \
  --arg type "lifecycle" \
  --arg kind "instructions_loaded" \
  --arg source "hook:InstructionsLoaded" \
  --arg detail "$FILE_PATH" \
  --arg timestamp "$TS" \
  '{type: $type, kind: $kind, source: $source, detail: $detail, timestamp: $timestamp}' \
  > "$SIGNAL_DIR/$SIGNAL_ID.json"

exit 0
