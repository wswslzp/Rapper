# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What This Repo Is

**Rapper** 🎤 — A Claude Code wrapper with safety guardrails.

Core components:
- **Entry point** (`rapper`): setup + launch Claude Code + manage Tmux sessions
- **bash-runner MCP**: safe shell execution with dangerous command blocking
- **outbound_guard**: whitelist-based outbound message safety (for scheduled tasks)

## Repo Structure

```
/app/rapper/
├── rapper                      # Main entry script (Bash)
├── bin/rapper                  # $PATH wrapper
├── config/
│   ├── default-config.yaml     # Default config template
│   ├── outbound_guard.py       # PreToolUse hook for message safety
│   └── setup_settings.py       # settings.json manager
├── mcp-servers/
│   └── bash-runner/
│       └── server.py           # FastMCP implementation
├── docs/
│   └── superpowers/
│       ├── specs/              # Design docs
│       └── plans/              # Implementation plans
├── tests/
│   ├── test_mcp_simple.py      # MCP protocol tests
│   └── test_outbound_guard.py  # Guard tests
├── pyproject.toml              # Python project config (uv)
├── CLAUDE.md                   # This file
└── README.md
```

## User Config

User configuration lives in `~/.rapper/`:
```
~/.rapper/
├── config.yaml                 # User settings (including whitelist)
└── logs/                       # Usage logs
```

Claude Code configuration:
```
~/.claude.json                  # MCP registration (bash-runner)
~/.claude/settings.json         # Hooks (outbound_guard, bash blocker)
```

## Commands

```bash
rapper                    # Launch interactive Claude Code
rapper -p "prompt"        # One-shot prompt mode
rapper --acp --stdio      # ACP mode (for Hermes delegate_task)
rapper --setup            # Force re-run setup
rapper --check            # Check status

rapper --tmux [name]      # Start Tmux session (rapper-<name>)
rapper --attach <name>    # Attach to session
rapper --list             # List Rapper sessions

rapper --background <name> --worktree -p "task" -w /project  # Isolated worktree mode
rapper --merge <task_id>                               # Merge worktree + cleanup
```

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
Blocks dangerous commands at MCP level — the blocked patterns cannot be executed.

### 2. Outbound Guard (scheduled tasks only)
When `RAPPER_SCHEDULED=1` is set (for cron/autonomous tasks):
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

# Run MCP tests
uv run python tests/test_mcp_simple.py

# Run outbound guard tests
uv run python tests/test_outbound_guard.py

# Test MCP server manually
echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test"}},"id":1}' | uv run python mcp-servers/bash-runner/server.py
```

## Integration with Hermes

Rapper is designed to be called by Hermes via `delegate_task`:

```python
delegate_task(
    goal="implement feature X",
    acp_command="/app/rapper/rapper",
    acp_args=["--acp", "--stdio"]
)
```

For scheduled tasks that need to send messages:
```python
# In cronjob, set RAPPER_SCHEDULED=1 to enable outbound guard
# The rapper script should be called with appropriate env vars
```
