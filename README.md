# Mentu Hooks

**Python enforcement hooks for mentu CLI sequences**

Mentu Hooks provides a collection of Python and shell scripts that integrate with [Claude Code](https://claude.ai/code) to enhance autonomous workflows. These hooks capture evidence, manage commitments, inject context, and enforce constraints during AI agent operations.

## What is Mentu?

Mentu is an autonomous CLI system that enables long-running, unattended AI workflows. The hooks in this package extend Claude Code with Mentu-specific capabilities including:

- **Evidence Capture**: Automatically records file modifications and tool usage as structured evidence
- **Commitment Tracking**: Manages and injects epistemic state about agent commitments  
- **Context Injection**: Enriches agent prompts with CIR (Commitment-Inquiry-Response) ledger data
- **Session Management**: Handles session lifecycle events and state persistence
- **Constraint Enforcement**: Validates tool usage and enforces workflow boundaries

## Installation

### Option 1: Git Clone (Recommended)

```bash
git clone https://github.com/mentu-ai/mentu-hooks.git ~/.mentu/mentu-hooks
```

### Option 2: Manual Installation

1. Download and extract this repository
2. Copy the `hooks/` directory to your desired location
3. Symlink or copy individual hooks to your Claude Code hooks directory:

```bash
# Link specific hooks to Claude Code
ln -sf ~/.mentu/mentu-hooks/hooks/mentu_session_start.py ~/.claude/hooks/
ln -sf ~/.mentu/mentu-hooks/hooks/mentu_post_tool.py ~/.claude/hooks/
ln -sf ~/.mentu/mentu-hooks/hooks/pre-tool-use-inject.sh ~/.claude/hooks/
```

### Option 3: Bulk Installation

Use the provided installation script:

```bash
~/.mentu/mentu-hooks/scripts/install-agent-hooks.sh
```

## Hook Lifecycle

Mentu hooks integrate with Claude Code's hook system at various lifecycle events:

### Session Hooks
- **SessionStart** (`mentu_session_start.py`): Injects claimed commitments into session context
- **SessionEnd** (`session-end.sh`): Handles session cleanup and state persistence

### Tool Hooks
- **PreToolUse** (`pre-tool-use-inject.sh`, `pre-tool-use-permission.sh`): Inject context and validate permissions before tool execution
- **PostToolUse** (`mentu_post_tool.py`, `post-tool-use-trust.sh`): Capture evidence and update state after tool usage

### Specialized Hooks
- **PreCompact** (`pre-compact.sh`): Preserves context before memory compaction
- **PostCompact** (`post-compact.sh`): Restores context after memory compaction  
- **TaskCompleted** (`task-completed.sh`): Handles task completion workflows
- **ReviewGate** (`review_gate.py`): Validates changes against expected outcomes

## Hook Configuration

Hooks are configured through Claude Code's `settings.json`. Each hook specifies:

- **Event matcher**: When the hook should fire (tool name, session event, etc.)
- **Input format**: JSON structure passed to the hook via stdin
- **Output format**: JSON response expected on stdout
- **Purpose**: Specific function within the Mentu workflow

Example configuration snippet:
```json
{
  "hooks": {
    "mentu_session_start.py": {
      "events": ["SessionStart"],
      "description": "Inject claimed commitments into session context"
    },
    "mentu_post_tool.py": {
      "events": ["PostToolUse"],
      "matchers": ["Edit", "Write"],
      "description": "Capture file modifications as evidence"
    }
  }
}
```

## Key Components

### Evidence System
- Captures file modifications, tool usage, and agent decisions as structured evidence
- Stores evidence in `.claude/mentu_evidence.json` for persistence across sessions
- Integrates with Mentu's CIR substrate for commitment tracking

### Context Injection  
- Enriches agent prompts with epistemic state from the CIR ledger
- Provides agents with awareness of prior commitments and decisions
- Enables long-running workflows with memory continuity

### Constraint Enforcement
- Validates tool usage against expected change specifications
- Enforces workspace boundaries and permission models
- Provides safety gates for autonomous operations

## Project Structure

```
mentu-hooks/
├── hooks/                    # Hook implementations
│   ├── *.py                 # Python hooks (evidence, context, validation)
│   └── *.sh                 # Shell hooks (lightweight processing)
├── scripts/                 # Installation and utility scripts
├── MIGRATION.md            # Migration guide for existing users
├── README.md               # This file
└── LICENSE                 # Apache-2.0 license
```

## Requirements

- **Python 3.10+** for Python hooks
- **Bash** for shell hooks  
- **Claude Code** with hooks enabled
- **Mentu CLI** (optional, for full CIR integration)

## Usage Notes

- Hooks are designed to be minimally intrusive and fail-safe
- Each hook handles errors gracefully to avoid disrupting Claude Code sessions
- Evidence and state are captured locally and can be synced with remote Mentu instances
- Hooks respect Claude Code's permission model and user consent workflows

## Learn More

- **Mentu Website**: https://mentu.ai
- **Claude Code**: https://claude.ai/code
- **Issues & Support**: [GitHub Issues](https://github.com/mentu-ai/mentu-hooks/issues)

## License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.