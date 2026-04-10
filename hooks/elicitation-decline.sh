#!/usr/bin/env bash
# elicitation-decline.sh — Auto-decline MCP elicitations in headless mode.
# Defense-in-depth: LoopRunner also handles elicitation stream events,
# but this hook intercepts at the CC level before the SDK transport layer.
# CC source: services/mcp/elicitationHandler.ts, cli/structuredIO.ts

set -euo pipefail

INPUT=$(cat)
SERVER=$(echo "$INPUT" | jq -r '.mcp_server_name // "unknown"' 2>/dev/null)
MESSAGE=$(echo "$INPUT" | jq -r '.message // "none"' 2>/dev/null)

echo "[mentu] Declining elicitation from ${SERVER}: ${MESSAGE}" >&2

# Write CIR signal
SIGNAL_DIR=".mentu/cir/signals"
mkdir -p "$SIGNAL_DIR"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SIGNAL_ID="elicitation-$(date +%s)-$$"
jq -n \
  --arg type "anomaly" \
  --arg kind "elicitation_declined" \
  --arg source "hook:Elicitation" \
  --arg detail "server=$SERVER message=$MESSAGE" \
  --arg timestamp "$TIMESTAMP" \
  '{type: $type, kind: $kind, source: $source, detail: $detail, timestamp: $timestamp}' \
  > "$SIGNAL_DIR/$SIGNAL_ID.json"

# Decline the elicitation
echo '{"action":"decline"}'
