# Rapper 🎤

Claude Code wrapper with safety guardrails — background task execution, git worktree isolation, daemon mode, and Agent Board integration.

## Features

- **🔧 Bash MCP**: Auto-run shell commands via MCP (no permission prompts)
- **🛡️ Safety Guard**: Blocks dangerous commands (rm -rf, sudo, etc.)
- **📨 Outbound Guard**: Whitelist-based control for scheduled task messaging
- **📦 Tmux Sessions**: Long-running interactive Claude sessions
- **🔄 Background Tasks**: Autonomous Claude tasks with status polling and structured results
- **🌿 Git Worktree**: Isolated task branches for parallel development
- **🤖 Daemon Mode**: Persistent Agent Board integration with heartbeat + webhook

## Installation

```bash
# First run — auto-setup happens
/app/rapper/rapper --check

# Or force setup
/app/rapper/rapper --setup

# Add to PATH (optional)
export PATH="/app/rapper/bin:$PATH"
```

## Usage

### Interactive

```bash
# Interactive Claude
rapper

# One-shot prompt
rapper -p "explain this code"

# ACP mode (for Hermes integration)
rapper --acp --stdio
```

### Tmux Sessions

```bash
rapper --tmux myproject       # Start session (rapper-myproject)
rapper --attach myproject     # Attach to existing session
rapper --list                 # List all sessions
```

### Background Tasks (Autonomous)

```bash
# Basic background task
rapper --background "fix-auth" -p "Fix the auth bug" --workdir /app/project

# Isolated git worktree (recommended for code changes)
rapper --background "add-feature" --worktree -p "Add user login feature" --workdir /app/project

# With cost cap and fallback model
rapper --background "big-task" -p "Refactor the API" --workdir /app/project \
    --budget 2.0 --fallback claude-haiku-3

# Monitor tasks
rapper --tasks                      # List all tasks
rapper --tasks running              # Filter by status
rapper --task-count                 # Running count (for concurrency check)
rapper --status fix-auth            # Detailed status + structured result
rapper --logs fix-auth 100          # Last 100 log lines

# Lifecycle
rapper --cancel fix-auth            # Cancel running task
rapper --merge fix-auth             # Auto-commit + merge + cleanup worktree
```

### Daemon Mode (Agent Board)

```bash
# Start persistent daemon (reads ~/.rapper/config.yaml)
rapper --daemon

# With explicit agent ID
rapper --daemon --agent-id rapper-1

# With debug logging
rapper --daemon --log-level debug

# Start all 3 named daemons
python /app/rapper/launch_daemons.py
```

### Version Management

```bash
rapper --claude-version         # Show current Claude Code version
rapper --check-update           # Check for updates (no install)
rapper --update-claude          # Update Claude Code
```

## Background Tasks

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `-p "prompt"` | Task description (required) | — |
| `-w / --workdir <path>` | Working directory | `$(pwd)` |
| `--worktree` | Isolated git worktree branch | false |
| `--budget <usd>` | Max spend in USD | none |
| `--fallback <model>` | Fallback model on overload | none |
| `--max-turns <n>` | Max Claude turns | 200 |

### Task Status Fields

When you run `rapper --status <id>`, you get:

```json
{
  "status": "completed",
  "result": "...",
  "structured_result": {
    "status": "completed",
    "output_path": "src/auth.py",
    "pr_url": null,
    "errors": []
  },
  "fail_reason": null,
  "session_id": "abc123",
  "worktree_path": "/app/project/.claude/worktrees/fix-auth",
  "branch_name": "rapper/fix-auth",
  "claude_version": "1.x.x"
}
```

`fail_reason` values: `error_max_turns` | `error_budget` | *(other)*

To resume a failed/interrupted task by session ID:
```bash
claude -p "continue the work" --resume <session_id>
```

### Worktree Isolation

Each `--worktree` task gets its own git branch (`rapper/<name>`) and directory (`.claude/worktrees/<name>`). Tasks work in complete isolation — no conflicts between concurrent tasks.

```bash
# Two parallel tasks, no interference
rapper --background "feat-a" --worktree -p "add feature A" --workdir /app/project
rapper --background "feat-b" --worktree -p "add feature B" --workdir /app/project

# Merge when done
rapper --merge feat-a
rapper --merge feat-b
```

`--merge` auto-commits any uncommitted files (Rapper writes files but doesn't always git commit), then merges the branch into main and removes the worktree.

## Daemon Mode

The daemon enables **persistent Agent Board integration**:

1. Registers with Agent Board (`POST /api/agents`)
2. Polls for tasks assigned to this agent (`GET /api/tasks?assignee=...&column=todo`)
3. Executes tasks via Claude Code
4. Updates task status (`PATCH /api/tasks/:id` → `done` / `failed`)
5. Listens on a webhook port for instant `task.assign` notifications

Configure in `~/.rapper/config.yaml`:

```yaml
agent_board:
  url: http://localhost:3456
  api_key: sk-rapper1
  agent_id: rapper-1
  poll_interval: 30         # seconds between polls
  webhook_port: 18789       # local port for webhook wakeup

tasks:
  max_concurrent_tasks: 5
```

Three named daemons (rapper-1/2/3) are launched by `launch_daemons.py`, each using its own config at `~/.rapper/config-rapper-{1,2,3}.yaml`.

## Configuration

Full config at `~/.rapper/config.yaml`:

```yaml
# Outbound message safety (for RAPPER_SCHEDULED=1 mode)
safety:
  outbound_whitelist:
    discord:
      - "1234567890"          # allowed channel IDs
    telegram:
      - "-1001234567890"
    email:
      - "allowed@example.com"

# Background task limits
tasks:
  max_concurrent_tasks: 5

# Agent Board integration (daemon mode)
agent_board:
  url: http://localhost:3456
  api_key: sk-rapper1
  agent_id: rapper-1
  poll_interval: 30
  webhook_port: 18789
```

## Safety

### Bash Guard (always active)

Dangerous commands are blocked at the MCP level and cannot be executed:

- `rm -rf`, `sudo`, `su root`
- Fork bombs, `shutdown`, `reboot`
- `chmod 777 /`, `chown /`
- netcat listen, nmap
- Indirect shell execution (`bash -c`, `eval`)

### Outbound Guard (autonomous tasks only)

When `RAPPER_SCHEDULED=1` is set:
- Blocks messages to non-whitelisted Discord/Telegram/Email/Slack targets
- Blocks HTTP POST/PUT from bash commands
- Interactive sessions are **unrestricted**

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   Hermes (协调层)                        │
│           Discord / Telegram / CLI / Cron               │
└────────────────────────┬────────────────────────────────┘
                         │ delegate_task / --background / daemon poll
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   Rapper (执行层)                        │
│                                                         │
│  rapper (CLI)                                           │
│  ├── Interactive → claude --acp --stdio                 │
│  ├── Tmux        → tmux + claude                        │
│  ├── Background  → task_runner.py                       │
│  │     ├── Task dataclass (persist to ~/.rapper/tasks/) │
│  │     ├── Git worktree isolation                       │
│  │     ├── Structured result parsing (5-layer)          │
│  │     └── Audit + progress files                       │
│  └── Daemon      → daemon.py                            │
│        ├── AgentBoardClient (heartbeat, poll, complete) │
│        └── WebhookServer (instant task.assign wakeup)   │
│                                                         │
│  Claude Code  ◀─▶  MCP: bash-runner                    │
│                          └── run_bash (safe shell)      │
│                                                         │
│  Safety Hooks (Claude settings.json)                    │
│  ├── PreToolUse: outbound_guard.py                      │
│  └── Bash blocker: dangerous command intercept          │
└─────────────────────────────────────────────────────────┘
```

## File Locations

| Path | Purpose |
|------|---------|
| `/app/rapper/rapper` | Main CLI entry script |
| `/app/rapper/lib/task_runner.py` | Background task engine |
| `/app/rapper/lib/daemon.py` | Daemon mode (Agent Board) |
| `/app/rapper/mcp-servers/bash-runner/server.py` | Safe shell MCP |
| `/app/rapper/launch_daemons.py` | Multi-daemon launcher |
| `/app/rapper/systemd/rapper@.service` | systemd template unit |
| `~/.rapper/config.yaml` | User config |
| `~/.rapper/tasks/` | Task state files |
| `~/.claude.json` | MCP registration |
| `~/.claude/settings.json` | Safety hooks |

## Hermes Integration

See `HERMES_INTEGRATION.md` for full Python code examples covering:
- Concurrency check before launching tasks
- Task result polling and structured result extraction
- Session ID resume for failed tasks
