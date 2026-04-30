# Rapper 🎤

Claude Code wrapper with safety guardrails — auto-run enabled, dangerous commands blocked.

## Features

- **🔧 Bash MCP**: Auto-run shell commands via MCP (no permission prompts)
- **🛡️ Safety Guard**: Blocks dangerous commands (rm -rf, sudo, etc.)
- **📨 Outbound Guard**: Whitelist-based control for scheduled task messaging
- **📦 Tmux Sessions**: Long-running Claude sessions with `rapper-<project>` naming
- **⚙️ Easy Config**: `~/.rapper/config.yaml` for all settings

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

```bash
# Interactive Claude
rapper

# One-shot prompt
rapper -p "explain this code"

# ACP mode (for Hermes integration)
rapper --acp --stdio

# Tmux session for long tasks
rapper --tmux myproject
rapper --attach myproject
rapper --list

# Background tasks (autonomous)
rapper --background <name> -p "task description" --workdir /path/to/project
rapper --background <name> --worktree -p "task" --workdir /path/to/project  # Isolated git worktree
rapper --tasks                          # List all tasks
rapper --status <task_id>               # Get task status  
rapper --logs <task_id> [lines]         # View task logs
rapper --cancel <task_id>               # Cancel running task
rapper --merge <task_id>                # Merge worktree branch + cleanup

# Check status
rapper --check
```

## Background Tasks

Rapper supports running autonomous Claude tasks in the background, perfect for long-running development work or scheduled automation.

### Basic Usage

```bash
# Start a background task with specific working directory
rapper --background "fix-auth-bug" -p "Fix the authentication issue in /app/project" --workdir /app/project

# Use git worktree for isolation (recommended for code changes)
rapper --background "add-feature" --worktree -p "Add user login feature" --workdir /app/project

# Monitor task progress
rapper --tasks                    # List all tasks
rapper --status fix-auth-bug      # Get detailed status
rapper --logs fix-auth-bug 100    # View last 100 log lines

# Merge completed worktree changes
rapper --merge fix-auth-bug       # Auto-merge branch + cleanup
```

### Workdir Parameter

The `--workdir` parameter is **critical** for background tasks:
- **Without `--workdir`**: Claude runs in the caller's directory (often wrong)
- **With `--workdir`**: Claude runs in the specified project directory

Example of why this matters:
```bash
# BAD: Claude will work in /tmp instead of your project
cd /tmp && rapper --background "fix-bug" -p "Fix the login bug"

# GOOD: Claude works in your project directory  
cd /tmp && rapper --background "fix-bug" -p "Fix the login bug" --workdir /app/myproject
```

The task will:
1. Change to the specified `--workdir` before running Claude
2. Record both `workdir` (intended) and `workdir_effective` (actual) in task status
3. Ensure all file operations use the correct project paths

### Git Worktree Mode

Use `--worktree` for isolated development:
- Creates `rapper/<task-name>` branch in separate worktree
- Safe parallel development without affecting main branch  
- Use `rapper --merge <task_id>` to merge changes when complete

## Safety Features

### Bash Runner
The bash-runner MCP replaces Claude's built-in Bash tool with safety guards:
- ❌ `rm -rf` — blocked
- ❌ `sudo`, `su root` — blocked  
- ❌ Fork bombs, shutdown — blocked
- ❌ Indirect shell execution — blocked
- ✅ Normal development commands — allowed

### Outbound Guard
For scheduled/autonomous tasks (RAPPER_SCHEDULED=1):
- Only whitelisted Discord channels, Telegram chats, emails can receive messages
- HTTP POST/PUT from bash commands is blocked
- Interactive sessions are unrestricted

Configure whitelist in `~/.rapper/config.yaml`:
```yaml
safety:
  outbound_whitelist:
    discord:
      - "123456789012345678"
    telegram:
      - "-1001234567890"
    email:
      - "user@example.com"
```

## Configuration

Config location: `~/.rapper/config.yaml`

```yaml
claude:
  model: "claude-sonnet-4-20250514"

safety:
  outbound_guard_enabled: true
  outbound_whitelist:
    discord: []
    telegram: []
    email: []
    slack: []

tmux:
  default_session_name: "default"

logging:
  enabled: true
  level: "info"
```

## Integration with Hermes

```python
# Use rapper as delegate_task backend
delegate_task(
    goal="build the project",
    acp_command="/app/rapper/rapper",
    acp_args=["--acp", "--stdio"]
)
```

## Files

```
/app/rapper/
├── rapper                          # Main entry script
├── bin/rapper                      # PATH wrapper
├── mcp-servers/bash-runner/        # Safe bash MCP server
├── config/
│   ├── outbound_guard.py           # PreToolUse hook
│   ├── setup_settings.py           # settings.json manager
│   └── default-config.yaml         # Default configuration
├── tests/
│   ├── test_outbound_guard.py      # Guard tests
│   └── test_mcp_simple.py          # MCP tests
├── pyproject.toml                  # uv project config
├── CLAUDE.md                       # Project guide
└── README.md

~/.rapper/
├── config.yaml                     # User configuration
└── logs/                           # Log files

~/.claude.json                      # MCP registration
~/.claude/settings.json             # Hooks configuration
```

## License

MIT
