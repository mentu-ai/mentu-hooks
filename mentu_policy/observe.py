#!/usr/bin/env python3
"""mentu_policy.observe — the observe engine (M2b).

``observe(event) -> Decision`` classifies a ``post_tool`` / ``post_tool_failure``
event into a typed audit signal, ported VERBATIM from the capture rules in
``hooks/mentu_post_tool.py:64-113``:

  * Edit  -> file evidence  (kind ``file_modified``, body ``"Modified: <path>"``)
  * Write -> file evidence  (kind ``file_created``,  body ``"Created: <path>"``)
  * Bash build/test command -> pass/fail by exit code
    (``build_pass`` / ``build_fail`` / ``test_pass`` / ``test_fail``); the body
    carries the command (<=100 chars) + the last 200 chars of output.
  * anything else (Read/Glob/Grep, a non-build Bash, a file op with no path,
    no tool) -> skip.

The engine returns ONLY ``Decision.pass_()`` or ``Decision.note(kind, body)``.
It is *structurally* incapable of ``deny`` / ``ask``: there is no code path
that constructs either verb. The returned annotation is the local audit
signal; the adapter (M3) persists it (CIR capture + the evidence side-file).
observe itself touches no substrate and never alters the tool result — the
exact invariant of the legacy hook's ``print(json.dumps({}))`` on every path.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .abi import Decision

# Verbatim keyword sets from mentu_post_tool.py:78-79.
_BUILD_KEYWORDS = ("build", "compile", "tsc", "swift build", "cargo build", "npm run build")
_TEST_KEYWORDS = ("test", "swift test", "cargo test", "npm test", "pytest", "jest")


def classify_evidence(event) -> Optional[Tuple[str, str]]:
    """Return ``(kind, body)`` for a captureable event, else ``None``.

    Defensive against malformed events (the fuzz corpus exercises odd tool
    shapes): any missing / wrong-typed field degrades to ``None`` (skip),
    never an exception.
    """
    tool = getattr(event, "tool", None)
    if tool is None:
        return None
    tool_name = getattr(tool, "name", "") or ""
    # Capture file changes AND significant bash outputs (mentu_post_tool.py:68).
    if tool_name not in ("Edit", "Write", "Bash"):
        return None

    tool_input = getattr(tool, "input", None)
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name == "Bash":
        command = tool_input.get("command", "") or ""
        # Only capture test/build results (not every ls/cd/echo).
        is_build = any(kw in command for kw in _BUILD_KEYWORDS)
        is_test = any(kw in command for kw in _TEST_KEYWORDS)
        if not is_build and not is_test:
            return None
        exit_code = getattr(tool, "exit_code", 0)
        if exit_code is None:
            exit_code = 0
        if is_test:
            evidence_type = "test_pass" if exit_code == 0 else "test_fail"
            body = f"Test {'passed' if exit_code == 0 else 'failed'}: {command[:100]}"
        else:
            evidence_type = "build_pass" if exit_code == 0 else "build_fail"
            body = f"Build {'passed' if exit_code == 0 else 'failed'}: {command[:100]}"
        output = getattr(tool, "output", None)
        if output:
            body += f"\n{str(output)[-200:]}"
        return (evidence_type, body)

    # Edit / Write: need a file path, else skip (mentu_post_tool.py:72,97-107).
    file_path = tool_input.get("file_path") or tool_input.get("path", "") or ""
    if not file_path:
        return None
    if tool_name == "Write":
        return ("file_created", f"Created: {file_path}")
    return ("file_modified", f"Modified: {file_path}")


def observe(event) -> Decision:
    """File 5 (mentu_post_tool.py). Audit/telemetry of the operator's own
    activity. Returns only ``pass`` or ``annotate`` — never ``deny`` / ``ask``."""
    classified = classify_evidence(event)
    if classified is None:
        return Decision.pass_()
    kind, body = classified
    return Decision.note(kind, body)


def observe_engine(event, ctx) -> Decision:
    """Adapter for ``core.evaluate``'s dispatch (post_tool / post_tool_failure).
    Fail-open: any internal fault degrades to PASS, never raises into a caller."""
    try:
        return observe(event)
    except BaseException:
        return Decision.pass_()
