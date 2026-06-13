#!/usr/bin/env python3
"""mentu_policy.adapters — the per-agent translation shims (M3).

An adapter's ONLY jobs are the three in the dispatch contract
(BUILD-Mentu-Policy-Harness-v1.0 §M3): (1) ``decode`` a native event into an
``AgentEvent``, (2) hand it to policy-core, (3) ``encode`` the returned
``Decision`` into the native response — down-shifting to the supported verb
where the agent is less capable. The policy decision lives in policy-core,
never in an adapter.

The single privileged-I/O surface is ``io.py`` (stdin / argv / env); every
adapter normalizes through it so actor precedence and stdin parsing are
auditable in one place. ``shim.py`` is the generic entry point that wires
``decode -> evaluate -> encode`` for any agent.
"""
