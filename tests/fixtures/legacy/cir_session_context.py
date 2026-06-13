#!/usr/bin/env python3
"""SessionStart + PostCompact hook: inject CIR substrate context into Claude sessions.

Queries mentu CIR for crystallized patterns and recent signals,
outputs them to stdout for Claude Code to inject into context.
Runs on every session start and after context compaction.

Graceful: exits silently if CIR is unavailable.
"""
import json
import subprocess
import sys
import os


def query_cir(args, timeout=5):
    """Run a mentu CIR command and return parsed JSON output."""
    mentu = os.path.expanduser("~/.local/bin/mentu")
    try:
        result = subprocess.run(
            [mentu] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        if not output or output == "[]":
            return None
        return json.loads(output)
    except Exception:
        return None


def main():
    # Read hook input (required by Claude Code hook protocol)
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        hook_input = {}

    # Query patterns
    patterns = query_cir(["cir", "patterns", "--format", "json"])

    # Query recent signals
    signals = query_cir(["cir", "query", "--limit", "5", "--format", "json"])

    if not signals and not patterns:
        return  # Nothing to inject — exit silently

    output = []

    if patterns:
        output.append("## CIR Patterns (compound learning)")
        output.append("")
        for p in patterns[:5]:
            name = p.get("name", p.get("id", "?"))
            count = p.get("recurrenceCount", p.get("recurrence_count", 0))
            strength = p.get("strength", 0)
            desc = p.get("description", "")
            line = f"- **{name}** (seen {count}x, strength: {strength:.0%})"
            if desc:
                line += f" — {desc}"
            output.append(line)
        output.append("")

    if signals:
        output.append("## Recent CIR Evidence")
        output.append("")
        for s in signals[:5]:
            conf = s.get("effectiveConfidence", s.get("effective_confidence"))
            conf_str = f"{conf:.0%}" if isinstance(conf, (int, float)) else "?"
            body = str(s.get("body", ""))[:100]
            ts = str(s.get("ts", "?"))[:10]
            output.append(f"- [{ts}] {body} (confidence: {conf_str})")
        output.append("")

    print("\n".join(output))


if __name__ == "__main__":
    main()
