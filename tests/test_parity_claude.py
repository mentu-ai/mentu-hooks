#!/usr/bin/env python3
"""Byte-parity: the in-process Claude pipeline vs the committed golden vectors.

For every Claude-surface golden case, rebuild the identical sandbox (reusing
``tests/sandbox.py``), run the in-process ``decode -> evaluate -> encode``
pipeline (``mentu_policy.adapters.shim.run``) inside that sandbox's environment,
and assert the emitted ``stdout`` + ``exit_code`` are byte-equal to the golden.

The pipeline is run in-process — but the substrate/probes/files it touches must
resolve to the sandbox, so the case's env + cwd are swapped in for the duration
(the same isolation the subprocess gets in the capture harness). No real home,
no real ``mentu`` binary, no real socket is ever touched.
"""

import contextlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

# sandbox.py is a test helper, imported by absolute name (the capture harness
# does the same with TESTS_DIR on the path); ensure tests/ is importable however
# this module is loaded (`unittest discover`, direct run, or the capture script).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox
from mentu_policy.adapters import shim

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"
HOOKS = Path(__file__).resolve().parent.parent / "hooks"

# The Claude-surface hooks rewired in Commit B to delegate to mentu_policy.
_REWIRED_SCRIPTS = {
    "context_isolation_gate.py",
    "pre-tool-use-permission.sh",
    "pre-tool-use-inject.sh",
    "review_gate.py",
}


def setUpModule():
    """Re-assert the real M2 engines into core.evaluate's dispatch. A prior
    test module (test_evaluate_failopen) resets them to the M1 no-op in its
    finally, which would make every gate/supply event PASS — same defensive
    pattern as test_supply_observe.TestCoreWiring."""
    from mentu_policy import core, gates, observe
    from mentu_policy import supply as supply_mod
    core.register_gate_engine(gates.gate_engine)
    core.register_supply_engine(supply_mod.supply_engine)
    core.register_observe_engine(observe.observe_engine)

# Map each legacy Claude-surface script to the native hook identifier the shim
# is invoked with (the ``--event`` hint).
_SCRIPT_TO_HOOK = {
    "context_isolation_gate.py": "subagent_stop",
    "pre-tool-use-permission.sh": "pre_tool",
    "pre-tool-use-inject.sh": "inject",
    "cir_session_context.py": "session_start",
    "compaction_reinjector.py": "post_compact",
    "review_gate.py": "stop",
}


@contextlib.contextmanager
def _sandbox_runtime(sb):
    """Swap the process env + CWD to the sandbox for the duration, so the
    in-process substrate (MENTU_HOME / PATH-resolved ``mentu`` stub) and the
    workspace-relative file reads resolve to the sandbox, then restore."""
    old_env = dict(os.environ)
    old_cwd = os.getcwd()
    try:
        os.environ.clear()
        os.environ.update(sb.env)
        os.chdir(str(sb.work))
        yield
    finally:
        os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_env)


def _claude_cases():
    have_git = shutil.which("git", path="/usr/bin:/bin:/usr/sbin:/sbin") is not None
    for case in sandbox.golden_cases():
        hook = _SCRIPT_TO_HOOK.get(case["script"])
        if hook is None:
            continue
        if case["script"] == "review_gate.py" and not have_git:
            continue
        yield case, hook


class TestClaudeParity(unittest.TestCase):
    pass


def _make_test(case, hook):
    name = case["name"]

    def test(self):
        gpath = GOLDEN / (name + ".golden.json")
        self.assertTrue(gpath.exists(), "missing golden: %s" % name)
        want = json.loads(gpath.read_text())

        sb = sandbox.make_sandbox_from_spec(case)
        try:
            with _sandbox_runtime(sb):
                stdout, code = shim.run("claude", hook, case.get("stdin", ""))
        finally:
            sb.cleanup()

        self.assertEqual(code, want["exit_code"],
                         "exit_code mismatch for %s" % name)
        self.assertEqual(stdout, want["stdout"],
                         "stdout mismatch for %s\n want=%r\n got =%r"
                         % (name, want["stdout"], stdout))

    test.__name__ = "test_parity_%s" % name
    return test


for _case, _hook in _claude_cases():
    setattr(TestClaudeParity, "test_parity_%s" % _case["name"],
            _make_test(_case, _hook))


# ---------------------------------------------------------------------------
# Commit B: the REWIRED hook files, executed via subprocess in the sandbox
# (reusing the capture harness's run_case), must byte-match the committed
# goldens FULLY — exit_code + stdout + mentu_argv + ledger_ops_normalized.
# This is the commit gate for the rewire.
# ---------------------------------------------------------------------------

class TestClaudeHookSubprocessParity(unittest.TestCase):
    pass


def _rewired_cases():
    have_git = shutil.which("git", path="/usr/bin:/bin:/usr/sbin:/sbin") is not None
    for case in sandbox.golden_cases():
        if case["script"] not in _REWIRED_SCRIPTS:
            continue
        if case["script"] == "review_gate.py" and not have_git:
            continue
        yield case


def _make_subprocess_test(case):
    name = case["name"]

    def test(self):
        want = json.loads((GOLDEN / (name + ".golden.json")).read_text())
        # Run the REWIRED hook (from hooks/, not the frozen legacy snapshot).
        rec = sandbox.run_case(case, str(HOOKS))
        self.assertEqual(rec, want,
                         "rewired-hook record mismatch for %s\n want=%r\n got =%r"
                         % (name, want, rec))

    test.__name__ = "test_rewired_%s" % name
    return test


for _case in _rewired_cases():
    setattr(TestClaudeHookSubprocessParity, "test_rewired_%s" % _case["name"],
            _make_subprocess_test(_case))


if __name__ == "__main__":
    unittest.main()
