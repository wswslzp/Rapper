#!/usr/bin/env python3
"""
Demonstration of the task assignment gap issue.

Shows:
1. Current behavior: tasks without assignee in 'todo' are not picked up
2. Three proposed solutions to fix this
"""

import os
import sys
from unittest.mock import MagicMock

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient


def demonstrate_current_issue():
    """Demonstrate the current problematic behavior."""
    print("=== CURRENT BEHAVIOR (PROBLEMATIC) ===")

    client = AgentBoardClient("http://localhost:3456", "sk-test")
    client._make_request = MagicMock()

    # Simulate Board UI drag: task moved to todo but no assignee set
    tasks_after_frontend_drag = [
        {
            'id': 'task_123',
            'title': 'Fix critical auth bug',
            'description': 'User login broken in production',
            'column': 'todo',
            'assignee': None  # ← This is the problem!
        }
    ]

    # Simulate API behavior: returns tasks that match BOTH assignee AND column
    # Since assignee=None, query for assignee=rapper-1 returns empty
    client._make_request.return_value = []

    # Daemon tries to get tasks
    agent_id = 'rapper-1'
    tasks = client.get_tasks(agent_id, 'todo')

    print(f"Frontend moved task to 'todo' column (assignee=None)")
    print(f"Daemon queries: ?assignee={agent_id}&column=todo")
    print(f"API returns: {tasks}")  # Empty!
    print(f"Result: Task never gets picked up ❌")
    print()


def demonstrate_solution_1_frontend():
    """Solution 1: Frontend sets assignee when dragging to todo."""
    print("=== SOLUTION 1: FRONTEND SETS ASSIGNEE ===")

    client = AgentBoardClient("http://localhost:3456", "sk-test")
    client._make_request = MagicMock()

    # Frontend is improved: when dragging to todo, shows assignee picker
    tasks_with_assignee = [
        {
            'id': 'task_123',
            'title': 'Fix critical auth bug',
            'column': 'todo',
            'assignee': 'rapper-1'  # ← Frontend now sets this!
        }
    ]

    client._make_request.return_value = tasks_with_assignee

    agent_id = 'rapper-1'
    tasks = client.get_tasks(agent_id, 'todo')

    print(f"Frontend sets assignee when moving to todo")
    print(f"Daemon queries: ?assignee={agent_id}&column=todo")
    print(f"API returns: {len(tasks)} task(s)")
    print(f"Result: Task gets picked up ✅")
    print()


def demonstrate_solution_2_daemon():
    """Solution 2: Daemon queries column=todo only, then filters."""
    print("=== SOLUTION 2: DAEMON RELAXES QUERY ===")

    client = AgentBoardClient("http://localhost:3456", "sk-test")
    client._make_request = MagicMock()

    # Multiple tasks in todo column with different assignee states
    all_todo_tasks = [
        {'id': 'task_123', 'assignee': None, 'title': 'Unassigned task'},
        {'id': 'task_456', 'assignee': 'rapper-1', 'title': 'My assigned task'},
        {'id': 'task_789', 'assignee': 'rapper-2', 'title': 'Other agent task'}
    ]

    client._make_request.return_value = all_todo_tasks

    # Modified daemon query: only by column
    def get_available_tasks(agent_id, column='todo'):
        """Modified get_tasks that queries by column only, then filters."""
        response = client._make_request('GET', f'/api/tasks?column={column}')
        all_tasks = response if isinstance(response, list) else response.get('tasks', [])

        # Filter for tasks this agent can claim:
        # - Unassigned tasks (can claim)
        # - Tasks already assigned to this agent (resume)
        available = []
        for task in all_tasks:
            assignee = task.get('assignee')
            if assignee is None or assignee == agent_id:
                available.append(task)

        return available

    agent_id = 'rapper-1'
    tasks = get_available_tasks(agent_id, 'todo')

    print(f"Daemon queries: ?column=todo (no assignee requirement)")
    print(f"API returns: {len(all_todo_tasks)} total tasks in todo")
    print(f"Daemon filters: {len(tasks)} available for {agent_id}")
    print(f"Available task IDs: {[t['id'] for t in tasks]}")
    print(f"Result: Unassigned tasks can now be picked up ✅")
    print()


def demonstrate_solution_3_hermes():
    """Solution 3: Hermes PM sets assignee during backlog→todo move."""
    print("=== SOLUTION 3: HERMES PM WORKAROUND ===")

    # Simulated Hermes PM workflow
    def hermes_move_task_to_todo(task_id, target_assignee=None):
        """Hermes PM function that moves task and sets assignee."""
        print(f"Hermes PM moving task {task_id}:")
        print(f"  1. PATCH /api/tasks/{task_id} {{ column: 'todo' }}")
        if target_assignee:
            print(f"  2. PATCH /api/tasks/{task_id} {{ assignee: '{target_assignee}' }}")
        else:
            print(f"  2. (No assignee set - issue persists)")

        return {
            'id': task_id,
            'column': 'todo',
            'assignee': target_assignee
        }

    # With assignee
    task_with_assignee = hermes_move_task_to_todo('task_123', 'rapper-1')
    print(f"Result: {task_with_assignee}")
    print(f"Daemon pickup: ✅ (assignee set)")
    print()

    # Without assignee (current problematic case)
    task_without_assignee = hermes_move_task_to_todo('task_456', None)
    print(f"Result: {task_without_assignee}")
    print(f"Daemon pickup: ❌ (assignee still None)")
    print()


def main():
    """Run all demonstrations."""
    print("TASK ASSIGNMENT GAP ISSUE DEMONSTRATION")
    print("=" * 50)
    print()

    demonstrate_current_issue()
    demonstrate_solution_1_frontend()
    demonstrate_solution_2_daemon()
    demonstrate_solution_3_hermes()

    print("SUMMARY:")
    print("--------")
    print("Current: Tasks dragged to todo without assignee never get picked up")
    print("Solution 1: Frontend assignee picker (UX change)")
    print("Solution 2: Daemon column-only query (most robust)")
    print("Solution 3: Hermes PM sets assignee (workaround)")
    print()
    print("Recommendation: Solution 2 (daemon-side fix) is most robust")
    print("because it doesn't depend on frontend behavior and handles")
    print("the case gracefully regardless of how tasks reach todo.")


if __name__ == '__main__':
    main()