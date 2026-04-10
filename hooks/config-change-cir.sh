#!/usr/bin/env bash
# Hook: config-change-cir
# Fires: ConfigChange — when CC settings change; CwdChanged — when working directory changes
# Input (stdin JSON): base hook input + source/file_path (ConfigChange) or old_cwd/new_cwd (CwdChanged)
# Purpose: Invalidate CIR cache and emit lifecycle signal

set -euo pipefail
INPUT=$(cat)

EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // "ConfigChange"')
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Ensure directories exist
SIGNAL_DIR=".mentu/cir/signals"
CIR_DIR=".mentu/cir"
mkdir -p "$SIGNAL_DIR"

# Touch cache invalidation sentinel
touch "$CIR_DIR/.cache-invalidated"

# Write CIR lifecycle signal
SIGNAL_ID="cfgchg-$(date +%s)-$$"
if [ "$EVENT" = "CwdChanged" ]; then
  OLD_CWD=$(echo "$INPUT" | jq -r '.old_cwd // ""')
  NEW_CWD=$(echo "$INPUT" | jq -r '.new_cwd // ""')
  DETAIL="cwd: $OLD_CWD -> $NEW_CWD"
  KIND="cwd_changed"
else
  SOURCE=$(echo "$INPUT" | jq -r '.source // "unknown"')
  DETAIL="source: $SOURCE"
  KIND="config_change"
fi

jq -n \
  --arg type "lifecycle" \
  --arg kind "$KIND" \
  --arg source "hook:$EVENT" \
  --arg detail "$DETAIL" \
  --arg timestamp "$TS" \
  '{type: $type, kind: $kind, source: $source, detail: $detail, timestamp: $timestamp}' \
  > "$SIGNAL_DIR/$SIGNAL_ID.json"

exit 0
