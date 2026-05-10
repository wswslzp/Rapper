# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What This Repo Is

**Rapper** 🎤 — A Claude Code wrapper with safety guardrails and autonomous task execution.

Core components:
- **Entry point** (`rapper`): setup + CLI dispatch + Claude Code launcher
- **bash-runner MCP**: safe shell execution with dangerous command blocking
- **outbound_guard**: whitelist-based outbound message safety (for scheduled tasks)
- **task_runner**: background task management — start, monitor, cancel, merge
- **daemon**: persistent Agent Board integration mode — heartbeat polling + webhook server

## Repo Structure

```
/app/rapper/
├── rapper                      # Main entry script (Bash, 789 lines)
├── bin/rapper                  # $PATH wrapper
├── launch_daemons.py           # Multi-daemon launcher (rapper-1/2/3)
├── config/
│   ├── default-config.yaml     # Default config template
│   ├── outbound_guard.py       # PreToolUse hook for message safety
│   └── setup_settings.py       # settings.json manager
├── lib/
│   ├── task_runner.py          # Background task engine (TaskRunner, Task dataclass)
│   └── daemon.py               # Daemon mode (AgentBoardClient, WebhookServer, RapperDaemon)
├── mcp-servers/
│   └── bash-runner/
│       └── server.py           # FastMCP implementation (375 lines)
├── systemd/
│   └── rapper@.service         # systemd template unit (multi-instance: rapper@1, rapper@2...)
├── docs/
│   └── superpowers/
│       ├── specs/              # Design docs
│       └── plans/              # Implementation plans
├── tests/
│   ├── test_mcp_simple.py          # MCP protocol tests
│   ├── test_outbound_guard.py      # Outbound guard tests
│   ├── test_audit_progress.py      # Audit + progress file tests
│   ├── test_version_management.py  # Version tracking tests
│   └── test_worktree_isolation.py  # Git worktree isolation tests
├── pyproject.toml              # Python project config (uv)
├── CLAUDE.md                   # This file
├── README.md                   # User-facing documentation
├── HERMES_INTEGRATION.md       # Hermes integration code examples
└── TODO.md                     # Backlog and development roadmap
```

## User Config

User configuration lives in `~/.rapper/`:
```
~/.rapper/
├── config.yaml                 # User settings (whitelist, agent_board, tasks)
├── tasks/                      # Background task state files (.json, .log, .audit.json, .progress)
│   └── <task_id>.json          # Task state: status, pid, result, structured_result, ...
└── logs/                       # Usage logs
```

Claude Code configuration:
```
~/.claude.json                  # MCP registration (bash-runner)
~/.claude/settings.json         # Hooks (outbound_guard, bash blocker)
```

## Commands

```bash
# Interactive
rapper                          # Launch interactive Claude Code
rapper -p "prompt"              # One-shot prompt mode
rapper --acp --stdio            # ACP mode (for Hermes delegate_task)
rapper --setup                  # Force re-run setup
rapper --check                  # Check status

# Tmux sessions (interactive, persistent terminal)
rapper --tmux <name>            # Start Tmux session (rapper-<name>)
rapper --attach <name>          # Attach to existing session
rapper --list                   # List Rapper sessions

# Background tasks (autonomous, non-blocking)
rapper --background <name> -p "task" --workdir /project          # Start background task
rapper --background <name> --worktree -p "task" --workdir /proj  # Isolated git worktree
rapper --background <name> -p "task" --budget 1.5 --fallback claude-haiku-3  # With cost cap
rapper --background <name> -p "task" --max-turns 100      # Custom max turns (default: 200)
rapper --tasks [status]                # List tasks (optionally filter by status)
rapper --task-count                    # Running task count (for concurrency check)
rapper --task-count-json               # Detailed task count + concurrency info in JSON (for Hermes)
rapper --status <task_id>             # Get task status + enhanced structured result
rapper --logs <task_id> [lines]       # View task logs (default: 50 lines)
rapper --cancel <task_id>             # Cancel running task
rapper --merge <task_id>              # Auto-commit + merge worktree branch + cleanup

# Daemon mode (persistent, Agent Board integration)
rapper --daemon                        # Start persistent daemon
rapper --daemon --agent-id rapper-1    # With explicit agent ID
rapper --daemon --log-level debug      # With log level override

# Version management
rapper --claude-version         # Show current Claude Code version
rapper --check-update           # Check for Claude Code updates (no install)
rapper --update-claude          # Update Claude Code to latest version
```

## Background Task Parameters

| Flag | Description | Default |
|------|-------------|---------|
| `-p "prompt"` | Task description (required) | — |
| `--workdir <path>` | Working directory for Claude | `$(pwd)` |
| `--worktree` | Create isolated git worktree | false |
| `--budget <usd>` | Max spend in USD (`--max-budget-usd`) | none |
| `--fallback <model>` | Fallback model on overload | none |
| `--max-turns <n>` | Max Claude turns | 200 |

## Task Dataclass Fields (`lib/task_runner.py`)

The `Task` dataclass persists full execution state to `~/.rapper/tasks/<id>.json`:

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique task ID (timestamp-based) |
| `name` | str | Human-readable name |
| `status` | str | `pending → running → completed\|failed\|cancelled` |
| `pid` | int | Claude subprocess PID |
| `result` | str | Final output text |
| `structured_result` | dict | **Enhanced** parsed JSON result `{status, output_path, pr_url, errors}` with fallback inference |
| `error` | str | Error message if failed |
| `fail_reason` | str | `error_max_turns \| error_budget \| (other)` |
| `session_id` | str | Claude session ID (for `--resume` continuation) |
| `max_budget_usd` | float | Cost cap |
| `fallback_model` | str | Fallback model |
| `worktree_path` | str | Absolute path to git worktree |
| `branch_name` | str | Feature branch name (e.g. `rapper/feat-auth`) |
| `repo_workdir` | str | Main repo path (distinct from worktree in `--worktree` mode) |
| `claude_version` | str | Claude Code version at task start |
| `board_task_id` | str | Agent Board task ID (e.g., task_7f25a48f) |
| `progress` | list[dict] | Last 20 tool calls (rolling) |

Associated files per task:
- `<id>.log` — raw Claude stream output
- `<id>.audit.json` — structured tool call audit log
- `<id>.progress` — progress summary (human-readable)

## Daemon Mode (`lib/daemon.py`)

Daemon mode enables persistent Agent Board integration:

```
RapperDaemon
├── AgentBoardClient       # HTTP client for Agent Board API
│   ├── register()         # POST /api/agents — register this agent
│   ├── heartbeat()        # PATCH /api/agents/:id — keepalive
│   ├── poll_tasks()       # GET /api/tasks?assignee=...&column=todo
│   ├── claim_task()       # PATCH /api/tasks/:id → column=doing
│   └── complete_task()    # PATCH /api/tasks/:id → column=done|failed
└── WebhookServer          # HTTP server on webhook_port (default: 18789)
    └── POST /             # Receives task.assign / comment.add events
```

**Config section** (`~/.rapper/config.yaml`):
```yaml
agent_board:
  url: http://localhost:3456
  api_key: sk-rapper1
  agent_id: rapper-1
  poll_interval: 30        # seconds
  webhook_port: 18789      # local port for webhook wakeup

tasks:
  max_concurrent_tasks: 5
```

**Signals:**
- `SIGTERM / SIGINT` → graceful shutdown, in-flight tasks marked `failed`, agent deregistered

**Launcher:** `launch_daemons.py` starts multiple named daemons (rapper-1/2/3) from individual config files at `~/.rapper/config-rapper-{1,2,3}.yaml`.

## MCP Servers

| Server | Function |
|--------|----------|
| bash-runner | Safe shell execution with guardrails |

### bash-runner Tools

- `run_bash(command, timeout, workdir, background, auto_background)` — Execute shell commands safely
- `check_background_task(task_id)` — Check background task status
- `list_background_tasks()` — List all background tasks
- `kill_background_task(task_id)` — Kill a background task

**Blocked patterns:**
- `rm -rf`, `sudo`, `su root`
- Fork bombs, shutdown, reboot
- `chmod 777 /`, `chown /`
- netcat listen, nmap
- Indirect shell (`bash -c`, `eval`)

## Safety Philosophy

### 1. Bash Guard (always active)
Blocks dangerous commands at MCP level — blocked patterns cannot be executed regardless of context.

### 2. Outbound Guard (scheduled/autonomous tasks only)
When `RAPPER_SCHEDULED=1` is set (cron/autonomous tasks):
- Blocks sends to non-whitelisted Discord/Telegram/Email/Slack targets
- Blocks HTTP POST/PUT from bash commands
- Interactive sessions are **unrestricted** — user is in the loop

Configure whitelist in `~/.rapper/config.yaml`:
```yaml
safety:
  outbound_whitelist:
    discord:
      - "channel_id_1"
    telegram:
      - "-1001234567890"
    email:
      - "allowed@example.com"
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/                          # All tests
uv run python tests/test_mcp_simple.py        # MCP protocol
uv run python tests/test_outbound_guard.py    # Guard logic
uv run python tests/test_worktree_isolation.py  # Worktree isolation
uv run python tests/test_audit_progress.py    # Audit/progress files
uv run python tests/test_version_management.py # Version tracking

# Test MCP server manually
echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}},"id":1}' | uv run python mcp-servers/bash-runner/server.py
```

## Integration with Hermes

### Ephemeral mode (current default)
Hermes spawns Rapper on-demand via `delegate_task`, Rapper exits when done:

```python
delegate_task(
    goal="implement feature X",
    acp_command="/app/rapper/rapper",
    acp_args=["--acp", "--stdio"]
)
```

### Background task mode
Hermes launches a long-running task and polls for completion:

```bash
rapper --background "fix-auth" -p "Fix the auth bug" -w /app/project --worktree
rapper --status fix-auth  # poll until completed/failed
rapper --merge fix-auth   # merge worktree when done
```

### Daemon mode (persistent)
Three named daemons (rapper-1/2/3) run continuously, polling Agent Board for tasks:

```bash
python /app/rapper/launch_daemons.py
```

Each daemon registers itself, polls for `todo` tasks assigned to its `agent_id`, executes them via Claude Code, and updates task status on completion.

See `HERMES_INTEGRATION.md` for full Python integration code examples and `HERMES_INTEGRATION_EXAMPLES.md` for detailed usage examples of enhanced features.

## Agent Skills

Skills are loaded automatically from `~/.claude/skills/` (global) and `.claude/skills/` (project).
Before starting any non-trivial task, scan the relevant skills and follow their instructions.

### Development Workflow (obra/superpowers)
Design docs and plans live in `docs/superpowers/`. Always follow this sequence:
**Brainstorm → Design Doc → Plan → TDD Implementation → Code Review → Finish**

| Phase | Location |
|-------|----------|
| Specs | `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` |
| Plans | `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` |

### Available Global Skills (`~/.claude/skills/`)

| Skill | When to use |
|-------|-------------|
| `diagnose` | Bug/regression: reproduce → minimise → hypothesise → fix |
| `tdd` | New feature or bugfix: red-green-refactor TDD loop |
| `grill-me` | Stress-test a plan — agent interviews you until shared understanding |
| `grill-with-docs` | Align plan with domain model, update CONTEXT.md + ADRs |
| `zoom-out` | Need broader context / higher-level view of the codebase |
| `improve-codebase-architecture` | Architecture health check, refactor opportunities |
| `triage` | Issue triage state machine (create / review / prepare for agent) |
| `to-issues` | Break a plan/spec/PRD into independently-grabbable GitHub issues |
| `to-prd` | Turn conversation context into a PRD on the issue tracker |
| `caveman` | 75% token reduction mode — ultra-compressed output |
| `write-a-skill` | Create a new SKILL.md with proper structure |

## Enhanced Hermes Integration

### Structured Result Reporting

Rapper now provides enhanced structured result parsing and reporting for better Hermes integration:

#### Features
- **Robust JSON parsing**: Handles multiple formats and provides fallback inference
- **Enhanced Claude guidance**: Clear, emphasized prompts ensure proper structured output
- **Machine-readable status**: `--status` includes `HERMES_INTEGRATION_JSON` line for programmatic access
- **Automatic fallback**: If explicit JSON isn't found, system infers results from text patterns

#### Structured Result Format
```json
{
  "status": "completed|failed|partial",
  "output_path": "relative/path/to/main/file",
  "pr_url": "https://github.com/user/repo/pull/123",
  "errors": ["error message if any"]
}
```

### Concurrency Control

Enhanced concurrent task management for multi-Rapper scenarios:

#### New Commands
- `rapper --task-count-json`: Detailed concurrency info in JSON format
- Enhanced `--task-count`: Backward-compatible simple count

#### Python Integration (`lib/hermes_integration.py`)
```python
from lib.hermes_integration import RapperTaskManager

manager = RapperTaskManager()

# Check capacity
if manager.can_start_task():
    task_id = manager.start_task("task-name", "prompt", workdir="/app/project")

# Wait for slot availability
if manager.wait_for_slot(max_wait=300):
    # Slot available, start task

# Monitor completion
result = manager.wait_for_completion(task_id, timeout=3600)
```

#### Concurrency JSON Format
```json
{
  "timestamp": 1778161033,
  "concurrency": {
    "running": 4,
    "max_concurrent": 5,
    "at_capacity": false,
    "available_slots": 1
  },
  "task_counts": {
    "pending": 0,
    "running": 4,
    "completed": 189,
    "failed": 48,
    "cancelled": 6,
    "total": 247
  }
}
```

### Configuration

Set maximum concurrent tasks in `~/.rapper/config.yaml`:
```yaml
tasks:
  max_concurrent_tasks: 5  # Adjust based on resource limits
```
