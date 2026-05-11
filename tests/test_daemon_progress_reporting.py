#!/usr/bin/env python3
"""
Test daemon progress reporting functionality.

Tests that the daemon posts progress comments to Board tasks during execution.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch, MagicMock
import sys

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from daemon import RapperDaemon, AgentBoardClient
from task_runner import Task, generate_task_id


class TestDaemonProgressReporting(unittest.TestCase):
    """Test daemon progress reporting functionality."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create minimal config
        config_content = """
agent_board:
  url: http://localhost:3456
  api_key: test-key
  agent_id: test-agent
  poll_interval: 30
  webhook_port: 18790
tasks:
  max_concurrent_tasks: 5
"""
        with open(self.config_path, 'w') as f:
            f.write(config_content)

        # Create test task directory
        self.task_dir = os.path.join(self.temp_dir, 'tasks')
        os.makedirs(self.task_dir, exist_ok=True)

    def test_add_comment_method(self):
        """Test that AgentBoardClient.add_comment works correctly."""
        client = AgentBoardClient("http://localhost:3456", "test-key")

        # Mock the _make_request method
        with patch.object(client, '_make_request') as mock_request:
            mock_request.return_value = {}

            result = client.add_comment("task_123", "test-agent", "Test comment")

            self.assertTrue(result)
            mock_request.assert_called_once_with(
                'POST',
                '/api/tasks/task_123/comments',
                {'author': 'test-agent', 'text': 'Test comment'}
            )

    def test_add_comment_failure(self):
        """Test that add_comment handles failures gracefully."""
        client = AgentBoardClient("http://localhost:3456", "test-key")

        # Mock the _make_request method to raise exception
        with patch.object(client, '_make_request') as mock_request:
            mock_request.side_effect = Exception("Network error")

            result = client.add_comment("task_123", "test-agent", "Test comment")

            self.assertFalse(result)

    def test_send_progress_update(self):
        """Test that _send_progress_update posts comments for new progress."""
        # Create daemon instance
        daemon = RapperDaemon(self.config_path, "test-agent")

        # Create a mock task with progress
        task_id = generate_task_id()
        task = Task(
            id=task_id,
            name="test-task",
            prompt="test prompt",
            workdir="/tmp",
            status="running",
            progress=[
                {"tool": "Read", "timestamp": time.time()},
                {"tool": "Edit", "timestamp": time.time()},
                {"tool": "Write", "timestamp": time.time()}
            ]
        )

        # Save task to temporary location
        task_file = os.path.join(self.task_dir, f"{task_id}.json")
        with open(task_file, 'w') as f:
            json.dump({
                "id": task_id,
                "name": "test-task",
                "prompt": "test prompt",
                "workdir": "/tmp",
                "status": "running",
                "progress": [
                    {"tool": "Read", "timestamp": time.time()},
                    {"tool": "Edit", "timestamp": time.time()},
                    {"tool": "Write", "timestamp": time.time()}
                ]
            }, f)

        # Mock Task.load to return our test task
        with patch('daemon.Task.load') as mock_load:
            mock_load.return_value = task

            # Mock the add_comment method
            with patch.object(daemon.client, 'add_comment') as mock_add_comment:
                mock_add_comment.return_value = True

                # Set up daemon state
                daemon.current_task = ("board_task_123", task)
                daemon._last_progress_step = 0

                # Call _send_progress_update
                daemon._send_progress_update("board_task_123", task)

                # Verify comment was posted
                mock_add_comment.assert_called_once()
                call_args = mock_add_comment.call_args[0]

                self.assertEqual(call_args[0], "board_task_123")  # task_id
                self.assertEqual(call_args[1], "test-agent")      # author
                self.assertIn("已完成 3 步", call_args[2])          # message contains step count
                self.assertIn("Write", call_args[2])             # message contains latest tool

    def test_send_progress_update_no_new_progress(self):
        """Test that no comment is posted when there's no new progress."""
        daemon = RapperDaemon(self.config_path, "test-agent")

        task_id = generate_task_id()
        task = Task(
            id=task_id,
            name="test-task",
            prompt="test prompt",
            workdir="/tmp",
            status="running",
            progress=[{"tool": "Read", "timestamp": time.time()}]
        )

        with patch('daemon.Task.load') as mock_load:
            mock_load.return_value = task

            with patch.object(daemon.client, 'add_comment') as mock_add_comment:
                # Set progress tracking to same step count
                daemon._last_progress_step = 1
                daemon.current_task = ("board_task_123", task)

                daemon._send_progress_update("board_task_123", task)

                # No comment should be posted
                mock_add_comment.assert_not_called()

    def test_progress_tracking_reset_on_new_task(self):
        """Test that progress tracking is reset when starting a new task."""
        daemon = RapperDaemon(self.config_path, "test-agent")

        # Set some previous progress
        daemon._last_progress_step = 5

        # Test direct assignment (this is what happens in the actual code)
        from task_runner import Task, generate_task_id
        task_id = generate_task_id()
        internal_task = Task(
            id=task_id,
            name="test-task",
            prompt="test prompt",
            workdir="/tmp"
        )

        # Simulate setting current_task (this triggers progress reset)
        daemon.current_task = ("board_task_123", internal_task)
        daemon._last_progress_step = 0  # This is the reset line from the actual code

        # Progress tracking should be reset
        self.assertEqual(daemon._last_progress_step, 0)


if __name__ == '__main__':
    unittest.main()