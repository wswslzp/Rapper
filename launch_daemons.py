#!/usr/bin/env python3
"""Launch three rapper daemon processes in the background."""

import os
import subprocess
import time
from pathlib import Path


def launch_daemon(agent_id, config_path, log_path):
    """Launch a single daemon process using daemon.py directly."""
    cmd = [
        "/app/rapper/.venv/bin/python3",
        "/app/rapper/lib/daemon.py",
        "--config", str(config_path),
        "--agent-id", agent_id
    ]

    env = os.environ.copy()
    env['RAPPER_DIR'] = "/app/rapper"
    # Clear PYTHONPATH to avoid pydantic_core version conflict
    env['PYTHONPATH'] = ""

    with open(log_path, 'w') as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=env,
            cwd="/app/rapper"
        )

    print(f"Started {agent_id} with PID {process.pid}, logging to {log_path}")
    return process


def main():
    """Launch all three daemons."""
    home = Path.home()
    log_dir = home / ".rapper/logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    daemons = [
        ("rapper-1", home / ".rapper/config-rapper-1.yaml", log_dir / "daemon-rapper-1.log"),
        ("rapper-2", home / ".rapper/config-rapper-2.yaml", log_dir / "daemon-rapper-2.log"),
        ("rapper-3", home / ".rapper/config-rapper-3.yaml", log_dir / "daemon-rapper-3.log"),
    ]

    processes = []
    for agent_id, config_path, log_path in daemons:
        try:
            process = launch_daemon(agent_id, config_path, log_path)
            processes.append((agent_id, process))
            time.sleep(1)
        except Exception as e:
            print(f"Failed to launch {agent_id}: {e}")

    print(f"\nLaunched {len(processes)} daemon processes. Waiting 8 seconds...")
    time.sleep(8)

    for agent_id, config_path, log_path in daemons:
        print(f"\n=== {agent_id} log ===")
        try:
            with open(log_path, 'r') as f:
                print(f.read()[-2000:] or "(empty)")
        except FileNotFoundError:
            print(f"Log not found: {log_path}")


if __name__ == "__main__":
    main()
