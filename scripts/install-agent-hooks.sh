#!/usr/bin/env bash
# Mentu Policy Harness — capability-aware agent hook installer (M4).
#
# Deploys the self-contained policy package (mentu_policy/) + the shim-based
# hook files to ~/.mentu/hooks/, then wires each detected agent's native config
# to them. The wiring is CAPABILITY-AWARE: pre-action GATE events are wired only
# where the capability registry (mentu_policy.capabilities) says gate:True
# (Claude/Codex/Cursor). Gemini gets OBSERVE-ONLY wiring — its lifecycle events
# are post-hoc (gate:False), so it is never advertised a pre-action gate it
# cannot honor.
#
# Preserved virtues of the legacy installer: opt-in, backs up each native config
# to ~/.mentu/backups/<ts>/ before editing, only touches agents whose config dir
# exists, and is idempotent (re-running adds no duplicate entries).
#
# Every path is derived from $HOME, so the test suite (tests/test_installer.py)
# exercises it against a mktemp HOME. NEVER run this against your real home —
# tests and manual checks always invoke it with HOME=$(mktemp -d).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOOKS_REPO=$(cd "$SCRIPT_DIR/.." && pwd)
SRC_HOOKS="$HOOKS_REPO/hooks"
SRC_PKG="$HOOKS_REPO/mentu_policy"

HOOKS_DST="$HOME/.mentu/hooks"
PKG_DST="$HOOKS_DST/mentu_policy"
BACKUP_DIR="$HOME/.mentu/backups/$(date +%Y%m%d-%H%M%S)-$$"

log()  { echo "[mentu-hooks] $*"; }
warn() { echo "[mentu-hooks] WARNING: $*" >&2; }
backup() {  # backup <file> <name-in-backup-dir>
    [[ -f "$1" ]] || return 0
    mkdir -p "$BACKUP_DIR"
    cp "$1" "$BACKUP_DIR/$2"
    log "Backed up $1 → $BACKUP_DIR/$2"
}

mkdir -p "$HOOKS_DST"

# ─── Step 1: deploy the self-contained policy package ────────────────────────
# A deployed copy lives at ~/.mentu/hooks/mentu_policy so the shims resolve the
# package relative to their own location (no PYTHONPATH / CWD dependency).
rm -rf "$PKG_DST"
cp -R "$SRC_PKG" "$PKG_DST"
find "$PKG_DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
log "Deployed mentu_policy package → $PKG_DST"

# ─── Step 2: deploy the shim-based hook files ────────────────────────────────
SHIM_HOOKS=(
    mentu_agent_hook.sh         # universal observe (all agents)
    codex_cir_hook.sh           # codex adapter shim
    cursor_cir_hook.sh          # cursor adapter shim (real permission verdict)
    gemini_cir_hook.sh          # gemini adapter shim (observe-only / degrade)
    pre-tool-use-permission.sh  # claude trust-banded gate
    pre-tool-use-firewall.py    # ABSOLUTE repo-wipe firewall (trust-independent backstop)
    pre-tool-use-inject.sh      # claude sub-agent prompt enrichment (supply)
    review_gate.py              # claude Stop gate
    context_isolation_gate.py   # claude SubagentStop gate
)
for f in "${SHIM_HOOKS[@]}"; do
    src="$SRC_HOOKS/$f"
    if [[ ! -f "$src" ]]; then
        warn "Source hook not found: $src"
        continue
    fi
    cp "$src" "$HOOKS_DST/$f"
    chmod +x "$HOOKS_DST/$f"
done
log "Deployed shim hooks → $HOOKS_DST"

# ─── Step 3: Claude Code — full wiring (gate:True) ───────────────────────────
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$CLAUDE_SETTINGS" ]]; then
    backup "$CLAUDE_SETTINGS" claude-settings.json
    MENTU_HOOKS_DST="$HOOKS_DST" CLAUDE_SETTINGS="$CLAUDE_SETTINGS" /usr/bin/env python3 <<'PY'
import json, os, sys
dst = os.environ["MENTU_HOOKS_DST"]
sp = os.environ["CLAUDE_SETTINGS"]
sys.path.insert(0, dst)
try:
    from mentu_policy import capabilities
    can_gate = capabilities.supports("claude", "gate")
except Exception:
    can_gate = True  # fail-open: a missing registry never silently drops gating

with open(sp) as f:
    settings = json.load(f)
if not isinstance(settings, dict):
    settings = {}
hooks = settings.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}


def H(name):
    return os.path.join(dst, name)


def entry(cmd, matcher=None, timeout=10):
    e = {}
    if matcher is not None:
        e["matcher"] = matcher
    e["hooks"] = [{"type": "command", "command": cmd, "timeout": timeout}]
    return e


def ensure(event, cmd, matcher=None, timeout=10):
    """Idempotent: append the mentu hook only if its command is not already
    wired for this event (re-running never duplicates)."""
    lst = hooks.get(event)
    if not isinstance(lst, list):
        lst = []
    if not any(cmd in json.dumps(e) for e in lst):
        lst = lst + [entry(cmd, matcher, timeout)]
    hooks[event] = lst


universal = H("mentu_agent_hook.sh")
perm = H("pre-tool-use-permission.sh")
inject = H("pre-tool-use-inject.sh")
review = H("review_gate.py")
iso = H("context_isolation_gate.py")

# observe (always) — the universal hook records each lifecycle signal.
ensure("UserPromptSubmit", universal, timeout=3)
ensure("PostToolUse", universal, matcher="*", timeout=3)
ensure("PostToolUseFailure", universal, matcher="*", timeout=3)
ensure("PermissionRequest", universal, matcher="*", timeout=3)

# ABSOLUTE safety firewall — blocks catastrophic repo-destroying Bash commands (rm -rf of a
# repo/.git/home/root, git clean -fx). Installed UNCONDITIONALLY (NOT gated by can_gate): it is
# a hard backstop, not a trust gate, and must fire even under --dangerously-skip-permissions.
firewall = "python3 " + H("pre-tool-use-firewall.py")
# One combined matcher (ensure() dedups by command, so a single entry must cover every tool the
# firewall inspects): Bash (rm/git-clean/find) + Write/Edit/... (writes into .git).
ensure("PreToolUse", firewall, matcher="Bash|Write|Edit|MultiEdit|NotebookEdit", timeout=5)

# gate / pre-action — only where the capability registry says gate:True.
if can_gate:
    ensure("PreToolUse", inject, matcher="Agent", timeout=5)
    ensure("PreToolUse", perm, matcher="*", timeout=5)
    ensure("Stop", review, timeout=15)
    ensure("SubagentStop", iso, timeout=10)

settings["hooks"] = hooks
with open(sp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
print("[mentu-hooks] Claude Code wiring updated (gate=%s)" % can_gate)
PY
else
    warn "Claude Code settings not found at $CLAUDE_SETTINGS — skipping"
fi

# ─── Step 4: Cursor — observe + pre-action gate (gate:True) ──────────────────
CURSOR_HOOKS="$HOME/.cursor/hooks.json"
if [[ -d "$HOME/.cursor" ]]; then
    backup "$CURSOR_HOOKS" cursor-hooks.json
    MENTU_HOOKS_DST="$HOOKS_DST" CURSOR_HOOKS="$CURSOR_HOOKS" /usr/bin/env python3 <<'PY'
import json, os, sys
dst = os.environ["MENTU_HOOKS_DST"]
sp = os.environ["CURSOR_HOOKS"]
sys.path.insert(0, dst)
try:
    from mentu_policy import capabilities
    can_gate = capabilities.supports("cursor", "gate")
except Exception:
    can_gate = True
hook = os.path.join(dst, "cursor_cir_hook.sh")

# Full overwrite of the mentu-owned map → idempotent by construction. The event
# name is passed as argv[1] so the cursor adapter resolves the native event.
m = {
    "beforeSubmitPrompt": "%s beforeSubmitPrompt" % hook,  # observe (prompt)
    "stop": "%s stop" % hook,                              # observe
}
if can_gate:
    m["beforeShellExecution"] = "%s beforeShellExecution" % hook  # gate
    m["beforeMCPExecution"] = "%s beforeMCPExecution" % hook      # gate
with open(sp, "w") as f:
    json.dump(m, f, indent=2)
    f.write("\n")
print("[mentu-hooks] Cursor wiring updated (gate=%s)" % can_gate)
PY
else
    log "Cursor not detected (no ~/.cursor/) — skipping"
fi

# ─── Step 5: Gemini — OBSERVE-ONLY (gate:False) ──────────────────────────────
# Gemini's lifecycle events are post-hoc, so the registry says gate:False. We
# wire BeforeAgent/AfterAgent/AfterTool for observe + annotate and NEVER a
# pre-action gate it cannot honor.
GEMINI_SETTINGS="$HOME/.gemini/settings.json"
if [[ -d "$HOME/.gemini" ]]; then
    backup "$GEMINI_SETTINGS" gemini-settings.json
    MENTU_HOOKS_DST="$HOOKS_DST" GEMINI_SETTINGS="$GEMINI_SETTINGS" /usr/bin/env python3 <<'PY'
import json, os, sys
dst = os.environ["MENTU_HOOKS_DST"]
sp = os.environ["GEMINI_SETTINGS"]
sys.path.insert(0, dst)
try:
    from mentu_policy import capabilities
    can_gate = capabilities.supports("gemini", "gate")  # False — load-bearing
except Exception:
    can_gate = False

settings = {}
if os.path.exists(sp):
    try:
        with open(sp) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, IOError):
        settings = {}
if not isinstance(settings, dict):
    settings = {}

hook = os.path.join(dst, "gemini_cir_hook.sh")
hooks = settings.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}

# Capability honesty: Gemini is OBSERVE-ONLY. Refuse to wire any gate for it.
if can_gate:
    raise SystemExit("[mentu-hooks] refusing: gemini registry says gate:False")
for event in ("BeforeAgent", "AfterAgent", "AfterTool"):
    hooks[event] = [{"hooks": [{"type": "command", "command": hook}]}]
settings["hooks"] = hooks

# Remove any spurious top-level hook keys (legacy hygiene).
for key in ("BeforeAgent", "AfterAgent", "AfterTool"):
    settings.pop(key, None)

with open(sp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
print("[mentu-hooks] Gemini observe-only wiring updated (no pre-action gate)")
PY
else
    log "Gemini not detected (no ~/.gemini/) — skipping"
fi

# ─── Step 6: Codex — observe + pre-action gate (gate:True) ───────────────────
CODEX_HOOKS="$HOME/.codex/hooks.json"
if [[ -d "$HOME/.codex" ]]; then
    backup "$CODEX_HOOKS" codex-hooks.json
    MENTU_HOOKS_DST="$HOOKS_DST" CODEX_HOOKS="$CODEX_HOOKS" /usr/bin/env python3 <<'PY'
import json, os, sys
dst = os.environ["MENTU_HOOKS_DST"]
sp = os.environ["CODEX_HOOKS"]
sys.path.insert(0, dst)
try:
    from mentu_policy import capabilities
    can_gate = capabilities.supports("codex", "gate")
except Exception:
    can_gate = True
hook = os.path.join(dst, "codex_cir_hook.sh")

# Full overwrite → idempotent. Observe events always; the approval/permission
# pre-action gates only where the registry says gate:True.
m = {
    "UserPromptSubmit": hook,
    "Stop": hook,
    "PostToolUse": hook,
    "PostToolUseFailure": hook,
}
if can_gate:
    m["PermissionRequest"] = hook
    m["_approval_request"] = hook
with open(sp, "w") as f:
    json.dump(m, f, indent=2)
    f.write("\n")
print("[mentu-hooks] Codex wiring updated (gate=%s)" % can_gate)
PY
else
    log "Codex not detected (no ~/.codex/) — skipping"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
log ""
log "Installation complete."
[[ -d "$BACKUP_DIR" ]] && log "Backups saved to: $BACKUP_DIR"
log "Policy package: $PKG_DST"
log "Capability-aware wiring: gate events only for Claude/Codex/Cursor;"
log "Gemini observe-only (post-hoc, gate:False)."
