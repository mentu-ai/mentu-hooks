#!/usr/bin/env bash
# Hook: stop-failure-cir
# Fires: StopFailure — when an agent crashes
# Input (stdin JSON): base hook input + error, error_details
# Purpose: Emit CIR anomaly signal so trust score adjusts on agent crash

set -euo pipefail
INPUT=$(cat)

ERROR=$(echo "$INPUT" | jq -r '.error // "unknown"')
DETAILS=$(echo "$INPUT" | jq -r '.error_details // ""')
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Ensure signals directory exists
SIGNAL_DIR=".mentu/cir/signals"
mkdir -p "$SIGNAL_DIR"

# Write CIR anomaly signal
SIGNAL_ID="stopfail-$(date +%s)-$$"
jq -n \
  --arg type "anomaly" \
  --arg kind "agent_crash" \
  --arg source "hook:StopFailure" \
  --arg detail "$ERROR: $DETAILS" \
  --arg timestamp "$TS" \
  '{type: $type, kind: $kind, source: $source, detail: $detail, timestamp: $timestamp}' \
  > "$SIGNAL_DIR/$SIGNAL_ID.json"

exit 0
