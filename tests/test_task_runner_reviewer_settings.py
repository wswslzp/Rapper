#!/usr/bin/env python3
"""
pytest tests for TaskRunner reviewer settings_path injection into Claude CLI.

Tests for AC-12 from requirements.md v1.1:
- When config contains claude.settings_path, Claude cmd should include --settings <path>
- When settings_path is not set, don't change existing cmd
- Test coverage for reviewer system prompt / append prompt injection paths

Based on:
- requirements.md v1.1 AC-12
- design.md v2.1 §3.2/3.3/6.2
- TEST-08 from design.md v2.1

This follows TDD approach - tests are written first (RED), then implementation (GREEN).
"""

import os
import sys
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass
from typing import Optional, Dict, Any

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from task_runner import TaskRunner


@dataclass
class MockConfig:
    """Mock config object for testing."""
    agent_board: Dict[str, Any]
    claude: Dict[str, Any]
    tasks: Dict[str, Any]
    reviewer: Optional[Dict[str, Any]] = None


class TestTaskRunnerReviewerSettings:
    """Tests for TaskRunner reviewer settings injection."""

    def setup_method(self):
        """Setup for each test method."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.test_settings_path = str(self.temp_dir / "settings-reviewer-1.json")
        self.test_system_prompt_path = str(self.temp_dir / "reviewer-system.md")

        # Create test files
        with open(self.test_settings_path, 'w') as f:
            f.write('{"permissions": {"deny": ["Write", "Edit"]}}')

        with open(self.test_system_prompt_path, 'w') as f:
            f.write("You are a reviewer. Do not modify code.")

    def teardown_method(self):
        """Cleanup after each test method."""
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_settings_path_injection_when_present(self):
        """Test that --settings is added when claude.settings_path exists in config."""
        # Arrange
        config = MockConfig(
            agent_board={
                "role": "reviewer",
                "agent_id": "reviewer-1"
            },
            claude={
                "model": "claude-sonnet-4-20250514",
                "settings_path": self.test_settings_path,
                "max_turns": 120
            },
            tasks={
                "max_concurrent_tasks": 1
            }
        )

        task_runner = TaskRunner(config=config)

        # Mock subprocess.Popen to capture the command
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345

            # Act - start a task
            task_runner.start_task(
                name="test-task",
                prompt="Test prompt",
                workdir="/tmp"
            )

            # Assert - should have been called (version check + actual command)
            assert mock_popen.call_count >= 1

            # Get the last call (the actual Claude command, not version check)
            args, kwargs = mock_popen.call_args
            cmd = args[0]  # First positional argument is the command list

            # Verify this is the actual Claude command, not version check
            assert "-p" in cmd, f"Expected Claude prompt mode, got: {cmd}"

            # Verify --settings is included
            assert "--settings" in cmd
            settings_index = cmd.index("--settings")
            assert cmd[settings_index + 1] == self.test_settings_path

            # Verify other expected flags are still present
            assert "--model" in cmd
            assert "--output-format" in cmd
            assert "stream-json" in cmd

    def test_no_settings_injection_when_absent(self):
        """Test that --settings is NOT added when claude.settings_path is not in config."""
        # Arrange
        config = MockConfig(
            agent_board={
                "role": "rapper",
                "agent_id": "rapper-1"
            },
            claude={
                "model": "claude-sonnet-4-20250514",
                "max_turns": 200
                # No settings_path
            },
            tasks={
                "max_concurrent_tasks": 5
            }
        )

        task_runner = TaskRunner(config=config)

        # Mock subprocess.Popen to capture the command
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345

            # Act - start a task
            task_runner.start_task(
                name="test-task",
                prompt="Test prompt",
                workdir="/tmp"
            )

            # Assert - should have been called (version check + actual command)
            assert mock_popen.call_count >= 1

            # Get the last call (the actual Claude command, not version check)
            args, kwargs = mock_popen.call_args
            cmd = args[0]

            # Verify this is the actual Claude command, not version check
            assert "-p" in cmd, f"Expected Claude prompt mode, got: {cmd}"

            # Verify --settings is NOT included
            assert "--settings" not in cmd

            # Verify other expected flags are still present
            assert "--model" in cmd
            assert "--output-format" in cmd

    def test_settings_path_none_or_empty(self):
        """Test that --settings is NOT added when claude.settings_path is None or empty."""
        # Test with None
        config_none = MockConfig(
            agent_board={"role": "reviewer"},
            claude={"model": "claude-sonnet-4", "settings_path": None},
            tasks={}
        )

        task_runner = TaskRunner(config=config_none)

        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345
            task_runner.start_task("test", "prompt", "/tmp")

            args, kwargs = mock_popen.call_args
            cmd = args[0]
            assert "--settings" not in cmd

        # Test with empty string
        config_empty = MockConfig(
            agent_board={"role": "reviewer"},
            claude={"model": "claude-sonnet-4", "settings_path": ""},
            tasks={}
        )

        task_runner = TaskRunner(config=config_empty)

        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345
            task_runner.start_task("test", "prompt", "/tmp")

            args, kwargs = mock_popen.call_args
            cmd = args[0]
            assert "--settings" not in cmd

    def test_system_prompt_injection_when_present(self):
        """Test that system prompt is injected when claude.append_system_prompt_path exists."""
        # Arrange
        config = MockConfig(
            agent_board={
                "role": "reviewer",
                "agent_id": "reviewer-1"
            },
            claude={
                "model": "claude-sonnet-4-20250514",
                "settings_path": self.test_settings_path,
                "append_system_prompt_path": self.test_system_prompt_path,
                "max_turns": 120
            },
            tasks={}
        )

        task_runner = TaskRunner(config=config)

        # Mock subprocess.Popen to capture the command
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345

            # Act
            task_runner.start_task(
                name="test-task",
                prompt="Review this implementation",
                workdir="/tmp"
            )

            # Assert - should have been called (version check + actual command)
            assert mock_popen.call_count >= 1

            # Get the last call (the actual Claude command, not version check)
            args, kwargs = mock_popen.call_args
            cmd = args[0]

            # Verify this is the actual Claude command, not version check
            assert "-p" in cmd, f"Expected Claude prompt mode, got: {cmd}"

            # The system prompt should be prepended to the task prompt
            # Find the prompt in the command (after "--")
            dash_dash_index = cmd.index("--")
            actual_prompt = cmd[dash_dash_index + 1]

            # Should include both system prompt and original prompt
            assert "You are a reviewer. Do not modify code." in actual_prompt
            assert "Review this implementation" in actual_prompt

    def test_no_system_prompt_injection_when_absent(self):
        """Test that no system prompt injection occurs when path is not in config."""
        # Arrange
        config = MockConfig(
            agent_board={"role": "rapper"},
            claude={
                "model": "claude-sonnet-4-20250514",
                "max_turns": 200
                # No append_system_prompt_path
            },
            tasks={}
        )

        task_runner = TaskRunner(config=config)

        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345

            # Act
            original_prompt = "Implement feature X"
            task_runner.start_task(
                name="test-task",
                prompt=original_prompt,
                workdir="/tmp"
            )

            # Assert - should have been called (version check + actual command)
            assert mock_popen.call_count >= 1

            # Get the last call (the actual Claude command, not version check)
            args, kwargs = mock_popen.call_args
            cmd = args[0]

            # Verify this is the actual Claude command, not version check
            assert "-p" in cmd, f"Expected Claude prompt mode, got: {cmd}"

            # Find the prompt in the command
            dash_dash_index = cmd.index("--")
            actual_prompt = cmd[dash_dash_index + 1]

            # Should contain original prompt but no system prompt injection
            # Note: structured result instructions are always added, so we check for original content
            assert original_prompt in actual_prompt
            assert "You are a reviewer. Do not modify code." not in actual_prompt

    def test_config_object_passed_to_constructor(self):
        """Test that TaskRunner can accept a config object in constructor."""
        # This tests the interface change needed for config injection
        config = MockConfig(
            agent_board={"role": "reviewer"},
            claude={"model": "test-model"},
            tasks={}
        )

        # Should not raise an exception
        task_runner = TaskRunner(config=config)

        # Should store config internally
        assert hasattr(task_runner, 'config')
        assert task_runner.config == config

    def test_backward_compatibility_without_config(self):
        """Test that TaskRunner still works without config object (backward compatibility)."""
        # Should not raise an exception when called the old way
        task_runner = TaskRunner(
            claude_path="claude",
            default_model="claude-sonnet-4-20250514"
        )

        # Should set config to None when not provided
        assert getattr(task_runner, 'config', None) is None

    def test_reviewer_prompt_protocol_construction(self):
        """Test that reviewer gets enhanced prompt following Reviewer Prompt Protocol."""
        # Arrange
        config = MockConfig(
            agent_board={
                "role": "reviewer",
                "agent_id": "reviewer-1"
            },
            claude={
                "model": "claude-sonnet-4-20250514",
                "append_system_prompt_path": self.test_system_prompt_path
            },
            tasks={},
            reviewer={
                "verdict_sentinel_start": "<<<REVIEW_VERDICT_JSON>>>",
                "verdict_sentinel_end": "<<<END_REVIEW_VERDICT_JSON>>>"
            }
        )

        task_runner = TaskRunner(config=config)

        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345

            # Act
            task_runner.start_task(
                name="review-task",
                prompt="Review the auth implementation",
                workdir="/app/project",
                board_task_id="task_123"
            )

            # Assert
            args, kwargs = mock_popen.call_args
            cmd = args[0]

            dash_dash_index = cmd.index("--")
            actual_prompt = cmd[dash_dash_index + 1]

            # Should contain reviewer protocol elements
            assert "You are Agent Board Reviewer reviewer-1" in actual_prompt
            assert "MUST NOT modify source code" in actual_prompt
            assert "board_task_id: task_123" in actual_prompt
            assert "workdir: /app/project" in actual_prompt
            assert "<<<REVIEW_VERDICT_JSON>>>" in actual_prompt
            assert "<<<END_REVIEW_VERDICT_JSON>>>" in actual_prompt

    def test_settings_file_nonexistent_error(self):
        """Test behavior when settings_path points to nonexistent file."""
        # Arrange
        nonexistent_path = "/nonexistent/settings.json"
        config = MockConfig(
            agent_board={"role": "reviewer"},
            claude={
                "model": "claude-sonnet-4",
                "settings_path": nonexistent_path
            },
            tasks={}
        )

        task_runner = TaskRunner(config=config)

        # Act & Assert
        # Should either warn or raise appropriate error - exact behavior TBD in implementation
        # For now, test that it doesn't crash silently
        with patch('subprocess.Popen') as mock_popen:
            mock_popen.return_value.pid = 12345

            # Should not raise exception - graceful degradation
            task_runner.start_task("test", "prompt", "/tmp")

            # Command should still be built (may warn but continue)
            assert mock_popen.call_count >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])