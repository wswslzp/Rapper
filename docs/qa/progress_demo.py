#!/usr/bin/env python3
"""
Demonstration script for daemon progress reporting functionality.

This script simulates a task with progress updates to test that
the daemon correctly posts progress comments to Board tasks.
"""

import json
import os
import sys
import time
from pathlib import Path

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'lib'))

from task_runner import Task, generate_task_id


def simulate_task_with_progress():
    """Create a task file with simulated progress updates."""

    # Create task with initial state
    task_id = generate_task_id()
    task = Task(
        id=task_id,
        name="Demo Task with Progress",
        prompt="Simulate a task with multiple tool calls",
        workdir="/app/rapper",
        status="running",
        board_task_id="demo_board_task_123"
    )

    print(f"Created demo task: {task_id}")
    print(f"Board task ID: {task.board_task_id}")
    print(f"Task file: {task.task_file}")

    # Simulate progress updates with different tools
    tools = [
        ("Read", "Read project files"),
        ("Glob", "Search for relevant files"),
        ("Edit", "Modify configuration file"),
        ("Write", "Create new documentation"),
        ("mcp__bash-runner__run_bash", "Run test command"),
        ("Read", "Verify changes"),
        ("Edit", "Fix issues found"),
        ("Write", "Update final output")
    ]

    for i, (tool, description) in enumerate(tools, 1):
        # Add progress entry
        progress_entry = {
            "tool": tool,
            "description": description,
            "timestamp": time.time(),
            "step": i
        }

        task.progress.append(progress_entry)
        task.save()

        print(f"Step {i}: Added {tool} to progress")

        # Simulate some work time
        time.sleep(1)

    # Mark as completed
    task.status = "completed"
    task.result = "Task completed successfully with 8 tool calls"
    task.end_time = time.time()
    task.save()

    print(f"\nTask completed with {len(task.progress)} progress entries")
    print(f"Final task file saved to: {task.task_file}")

    # Show what the progress comments would look like
    print("\nProgress comments that would be posted to Board:")
    print("-" * 50)

    for i in range(3, len(task.progress) + 1, 3):  # Every 3 steps
        latest_tool = task.progress[i-1]["tool"]
        comment = f"执行中：已完成 {i} 步 | 最近：{latest_tool}"
        print(f"After step {i}: {comment}")

    return task


def show_task_progress(task_id):
    """Display the progress of an existing task."""
    task = Task.load(task_id)
    if not task:
        print(f"Task {task_id} not found")
        return

    print(f"Task: {task.name}")
    print(f"Status: {task.status}")
    print(f"Board Task ID: {task.board_task_id}")
    print(f"Progress entries: {len(task.progress)}")
    print()

    for i, entry in enumerate(task.progress, 1):
        tool = entry.get("tool", "Unknown")
        desc = entry.get("description", "")
        print(f"  {i:2d}. {tool:<25} {desc}")


def main():
    """Main demo function."""
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        if len(sys.argv) < 3:
            print("Usage: python progress_demo.py show <task_id>")
            return
        show_task_progress(sys.argv[2])
    else:
        print("=== Daemon Progress Reporting Demo ===")
        print()
        print("This script simulates a task with progress updates.")
        print("In real daemon mode, these would trigger progress comments to the Board.")
        print()

        task = simulate_task_with_progress()

        print(f"\nTo view task details: python {__file__} show {task.id}")


if __name__ == "__main__":
    main()