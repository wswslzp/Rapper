#!/usr/bin/env python3
"""
Setup settings.json for Rapper.

Configures Claude Code's settings.json with:
- PreToolUse hook for outbound_guard
- Bash blocker (force use of bash-runner MCP)
- Permissions for MCP tools

Usage:
    python setup_settings.py [--dry-run]
"""

import argparse
import json
import os
import sys
from pathlib import Path

RAPPER_DIR = Path(__file__).parent.parent.absolute()

# Bash tool blocker — force use of bash-runner MCP
BASH_BLOCKER_CMD = (
    "echo 'BLOCKED: Use mcp__bash-runner__run_bash instead of the built-in "
    "Bash tool' >&2; exit 2"
)

# Outbound guard matcher pattern
OUTBOUND_MATCHER = (
    "mcp__discord__*|mcp__telegram__*|mcp__mail__*|mcp__gmail__*|"
    "mcp__slack__*|mcp__bash-runner__run_bash"
)

# Permissions to add (allow list)
PERMISSIONS_ALLOW = [
    "Read", "Write", "Edit", "Glob", "Grep", 
    "WebFetch", "WebSearch", "NotebookEdit",
    "mcp__bash-runner__*",
    # Read-only bash for safe commands
    "Bash(git *)", "Bash(ls *)", "Bash(ls)", 
    "Bash(cat *)", "Bash(head *)", "Bash(tail *)",
    "Bash(find *)", "Bash(grep *)", "Bash(rg *)", 
    "Bash(pwd)", "Bash(which *)", "Bash(whoami)", "Bash(hostname)",
    "Bash(echo *)", "Bash(date *)", "Bash(uname *)",
]

# Permissions to deny
PERMISSIONS_DENY = []


def merge_settings(settings: dict, rapper_dir: Path) -> dict:
    """Apply Rapper managed keys to settings."""
    
    # --- Hooks ---
    settings.setdefault("hooks", {})
    
    # PreToolUse hooks
    pre_hooks = settings["hooks"].setdefault("PreToolUse", [])
    
    # 1. Bash blocker — force use of bash-runner
    if not any(h.get("matcher") == "Bash" for h in pre_hooks):
        pre_hooks.append({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": BASH_BLOCKER_CMD}],
        })
    
    # 2. Outbound guard
    venv_py = str(rapper_dir / ".venv" / "bin" / "python3")
    outbound_guard_cmd = f"{venv_py} {rapper_dir}/config/outbound_guard.py"
    
    # Remove stale outbound_guard entries
    stale = [
        i for i, h in enumerate(pre_hooks)
        if "outbound_guard.py" in str(h)
        and (outbound_guard_cmd not in str(h) or h.get("matcher") != OUTBOUND_MATCHER)
    ]
    for i in reversed(stale):
        del pre_hooks[i]
    
    # Add fresh entry if not present
    if not any("outbound_guard.py" in str(h) for h in pre_hooks):
        pre_hooks.append({
            "matcher": OUTBOUND_MATCHER,
            "hooks": [{"type": "command", "command": outbound_guard_cmd, "timeout": 5}],
        })
    
    # --- Permissions ---
    settings.setdefault("permissions", {})
    allow = settings["permissions"].setdefault("allow", [])
    for item in PERMISSIONS_ALLOW:
        if item not in allow:
            allow.append(item)
    
    deny = settings["permissions"].setdefault("deny", [])
    for item in PERMISSIONS_DENY:
        if item not in deny:
            deny.append(item)
    
    return settings


def main():
    parser = argparse.ArgumentParser(description="Setup Claude Code settings.json for Rapper")
    parser.add_argument("--dry-run", action="store_true", help="Print merged JSON, don't write")
    parser.add_argument("--rapper-dir", type=Path, default=RAPPER_DIR, help="Rapper directory")
    args = parser.parse_args()
    
    settings_path = Path.home() / ".claude" / "settings.json"
    
    # Load existing settings
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except json.JSONDecodeError as e:
            print(f"ERROR: {settings_path} is not valid JSON: {e}", file=sys.stderr)
            return 1
    else:
        settings = {}
        settings_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Merge our settings
    settings = merge_settings(settings, args.rapper_dir)
    
    if args.dry_run:
        print(json.dumps(settings, indent=2))
        return 0
    
    # Write atomically
    tmp_path = settings_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(settings, f, indent=2)
    tmp_path.rename(settings_path)
    
    print(f"✓ Updated {settings_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
