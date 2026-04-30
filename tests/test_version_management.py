#!/usr/bin/env python3
"""
Tests for version management functionality in Rapper.

These tests verify:
1. Claude version capture during task creation
2. Version information storage in task JSON
3. CLI commands for version management

Note: These are static tests that verify code structure and logic,
not actual Claude Code version detection (which requires Claude to be installed).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from task_runner import Task, TaskRunner


class TestVersionManagement(unittest.TestCase):
    """Test version management functionality."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.task_dir = self.temp_dir / "tasks"
        self.task_dir.mkdir(parents=True)

        # Patch TASK_DIR to use temp directory
        self.task_dir_patch = patch('task_runner.TASK_DIR', self.task_dir)
        self.task_dir_patch.start()

    def tearDown(self):
        """Clean up test environment."""
        self.task_dir_patch.stop()
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_task_dataclass_has_claude_version_field(self):
        """Test that Task dataclass has claude_version field."""
        task = Task(
            id="test-001",
            name="Test Task",
            prompt="test prompt",
            workdir="/tmp",
            claude_version="2.5.10"
        )

        self.assertEqual(task.claude_version, "2.5.10")

    def test_task_save_includes_claude_version(self):
        """Test that task.save() includes claude_version in JSON."""
        task = Task(
            id="test-002",
            name="Test Task",
            prompt="test prompt",
            workdir="/tmp",
            claude_version="2.5.10"
        )
        task.save()

        # Read the saved JSON
        task_file = self.task_dir / "test-002.json"
        with open(task_file) as f:
            data = json.load(f)

        self.assertEqual(data["claude_version"], "2.5.10")

    def test_task_load_restores_claude_version(self):
        """Test that Task.load() restores claude_version from JSON."""
        # Create a task JSON with claude_version
        task_data = {
            "id": "test-003",
            "name": "Test Task",
            "prompt": "test prompt",
            "workdir": "/tmp",
            "status": "completed",
            "claude_version": "2.5.10",
            "progress": []
        }

        task_file = self.task_dir / "test-003.json"
        with open(task_file, "w") as f:
            json.dump(task_data, f)

        # Load the task
        task = Task.load("test-003")

        self.assertIsNotNone(task)
        self.assertEqual(task.claude_version, "2.5.10")

    def test_task_load_handles_missing_claude_version(self):
        """Test that Task.load() handles missing claude_version gracefully."""
        # Create a task JSON without claude_version (backward compatibility)
        task_data = {
            "id": "test-004",
            "name": "Test Task",
            "prompt": "test prompt",
            "workdir": "/tmp",
            "status": "completed",
            "progress": []
        }

        task_file = self.task_dir / "test-004.json"
        with open(task_file, "w") as f:
            json.dump(task_data, f)

        # Load the task
        task = Task.load("test-004")

        self.assertIsNotNone(task)
        self.assertIsNone(task.claude_version)

    @patch('subprocess.run')
    def test_task_runner_captures_claude_version(self):
        """Test that TaskRunner captures Claude version during task creation."""
        # Mock subprocess.run to return a version
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Claude Code 2.5.10\nCopyright 2024 Anthropic\n"

        mock_run = Mock(return_value=mock_result)

        with patch('task_runner.subprocess.run', mock_run):
            runner = TaskRunner(claude_path="claude", rapper_dir="/test")

            # Mock the actual claude execution to avoid running real commands
            with patch.object(runner, '_running_tasks', {}):
                with patch('subprocess.Popen'):
                    with patch('builtins.open', create=True):
                        task = runner.start_task(
                            name="test-task",
                            prompt="test prompt"
                        )

        self.assertEqual(task.claude_version, "Claude Code 2.5.10")

        # Verify subprocess was called with --version
        mock_run.assert_called_with(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )

    @patch('subprocess.run')
    def test_task_runner_handles_version_capture_failure(self):
        """Test that TaskRunner handles version capture failure gracefully."""
        # Mock subprocess.run to raise an exception
        mock_run = Mock(side_effect=Exception("Command failed"))

        with patch('task_runner.subprocess.run', mock_run):
            runner = TaskRunner(claude_path="claude", rapper_dir="/test")

            # Mock the actual claude execution
            with patch.object(runner, '_running_tasks', {}):
                with patch('subprocess.Popen'):
                    with patch('builtins.open', create=True):
                        task = runner.start_task(
                            name="test-task",
                            prompt="test prompt"
                        )

        # Should still create task but with None version
        self.assertIsNone(task.claude_version)


class TestVersionManagementCLI(unittest.TestCase):
    """Test version management CLI commands."""

    def test_rapper_script_has_version_functions(self):
        """Test that rapper script has the version management functions."""
        rapper_script = Path(__file__).parent.parent / "rapper"

        with open(rapper_script) as f:
            content = f.read()

        # Check that the functions exist
        self.assertIn("do_claude_version()", content)
        self.assertIn("do_check_update()", content)
        self.assertIn("do_update_claude()", content)

        # Check that they're called in the case statement
        self.assertIn("--claude-version)", content)
        self.assertIn("--check-update)", content)
        self.assertIn("--update-claude)", content)

        # Check help text includes them
        self.assertIn("VERSION MANAGEMENT:", content)

    def test_version_functions_call_claude_commands(self):
        """Test that version functions use appropriate claude commands."""
        rapper_script = Path(__file__).parent.parent / "rapper"

        with open(rapper_script) as f:
            content = f.read()

        # Check that functions use claude --version and claude update
        self.assertIn("claude --version", content)
        self.assertIn("claude update", content)


if __name__ == "__main__":
    # Run the tests
    unittest.main(verbosity=2)