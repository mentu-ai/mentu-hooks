#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Genesis Key Reader — Reads workspace constitution for hook enforcement.

Reads .mentu/genesis.json (machine-readable twin of genesis.key).
Provides role-based permission checks, tier classification, and
scope constraints. Falls back to permissive defaults when no
genesis.json exists.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class GenesisReader:
    """Read and query the workspace Genesis Key (v2.0 JSON)."""

    DEFAULT_TIER = 1
    DEFAULT_CONSTRAINTS: list[str] = []
    DEFAULT_SCOPE: list[str] = ["*"]

    def __init__(self, workspace_dir: str | None = None):
        self.workspace_dir = workspace_dir or os.getcwd()
        self.config: dict[str, Any] = {}
        self.governed = False
        self._load()

    def _load(self) -> None:
        """Load Genesis Key from .mentu/genesis.json."""
        json_path = Path(self.workspace_dir) / ".mentu" / "genesis.json"
        if not json_path.exists():
            return

        try:
            with open(json_path) as f:
                self.config = json.load(f)
            if not isinstance(self.config, dict):
                print(f"genesis_reader: WARNING: {json_path} is not a JSON object — treating as ungoverned")
                self.config = {}
                return
            self.governed = True
        except json.JSONDecodeError as e:
            # Corrupt genesis.json = fail closed (ungoverned), not silent
            print(f"genesis_reader: WARNING: corrupt {json_path}: {e} — treating as ungoverned")
        except Exception:
            pass

    @property
    def exists(self) -> bool:
        return self.governed

    @property
    def _data(self) -> dict[str, Any] | None:
        """Backward-compatible accessor."""
        return self.config if self.governed else None

    def resolve_role(self, actor: str) -> str:
        """Match actor against actors list with wildcard support. Return role or 'unknown'."""
        for entry in self.config.get("actors", []):
            pattern = entry.get("id", "")
            role = entry.get("role", "")
            if pattern and role and self._actor_matches(pattern, actor):
                return role
        return "unknown"

    def _actor_matches(self, pattern: str, actor: str) -> bool:
        if pattern == "*":
            return True
        if pattern == actor:
            return True
        if pattern.endswith(":*"):
            prefix = pattern[:-1]  # "agent:" or "human:"
            return actor.startswith(prefix)
        return False

    def actor_allowed(self, actor: str, operation: str) -> bool:
        """Role-based permission check."""
        if not self.governed:
            return True
        role = self.resolve_role(actor)
        allowed_ops = self.config.get("permissions", {}).get(role, [])
        return "*" in allowed_ops or operation in allowed_ops

    def get_allowed_ops(self, actor: str) -> list[str]:
        """Get list of allowed operations for an actor."""
        if not self.governed:
            return ["*"]
        role = self.resolve_role(actor)
        return self.config.get("permissions", {}).get(role, [])

    def get_denied_ops(self, actor: str) -> list[str]:
        """Get list of denied operations for an actor."""
        if not self.governed:
            return []
        all_ops = ["capture", "commit", "claim", "release", "close",
                    "annotate", "submit", "approve", "reopen"]
        allowed = self.get_allowed_ops(actor)
        if "*" in allowed:
            return []
        return [op for op in all_ops if op not in allowed]

    def classify_tier(self, tags: list[str]) -> str:
        """Classify tier from validation.classification rules."""
        validation = self.config.get("validation", {})
        for rule in validation.get("classification", []):
            if "default" in rule:
                continue
            match_tags = rule.get("match", {}).get("tags", [])
            if any(t in match_tags for t in tags):
                return rule.get("tier", "tier_2")
        # Find default rule
        for rule in validation.get("classification", []):
            if "default" in rule:
                return rule["default"]
        return "tier_2"

    def get_tier_config(self, tier_name: str) -> dict[str, Any]:
        """Get tier configuration (auto_close, require_human, etc.)."""
        return self.config.get("validation", {}).get("tiers", {}).get(tier_name, {})

    @property
    def validation_tier(self) -> int:
        """Get default validation tier (1-3) for backward compat."""
        if not self.governed:
            return self.DEFAULT_TIER
        # Default tier from classification rules
        tier_str = self.classify_tier([])
        try:
            return int(tier_str.split("_")[1])
        except (IndexError, ValueError):
            return self.DEFAULT_TIER

    @property
    def constraints(self) -> list[str]:
        """Get workspace constraints."""
        if not self.governed:
            return self.DEFAULT_CONSTRAINTS
        c = self.config.get("constraints", {})
        return list(c.keys()) if isinstance(c, dict) else self.DEFAULT_CONSTRAINTS

    @property
    def scope(self) -> list[str]:
        """Get allowed scope (file paths/patterns)."""
        return self.DEFAULT_SCOPE

    @property
    def owner(self) -> str:
        """Get workspace owner."""
        if not self.governed:
            return "unknown"
        return self.config.get("identity", {}).get("owner", "unknown")

    def get_step_tier(self, step_tier: int | None = None) -> int:
        """Get effective tier for a step (step override > workspace default)."""
        if step_tier is not None:
            return step_tier
        return self.validation_tier

    def format_context(self, commitment_id: str | None = None) -> str:
        """Format Genesis Key info for injection into agent context."""
        lines = []
        if self.exists:
            lines.append(f"**Genesis Key:** active (v2.0 role-based)")
            if self.constraints:
                lines.append(f"**Constraints:** {', '.join(self.constraints)}")
        else:
            lines.append("**Genesis Key:** none (permissive mode)")
        return "\n".join(lines)
