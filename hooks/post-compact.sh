#!/usr/bin/env bash
# Hook: PostCompact
# Fires: after CC context compaction completes
# Input (stdin JSON): CC base fields + {summary_tokens, removed_tokens} + mentu enrichment
# Purpose: CIR capture of post-compaction state — evidence loss measurement
set -euo pipefail

INPUT=$(cat)

STEP=$(echo "$INPUT" | jq -r '.step_label // "unknown"')
USED=$(echo "$INPUT" | jq -r '.context_tokens_used // .summary_tokens // "unknown"')
RESTORED=$(echo "$INPUT" | jq -r '.files_restored // 0')
REMOVED=$(echo "$INPUT" | jq -r '.removed_tokens // "unknown"')

# Write CIR annotation to ledger
LEDGER=".mentu/ledger.jsonl"
if [ -f "$LEDGER" ]; then
    echo "{\"op\":\"annotate\",\"target\":\"$STEP\",\"body\":\"compact.end: tokens=$USED files_restored=$RESTORED removed=$REMOVED\",\"kind\":\"event\",\"actor\":\"hook:post-compact\"}" >> "$LEDGER"
fi

# Return immediately
echo '{}'

# Fire CIR signal (best-effort)
timeout 2 mentu cir capture \
    --kind context_compact_result \
    --body "compact.end: tokens=$USED files_restored=$RESTORED removed=$REMOVED (step=$STEP)" \
    --domain "$(basename "$PWD")" \
    --actor "hook:post-compact" >/dev/null 2>&1 || true

exit 0
