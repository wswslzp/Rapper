#!/usr/bin/env python3
"""
Test board_task_id binding functionality.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

# Add lib directory to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from task_runner import Task, generate_task_id, get_task


class TestBoardTaskIdBinding(unittest.TestCase):

    def setUp(self):
        """Create a temporary directory for test task files."""
        self.temp_dir = tempfile.mkdtemp()
        self.original_task_dir = os.environ.get('TASK_DIR')
        os.environ['TASK_DIR'] = self.temp_dir

        # Update the TASK_DIR in the module
        import task_runner
        task_runner.TASK_DIR = Path(self.temp_dir)

    def tearDown(self):
        """Clean up test files."""
        if self.original_task_dir:
            os.environ['TASK_DIR'] = self.original_task_dir
        else:
            os.environ.pop('TASK_DIR', None)

        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_task_serialization_with_board_task_id(self):
        """Test that Task serialization includes board_task_id."""
        task_id = generate_task_id()
        board_task_id = "task_7f25a48f"

        task = Task(
            id=task_id,
            name="test-task",
            prompt="Test task",
            workdir="/tmp",
            board_task_id=board_task_id
        )

        # Save the task
        task.save()

        # Verify file was created
        task_file = Path(self.temp_dir) / f"{task_id}.json"
        self.assertTrue(task_file.exists())

        # Load raw JSON and verify board_task_id is present
        with open(task_file) as f:
            data = json.load(f)

        self.assertEqual(data["board_task_id"], board_task_id)
        self.assertEqual(data["id"], task_id)
        self.assertEqual(data["name"], "test-task")

    def test_task_deserialization_with_board_task_id(self):
        """Test that Task.load() correctly handles board_task_id."""
        task_id = generate_task_id()
        board_task_id = "task_abc123"

        # Create a task with board_task_id
        original_task = Task(
            id=task_id,
            name="test-load",
            prompt="Test loading",
            workdir="/tmp",
            board_task_id=board_task_id
        )
        original_task.save()

        # Load the task back
        loaded_task = Task.load(task_id)

        self.assertIsNotNone(loaded_task)
        self.assertEqual(loaded_task.id, task_id)
        self.assertEqual(loaded_task.board_task_id, board_task_id)
        self.assertEqual(loaded_task.name, "test-load")

    def test_task_deserialization_without_board_task_id(self):
        """Test backward compatibility - loading tasks without board_task_id."""
        task_id = generate_task_id()

        # Create task file manually without board_task_id field
        task_file = Path(self.temp_dir) / f"{task_id}.json"
        data = {
            "id": task_id,
            "name": "legacy-task",
            "prompt": "Legacy task without board_task_id",
            "workdir": "/tmp",
            "status": "completed",
            "updated_at": 1234567890
        }

        with open(task_file, "w") as f:
            json.dump(data, f)

        # Load the task
        loaded_task = Task.load(task_id)

        self.assertIsNotNone(loaded_task)
        self.assertEqual(loaded_task.id, task_id)
        self.assertIsNone(loaded_task.board_task_id)  # Should be None for backward compatibility
        self.assertEqual(loaded_task.name, "legacy-task")

    def test_get_task_by_board_id(self):
        """Test looking up tasks by board_task_id."""
        task_id_1 = generate_task_id()
        task_id_2 = generate_task_id()
        board_task_id = "task_xyz789"

        # Create two tasks, one with board_task_id
        task1 = Task(
            id=task_id_1,
            name="regular-task",
            prompt="Regular task",
            workdir="/tmp"
        )
        task1.save()

        task2 = Task(
            id=task_id_2,
            name="board-task",
            prompt="Board task",
            workdir="/tmp",
            board_task_id=board_task_id
        )
        task2.save()

        # Should find task2 by board_task_id
        found_task = get_task(board_task_id)

        self.assertIsNotNone(found_task)
        self.assertEqual(found_task.id, task_id_2)
        self.assertEqual(found_task.board_task_id, board_task_id)
        self.assertEqual(found_task.name, "board-task")

        # Should still find by regular task ID
        found_by_id = get_task(task_id_1)
        self.assertIsNotNone(found_by_id)
        self.assertEqual(found_by_id.id, task_id_1)

        # Should not find non-existent board task ID
        not_found = get_task("task_nonexistent")
        self.assertIsNone(not_found)

    def test_multiple_tasks_same_board_id_returns_first_match(self):
        """Test that multiple tasks with same board_task_id returns first match."""
        board_task_id = "task_duplicate"

        # This shouldn't happen in practice, but test robustness
        task1 = Task(
            id=generate_task_id(),
            name="first-task",
            prompt="First task",
            workdir="/tmp",
            board_task_id=board_task_id
        )
        task1.save()

        task2 = Task(
            id=generate_task_id(),
            name="second-task",
            prompt="Second task",
            workdir="/tmp",
            board_task_id=board_task_id
        )
        task2.save()

        # Should return one of them (first found)
        found_task = get_task(board_task_id)
        self.assertIsNotNone(found_task)
        self.assertEqual(found_task.board_task_id, board_task_id)
        self.assertIn(found_task.name, ["first-task", "second-task"])


if __name__ == "__main__":
    unittest.main()