#!/usr/bin/env python3
"""mentu-local JSON-RPC 2.0 client — fail-safe, zero-dependency."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Optional


class MentuLocalClient:
    """JSON-RPC 2.0 client for mentu-local-daemon Unix socket."""

    SOCKET_PATH = Path.home() / ".mentu" / "mentu-local.sock"
    DEFAULT_TIMEOUT = 10.0
    _request_id = 0

    @classmethod
    def is_available(cls) -> bool:
        """Check if the daemon socket exists."""
        return cls.SOCKET_PATH.exists()

    @classmethod
    def _next_id(cls) -> int:
        cls._request_id += 1
        return cls._request_id

    @classmethod
    def call(cls, method: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[dict]:
        """Make a JSON-RPC call. Returns result dict or None on any failure."""
        sock = None
        try:
            if not cls.is_available():
                return None

            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(str(cls.SOCKET_PATH))

            msg = json.dumps({
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": cls._next_id()
            })
            sock.sendall(msg.encode("utf-8") + b"\n")

            # Read response (accumulate until valid JSON)
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

    # --- Convenience methods ---

    @classmethod
    def capture(cls, content: str, kind: str = "evidence", **kwargs) -> Optional[str]:
        """Capture a memory. Returns mem_id or None."""
        params = {"type": kind, "content": content}
        params.update(kwargs)
        result = cls.call("capture", params)
        return result.get("id") if result else None

    @classmethod
    def commit(cls, title: str, actor: str, source: str | None = None,
               tags: list[str] | None = None, **kwargs) -> Optional[str]:
        """Create a commitment. Returns cmt_id or None."""
        params = {"title": title, "actor": actor}
        if source:
            params["sourceMemoryId"] = source
        if tags:
            params["tags"] = tags
        params.update(kwargs)
        result = cls.call("commit", params)
        return result.get("id") if result else None

    @classmethod
    def claim(cls, commitment_id: str) -> bool:
        """Claim a commitment. Returns True on success."""
        return cls.call("claim", {"commitmentId": commitment_id}) is not None

    @classmethod
    def annotate(cls, commitment_id: str, note: str) -> bool:
        """Annotate a commitment. Returns True on success."""
        return cls.call("annotate", {"commitmentId": commitment_id, "note": note}) is not None

    @classmethod
    def submit(cls, commitment_id: str) -> bool:
        """Submit a commitment. Returns True on success."""
        return cls.call("submit", {"commitmentId": commitment_id}) is not None

    @classmethod
    def close(cls, commitment_id: str, verdict: str = "pass") -> bool:
        """Close a commitment. Returns True on success."""
        return cls.call("close", {"commitment": commitment_id, "evidence": verdict}) is not None

    @classmethod
    def release(cls, commitment_id: str, reason: str | None = None) -> bool:
        """Release a commitment. Returns True on success."""
        params = {"commitmentId": commitment_id}
        if reason:
            params["reason"] = reason
        return cls.call("release", params) is not None

    @classmethod
    def link(cls, source_id: str, target_id: str, kind: str = "related") -> Optional[str]:
        """Link two entities. Returns lnk_id or None."""
        result = cls.call("link", {"sourceId": source_id, "targetId": target_id, "kind": kind})
        return result.get("id") if result else None

    @classmethod
    def list_commitments(cls, state: str | None = None, actor: str | None = None,
                         limit: int = 50) -> list[dict]:
        """List commitments. Returns list of commitment dicts."""
        params: dict[str, Any] = {"limit": limit}
        if state:
            params["state"] = state
        if actor:
            params["actor"] = actor
        result = cls.call("list", params)
        return result if isinstance(result, list) else []

    @classmethod
    def status(cls, commitment_id: str) -> Optional[dict]:
        """Get commitment status. Returns commitment dict or None."""
        return cls.call("status", {"commitmentId": commitment_id})

    @classmethod
    def sync(cls) -> Optional[dict]:
        """Trigger sync. Returns status dict or None."""
        return cls.call("sync")

    @classmethod
    def search(cls, query: str) -> list[dict]:
        """Search memories. Returns list of memory dicts."""
        result = cls.call("search", {"query": query})
        return result if isinstance(result, list) else []
