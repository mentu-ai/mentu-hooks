#!/usr/bin/env bash
# Hook: PreCompact
# Fires: when CC begins context compaction (before evidence is destroyed)
# Input (stdin JSON): CC base fields + {token_count, compaction_type} + mentu enrichment
# Purpose: CIR capture of pre-compaction state — evidence preservation signal
set -euo pipefail

INPUT=$(cat)

STEP=$(echo "$INPUT" | jq -r '.step_label // "unknown"')
USED=$(echo "$INPUT" | jq -r '.context_tokens_used // .token_count // "unknown"')
MAX=$(echo "$INPUT" | jq -r '.context_tokens_max // "unknown"')

# Write CIR annotation to ledger
LEDGER=".mentu/ledger.jsonl"
if [ -f "$LEDGER" ]; then
    echo "{\"op\":\"annotate\",\"target\":\"$STEP\",\"body\":\"compact.start: tokens=$USED/$MAX\",\"kind\":\"warning\",\"actor\":\"hook:pre-compact\"}" >> "$LEDGER"
fi

# Return immediately
echo '{}'

# Fire CIR signal (best-effort)
timeout 2 mentu cir capture \
    --kind context_compact \
    --body "compact.start: tokens=$USED/$MAX (step=$STEP)" \
    --domain "$(basename "$PWD")" \
    --actor "hook:pre-compact" >/dev/null 2>&1 || true

exit 0
