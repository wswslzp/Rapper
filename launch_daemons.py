#!/usr/bin/env python3
"""Launch daemon processes and supervise them.

This script is designed to run as a Type=simple systemd service.
It forks rapper-1/2/3 and reviewer-1/2/3 subprocesses (based on configuration),
then enters a supervisor loop that restarts any child that exits unexpectedly.
On SIGTERM, it gracefully shuts down all children and exits cleanly.

Supports reviewer gradual rollout via ENABLE_REVIEWERS environment variable:
- Default: only rapper-1/2/3 enabled
- ENABLE_REVIEWERS=1: enable all available reviewer configs
- ENABLE_REVIEWERS=gradual: enable only reviewer-1
"""

import json
import os
import signal
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import List, Tuple, Optional, Dict


# Global flag set by SIGTERM handler
_shutdown = False

# Global failure tracking for backoff logic
_failure_counts = {}  # agent_id -> consecutive failure count
MAX_RESTART_ATTEMPTS = 5


def build_daemon_specs() -> List[Tuple[str, Path, Path]]:
    """Build daemon specs based on ENABLE_REVIEWERS environment variable and available configs.

    Returns:
        List of (agent_id, config_path, log_path) tuples for daemons to start
    """
    home = Path.home()
    log_dir = home / ".rapper/logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    specs = []

    # Always include rapper specs if configs exist
    for i in range(1, 4):
        agent_id = f"rapper-{i}"
        config_path = home / f".rapper/config-{agent_id}.yaml"
        log_path = log_dir / f"daemon-{agent_id}.log"

        if config_path.exists():
            specs.append((agent_id, config_path, log_path))
        else:
            print(f"Supervisor: {agent_id} config missing at {config_path}, skipping", flush=True)

    # Check for reviewer specs based on ENABLE_REVIEWERS
    enable_reviewers = os.environ.get("ENABLE_REVIEWERS", "")

    if enable_reviewers in ("1", "gradual"):
        if enable_reviewers == "gradual":
            # Only reviewer-1 for gradual rollout
            reviewer_ids = ["reviewer-1"]
        else:
            # All available reviewers for full enablement
            reviewer_ids = ["reviewer-1", "reviewer-2", "reviewer-3"]

        for agent_id in reviewer_ids:
            config_path = home / f".rapper/config-{agent_id}.yaml"
            log_path = log_dir / f"daemon-{agent_id}.log"

            if config_path.exists():
                # Validate reviewer config before including
                if validate_reviewer_config(config_path):
                    # For build_daemon_specs, include reviewer if config is valid
                    # Settings validation will be done later in main() for actual startup
                    specs.append((agent_id, config_path, log_path))
                else:
                    print(f"Supervisor: {agent_id} reviewer config validation failed, skipping", flush=True)
            else:
                print(f"Supervisor: {agent_id} config missing at {config_path}, skipping", flush=True)

    return specs


def validate_daemon_config(agent_id: str, config_path: Path, settings_path: Optional[Path]) -> bool:
    """Validate that daemon config files exist and are properly formatted.

    Args:
        agent_id: Agent identifier (e.g., "reviewer-1")
        config_path: Path to the YAML config file
        settings_path: Path to the settings JSON file (optional for rappers)

    Returns:
        True if config is valid, False otherwise
    """
    # Check config file exists
    if not config_path.exists():
        print(f"Supervisor: {agent_id} config missing at {config_path}", flush=True)
        return False

    # Check config file is valid YAML
    try:
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f)
        if not isinstance(config_data, dict):
            print(f"Supervisor: {agent_id} config invalid YAML format", flush=True)
            return False
    except Exception as e:
        print(f"Supervisor: {agent_id} config YAML parse error: {e}", flush=True)
        return False

    # For reviewers, check settings file if specified
    if agent_id.startswith("reviewer-") and settings_path:
        if not settings_path.exists():
            print(f"Supervisor: {agent_id} settings missing at {settings_path}", flush=True)
            return False

        try:
            with open(settings_path, 'r') as f:
                settings_data = json.load(f)
            if not isinstance(settings_data, dict):
                print(f"Supervisor: {agent_id} settings invalid JSON format", flush=True)
                return False
        except Exception as e:
            print(f"Supervisor: {agent_id} settings JSON parse error: {e}", flush=True)
            return False

    return True


def validate_reviewer_config(config_path: Path) -> bool:
    """Validate that reviewer config has required reviewer-specific fields.

    Args:
        config_path: Path to the reviewer YAML config file

    Returns:
        True if reviewer config is valid, False otherwise
    """
    try:
        with open(config_path, 'r') as f:
            config_data = yaml.safe_load(f)
    except Exception as e:
        print(f"Supervisor: reviewer config parse error at {config_path}: {e}", flush=True)
        return False

    if not isinstance(config_data, dict):
        print(f"Supervisor: reviewer config invalid format at {config_path}", flush=True)
        return False

    agent_board = config_data.get("agent_board", {})
    if not isinstance(agent_board, dict):
        print(f"Supervisor: reviewer config missing agent_board section at {config_path}", flush=True)
        return False

    # Check all required reviewer fields and report all missing ones
    is_valid = True

    if agent_board.get("role") != "reviewer":
        print(f"Supervisor: reviewer config missing role=reviewer at {config_path}", flush=True)
        is_valid = False

    poll_columns = agent_board.get("poll_columns")
    if not isinstance(poll_columns, list) or "review" not in poll_columns:
        print(f"Supervisor: reviewer config missing poll_columns=[\"review\"] at {config_path}", flush=True)
        is_valid = False

    return is_valid


def restart_with_backoff(agent_id: str, config_path: str, log_path: str, attempt: int):
    """Restart daemon with exponential backoff on repeated failures.

    Args:
        agent_id: Agent identifier
        config_path: Path to config file
        log_path: Path to log file
        attempt: Restart attempt number (1-based)
    """
    if not should_attempt_restart(agent_id, attempt):
        print(f"Supervisor: {agent_id} max restart attempts ({MAX_RESTART_ATTEMPTS}) reached, giving up permanently", flush=True)
        return None

    # Calculate backoff time: attempt^2 seconds
    backoff_seconds = attempt ** 2
    print(f"Supervisor: {agent_id} backoff {backoff_seconds}s before restart attempt {attempt}", flush=True)
    time.sleep(backoff_seconds)

    try:
        proc = start_daemon(agent_id, Path(config_path), log_path)
        reset_failure_count(agent_id)
        return proc
    except Exception as exc:
        track_restart_failure(agent_id)
        print(f"Supervisor: {agent_id} restart attempt {attempt} failed: {exc}", flush=True)

        # Log when max attempts reached
        if not should_attempt_restart(agent_id, attempt + 1):
            print(f"Supervisor: {agent_id} max restart attempts ({MAX_RESTART_ATTEMPTS}) reached, will not attempt further restarts", flush=True)

        return None


def track_restart_failure(agent_id: str):
    """Track consecutive restart failures per agent.

    Args:
        agent_id: Agent identifier
    """
    _failure_counts[agent_id] = _failure_counts.get(agent_id, 0) + 1


def reset_failure_count(agent_id: str):
    """Reset failure count on successful restart.

    Args:
        agent_id: Agent identifier
    """
    _failure_counts[agent_id] = 0


def clear_all_failure_counts():
    """Clear all failure counts (useful for testing)."""
    global _failure_counts
    _failure_counts = {}


def get_failure_count(agent_id: str) -> int:
    """Get current failure count for agent.

    Args:
        agent_id: Agent identifier

    Returns:
        Current consecutive failure count
    """
    return _failure_counts.get(agent_id, 0)


def should_attempt_restart(agent_id: str, attempt: int) -> bool:
    """Determine if restart should be attempted based on max attempts limit.

    Args:
        agent_id: Agent identifier
        attempt: Current attempt number (1-based)

    Returns:
        True if restart should be attempted, False if max attempts reached
    """
    should_restart = attempt <= MAX_RESTART_ATTEMPTS
    if not should_restart:
        print(f"Supervisor: {agent_id} max restart attempts ({MAX_RESTART_ATTEMPTS}) reached", flush=True)
    return should_restart


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
    """Launch all daemon processes and supervise them forever."""
    # Register signal handler before forking children
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigterm_handler)

    # Build daemon specs based on configuration and feature flags
    daemon_specs = build_daemon_specs()

    if not daemon_specs:
        print("Supervisor: No valid daemon configs found, exiting", flush=True)
        sys.exit(1)

    print(f"Supervisor: Found {len(daemon_specs)} daemon specs to launch", flush=True)
    for agent_id, config_path, log_path in daemon_specs:
        print(f"Supervisor: Will launch {agent_id} with config {config_path}", flush=True)

    # Initial launch — stagger by 1 second each to avoid thundering-herd
    processes = {}  # agent_id -> (process, config_path, log_path)
    for agent_id, config_path, log_path in daemon_specs:
        # Validate config before starting
        settings_path = None
        try:
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)
            settings_path_str = config_data.get("claude", {}).get("settings_path")
            if settings_path_str:
                settings_path = Path(settings_path_str)
        except Exception:
            pass

        if not validate_daemon_config(agent_id, config_path, settings_path):
            print(f"Supervisor: Skipping {agent_id} due to validation failure", flush=True)
            continue

        try:
            proc = start_daemon(agent_id, config_path, log_path)
            processes[agent_id] = (proc, config_path, log_path)
            reset_failure_count(agent_id)  # Reset failure count on successful start
            time.sleep(1)
        except Exception as exc:
            track_restart_failure(agent_id)
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
                # Child exited — log and restart with backoff
                track_restart_failure(agent_id)
                failure_count = get_failure_count(agent_id)
                print(
                    f"Supervisor: {agent_id} (PID {proc.pid}) exited with code {ret}. "
                    f"Failure count: {failure_count}. Restarting with backoff...",
                    flush=True
                )
                try:
                    proc._log_file.close()
                except Exception:
                    pass

                # Use backoff logic for restart
                new_proc = restart_with_backoff(agent_id, str(config_path), log_path, failure_count)
                if new_proc is not None:
                    processes[agent_id] = (new_proc, config_path, log_path)
                    print(f"Supervisor: {agent_id} restarted successfully", flush=True)
                else:
                    # Remove from processes if max attempts reached
                    del processes[agent_id]
                    print(f"Supervisor: {agent_id} permanently failed, removing from supervision", flush=True)

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
