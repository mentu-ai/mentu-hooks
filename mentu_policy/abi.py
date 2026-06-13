#!/usr/bin/env python3
"""mentu_policy.abi — the AgentEvent / Decision ABI (M1).

The normalized contract between agent adapters and policy-core:
an AgentEvent goes in, a Decision comes out. The core only ever sees
normalized facts — no agent credentials, no socket handles, no native
I/O envelopes. Reference contract: BUILD-Mentu-Policy-Harness-v1.0 §M1.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# The union of every supported agent's hook points, collapsed to one schema.
EVENTS = {
    "session_start", "prompt_submit", "pre_tool", "post_tool", "post_tool_failure",
    "permission_request", "pre_compact", "post_compact", "subagent_stop", "stop", "session_end",
}


@dataclass
class ToolRef:
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    output: Optional[str] = None
    exit_code: Optional[int] = None


@dataclass
class AgentEvent:
    agent: str
    event: str
    session_id: str = "unknown"
    actor: str = "agent:unknown"
    cwd: str = ""
    tool: Optional[ToolRef] = None
    prompt: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = None


class Verb(str, Enum):
    ALLOW = "allow"       # permit the action
    DENY = "deny"         # refuse at this boundary (fail-closed), with reason
    ASK = "ask"           # defer to the operator (human approval)
    PASS = "pass"         # no opinion — fall through to the agent's own default
    INJECT = "inject"     # supply context (additionalContext or updated_input)
    ANNOTATE = "annotate" # record a local audit signal; does not alter the action


@dataclass
class Decision:
    verb: Verb = Verb.PASS
    reason: str = ""
    inject_context: Optional[str] = None
    updated_input: Optional[dict] = None
    annotate: Optional[dict] = None

    @staticmethod
    def allow(reason=""):
        return Decision(Verb.ALLOW, reason)

    @staticmethod
    def deny(reason):
        return Decision(Verb.DENY, reason)

    @staticmethod
    def ask(reason):
        return Decision(Verb.ASK, reason)

    @staticmethod
    def pass_():
        return Decision(Verb.PASS)

    @staticmethod
    def supply(md=None, prompt=None):
        return Decision(Verb.INJECT, inject_context=md,
                        updated_input=({"prompt": prompt} if prompt else None))

    @staticmethod
    def note(kind, body):
        return Decision(Verb.ANNOTATE, annotate={"kind": kind, "body": body})


def event_from_dict(data: dict) -> AgentEvent:
    """Decode the ABI's JSON shape into an AgentEvent.

    Tolerates missing optional keys (dataclass defaults apply); adapters
    are responsible for producing the dict from their native envelope.
    """
    tool = data.get("tool")
    tool_ref = None
    if isinstance(tool, dict):
        tool_input = tool.get("input")
        tool_ref = ToolRef(
            name=tool.get("name", ""),
            input=tool_input if isinstance(tool_input, dict) else {},
            output=tool.get("output"),
            exit_code=tool.get("exit_code"),
        )
    return AgentEvent(
        agent=data.get("agent", "unknown"),
        event=data.get("event", ""),
        session_id=data.get("session_id", "unknown"),
        actor=data.get("actor", "agent:unknown"),
        cwd=data.get("cwd", ""),
        tool=tool_ref,
        prompt=data.get("prompt"),
        message=data.get("message"),
        source=data.get("source"),
    )
