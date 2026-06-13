# Mentu Policy Harness

**A cross-agent governance layer for AI coding agents.**

The Mentu Policy Harness is a consent-based, fail-open, auditable governance layer you install on
*your own* machine to govern *your own* AI coding agents — Claude Code, Codex, Cursor, Gemini, and
mentu's own runner. It does three things, all defensive, at well-defined decision boundaries:

- **Observe** — record your own agent's activity as a local audit trail.
- **Supply context** — enrich an agent's working context with your own prior decisions, trust state,
  and commitments, so long sessions stay coherent.
- **Gate** — enforce your own safety policy at decision boundaries: refuse to let an agent stage a
  hardcoded credential, run a destructive command below a trust threshold, or flood a sub-agent's
  return with raw data.

One policy core, written once, sees a normalized `AgentEvent` and returns a normalized `Decision`.
Thin per-agent adapters translate each vendor's native hook I/O into that contract and back. Where an
agent *cannot* enforce a verb, the harness degrades transparently and records that it could not — it
never claims a guarantee it can't deliver.

> **What this is NOT.** It is not a tool for acting on third-party systems, observing anyone other
> than the operator's own agents, or doing anything without the operator's explicit configuration.
> Every hook is installed by you, into your own agent config. Every decision is logged. The substrate
> is optional and **fail-open**: when it is absent, the harness is a permissive no-op.

Licensed under the **Apache License 2.0**.

---

## How it works

```
        native hook I/O (each agent's own, opt-in)          MENTU POLICY HARNESS
   ┌──────────┐                                     ┌──────────────────────────────────┐
   │  Claude  │── settings.json hooks ──┐           │                                  │
   │  Codex   │── ~/.codex/hooks.json ──┤           │  AgentEvent ──▶  policy core      │
   │  Cursor  │── ~/.cursor/hooks.json ─┼─ adapter ▶│  (normalized)    observe          │
   │  Gemini  │── ~/.gemini/settings ───┤           │      │           supply_context   │
   │  mentu   │── runner events ────────┘           │      │           gate             │
   └──────────┘                                     │      ▼                            │
                                                    │  capability registry ─▶ (degrade) │
                                                    │      │                            │
                                                    │      ▼  Decision (normalized out)  │
                                                    │  substrate: ledger · CIR (fail-open)
                                                    └──────────────────────────────────┘
```

Every adapter does exactly the same three steps; the policy lives in the core, not in the adapter:

1. **decode** the agent's native event envelope into an `AgentEvent`,
2. ask the core to **evaluate** it into a `Decision`,
3. reconcile that `Decision` against what the agent can actually enforce (the degradation ladder),
   then **encode** it back into the agent's native response.

---

## The AgentEvent → Decision ABI

The contract between adapters and the policy core is a plain JSON shape (with reference Python
dataclasses in `mentu_policy/abi.py`). The core only ever sees normalized facts — no agent
credentials, no socket handles, no native envelopes. This is the least-privilege seam.

### `AgentEvent` — normalized lifecycle event (input)

The union of every supported agent's hook points, collapsed to one schema:

| field | meaning |
|---|---|
| `agent` | `claude` · `codex` · `cursor` · `gemini` · `mentu` |
| `event` | one of the normalized lifecycle events (below) |
| `session_id` | the agent's session identifier |
| `actor` | who is acting (`human:<name>` or `agent:<kind>`) |
| `cwd` | working directory |
| `tool` | `{name, input, output, exit_code}` for tool events |
| `prompt` / `message` | the prompt being submitted / the message at a stop boundary |
| `source` | optional provenance tag |

Normalized events: `session_start`, `prompt_submit`, `pre_tool`, `post_tool`, `post_tool_failure`,
`permission_request`, `pre_compact`, `post_compact`, `subagent_stop`, `stop`, `session_end`.

### `Decision` — normalized verdict (output)

`Decision.verb` is a closed enum — the core cannot emit an action an adapter doesn't know how to
encode safely:

| verb | meaning |
|---|---|
| `allow` | permit the action |
| `deny` | refuse at this boundary (fail-closed), with a reason |
| `ask` | defer to the operator (human approval) |
| `pass` | no opinion — fall through to the agent's own default |
| `inject` | supply context (`inject_context` / `updated_input`) |
| `annotate` | record a local audit signal; does **not** alter the action |

Alongside the verb a `Decision` carries an optional `reason`, `inject_context`, `updated_input`, and
`annotate` payload.

---

## Capability matrix (single source of truth)

Not every agent can enforce every verb. The matrix in `mentu_policy/capabilities.py` is consulted
*before* a decision is enforced, and is the honest record of where the harness can and cannot
guarantee enforcement. `✓` = native enforcement, `~` = a constrained channel, `✗` = not possible →
degrade.

| Agent | observe | supply_context | gate (refuse pre-action) | compaction re-seed |
|---|---|---|---|---|
| **Claude Code** | ✓ | ✓ (`additionalContext` + `updated_input`) | ✓ (`permissionDecision:"deny"` / `exit 2`) | ✓ |
| **Codex** | ✓ | ~ (prompt prefix) | ✓ (`_approval_request` reject) | ✗ |
| **Cursor** | ✓ | ~ (`beforeSubmitPrompt`) | ✓ (`{"continue":false}`) | ✗ |
| **Gemini** | ✓ | ~ (`BeforeAgent`) | ✗ (post-hoc events) | ✗ |
| **mentu** | ✓ | ✓ | ✓ (runner approval boundary) | ✓ |

The load-bearing entry is **Gemini's `gate:✗`**: its lifecycle events fire *after* the action, so a
`deny` cannot prevent anything. The harness is honest about this rather than advertising a gate it
cannot honor.

### The degradation ladder

When the registry says a verb is unsupported, `mentu_policy/degrade.py` reconciles it before the
adapter encodes a response:

- **`deny` / `ask` on a non-gating agent (Gemini)** → down-shift to **`annotate`** (observe + warn).
  The adapter emits the agent's additive no-op — never a false "blocked" claim — and one
  `capability_degraded` signal `{agent, requested_verb, applied_verb, reason}` records that the
  refusal could not be enforced, so it is auditable and never silently dropped.
- **`inject` on a constrained (`~`) channel** → re-encode the context as a prompt prefix. It is still
  delivered, just through the prompt, so nothing is lost and no signal is emitted. If no supply
  channel exists at all, skip (`pass`) and log a `supply_skipped` signal.
- **A full-capability agent** passes through unchanged. An **unknown agent fails open** — the ladder
  never invents a capability claim it cannot ground in the registry, and never raises.

---

## Fail-open guarantees

The harness is designed so its own failure can never block your work or fake an enforcement it didn't
perform:

- **Absent substrate ⇒ permissive no-op.** If the local ledger / substrate is missing or errors, the
  harness has no opinion (`pass`) — it never denies on its own infrastructure failure.
- **Garbage input ⇒ exit 0.** Every entry hook tolerates malformed stdin and exits cleanly.
- **Internal exception ⇒ `pass`.** Each gate body is wrapped fail-open; an internal fault returns a
  no-opinion `Decision`, never a refusal.
- **Boundary-only enforcement.** Gates fire at decision boundaries (pre-tool, stop, subagent-stop,
  permission-request). Nothing here aborts a running action mid-flight; the worst possible outcome is
  "refuse at the next boundary."
- **Least privilege.** The policy core holds no agent credentials and no agent API surface — it sees
  a normalized event and returns a normalized decision. All privileged I/O stays in the adapters.
- **Local-first.** All evidence and decisions are written to your own machine. Nothing leaves the
  device.

---

## Installation

Installation is **opt-in** and **idempotent**. The installer only touches agents whose config
directory already exists, backs up each native config before editing, and never wires a pre-action
gate to an agent that cannot honor one.

```bash
git clone https://github.com/mentu-ai/mentu-hooks.git ~/.mentu/mentu-hooks
~/.mentu/mentu-hooks/scripts/install-agent-hooks.sh
```

What the installer does:

1. **Deploys a self-contained copy** of the `mentu_policy/` package to `~/.mentu/hooks/`, so the
   adapter shims resolve the policy core relative to their own location (no `PYTHONPATH` / CWD
   dependency).
2. **Deploys the adapter shim hooks** to `~/.mentu/hooks/`.
3. **Wires each detected agent's native config** capability-aware:
   - **Claude Code** (`~/.claude/settings.json`) — observe + supply + gate.
   - **Cursor** (`~/.cursor/hooks.json`) — observe + pre-action gate.
   - **Codex** (`~/.codex/hooks.json`) — observe + pre-action gate.
   - **Gemini** (`~/.gemini/settings.json`) — **observe-only**; the installer refuses to wire any
     pre-action gate for it, because the registry says it cannot honor one.
4. **Backs up** every native config it edits to `~/.mentu/backups/<timestamp>/` first.

Re-running the installer adds no duplicate entries. Every path is derived from `$HOME`.

> The installer and the e2e harness derive every path from `$HOME` so they can be exercised against a
> throwaway `HOME=$(mktemp -d)`. Run automated checks that way; never point the test harness at your
> real home.

---

## Testing

```bash
# Unit + parity + golden suites
python3 -m unittest discover

# End-to-end invariant harness (fully sandboxed: HOME + MENTU_HOME under a throwaway ROOT;
# never reads or writes your real ~)
bash scripts/e2e-policy-harness.sh

# Behavior-preservation: replay the pinned golden vectors against the committed snapshots
bash scripts/capture-golden-vectors.sh --verify
```

The golden vectors pin the *exact* byte-level outputs of the original Claude-Code hooks. The policy
core was extracted from those hooks rule-body-verbatim, and the golden harness proves the refactor
preserved their behavior.

---

## Project structure

```
mentu-hooks/
├── mentu_policy/            # the host-agnostic policy core
│   ├── abi.py              # AgentEvent / Decision contract
│   ├── core.py             # evaluate(): dispatches a normalized event to a Decision
│   ├── gates.py            # the gate engine (review / context-isolation / trust-banded permission)
│   ├── supply.py           # context-supply engine
│   ├── observe.py          # local audit-trail engine
│   ├── capabilities.py     # the per-agent capability registry (single source of truth)
│   ├── degrade.py          # the capability degradation ladder
│   ├── substrate.py        # fail-open local ledger / substrate binding
│   └── adapters/           # per-agent I/O shims (claude, codex, cursor, gemini, mentu) + shim.py
├── hooks/                  # the installable hook entry points (thin shims over mentu_policy)
├── scripts/                # opt-in installer + e2e + golden-vector harnesses
├── tests/                  # unit, parity, golden, degrade, installer, and bridge suites
├── README.md              # this file
└── LICENSE                # Apache-2.0
```

---

## Requirements

- **Python 3.10+** (standard library only — no third-party runtime dependency)
- **Bash** for the adapter shims and scripts
- One or more of: Claude Code, Codex, Cursor, Gemini, or the mentu runner
- The mentu CLI is **optional** — it enriches the substrate, but the harness is fail-open without it

---

## Learn more

- **Mentu**: https://mentu.ai
- **Issues & support**: [GitHub Issues](https://github.com/mentu-ai/mentu-hooks/issues)

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
