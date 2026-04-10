#!/bin/bash
# Bridge: mentu-interceptor → CIR substrate
#
# Reads the interceptor's span_store (SQLite) and service_edges table,
# converts rows to `mentu cir capture` signals.
#
# Usage:
#   interceptor-cir-bridge.sh [span_db_path]
#
# Default span_db_path: ~/.mentu/interceptor/spans.db
#
# Called as a post-step hook in interceptor formulas:
#   "pre": [{"shell": "bash ~/Desktop/mentu-complete/mentu-hooks/scripts/interceptor-cir-bridge.sh"}]

set -euo pipefail

MENTU="${HOME}/.local/bin/mentu"
SPAN_DB="${1:-${HOME}/.mentu/interceptor/spans.db}"

if [ ! -f "$SPAN_DB" ]; then
    echo "  No span database at $SPAN_DB — skipping CIR bridge"
    exit 0
fi

if [ ! -x "$MENTU" ]; then
    echo "  mentu binary not found at $MENTU — skipping CIR bridge"
    exit 0
fi

# Check if CIR is available
$MENTU cir stats >/dev/null 2>&1 || {
    echo "  CIR unavailable — skipping bridge"
    exit 0
}

echo "  CIR bridge: reading $SPAN_DB"

# Export service edges as CIR signals
EDGE_COUNT=0
while IFS='|' read -r src tgt count avg_dur; do
    $MENTU cir capture \
        --kind service_edge \
        --body "$src → $tgt ($count calls, avg ${avg_dur}ms)" \
        --domain traffic \
        --actor "agent:mentu-interceptor" \
        >/dev/null 2>&1 || true
    EDGE_COUNT=$((EDGE_COUNT + 1))
done < <(sqlite3 "$SPAN_DB" "SELECT source, target, call_count, CAST(avg_duration_ms AS INTEGER) FROM service_edges WHERE call_count > 0" 2>/dev/null || true)

# Export recent span summaries (last 50 spans)
SPAN_COUNT=0
while IFS='|' read -r service operation status_code duration; do
    $MENTU cir capture \
        --kind http_flow \
        --body "$service $operation → $status_code (${duration}ms)" \
        --domain traffic \
        --actor "agent:mentu-interceptor" \
        >/dev/null 2>&1 || true
    SPAN_COUNT=$((SPAN_COUNT + 1))
done < <(sqlite3 "$SPAN_DB" "SELECT service_name, operation_name, status_code, duration_ms FROM spans ORDER BY start_time DESC LIMIT 50" 2>/dev/null || true)

echo "  CIR bridge: wrote $EDGE_COUNT edges + $SPAN_COUNT spans to CIR"
