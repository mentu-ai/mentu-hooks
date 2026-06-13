#!/usr/bin/env python3
"""tests.sandbox — mktemp sandbox builder for the golden-vector harness.

Helper, not a test. Builds fully isolated environments the legacy hook
snapshots (tests/fixtures/legacy/) run inside:

  - a temp HOME (so Path.home()/~ expansion never touches the real home)
    with an empty MENTU_HOME dir under it
  - a PATH-front `mentu` stub that appends its argv (one JSON array per
    line) to $MENTU_STUB_LOG and emits canned JSON for `cir patterns` /
    `cir query`; plus a `timeout` shim (macOS has no coreutils timeout)
  - a temp git repo with a tracked base file and a CLAUDE.md whose
    ## Commands build line is `echo build ok` (or `false`)
  - fake ledgers, protocol-state / pre-compact-state / genesis writers

Everything lives under one tempfile.mkdtemp root — never the real `~`.

Secret-shaped strings are NEVER literal in committed files: secret_line()
assembles one at runtime by concatenation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SANDBOX_SEEDS = FIXTURES / "sandbox"

_MENTU_STUB = """#!/bin/bash
# mentu stub: log argv as a JSON array, emit canned CIR JSON when wired.
LOG="${MENTU_STUB_LOG:-$HOME/mentu-argv.log}"
/usr/bin/python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@" >> "$LOG"
if [ "${1:-}" = "cir" ] && [ "${2:-}" = "patterns" ] && [ -n "${MENTU_STUB_PATTERNS:-}" ]; then
    cat "$MENTU_STUB_PATTERNS"
elif [ "${1:-}" = "cir" ] && [ "${2:-}" = "query" ] && [ -n "${MENTU_STUB_SIGNALS:-}" ]; then
    cat "$MENTU_STUB_SIGNALS"
fi
exit 0
"""

_TIMEOUT_STUB = """#!/bin/bash
# minimal `timeout DURATION CMD...` shim — drop the duration, run the command.
shift
exec "$@"
"""


def secret_line() -> str:
    """A synthetic credential assignment, assembled at runtime so no
    secret-shaped string is ever literal in a committed file."""
    return 'api_key = "' + "FAKE" + "0" * 16 + '"'


def write_ledger_counts(path: Path, approves: int = 0, warns: int = 0,
                        blocks: int = 0) -> None:
    """Fake ledger with one marker per line; the three markers are
    disjoint because the legacy permission script counts them with three
    separate `grep -c` passes."""
    lines = []
    for i in range(approves):
        lines.append('{"op":"approve","payload":{"body":"ok-%d"}}' % i)
    for i in range(warns):
        lines.append('{"kind":"warning","payload":{"body":"warn-%d"}}' % i)
    for i in range(blocks):
        lines.append('{"op":"reopen","payload":{"reason":"BLOCKED by gate %d"}}' % i)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + ("\n" if lines else ""))


class Sandbox:
    """One isolated case environment. `env` is built from scratch — the
    parent process environment never leaks in (actor-detection env vars
    like CURSOR_SESSION_ID must come only from the case spec)."""

    def __init__(self, root: Path, home: Path, work: Path, env: dict):
        self.root = root
        self.home = home
        self.work = work
        self.env = env
        self.stub_log = Path(env["MENTU_STUB_LOG"])
        self.ledger_path = work / ".mentu" / "ledger.jsonl"

    def ledger_lines(self) -> Optional[list]:
        if not self.ledger_path.exists():
            return None
        return self.ledger_path.read_text().splitlines()

    def mentu_argv(self) -> list:
        """Parsed argv arrays the mentu stub logged, in call order."""
        if not self.stub_log.exists():
            return []
        return [json.loads(line) for line in self.stub_log.read_text().splitlines() if line.strip()]

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _install_stub(bin_dir: Path, name: str, body: str) -> None:
    stub = bin_dir / name
    stub.write_text(body)
    stub.chmod(0o755)


def _git(work: Path, env: dict, *args: str) -> None:
    subprocess.run(["git", "-c", "commit.gpgsign=false", *args],
                   cwd=str(work), env=env, check=True,
                   capture_output=True, text=True)


def make_sandbox(*,
                 cwd_name: str = "golden-ws",
                 git_repo: bool = False,
                 build_cmd: str = "echo build ok",
                 mentu_stub: bool = True,
                 canned_cir: bool = False,
                 ledger: Optional[str] = None,
                 ledger_counts: Optional[list] = None,
                 protocol_state=None,
                 pre_compact_state: bool = False,
                 genesis=None,
                 secret_in_diff: bool = False,
                 benign_change: bool = False,
                 extra_env: Optional[dict] = None) -> Sandbox:
    root = Path(tempfile.mkdtemp(prefix="mentu-golden-"))
    home = root / "home"
    mentu_home = home / ".mentu"          # empty MENTU_HOME — never the real one
    bin_dir = home / ".local" / "bin"     # cir_session_context resolves ~/.local/bin/mentu
    tmp_dir = root / "tmp"
    work = root / "work" / cwd_name       # fixed leaf name => stable basename($PWD)
    for d in (mentu_home, bin_dir, tmp_dir, work):
        d.mkdir(parents=True, exist_ok=True)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "MENTU_HOME": str(mentu_home),
        "MENTU_STUB_LOG": str(home / "mentu-argv.log"),
        # codex/cursor/gemini adapters derive their CIR domain from $PWD;
        # pin it so basename($PWD) is the stable leaf cwd_name.
        "PWD": str(work),
        "TMPDIR": str(tmp_dir),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "Golden", "GIT_AUTHOR_EMAIL": "golden@sandbox.test",
        "GIT_COMMITTER_NAME": "Golden", "GIT_COMMITTER_EMAIL": "golden@sandbox.test",
    }

    if mentu_stub:
        _install_stub(bin_dir, "mentu", _MENTU_STUB)
    _install_stub(bin_dir, "timeout", _TIMEOUT_STUB)
    if canned_cir:
        env["MENTU_STUB_PATTERNS"] = str(SANDBOX_SEEDS / "cir_patterns.json")
        env["MENTU_STUB_SIGNALS"] = str(SANDBOX_SEEDS / "cir_signals.json")

    if git_repo:
        (work / "base.txt").write_text("base content\n")
        (work / "CLAUDE.md").write_text(
            "# Golden Sandbox Repo\n\n## Commands\n\n```bash\n" + build_cmd + "\n```\n")
        _git(work, env, "init", "-q")
        _git(work, env, "add", "base.txt", "CLAUDE.md")
        _git(work, env, "commit", "-q", "-m", "base")

    if secret_in_diff:
        with open(work / "base.txt", "a") as f:
            f.write(secret_line() + "\n")
    if benign_change:
        with open(work / "base.txt", "a") as f:
            f.write("# touched by golden case\n")

    if ledger is not None:
        ledger_path = work / ".mentu" / "ledger.jsonl"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        if ledger == "empty":
            ledger_path.write_text("")
        else:
            shutil.copy(SANDBOX_SEEDS / f"ledger_{ledger}.jsonl", ledger_path)
    elif ledger_counts:
        write_ledger_counts(work / ".mentu" / "ledger.jsonl", *ledger_counts)

    if protocol_state is not None:
        state_path = work / ".claude" / "protocol-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(protocol_state, dict):
            state_path.write_text(json.dumps(protocol_state, indent=2))
        else:
            shutil.copy(SANDBOX_SEEDS / f"protocol_state_{protocol_state}.json", state_path)

    if pre_compact_state:
        pc_path = work / ".claude" / "pre-compact-state.json"
        pc_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SANDBOX_SEEDS / "pre-compact-state.json", pc_path)

    if genesis is not None:
        g_path = work / ".mentu" / "genesis.json"
        g_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(genesis, dict):
            g_path.write_text(json.dumps(genesis, indent=2))
        else:
            shutil.copy(SANDBOX_SEEDS / f"genesis_{genesis}.json", g_path)

    if extra_env:
        env.update({str(k): str(v) for k, v in extra_env.items()})

    return Sandbox(root, home, work, env)


def make_sandbox_from_spec(spec: dict) -> Sandbox:
    """Build a sandbox from a native-fixture case's `sandbox` dict + env."""
    sb = spec.get("sandbox") or {}
    return make_sandbox(
        cwd_name=spec.get("cwd_name", "golden-ws"),
        git_repo=sb.get("git_repo", False),
        build_cmd=sb.get("build_cmd", "echo build ok"),
        mentu_stub=sb.get("mentu_stub", True),
        canned_cir=sb.get("canned_cir", False),
        ledger=sb.get("ledger"),
        ledger_counts=sb.get("ledger_counts"),
        protocol_state=sb.get("protocol_state"),
        pre_compact_state=sb.get("pre_compact_state", False),
        genesis=sb.get("genesis"),
        secret_in_diff=sb.get("secret_in_diff", False),
        benign_change=sb.get("benign_change", False),
        extra_env=spec.get("env"),
    )


# ───────────────────────── golden-vector harness ──────────────────────────
# The capture/verify driver (scripts/capture-golden-vectors.sh) imports the
# three names below. A "case" is a dict: which legacy SNAPSHOT to run, the
# stdin/argv/env it gets, and the sandbox seeds it runs against. run_case()
# builds the sandbox, runs the snapshot, and returns the golden record
# {exit_code, stdout, mentu_argv, ledger_ops_normalized}.

_INTERP_BY_SUFFIX = {".py": "python3", ".sh": "bash"}


def _j(d: dict) -> str:
    """Compact JSON stdin payload."""
    return json.dumps(d)


def _lines(n: int) -> str:
    """An n-line message (n-1 newlines) for the line-count heuristic."""
    return "\n".join("line %d" % i for i in range(n))


def normalize_ledger_ops(lines) -> list:
    """Parse the JSONL ops a hook APPENDED to the ledger and zero the only
    nondeterministic review-gate fields: `ts` (UTC clock) and `id` (md5 of
    ts). Everything else (op, actor, workspace, payload) is deterministic."""
    ops = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        try:
            op = json.loads(s)
        except json.JSONDecodeError:
            ops.append({"_unparsed": s})
            continue
        if isinstance(op, dict):
            if "ts" in op:
                op["ts"] = 0
            if "id" in op:
                op["id"] = 0
        ops.append(op)
    return ops


def run_case(case: dict, legacy_dir, python_exe: Optional[str] = None,
             bash_exe: str = "/bin/bash") -> dict:
    """Run one case against its legacy snapshot inside a fresh sandbox.

    Interpreters are invoked by ABSOLUTE path — subprocess resolves argv[0]
    via the parent PATH, not the sandbox env, so a bare name would escape the
    sandbox. The hooks' own helper tools (mentu/timeout stubs, jq, bc, git)
    resolve through the sandbox PATH as intended."""
    sb = make_sandbox_from_spec(case)
    try:
        script = Path(legacy_dir) / case["script"]
        interp = case.get("interp") or _INTERP_BY_SUFFIX[script.suffix]
        if interp == "python3":
            argv0 = [python_exe or sys.executable]
        else:
            argv0 = [bash_exe]
        cmd = argv0 + [str(script)] + list(case.get("argv", []))
        cwd = sb.work if case.get("cwd_in", "work") == "work" else sb.root
        before = sb.ledger_lines() or []
        proc = subprocess.run(
            cmd,
            input=case.get("stdin", ""),
            env=sb.env,
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        after = sb.ledger_lines() or []
        appended = after[len(before):]
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "mentu_argv": sb.mentu_argv(),
            "ledger_ops_normalized": normalize_ledger_ops(appended),
        }
    finally:
        sb.cleanup()


def _isolation_cases() -> list:
    active = {"sandbox": {"protocol_state": "isolation_active"}}
    return [
        {"name": "iso_block_201", "script": "context_isolation_gate.py",
         "stdin": _j({"last_assistant_message": _lines(201)}), **active},
        {"name": "iso_allow_199", "script": "context_isolation_gate.py",
         "stdin": _j({"last_assistant_message": _lines(199)}), **active},
        {"name": "iso_inactive", "script": "context_isolation_gate.py",
         "stdin": _j({"last_assistant_message": _lines(201)}), "sandbox": {}},
        {"name": "iso_garbage", "script": "context_isolation_gate.py",
         "stdin": "this is not valid json {{{", **active},
    ]


def _permission_cases() -> list:
    def case(name, tool, tool_input, sb):
        return {"name": name, "script": "pre-tool-use-permission.sh",
                "requires": ["jq", "bc"],
                "stdin": _j({"tool_name": tool, "tool_input": tool_input}),
                "sandbox": sb}
    read = {}
    return [
        case("perm_allow_090", "Read", read, {"ledger": "trust_high"}),   # .90 -> allow
        case("perm_deny_033", "Read", read, {"ledger": "trust_low"}),     # .33 -> deny
        case("perm_pass_050", "Read", read, {"ledger": "trust_mid"}),     # .50 -> {}
        case("perm_destructive_085", "Bash", {"command": "rm -rf build/"},
             {"ledger_counts": [17, 3, 0]}),                              # .85 destructive -> {}
        case("perm_no_ledger", "Read", read, {}),                         # default .50 -> {}
    ]


def _inject_cases() -> list:
    return [
        {"name": "inject_agent_enriched", "script": "pre-tool-use-inject.sh",
         "requires": ["jq"],
         "stdin": _j({"tool_name": "Agent", "tool_input": {
             "prompt": "Investigate the auth regression and summarize findings."}}),
         "sandbox": {"ledger": "inject"}},
        {"name": "inject_non_agent", "script": "pre-tool-use-inject.sh",
         "requires": ["jq"],
         "stdin": _j({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
         "sandbox": {}},
        {"name": "inject_empty_ledger", "script": "pre-tool-use-inject.sh",
         "requires": ["jq"],
         "stdin": _j({"tool_name": "Agent", "tool_input": {
             "prompt": "Investigate the auth regression."}}),
         "sandbox": {"ledger": "empty"}},
    ]


# DOMAIN for the mentu universal hook is basename(stdin.cwd); a leaf of
# "golden-ws" keeps it stable. NOTE: the legacy hook parses a tab-joined line
# with `read`, whose default IFS coalesces empty fields — so events with no
# tool_name shift the cwd field. The golden pins that legacy quirk verbatim.
_MENTU_CWD = "/work/golden-ws"


def _mentu_agent_cases() -> list:
    script = "mentu_agent_hook.sh"

    def ev(name, payload, env=None):
        c = {"name": name, "script": script, "stdin": _j(payload),
             "sandbox": {}}
        if env:
            c["env"] = env
        return c

    rows = [
        ev("adapter_mentu_userpromptsubmit",
           {"hook_event_name": "UserPromptSubmit", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_posttool_bash",
           {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_posttool_edit",
           {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_posttool_read",
           {"hook_event_name": "PostToolUse", "tool_name": "Read", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_posttool_agent",
           {"hook_event_name": "PostToolUse", "tool_name": "Agent", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_posttool_other",
           {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_posttoolfailure",
           {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_stop",
           {"hook_event_name": "Stop", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_permissionrequest",
           {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_aftertool",
           {"hook_event_name": "AfterTool", "tool_name": "Edit", "cwd": _MENTU_CWD}),
        ev("adapter_mentu_unknown",
           {"hook_event_name": "Frobnicate", "cwd": _MENTU_CWD}),
    ]
    # actor-precedence matrix on a fixed event (Stop)
    stop = {"hook_event_name": "Stop", "cwd": _MENTU_CWD}
    matrix = [
        ev("adapter_mentu_actor_cursor", stop, {"CURSOR_SESSION_ID": "cs-1"}),
        ev("adapter_mentu_actor_codex", stop, {"CODEX_SESSION_ID": "cx-1"}),
        ev("adapter_mentu_actor_gemini", stop, {"GEMINI_SESSION_ID": "gm-1"}),
        ev("adapter_mentu_actor_superset", stop, {"SUPERSET_TAB_ID": "ss-1"}),
        ev("adapter_mentu_actor_mentu_precedence", stop,
           {"MENTU_ACTOR": "human:rashid", "CURSOR_SESSION_ID": "cs-1"}),
        ev("adapter_mentu_actor_invalid", stop, {"MENTU_ACTOR": "noColon"}),
    ]
    return rows + matrix


def _codex_cases() -> list:
    # LEGACY-FAITHFUL NOTE: codex_cir_hook.sh parses its event with
    #   <<< "${INPUT:-{}}"
    # which bash expands as ${INPUT:-{} followed by a stray literal `}` — so the
    # JSON handed to python is always `<input>}` (invalid) or `{}` (no type).
    # json.load therefore always fails/whiffs and EVENT collapses to "unknown",
    # routing EVERY codex event to the default arm ("codex: unknown"). These
    # per-row cases pin that real pre-M3 bug; when M3 fixes the parse, each
    # golden flips and the parity diff surfaces exactly which events changed.
    s = "codex_cir_hook.sh"

    def ev(name, typ):
        return {"name": name, "script": s, "stdin": _j({"type": typ}), "sandbox": {}}
    return [
        ev("adapter_codex_task_started", "task_started"),
        ev("adapter_codex_exec_begin", "exec_command_begin"),
        ev("adapter_codex_approval", "_approval_request"),
        ev("adapter_codex_stop", "Stop"),
        ev("adapter_codex_posttool", "PostToolUse"),
        ev("adapter_codex_posttoolfailure", "PostToolUseFailure"),
        ev("adapter_codex_unknown", "Frobnicate"),
    ]


def _cursor_cases() -> list:
    s = "cursor_cir_hook.sh"

    def ev(name, event):
        # Cursor takes its event as argv[1]; stdin is read but unused.
        return {"name": name, "script": s, "argv": [event], "stdin": "{}",
                "sandbox": {}}
    return [
        ev("adapter_cursor_beforesubmit", "beforeSubmitPrompt"),
        ev("adapter_cursor_stop", "stop"),
        ev("adapter_cursor_beforeshell", "beforeShellExecution"),
        ev("adapter_cursor_beforemcp", "beforeMCPExecution"),
        ev("adapter_cursor_permissionrequest", "PermissionRequest"),
        ev("adapter_cursor_unknown", "Frobnicate"),
    ]


def _gemini_cases() -> list:
    # LEGACY-FAITHFUL NOTE: gemini_cir_hook.sh has the same `${INPUT:-{}}`
    # parse bug as codex (see _codex_cases) — every event collapses to
    # "gemini: unknown". Pinned verbatim as the pre-M3 baseline.
    s = "gemini_cir_hook.sh"

    def ev(name, event):
        return {"name": name, "script": s,
                "stdin": _j({"hook_event_name": event}), "sandbox": {}}
    return [
        ev("adapter_gemini_beforeagent", "BeforeAgent"),
        ev("adapter_gemini_afteragent", "AfterAgent"),
        ev("adapter_gemini_aftertool", "AfterTool"),
        ev("adapter_gemini_unknown", "Frobnicate"),
    ]


def _session_context_cases() -> list:
    return [
        {"name": "sess_ctx_canned", "script": "cir_session_context.py",
         "stdin": "{}", "sandbox": {"canned_cir": True}},
        {"name": "sess_ctx_absent", "script": "cir_session_context.py",
         "stdin": "{}", "sandbox": {"mentu_stub": False}},
    ]


def _compaction_cases() -> list:
    return [
        {"name": "compact_brief", "script": "compaction_reinjector.py",
         "stdin": _j({"source": "compact"}),
         "sandbox": {"pre_compact_state": True}},
        {"name": "compact_startup", "script": "compaction_reinjector.py",
         "stdin": _j({"source": "startup"}), "sandbox": {}},
    ]


def _review_gate_cases() -> list:
    msg = _j({"last_assistant_message": "Implemented the fix. LOOP_COMPLETE"})
    base_env = {
        "MENTU_STEP_CMT_LEDGER": "cmt_golden",
        "MENTU_ACTOR": "agent:golden",
        "MENTU_WORKSPACE": "golden-ws",
    }

    def case(name, tier, sb_extra, requires=None):
        sb = {"git_repo": True, "protocol_state": "auto_review", "ledger": "empty"}
        sb.update(sb_extra)
        env = dict(base_env)
        env["MENTU_STEP_TIER"] = str(tier)
        c = {"name": name, "script": "review_gate.py", "stdin": msg,
             "env": env, "sandbox": sb, "requires": ["git"]}
        return c
    return [
        case("review_secret_reopen", 2, {"secret_in_diff": True}),
        case("review_clean_close", 1, {"benign_change": True}),
        case("review_tier3_defer", 3, {"benign_change": True}),
    ]


# Legacy snapshots that the harness freezes into tests/fixtures/legacy/.
# review_gate.py imports its three siblings from its own dir, so they ride
# along in the snapshot and resolve to the frozen copies (not hooks/).
SNAPSHOT_SCRIPTS = [
    "context_isolation_gate.py",
    "pre-tool-use-permission.sh",
    "pre-tool-use-inject.sh",
    "mentu_agent_hook.sh",
    "codex_cir_hook.sh",
    "cursor_cir_hook.sh",
    "gemini_cir_hook.sh",
    "cir_session_context.py",
    "compaction_reinjector.py",
    "review_gate.py",
    "mentu_local_client.py",   # review_gate dependency
    "genesis_reader.py",       # review_gate dependency
    "dual_triad_validator.py", # review_gate dependency
]


def golden_cases() -> list:
    """The full ordered list of golden-vector cases."""
    cases = []
    cases += _isolation_cases()
    cases += _permission_cases()
    cases += _inject_cases()
    cases += _mentu_agent_cases()
    cases += _codex_cases()
    cases += _cursor_cases()
    cases += _gemini_cases()
    cases += _session_context_cases()
    cases += _compaction_cases()
    cases += _review_gate_cases()
    return cases
