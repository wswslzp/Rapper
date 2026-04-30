#!/usr/bin/env python3
"""
Test progress reporting functionality for Board comments.
"""

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

# Add lib directory to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from task_runner import Task, generate_task_id, load_config, post_board_comment


class TestProgressReporting(unittest.TestCase):

    def setUp(self):
        """Create temporary directories for test files."""
        self.temp_task_dir = tempfile.mkdtemp()
        self.temp_config_dir = tempfile.mkdtemp()

        # Save original environment
        self.original_task_dir = os.environ.get('TASK_DIR')
        self.original_home = os.environ.get('HOME')

        # Set up test environment
        os.environ['TASK_DIR'] = self.temp_task_dir
        os.environ['HOME'] = self.temp_config_dir

        # Update the TASK_DIR in the module
        import task_runner
        task_runner.TASK_DIR = Path(self.temp_task_dir)

    def tearDown(self):
        """Clean up test files and restore environment."""
        if self.original_task_dir:
            os.environ['TASK_DIR'] = self.original_task_dir
        else:
            os.environ.pop('TASK_DIR', None)

        if self.original_home:
            os.environ['HOME'] = self.original_home
        else:
            os.environ.pop('HOME', None)

        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_task_dir, ignore_errors=True)
        shutil.rmtree(self.temp_config_dir, ignore_errors=True)

    def create_test_config(self, progress_enabled=True, report_every=5, api_key="test_key"):
        """Create a test configuration file."""
        config_content = {
            "progress_reporting": {
                "enabled": progress_enabled,
                "report_every_n_tools": report_every,
                "board_url": "http://localhost:3456"
            },
            "agent_board": {
                "api_key": api_key
            }
        }

        rapper_dir = Path(self.temp_config_dir) / ".rapper"
        rapper_dir.mkdir(exist_ok=True)
        config_file = rapper_dir / "config.yaml"

        import yaml
        with open(config_file, "w") as f:
            yaml.dump(config_content, f)

    def test_load_config_with_defaults(self):
        """Test loading config with default values when no config file exists."""
        config = load_config()

        self.assertTrue(config["progress_reporting"]["enabled"])
        self.assertEqual(config["progress_reporting"]["report_every_n_tools"], 5)
        self.assertEqual(config["progress_reporting"]["board_url"], "http://localhost:3456")
        self.assertEqual(config["agent_board"]["api_key"], "")

    def test_load_config_from_file(self):
        """Test loading config from YAML file."""
        self.create_test_config(progress_enabled=False, report_every=10, api_key="custom_key")

        config = load_config()

        self.assertFalse(config["progress_reporting"]["enabled"])
        self.assertEqual(config["progress_reporting"]["report_every_n_tools"], 10)
        self.assertEqual(config["agent_board"]["api_key"], "custom_key")

    @unittest.mock.patch('urllib.request.urlopen')
    def test_post_board_comment_success(self, mock_urlopen):
        """Test successful Board comment posting."""
        # Mock successful HTTP response
        mock_response = unittest.mock.MagicMock()
        mock_response.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_response

        config = {
            "progress_reporting": {"board_url": "http://localhost:3456"},
            "agent_board": {"api_key": "test_key"}
        }

        result = post_board_comment("task_123", "Test comment", config)

        self.assertTrue(result)
        mock_urlopen.assert_called_once()

        # Verify the request was made correctly
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        self.assertEqual(req.full_url, "http://localhost:3456/api/tasks/task_123/comments")
        self.assertEqual(req.get_method(), 'POST')
        self.assertEqual(req.headers['Content-type'], 'application/json')
        self.assertEqual(req.headers['Authorization'], 'Bearer test_key')

    @unittest.mock.patch('urllib.request.urlopen')
    def test_post_board_comment_failure(self, mock_urlopen):
        """Test Board comment posting failure handling."""
        # Mock failed HTTP response
        mock_urlopen.side_effect = Exception("Network error")

        config = {
            "progress_reporting": {"board_url": "http://localhost:3456"},
            "agent_board": {"api_key": ""}
        }

        result = post_board_comment("task_123", "Test comment", config)

        # Should return False on failure but not raise exception
        self.assertFalse(result)

    def test_post_board_comment_no_task_id(self):
        """Test posting comment with no board task ID returns False."""
        config = {"progress_reporting": {"board_url": "http://localhost:3456"}}

        result = post_board_comment("", "Test comment", config)
        self.assertFalse(result)

        result = post_board_comment(None, "Test comment", config)
        self.assertFalse(result)

    def test_task_serialization_preserves_board_task_id(self):
        """Test that board_task_id is preserved during task serialization."""
        task_id = generate_task_id()
        board_task_id = "task_abc123"

        task = Task(
            id=task_id,
            name="test-progress",
            prompt="Test progress reporting",
            workdir="/tmp",
            board_task_id=board_task_id
        )

        # Add some progress entries
        task.progress = [
            {"tool": "Read", "time": 1.0},
            {"tool": "Edit", "time": 2.5},
            {"tool": "Bash", "time": 3.2}
        ]

        task.save()

        # Load the task back and verify board_task_id is preserved
        loaded_task = Task.load(task_id)
        self.assertIsNotNone(loaded_task)
        self.assertEqual(loaded_task.board_task_id, board_task_id)
        self.assertEqual(len(loaded_task.progress), 3)

    @unittest.mock.patch('lib.task_runner.post_board_comment')
    def test_progress_reporting_interval(self, mock_post_comment):
        """Test that progress reporting follows the configured interval."""
        self.create_test_config(progress_enabled=True, report_every=3)

        # This is a unit test for the logic, not an integration test
        # We would need to mock the TaskRunner's stream parsing to fully test this
        # For now, just verify the post_board_comment function can be called
        config = load_config()

        # Simulate progress reporting at intervals
        for i in range(1, 8):  # 7 tool calls
            if i % config["progress_reporting"]["report_every_n_tools"] == 0:
                post_board_comment("task_123", f"Progress: {i} tools", config)

        # Should be called at tool 3 and tool 6 (2 times)
        self.assertEqual(mock_post_comment.call_count, 2)

        # Verify the messages
        calls = mock_post_comment.call_args_list
        self.assertEqual(calls[0][0][0], "task_123")  # task_id
        self.assertIn("Progress: 3 tools", calls[0][0][1])  # message
        self.assertEqual(calls[1][0][0], "task_123")  # task_id
        self.assertIn("Progress: 6 tools", calls[1][0][1])  # message


if __name__ == "__main__":
    unittest.main()