#!/usr/bin/env python3
"""
Test daemon board_task_id integration.
"""

import os
import sys
import unittest

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from task_runner import Task, generate_task_id


class TestDaemonBoardTaskIdIntegration(unittest.TestCase):

    def test_task_creation_with_board_task_id(self):
        """Test creating a Task with board_task_id like daemon does."""
        task_id = "task_7f25a48f"  # Board task ID
        internal_id = generate_task_id()

        # Simulate what daemon.py does
        internal_task = Task(
            id=internal_id,
            name="board-task-title",
            prompt="Task description from board",
            workdir="/app/project",
            status="pending",
            board_task_id=task_id
        )

        # Verify the task has correct binding
        self.assertEqual(internal_task.id, internal_id)
        self.assertEqual(internal_task.board_task_id, task_id)
        self.assertEqual(internal_task.name, "board-task-title")
        self.assertEqual(internal_task.status, "pending")

    def test_task_without_board_task_id(self):
        """Test creating a Task without board_task_id (normal background task)."""
        internal_id = generate_task_id()

        # Simulate normal --background task
        normal_task = Task(
            id=internal_id,
            name="background-task",
            prompt="Regular background task",
            workdir="/app/project",
            status="pending"
            # No board_task_id
        )

        # Verify the task has no board binding
        self.assertEqual(normal_task.id, internal_id)
        self.assertIsNone(normal_task.board_task_id)
        self.assertEqual(normal_task.name, "background-task")


if __name__ == "__main__":
    unittest.main()