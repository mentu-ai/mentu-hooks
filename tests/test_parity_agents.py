#!/usr/bin/env python3
"""Byte/argv-parity: the REWIRED agent hooks (mentu/codex/cursor/gemini) run via
subprocess in the sandbox vs the committed golden vectors.

Per agent golden case we assert stdout byte-equality + the ``mentu cir capture``
argv-list (kind/body/domain/actor) recorded by the sandbox PATH-stub log.

  * PRESERVED hooks (cursor, mentu_agent_hook): the legacy was UNAFFECTED by the
    ``${INPUT:-{}}`` parse bug (cursor reads argv; mentu reads ``<<< "$INPUT"``
    correctly), so the rewired shim is BYTE-EXACT to the committed golden —
    asserted as full-record equality.

  * codex / gemini: the legacy hooks parsed stdin with ``<<< "${INPUT:-{}}"``,
    which bash mis-expands so the event/actor ALWAYS collapsed to ``unknown``;
    every codex/gemini golden is therefore pinned-buggy ("codex: unknown" /
    "gemini: unknown"). The shim parses stdin correctly via io.py, so the real
    event resolves — an intentional FIX. We assert the CORRECTED post-fix
    capture as an EXPLICIT expected value here; the committed golden remains the
    documented "before" (and we assert the fix actually flips it)."""

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden"
HOOKS = Path(__file__).resolve().parent.parent / "hooks"

# Domain for the agent hooks in the sandbox: codex/cursor/gemini derive it from
# basename($PWD) -> "golden-ws"; the mentu universal hook derives it from the
# (collapsed) stdin cwd, which the goldens pin per-case.
_PWD_DOMAIN = "golden-ws"


def _cap(kind, body, domain, actor):
    return ["cir", "capture", "--kind", kind, "--body", body,
            "--domain", domain, "--actor", actor]


# The CORRECTED post-fix capture for each codex/gemini case (the "after"). The
# legacy golden for each of these is pinned to the buggy "<agent>: unknown".
_CODEX_FIXED = {
    "adapter_codex_task_started":     ("prompt_submit", "codex: task started"),
    "adapter_codex_exec_begin":       ("command_exec", "codex: command execution"),
    "adapter_codex_approval":         ("permission_gate", "codex: approval requested"),
    "adapter_codex_stop":             ("session_stop", "codex: session ended"),
    "adapter_codex_posttool":         ("tool_use", "codex: tool completed"),
    "adapter_codex_posttoolfailure":  ("tool_failure", "codex: tool failed"),
    "adapter_codex_unknown":          ("agent_event", "codex: Frobnicate"),
}
_GEMINI_FIXED = {
    "adapter_gemini_beforeagent": ("prompt_submit", "gemini: agent starting"),
    "adapter_gemini_afteragent":  ("session_stop", "gemini: agent completed"),
    "adapter_gemini_aftertool":   ("tool_use", "gemini: tool completed"),
    "adapter_gemini_unknown":     ("agent_event", "gemini: Frobnicate"),
}


def _have_python_and_bash():
    return shutil.which("python3", path="/usr/bin:/bin") is not None


class TestMentuAndCursorByteExact(unittest.TestCase):
    """PRESERVED hooks — full-record byte equality to the committed golden."""


class TestCodexGeminiFix(unittest.TestCase):
    """codex/gemini — the parse-bug fix flips the capture; assert the corrected
    value explicitly and prove it differs from the pinned-buggy golden."""


def _byte_exact_test(case):
    name = case["name"]

    def test(self):
        want = json.loads((GOLDEN / (name + ".golden.json")).read_text())
        rec = sandbox.run_case(case, str(HOOKS))
        self.assertEqual(rec, want,
                         "preserved-hook record mismatch for %s\n want=%r\n got =%r"
                         % (name, want, rec))
    test.__name__ = "test_%s" % name
    return test


def _fixed_test(case, fixed_map, actor):
    name = case["name"]
    kind, body = fixed_map[name]
    expected_argv = _cap(kind, body, _PWD_DOMAIN, actor)

    def test(self):
        golden = json.loads((GOLDEN / (name + ".golden.json")).read_text())
        rec = sandbox.run_case(case, str(HOOKS))
        # stdout is unchanged by the fix (still {}); only the capture flips.
        self.assertEqual(rec["stdout"], golden["stdout"],
                         "stdout changed for %s (should be unchanged)" % name)
        self.assertEqual(rec["mentu_argv"], [expected_argv],
                         "corrected capture mismatch for %s" % name)
        # The fix genuinely flips it: the legacy golden pinned actor/kind=unknown
        # due to the ${INPUT:-{}} parse bug; the shim resolves the real event.
        self.assertNotEqual(rec["mentu_argv"], golden["mentu_argv"],
                            "expected the parse-bug fix to flip %s, but it matched "
                            "the buggy golden" % name)
    test.__name__ = "test_%s" % name
    return test


if _have_python_and_bash():
    for _case in sandbox._mentu_agent_cases() + sandbox._cursor_cases():
        setattr(TestMentuAndCursorByteExact, "test_%s" % _case["name"],
                _byte_exact_test(_case))
    for _case in sandbox._codex_cases():
        setattr(TestCodexGeminiFix, "test_%s" % _case["name"],
                _fixed_test(_case, _CODEX_FIXED, "agent:codex"))
    for _case in sandbox._gemini_cases():
        setattr(TestCodexGeminiFix, "test_%s" % _case["name"],
                _fixed_test(_case, _GEMINI_FIXED, "agent:gemini"))


class TestCursorPassCaseLiteral(unittest.TestCase):
    """The cursor permission pass-case must be byte-equal the legacy literal
    {"continue":true} — absent substrate -> middle trust band -> pass."""

    def test_beforeshell_pass_is_continue_true(self):
        case = {"name": "x", "script": "cursor_cir_hook.sh",
                "argv": ["beforeShellExecution"], "stdin": "{}", "sandbox": {}}
        rec = sandbox.run_case(case, str(HOOKS))
        self.assertEqual(rec["stdout"], '{"continue":true}\n')

    def test_beforemcp_pass_is_continue_true(self):
        case = {"name": "x", "script": "cursor_cir_hook.sh",
                "argv": ["beforeMCPExecution"], "stdin": "{}", "sandbox": {}}
        rec = sandbox.run_case(case, str(HOOKS))
        self.assertEqual(rec["stdout"], '{"continue":true}\n')


if __name__ == "__main__":
    unittest.main()
