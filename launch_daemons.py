#!/usr/bin/env python3
"""Launch three rapper daemon processes and supervise them.

This script is designed to run as a Type=simple systemd service.
It forks rapper-1/2/3 subprocesses, then enters a supervisor loop
that restarts any child that exits unexpectedly. On SIGTERM, it
gracefully shuts down all children and exits cleanly.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


# Global flag set by SIGTERM handler
_shutdown = False


def sigterm_handler(signum, frame):
    """Handle SIGTERM: set shutdown flag so supervisor loop exits."""
    global _shutdown
    print("Supervisor: received SIGTERM, initiating graceful shutdown...", flush=True)
    _shutdown = True


def build_cmd(agent_id, config_path):
    """Build the command list for a daemon subprocess."""
    return [
        "/app/rapper/.venv/bin/python3",
        "/app/rapper/lib/daemon.py",
        "--config", str(config_path),
        "--agent-id", agent_id
    ]


def build_env():
    """Build environment for daemon subprocesses."""
    env = os.environ.copy()
    env['RAPPER_DIR'] = "/app/rapper"
    # Clear PYTHONPATH to avoid pydantic_core version conflict
    env['PYTHONPATH'] = ""
    return env


def start_daemon(agent_id, config_path, log_path):
    """Start a single daemon subprocess. Returns the Popen object."""
    cmd = build_cmd(agent_id, config_path)
    env = build_env()

    log_file = open(log_path, 'a')
    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        cwd="/app/rapper"
    )
    # Keep a reference to the log file on the process object so we can
    # close it if we need to replace the process later.
    process._log_file = log_file

    print(f"Supervisor: started {agent_id} PID={process.pid}, log={log_path}", flush=True)
    return process


def main():
    """Launch all three daemons and supervise them forever."""
    # Register signal handler before forking children
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigterm_handler)

    home = Path.home()
    log_dir = home / ".rapper/logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    daemon_specs = [
        ("rapper-1", home / ".rapper/config-rapper-1.yaml", log_dir / "daemon-rapper-1.log"),
        ("rapper-2", home / ".rapper/config-rapper-2.yaml", log_dir / "daemon-rapper-2.log"),
        ("rapper-3", home / ".rapper/config-rapper-3.yaml", log_dir / "daemon-rapper-3.log"),
    ]

    # Initial launch — stagger by 1 second each to avoid thundering-herd
    processes = {}  # agent_id -> (process, config_path, log_path)
    for agent_id, config_path, log_path in daemon_specs:
        try:
            proc = start_daemon(agent_id, config_path, log_path)
            processes[agent_id] = (proc, config_path, log_path)
            time.sleep(1)
        except Exception as exc:
            print(f"Supervisor: FAILED to start {agent_id}: {exc}", flush=True)

    print(f"Supervisor: {len(processes)} daemon(s) started, entering supervisor loop.", flush=True)

    # ── Supervisor loop ──────────────────────────────────────────────────────
    while not _shutdown:
        time.sleep(10)

        if _shutdown:
            break

        for agent_id, (proc, config_path, log_path) in list(processes.items()):
            ret = proc.poll()
            if ret is not None:
                # Child exited — log and restart
                print(
                    f"Supervisor: {agent_id} (PID {proc.pid}) exited with code {ret}. "
                    "Restarting...",
                    flush=True
                )
                try:
                    proc._log_file.close()
                except Exception:
                    pass
                try:
                    new_proc = start_daemon(agent_id, config_path, log_path)
                    processes[agent_id] = (new_proc, config_path, log_path)
                except Exception as exc:
                    print(f"Supervisor: FAILED to restart {agent_id}: {exc}", flush=True)

    # ── Graceful shutdown ────────────────────────────────────────────────────
    print("Supervisor: shutting down — sending SIGTERM to all children...", flush=True)
    for agent_id, (proc, _, _) in processes.items():
        try:
            proc.send_signal(signal.SIGTERM)
            print(f"Supervisor: sent SIGTERM to {agent_id} (PID {proc.pid})", flush=True)
        except ProcessLookupError:
            print(f"Supervisor: {agent_id} (PID {proc.pid}) already gone", flush=True)
        except Exception as exc:
            print(f"Supervisor: error signalling {agent_id}: {exc}", flush=True)

    # Wait up to 30 seconds for each child to exit
    deadline = time.time() + 30
    for agent_id, (proc, _, _) in processes.items():
        remaining = max(0, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
            print(f"Supervisor: {agent_id} exited cleanly", flush=True)
        except subprocess.TimeoutExpired:
            print(f"Supervisor: {agent_id} did not exit in time, sending SIGKILL", flush=True)
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc._log_file.close()
        except Exception:
            pass

    print("Supervisor: all children stopped. Exiting.", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
