#!/usr/bin/env python3
"""mentu_policy.adapters.io — the ONLY stdin / argv / env surface in the package.

Every adapter normalizes its native input through here, so the two pieces of
privileged I/O an adapter touches — parsing the hook's stdin JSON and resolving
the acting actor from the environment — live in exactly one auditable place.

``resolve_actor`` reproduces the verbatim precedence from the universal hook
(``hooks/mentu_agent_hook.sh:32-50``); the env-var name checks are kept in that
textual order so the precedence is auditable against the legacy source.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Mapping


def parse_json(text: str) -> dict:
    """Parse a JSON object from ``text``; ``{}`` on any error or non-object.

    Mirrors the legacy hooks' ``try: json.load(...) except: {}`` guard — a
    malformed envelope degrades to an empty dict, never an exception."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_stdin_json() -> dict:
    """Read stdin and parse it as a JSON object (``{}`` on any failure)."""
    try:
        text = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    return parse_json(text)


def resolve_actor(environ: Mapping[str, Any]) -> str:
    """Resolve ``type:name`` actor by the legacy precedence
    (mentu_agent_hook.sh:32-50):

        MENTU_ACTOR override -> SUPERSET_TAB_ID -> CURSOR_SESSION_ID
        -> CODEX_SESSION_ID -> GEMINI_SESSION_ID -> default ``agent:claude``

    Any value lacking ``':'`` normalizes to ``agent:unknown`` (the legacy
    format-validation line). The ``environ.get(...)`` truthiness check matches
    bash ``[[ -n "${VAR:-}" ]]`` (a present-but-empty var is skipped)."""
    if environ.get("MENTU_ACTOR"):
        actor = str(environ["MENTU_ACTOR"])
    elif environ.get("SUPERSET_TAB_ID"):
        actor = "agent:superset-hosted"
    elif environ.get("CURSOR_SESSION_ID"):
        actor = "agent:cursor"
    elif environ.get("CODEX_SESSION_ID"):
        actor = "agent:codex"
    elif environ.get("GEMINI_SESSION_ID"):
        actor = "agent:gemini"
    else:
        actor = "agent:claude"

    if ":" not in actor:
        actor = "agent:unknown"
    return actor
