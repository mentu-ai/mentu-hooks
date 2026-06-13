#!/usr/bin/env python3
"""mentu_policy.substrate — the fail-open substrate facade (M2b).

Probe-then-use-then-degrade-to-no-op over the two local substrate backends,
unified behind one object:

  * mentu-local Unix-socket JSON-RPC at ``<base>/mentu-local.sock`` — ported
    from ``hooks/mentu_local_client.py``. ``socket_available()`` is a single
    ``SOCKET_PATH.exists()``; ``call()`` returns None on ANY failure;
    ``capture`` / ``annotate`` / ``status`` / ``sync`` (+ read ``list``)
    convenience methods.
  * the ``mentu`` CLI shell-out for ``cir patterns`` / ``cir query`` /
    ``cir capture`` — ported from ``hooks/cir_session_context.py``'s
    ``query_cir`` boundary (5s timeout; non-zero / empty / exception => None).

Base dir = ``$MENTU_HOME`` if set else ``~/.mentu`` — so a test that points
MENTU_HOME at an empty mktemp never touches the operator's real home. The CLI
is spawned with that base dir exported as ``MENTU_HOME`` so that even when the
real binary is on PATH it reads the test's empty substrate, not production.

Every path returns ``None`` / ``False`` / ``[]`` on any failure — absent
socket, absent binary, non-zero exit, empty output, timeout, malformed JSON,
or any exception. No substrate, no daemon, no binary => the caller behaves
exactly as if the harness were not installed. This fail-open contract IS the
safety property; preserve it (BUILD-Mentu-Policy-Harness-v1.0 §M2b).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, List, Optional


def default_base_dir() -> Path:
    """``$MENTU_HOME`` if set, else ``~/.mentu``. Resolved per-call so a test
    that patches the env is honored without re-import."""
    home = os.environ.get("MENTU_HOME")
    if home:
        return Path(home)
    return Path.home() / ".mentu"


class Substrate:
    """Fail-open facade over the mentu-local socket + the mentu CLI.

    Construct with no args for production (base = ``$MENTU_HOME`` or
    ``~/.mentu``). Tests pass ``base_dir=<mktemp>`` (and, to force the
    no-binary branch even when the real binary is on PATH,
    ``mentu_bin=<nonexistent path>``) to assert absence without touching the
    operator's real home.
    """

    CLI_TIMEOUT = 5.0
    SOCKET_TIMEOUT = 10.0

    def __init__(self, base_dir: Optional[str] = None, mentu_bin: Optional[str] = None):
        self.base_dir = Path(base_dir) if base_dir is not None else default_base_dir()
        self.socket_path = self.base_dir / "mentu-local.sock"
        self._mentu_bin = self._resolve_bin(mentu_bin)
        self._request_id = 0

    @staticmethod
    def _resolve_bin(mentu_bin: Optional[str]) -> Optional[str]:
        """Resolve the mentu binary, or None when absent. An explicit override
        is honored only when it exists on disk (so tests can force absence);
        otherwise PATH, then the canonical ``~/.local/bin/mentu`` install path
        (cir_session_context.py:18)."""
        if mentu_bin is not None:
            cand = Path(mentu_bin)
            return str(cand) if cand.exists() else None
        found = shutil.which("mentu")
        if found:
            return found
        fallback = Path.home() / ".local" / "bin" / "mentu"
        return str(fallback) if fallback.exists() else None

    # -- availability ------------------------------------------------------

    def socket_available(self) -> bool:
        try:
            return self.socket_path.exists()
        except OSError:
            return False

    def cli_available(self) -> bool:
        return self._mentu_bin is not None

    def available(self) -> bool:
        """True when EITHER backend is reachable. A reachable backend that
        then returns nothing still degrades the caller to a no-op."""
        return self.socket_available() or self.cli_available()

    # -- mentu-local JSON-RPC (ported from mentu_local_client.py) -----------

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def call(self, method: str, params: Optional[dict] = None,
             timeout: float = SOCKET_TIMEOUT) -> Optional[dict]:
        """JSON-RPC 2.0 call. Returns the result, or None on ANY failure."""
        sock = None
        try:
            if not self.socket_available():
                return None
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(str(self.socket_path))
            msg = json.dumps({
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": self._next_id(),
            })
            sock.sendall(msg.encode("utf-8") + b"\n")
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                try:
                    json.loads(data)
                    break
                except json.JSONDecodeError:
                    continue
            response = json.loads(data)
            if "error" in response and response["error"]:
                return None
            return response.get("result")
        except Exception:
            return None
        finally:
            if sock:
                sock.close()

    # -- socket reads (used by the read-only supply path) -------------------

    def status(self, commitment_id: str) -> Optional[dict]:
        """Commitment status (read). None on any failure."""
        return self.call("status", {"commitmentId": commitment_id})

    def list_commitments(self, state: Optional[str] = None, actor: Optional[str] = None,
                         limit: int = 50) -> List[dict]:
        """Commitments (read). Empty list on any failure."""
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state
        if actor:
            params["actor"] = actor
        result = self.call("list", params)
        return result if isinstance(result, list) else []

    # -- socket mutations (NEVER called on the supply path; the observe
    #    -> CIR persistence adapter in M3 is the only caller) ---------------

    def capture(self, content: str, kind: str = "evidence", **kwargs) -> Optional[str]:
        """Capture a memory (MUTATING). Returns mem_id or None."""
        params = {"type": kind, "content": content}
        params.update(kwargs)
        result = self.call("capture", params)
        return result.get("id") if result else None

    def annotate(self, commitment_id: str, note: str) -> bool:
        """Annotate a commitment (MUTATING). True on success."""
        return self.call("annotate", {"commitmentId": commitment_id, "note": note}) is not None

    def sync(self) -> Optional[dict]:
        """Trigger a sync (MUTATING — local push). Status dict or None."""
        return self.call("sync")

    # -- mentu CLI shell-out (ported from cir_session_context.py) -----------

    def _cli_json(self, args: List[str], timeout: float = CLI_TIMEOUT):
        """Run ``mentu <args>`` with the facade's base dir exported as
        MENTU_HOME and return parsed JSON, or None on non-zero / empty /
        exception (the fail-open boundary, cir_session_context.py:16-33)."""
        if self._mentu_bin is None:
            return None
        try:
            env = dict(os.environ)
            env["MENTU_HOME"] = str(self.base_dir)
            result = subprocess.run(
                [self._mentu_bin] + list(args),
                capture_output=True, text=True, timeout=timeout, env=env,
            )
            if result.returncode != 0:
                return None
            output = result.stdout.strip()
            if not output or output == "[]":
                return None
            return json.loads(output)
        except Exception:
            return None

    def cir_patterns(self):
        """CIR crystallized patterns (read). None when unavailable/empty."""
        return self._cli_json(["cir", "patterns", "--format", "json"])

    def cir_query(self, limit: int = 5):
        """Recent CIR signals (read). None when unavailable/empty."""
        return self._cli_json(["cir", "query", "--limit", str(limit), "--format", "json"])

    def cir_capture(self, kind: str, body: str, domain: Optional[str] = None,
                    actor: Optional[str] = None) -> bool:
        """Land an audit signal as a CIR row (MUTATING). Not used by supply;
        the observe-persistence adapter (M3) is the only caller. False on any
        failure (non-zero exit / absent binary / exception). Omits an empty
        ``--domain`` / ``--actor`` (the evidence-capture path)."""
        if self._mentu_bin is None:
            return False
        args = ["cir", "capture", "--kind", str(kind), "--body", str(body)]
        if domain:
            args += ["--domain", str(domain)]
        if actor:
            args += ["--actor", str(actor)]
        return self._run_cli_ok(args)

    def capture_signal(self, kind: str, body: str, domain: str = "",
                       actor: str = "") -> bool:
        """Land an agent-lifecycle CIR signal (MUTATING), ALWAYS passing
        ``--domain`` and ``--actor`` even when empty — the fixed argv shape the
        legacy agent hooks (mentu_agent/codex/cursor/gemini) emit and the golden
        vectors pin. Fire-and-forget: False on any failure."""
        if self._mentu_bin is None:
            return False
        args = ["cir", "capture", "--kind", str(kind), "--body", str(body),
                "--domain", str(domain), "--actor", str(actor)]
        return self._run_cli_ok(args)

    def _run_cli_ok(self, args: List[str]) -> bool:
        """Run ``mentu <args>`` with the facade's base dir exported as
        MENTU_HOME; True on exit 0, False on any failure."""
        try:
            env = dict(os.environ)
            env["MENTU_HOME"] = str(self.base_dir)
            result = subprocess.run(
                [self._mentu_bin] + list(args),
                capture_output=True, text=True, timeout=self.CLI_TIMEOUT, env=env,
            )
            return result.returncode == 0
        except Exception:
            return False
