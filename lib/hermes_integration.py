#!/usr/bin/env python3
"""
Hermes Integration Module — Python interface for Hermes to interact with Rapper.

Provides high-level functions for:
- Starting and monitoring background tasks with automatic concurrency control
- Checking concurrency limits and waiting for available slots
- Retrieving structured task results with robust parsing
- Waiting for task completion with timeout handling

Example usage:
```python
from lib.hermes_integration import RapperTaskManager

manager = RapperTaskManager()

# Check if we can start a task
if manager.can_start_task():
    task_id = manager.start_task("fix-auth", "Fix the authentication bug", workdir="/app/project")

    # Wait for completion with timeout
    result = manager.wait_for_completion(task_id, timeout=3600)
    print(f"Task status: {result['status']}")
    print(f"Output: {result['structured_result']}")

# Or use convenience function
result = start_and_wait("deploy", "Deploy the feature", timeout=1800)
if result and result.get('status') == 'completed':
    print(f"Successfully deployed! Output: {result['structured_result']['output_path']}")
```

Enhanced concurrency management:
```python
# Wait for a slot if at capacity
if not manager.can_start_task():
    print("At capacity, waiting for available slot...")
    if manager.wait_for_slot(max_wait=300):
        task_id = manager.start_task("urgent-fix", "Fix critical bug")

# Check detailed concurrency info
info = manager.get_task_counts()
print(f"Running: {info['concurrency']['running']}/{info['concurrency']['max_concurrent']}")
print(f"Available slots: {info['concurrency']['available_slots']}")
```
"""

import json
import subprocess
import time
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass


@dataclass
class TaskInfo:
    """Represents comprehensive task information."""
    id: str
    name: str
    status: str
    structured_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    elapsed: float = 0.0
    workdir: Optional[str] = None
    worktree_path: Optional[str] = None
    board_task_id: Optional[str] = None

    @property
    def is_completed(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status in ["completed", "failed", "cancelled"]

    @property
    def is_successful(self) -> bool:
        """Check if task completed successfully."""
        return self.status == "completed"


@dataclass
class ConcurrencyInfo:
    """Represents detailed concurrency status."""
    running: int
    max_concurrent: int
    at_capacity: bool
    available_slots: int
    task_counts: Dict[str, int]
    timestamp: int

    @property
    def utilization_percent(self) -> float:
        """Get capacity utilization as percentage."""
        if self.max_concurrent == 0:
            return 0.0
        return (self.running / self.max_concurrent) * 100


@dataclass
class TaskResult:
    """Represents the final result of a completed task."""
    task_info: TaskInfo
    structured_result: Optional[Dict[str, Any]] = None
    success: bool = False
    output_path: Optional[str] = None
    pr_url: Optional[str] = None
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

        # Extract info from structured result if available
        if self.task_info.structured_result:
            sr = self.task_info.structured_result
            self.structured_result = sr
            self.success = sr.get('status') == 'completed'
            self.output_path = sr.get('output_path')
            self.pr_url = sr.get('pr_url')
            self.errors = sr.get('errors', [])


class RapperTaskManager:
    """Helper class for Hermes to manage Rapper tasks with concurrency control."""

    def __init__(self, rapper_path: str = "/app/rapper/rapper"):
        self.rapper_path = rapper_path

    def get_task_counts(self) -> Dict[str, Any]:
        """Get detailed task count information.

        Returns:
            Dict with concurrency info, task counts, and availability status.
        """
        try:
            result = subprocess.run(
                [self.rapper_path, "--task-count-json"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
            pass

        # Fallback to basic count
        try:
            result = subprocess.run(
                [self.rapper_path, "--task-count"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                running = int(result.stdout.strip())
                return {
                    "concurrency": {
                        "running": running,
                        "max_concurrent": 5,  # default
                        "at_capacity": running >= 5,
                        "available_slots": max(0, 5 - running)
                    },
                    "task_counts": {
                        "running": running
                    }
                }
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass

        return {"error": "Unable to get task counts"}

    def get_concurrency_info(self) -> ConcurrencyInfo:
        """Get detailed concurrency information as a structured object."""
        counts = self.get_task_counts()

        if "error" in counts:
            # Return default/empty info on error
            return ConcurrencyInfo(
                running=0, max_concurrent=5, at_capacity=False, available_slots=5,
                task_counts={}, timestamp=int(time.time())
            )

        conc = counts.get("concurrency", {})
        task_counts = counts.get("task_counts", {})

        return ConcurrencyInfo(
            running=conc.get("running", 0),
            max_concurrent=conc.get("max_concurrent", 5),
            at_capacity=conc.get("at_capacity", False),
            available_slots=conc.get("available_slots", 0),
            task_counts=task_counts,
            timestamp=counts.get("timestamp", int(time.time()))
        )

    def can_start_task(self) -> bool:
        """Check if a new task can be started (not at capacity)."""
        counts = self.get_task_counts()
        if "error" in counts:
            return False
        return not counts.get("concurrency", {}).get("at_capacity", True)

    def wait_for_slot(self, max_wait: int = 300, check_interval: int = 10) -> bool:
        """Wait for an available task slot.

        Args:
            max_wait: Maximum wait time in seconds
            check_interval: How often to check for availability

        Returns:
            True if a slot becomes available, False if timeout
        """
        start_time = time.time()
        while time.time() - start_time < max_wait:
            if self.can_start_task():
                return True
            time.sleep(check_interval)
        return False

    def start_task(self, name: str, prompt: str, **kwargs) -> Optional[str]:
        """Start a new Rapper task if resources are available.

        Args:
            name: Task name
            prompt: Task prompt
            **kwargs: Additional task options (workdir, budget, etc.)

        Returns:
            Task ID if started successfully, None if at capacity or failed
        """
        if not self.can_start_task():
            return None

        cmd = [self.rapper_path, "--background", name, "-p", prompt]

        # Add optional parameters
        if "workdir" in kwargs:
            cmd.extend(["--workdir", kwargs["workdir"]])
        if "budget" in kwargs:
            cmd.extend(["--budget", str(kwargs["budget"])])
        if "fallback" in kwargs:
            cmd.extend(["--fallback", kwargs["fallback"]])
        if "max_turns" in kwargs:
            cmd.extend(["--max-turns", str(kwargs["max_turns"])])
        if kwargs.get("worktree"):
            cmd.append("--worktree")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                # Extract task ID from output
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if "Started task:" in line:
                        return line.split(":")[-1].strip()
        except subprocess.TimeoutExpired:
            pass

        return None

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get structured status for a specific task.

        Args:
            task_id: Task ID

        Returns:
            Dict with task status and structured result, None if failed
        """
        try:
            result = subprocess.run(
                [self.rapper_path, "--status", task_id],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                # Look for the HERMES_INTEGRATION_JSON line
                for line in result.stdout.split('\n'):
                    if line.startswith('# HERMES_INTEGRATION_JSON:'):
                        json_str = line.split(':', 1)[1].strip()
                        return json.loads(json_str)

                # Fallback: parse text output
                lines = result.stdout.strip().split('\n')
                status_info = {}
                for line in lines:
                    if line.startswith("Status:"):
                        status_info["status"] = line.split(":", 1)[1].strip()
                    elif line.startswith("ID:"):
                        status_info["task_id"] = line.split(":", 1)[1].strip()

                return status_info
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            pass

        return None

    def wait_for_completion(self, task_id: str, timeout: int = 3600, check_interval: int = 30) -> Optional[Dict[str, Any]]:
        """Wait for a task to complete and return its structured result.

        Args:
            task_id: Task ID to monitor
            timeout: Maximum wait time in seconds
            check_interval: How often to check status

        Returns:
            Task status dict when completed, None on timeout
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            status = self.get_task_status(task_id)
            if status and status.get("status") in ["completed", "failed", "cancelled"]:
                return status
            time.sleep(check_interval)
        return None


# Convenience functions for direct use
def get_available_task_slots() -> int:
    """Get number of available task slots."""
    manager = RapperTaskManager()
    counts = manager.get_task_counts()
    return counts.get("concurrency", {}).get("available_slots", 0)


def can_start_rapper_task() -> bool:
    """Check if a new Rapper task can be started."""
    return RapperTaskManager().can_start_task()


def start_rapper_task_with_concurrency_check(name: str, prompt: str, **kwargs) -> Optional[str]:
    """Start a Rapper task with automatic concurrency control.

    Returns task ID if successful, None if at capacity or failed.
    """
    return RapperTaskManager().start_task(name, prompt, **kwargs)


if __name__ == "__main__":
    # CLI interface for testing
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "test":
        manager = RapperTaskManager()
        print("Task counts:", json.dumps(manager.get_task_counts(), indent=2))
        print("Can start task:", manager.can_start_task())
        print("Available slots:", get_available_task_slots())