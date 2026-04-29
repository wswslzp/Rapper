#!/usr/bin/env python3
"""
Outbound Guard — PreToolUse hook for Rapper

Blocks send-style tool calls whose target isn't on the user's whitelist.
Designed for scheduled/autonomous tasks where Claude picks destinations.

Input (stdin): Claude Code PreToolUse JSON, e.g.:
    {"tool_name": "mcp__discord__send_message",
     "tool_input": {"channel_id": "123456789", "content": "..."}}

Exit:
    0 — allow
    2 — block (stderr = user-visible reason)

Whitelist sources:
  - ~/.rapper/config.yaml → safety.outbound_whitelist.*

Scope: Only active when RAPPER_SCHEDULED=1 (set by rapper for cron/autonomous tasks).
Interactive sessions are unrestricted — user is in the loop.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # hook becomes no-op if PyYAML missing


# ---------- whitelist loader ----------

def _read_yaml(path: str) -> dict:
    if not yaml or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            d = yaml.safe_load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _flatten(items: Any) -> list[str]:
    """Flatten nested lists/tuples into a list of strings."""
    out: list[str] = []
    if items is None:
        return out
    if isinstance(items, (list, tuple)):
        for item in items:
            out.extend(_flatten(item))
    else:
        s = str(items).strip()
        if s:
            out.append(s)
    return out


def load_whitelist() -> dict[str, set[str]]:
    """Load allowed targets from ~/.rapper/config.yaml."""
    cfg = _read_yaml(os.path.expanduser("~/.rapper/config.yaml"))
    
    safety = cfg.get("safety", {}) or {}
    wl = safety.get("outbound_whitelist", {}) or {}
    
    return {
        "discord_channels": set(_flatten(wl.get("discord", []))),
        "telegram_chats": set(_flatten(wl.get("telegram", []))),
        "emails": {e.lower() for e in _flatten(wl.get("email", []))},
        "slack_channels": set(_flatten(wl.get("slack", []))),
    }


# ---------- tool-input validators ----------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def _extract_emails(obj: Any) -> list[str]:
    """Extract all email-looking strings from a payload."""
    if obj is None:
        return []
    if isinstance(obj, str):
        return [m.group(0).lower() for m in _EMAIL_RE.finditer(obj)]
    if isinstance(obj, (list, tuple)):
        out: list[str] = []
        for item in obj:
            out.extend(_extract_emails(item))
        return out
    if isinstance(obj, dict):
        out = []
        for v in obj.values():
            out.extend(_extract_emails(v))
        return out
    return []


def _check_email(tool_input: dict, wl: dict[str, set[str]]) -> tuple[bool, str]:
    """All email recipients must be on the whitelist."""
    candidates: list[str] = []
    for key in ("email", "to", "cc", "bcc", "recipients", "recipient"):
        v = tool_input.get(key)
        if v:
            candidates.extend(_extract_emails(v))
    
    wanted = {c.lower() for c in candidates}
    if not wanted:
        return True, ""  # No recipient detected, allow
    
    blocked = sorted(wanted - wl["emails"])
    if blocked:
        return False, f"recipient(s) not on whitelist: {', '.join(blocked)}"
    return True, ""


def _check_discord(tool_input: dict, wl: dict[str, set[str]]) -> tuple[bool, str]:
    """Discord channel_id must be on the whitelist."""
    channel = tool_input.get("channel_id") or tool_input.get("channel") or ""
    if not channel:
        return False, "Discord send requires channel_id"
    
    channel = str(channel)
    if channel in wl["discord_channels"]:
        return True, ""
    
    return False, f"Discord channel {channel!r} not on whitelist"


def _check_telegram(tool_input: dict, wl: dict[str, set[str]]) -> tuple[bool, str]:
    """Telegram chat_id must be on the whitelist."""
    chat = tool_input.get("chat_id") or tool_input.get("chat") or ""
    if not chat:
        return False, "Telegram send requires chat_id"
    
    chat = str(chat)
    if chat in wl["telegram_chats"]:
        return True, ""
    
    return False, f"Telegram chat {chat!r} not on whitelist"


def _check_slack(tool_input: dict, wl: dict[str, set[str]]) -> tuple[bool, str]:
    """Slack channel must be on the whitelist."""
    channel = tool_input.get("channel_id") or tool_input.get("channel") or ""
    if not channel:
        return False, "Slack send requires channel"
    
    channel = str(channel)
    if channel in wl["slack_channels"]:
        return True, ""
    
    return False, f"Slack channel {channel!r} not on whitelist"


# HTTP POST patterns for bash-runner guard
_HTTP_POST_PATTERNS = [
    r"\bcurl\b[^\n]*\s-X\s*(POST|PUT|PATCH|DELETE)",
    r"\bcurl\b[^\n]*\s(-d\b|--data\b|--data-raw\b|--data-binary\b)",
    r"\bwget\b[^\n]*\s--post-data\b",
    r"\bwget\b[^\n]*\s--method=(POST|PUT|PATCH|DELETE)",
    r"requests\.(post|put|patch|delete)\s*\(",
]
_HTTP_POST_RE = re.compile("|".join(_HTTP_POST_PATTERNS), re.IGNORECASE)


def _check_bash_http(tool_input: dict, wl: dict[str, set[str]]) -> tuple[bool, str]:
    """Block outbound HTTP POST/PUT from bash commands in scheduled mode."""
    cmd = tool_input.get("command", "") or ""
    if not isinstance(cmd, str) or not cmd:
        return True, ""
    
    m = _HTTP_POST_RE.search(cmd)
    if m:
        return False, (
            f"bash command contains outbound HTTP write ({m.group(0)!r}). "
            f"Scheduled tasks must use proper send tools, not raw HTTP."
        )
    return True, ""


# Tool → validator mapping
# Adjust tool names to match your MCP server naming
_CHECKS = {
    # Discord
    "mcp__discord__send_message": _check_discord,
    "mcp__discord__send": _check_discord,
    # Telegram  
    "mcp__telegram__send_message": _check_telegram,
    "mcp__telegram__send": _check_telegram,
    # Email
    "mcp__mail__send_mail": _check_email,
    "mcp__mail__send": _check_email,
    "mcp__gmail__send": _check_email,
    "mcp__email__send": _check_email,
    # Slack
    "mcp__slack__send_message": _check_slack,
    "mcp__slack__post": _check_slack,
    # Bash HTTP guard
    "mcp__bash-runner__run_bash": _check_bash_http,
}


# ---------- entry ----------

def main() -> int:
    # Only active for scheduled/autonomous tasks
    if os.environ.get("RAPPER_SCHEDULED", "") != "1":
        return 0
    
    # Read PreToolUse JSON from stdin
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # Can't parse → allow (fail-open)
    
    tool = payload.get("tool_name") if isinstance(payload, dict) else ""
    if tool not in _CHECKS:
        return 0  # Not a gated tool
    
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    
    wl = load_whitelist()
    
    # Safety valve: if whitelist is completely empty, allow everything
    # but warn about misconfiguration
    if not any(wl.values()):
        print(
            "[outbound_guard] WARNING: empty whitelist, allowing everything. "
            "Configure ~/.rapper/config.yaml safety.outbound_whitelist.",
            file=sys.stderr
        )
        return 0
    
    allow, reason = _CHECKS[tool](tool_input, wl)
    if allow:
        return 0
    
    # Block: stderr message goes back to Claude
    print(
        f"[outbound_guard] BLOCKED {tool}: {reason}\n"
        f"To allow this target, add it to ~/.rapper/config.yaml under "
        f"safety.outbound_whitelist (discord/telegram/email/slack).",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
