#!/usr/bin/env bash
# Mentu CIR Agent Hook Installer
# Installs universal CIR signal hooks into all detected AI tool configs.
# Replaces Superset's notification hooks with Mentu intelligence hooks.
#
# Targets: Claude Code, Cursor, Gemini, Codex
# Idempotent: safe to run multiple times.
set -euo pipefail

HOOKS_SRC="$(cd "$(dirname "$0")/../hooks" && pwd)"
HOOKS_DST="$HOME/.mentu/hooks"
BACKUP_DIR="$HOME/.mentu/backups/$(date +%Y%m%d-%H%M%S)"

log() { echo "[mentu-hooks] $*"; }
warn() { echo "[mentu-hooks] WARNING: $*" >&2; }

mkdir -p "$HOOKS_DST"

# ─── Step 1: Install hook scripts to ~/.mentu/hooks/ ─────────────────────────

HOOK_FILES=(
    mentu_agent_hook.sh
    cursor_cir_hook.sh
    gemini_cir_hook.sh
    codex_cir_hook.sh
)

for f in "${HOOK_FILES[@]}"; do
    src="$HOOKS_SRC/$f"
    dst="$HOOKS_DST/$f"
    if [[ ! -f "$src" ]]; then
        warn "Source hook not found: $src"
        continue
    fi
    cp "$src" "$dst"
    chmod +x "$dst"
    log "Installed $f → $dst"
done

# Also ensure the universal hook has a short alias
ln -sf "$HOOKS_DST/mentu_agent_hook.sh" "$HOOKS_DST/mentu-agent-hook.sh" 2>/dev/null || true

# ─── Step 2: Claude Code — replace Superset hooks in settings.json ───────────

CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$CLAUDE_SETTINGS" ]]; then
    mkdir -p "$BACKUP_DIR"
    cp "$CLAUDE_SETTINGS" "$BACKUP_DIR/claude-settings.json"
    log "Backed up Claude settings → $BACKUP_DIR/claude-settings.json"

    /usr/bin/python3 <<'PYEOF'
import json, sys, os

settings_path = os.path.expanduser("~/.claude/settings.json")
with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.get("hooks", {})

mentu_hook = os.path.expanduser("~/.mentu/hooks/mentu-agent-hook.sh")
mentu_stop = os.path.expanduser("~/.mentu/hooks/stop.sh")
mentu_post = os.path.expanduser("~/.mentu/hooks/post-tool-use.sh")
mentu_pre  = os.path.expanduser("~/.mentu/hooks/pre-tool-use-snapshot.sh")

def is_superset_hook(cmd):
    return "SUPERSET_HOME_DIR" in cmd or "superset" in cmd.lower()

def filter_superset(hook_list):
    """Remove Superset hooks from a hook list, keep everything else."""
    result = []
    for entry in hook_list:
        if "hooks" in entry:
            filtered = [h for h in entry["hooks"] if not is_superset_hook(h.get("command", ""))]
            if filtered:
                entry["hooks"] = filtered
                result.append(entry)
        else:
            result.append(entry)
    return result

# --- PreToolUse: keep existing mentu snapshot hook ---
hooks["PreToolUse"] = [{
    "matcher": "Edit|Write|MultiEdit",
    "hooks": [{"type": "command", "command": mentu_pre, "timeout": 5}]
}]

# --- UserPromptSubmit: mentu universal hook ---
existing = filter_superset(hooks.get("UserPromptSubmit", []))
hooks["UserPromptSubmit"] = existing + [{
    "hooks": [{"type": "command", "command": mentu_hook, "timeout": 3}]
}] if not any(mentu_hook in str(e) for e in existing) else existing

# --- SessionStart: keep existing session-start.sh ---
# Don't touch SessionStart — it's managed separately

# --- Stop: existing stop.sh + universal hook ---
existing = filter_superset(hooks.get("Stop", []))
stop_hooks = []
# Ensure stop.sh is present
if not any(mentu_stop in str(e) for e in existing):
    stop_hooks.append({
        "hooks": [{"type": "command", "command": mentu_stop, "timeout": 10}]
    })
# Ensure universal hook is present
if not any(mentu_hook in str(e) for e in existing):
    stop_hooks.append({
        "hooks": [{"type": "command", "command": mentu_hook, "timeout": 3}]
    })
hooks["Stop"] = existing + stop_hooks

# --- PostToolUse: existing post-tool-use.sh + universal hook ---
existing = filter_superset(hooks.get("PostToolUse", []))
post_hooks = []
if not any(mentu_post in str(e) for e in existing):
    post_hooks.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": mentu_post, "timeout": 10}]
    })
if not any(mentu_hook in str(e) for e in existing):
    post_hooks.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": mentu_hook, "timeout": 3}]
    })
hooks["PostToolUse"] = existing + post_hooks

# --- PostToolUseFailure: universal hook only ---
existing = filter_superset(hooks.get("PostToolUseFailure", []))
if not any(mentu_hook in str(e) for e in existing):
    existing.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": mentu_hook, "timeout": 3}]
    })
hooks["PostToolUseFailure"] = existing

# --- PermissionRequest: universal hook only ---
existing = filter_superset(hooks.get("PermissionRequest", []))
if not any(mentu_hook in str(e) for e in existing):
    existing.append({
        "matcher": "*",
        "hooks": [{"type": "command", "command": mentu_hook, "timeout": 3}]
    })
hooks["PermissionRequest"] = existing

settings["hooks"] = hooks

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print("[mentu-hooks] Claude Code settings updated — Superset hooks replaced with Mentu CIR hooks")
PYEOF
else
    warn "Claude Code settings not found at $CLAUDE_SETTINGS"
fi

# ─── Step 3: Cursor — install CIR hooks ──────────────────────────────────────

CURSOR_HOOKS="$HOME/.cursor/hooks.json"
if [[ -d "$HOME/.cursor" ]]; then
    if [[ -f "$CURSOR_HOOKS" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$CURSOR_HOOKS" "$BACKUP_DIR/cursor-hooks.json"
        log "Backed up Cursor hooks → $BACKUP_DIR/cursor-hooks.json"
    fi

    cursor_hook="$HOOKS_DST/cursor_cir_hook.sh"
    cat > "$CURSOR_HOOKS" <<CURSOREOF
{
  "beforeSubmitPrompt": "$cursor_hook Start",
  "stop": "$cursor_hook Stop",
  "beforeShellExecution": "$cursor_hook PermissionRequest",
  "beforeMCPExecution": "$cursor_hook PermissionRequest"
}
CURSOREOF
    log "Cursor hooks installed → $CURSOR_HOOKS"
else
    log "Cursor not detected (no ~/.cursor/) — skipping"
fi

# ─── Step 4: Gemini — install CIR hooks ──────────────────────────────────────

GEMINI_SETTINGS="$HOME/.gemini/settings.json"
if [[ -d "$HOME/.gemini" ]]; then
    if [[ -f "$GEMINI_SETTINGS" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$GEMINI_SETTINGS" "$BACKUP_DIR/gemini-settings.json"
        log "Backed up Gemini settings → $BACKUP_DIR/gemini-settings.json"
    fi

    gemini_hook="$HOOKS_DST/gemini_cir_hook.sh"
    /usr/bin/python3 <<PYEOF
import json, os

path = os.path.expanduser("~/.gemini/settings.json")
settings = {}
if os.path.exists(path):
    try:
        with open(path) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, IOError):
        settings = {}

hook = "$gemini_hook"

# Gemini uses nested hooks structure (same as Claude Code)
hooks = settings.get("hooks", {})
for event in ("BeforeAgent", "AfterAgent", "AfterTool"):
    hooks[event] = [{"hooks": [{"type": "command", "command": hook}]}]
settings["hooks"] = hooks

# Remove any spurious top-level hook keys
for key in ("BeforeAgent", "AfterAgent", "AfterTool"):
    settings.pop(key, None)

with open(path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print("[mentu-hooks] Gemini settings updated with CIR hooks")
PYEOF
else
    log "Gemini not detected (no ~/.gemini/) — skipping"
fi

# ─── Step 5: Codex — install CIR hooks ───────────────────────────────────────

CODEX_HOOKS="$HOME/.codex/hooks.json"
if [[ -d "$HOME/.codex" ]]; then
    if [[ -f "$CODEX_HOOKS" ]]; then
        mkdir -p "$BACKUP_DIR"
        cp "$CODEX_HOOKS" "$BACKUP_DIR/codex-hooks.json"
        log "Backed up Codex hooks → $BACKUP_DIR/codex-hooks.json"
    fi

    codex_hook="$HOOKS_DST/codex_cir_hook.sh"
    cat > "$CODEX_HOOKS" <<CODEXEOF
{
  "UserPromptSubmit": "$codex_hook",
  "Stop": "$codex_hook",
  "PostToolUse": "$codex_hook",
  "PostToolUseFailure": "$codex_hook",
  "PermissionRequest": "$codex_hook"
}
CODEXEOF
    log "Codex hooks installed → $CODEX_HOOKS"
else
    log "Codex not detected (no ~/.codex/) — skipping"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────

log ""
log "Installation complete."
log "Backups saved to: $BACKUP_DIR"
log ""
log "All AI tool events now flow through CIR:"
log "  mentu cir query --limit 10          # see recent signals"
log "  mentu cir query --actor agent:cursor # filter by tool"
log "  mentu cir patterns --detect          # find cross-tool patterns"
