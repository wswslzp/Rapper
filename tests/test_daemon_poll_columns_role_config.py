#!/usr/bin/env python3
"""
Test daemon respects poll_columns config for role-based polling.

This implements TEST-01 from the Agent Board Reviewer design:
"daemon respects poll_columns - rapper polls todo/ready, reviewer polls review"

Verifies:
- Rapper default behavior: poll 'todo' + 'ready' columns
- Reviewer behavior: poll 'review' column only, not 'todo'/'ready'
- Backward compatibility: missing poll_columns defaults to ['todo', 'ready']

RED phase expectations:
These tests should FAIL initially because the current daemon.py implementation
does not respect the poll_columns config - it hardcodes polling 'todo' and 'ready'.

References:
- /app/agent-board-reviewer/requirements.md v1.1 AC-02
- /app/agent-board-reviewer/design.md v2.1 §3.1/3.2/6.1
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add lib directory to path for daemon import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

try:
    from daemon import RapperDaemon
except ImportError as e:
    print(f"Failed to import daemon: {e}")
    sys.exit(1)


class TestDaemonPollColumnsRoleConfig(unittest.TestCase):
    """Test daemon poll_columns configuration for role-based column polling."""

    @classmethod
    def setUpClass(cls):
        """Set up test class - verify imports work."""
        cls.maxDiff = None  # Show full diff on assertion failures

    def _create_base_config(self):
        """Create base configuration dict."""
        return {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test-key',
                'agent_id': 'test-agent',
                'poll_interval': 30,
                'webhook_port': 19999,
            },
            'tasks': {
                'max_concurrent_tasks': 5
            },
            'logging': {
                'level': 'warning'  # Reduce log noise during tests
            }
        }

    def _create_daemon_with_config(self, config_dict):
        """Create daemon instance with given config, mocked appropriately for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'test_config.yaml')

            # Mock config loading to return our test config directly
            with patch.object(RapperDaemon, '_load_config', return_value=config_dict):
                with patch('daemon.init_db'):  # Mock database initialization
                    daemon = RapperDaemon(config_path, agent_id='test-agent')

                    # Set up test-specific paths
                    daemon.picked_tasks_file = os.path.join(temp_dir, 'picked_tasks.json')

                    # Mock client methods to prevent actual API calls
                    daemon.client.get_tasks = MagicMock(return_value=[])
                    daemon.client.claim_task = MagicMock(return_value=True)
                    daemon.client.update_task_status = MagicMock(return_value=True)

                    # Mock task execution dependencies
                    daemon._count_running_tasks = MagicMock(return_value=0)
                    daemon._load_picked_tasks = MagicMock(return_value=set())
                    daemon._cleanup_completed_futures = MagicMock()

                    return daemon

    def test_rapper_default_polls_todo_and_ready(self):
        """TEST: Rapper with default config polls both 'todo' and 'ready' columns.

        Expected behavior: daemon should query both columns per default poll_columns.
        RED expectation: Will FAIL because current implementation hardcodes columns.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'rapper'
        config['agent_board']['poll_columns'] = ['todo', 'ready']

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should call get_tasks for both configured columns
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(
            daemon.client.get_tasks.call_count,
            2,
            "Should query exactly 2 columns (todo + ready)"
        )

    def test_reviewer_polls_only_review_column(self):
        """TEST: Reviewer with poll_columns=['review'] polls only review column.

        Expected behavior: daemon should query only 'review' column per config.
        RED expectation: Will FAIL because current implementation hardcodes todo/ready.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        config['agent_board']['poll_columns'] = ['review']

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should call get_tasks only for review column
        daemon.client.get_tasks.assert_called_once_with(None, 'review')

        # ASSERTION: Should NOT query todo or ready columns
        all_call_args = [call_obj[0] for call_obj in daemon.client.get_tasks.call_args_list]
        for call_args in all_call_args:
            if len(call_args) > 1:
                column = call_args[1]
                self.assertNotIn(
                    column,
                    ['todo', 'ready'],
                    f"Reviewer should not poll {column} column, only review"
                )

    def test_backward_compatibility_missing_poll_columns(self):
        """TEST: Config without poll_columns defaults to ['todo', 'ready'].

        Expected behavior: Should maintain backward compatibility with current behavior.
        RED expectation: May PASS since this matches current hardcoded behavior.
        """
        config = self._create_base_config()
        # Deliberately omit poll_columns key to test default

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should default to todo + ready columns for backward compatibility
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(
            daemon.client.get_tasks.call_count,
            2,
            "Should default to 2 columns (todo + ready) for backward compatibility"
        )

    def test_empty_poll_columns_fallback(self):
        """TEST: Empty poll_columns list falls back to default behavior.

        Expected behavior: Should fallback to ['todo', 'ready'] for safety.
        RED expectation: Will FAIL because current implementation doesn't check config.
        """
        config = self._create_base_config()
        config['agent_board']['poll_columns'] = []  # Empty list

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should fallback to default columns for safety
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(
            daemon.client.get_tasks.call_count,
            2,
            "Empty poll_columns should fallback to default behavior"
        )

    def test_single_custom_column_configuration(self):
        """TEST: Single column configuration works correctly.

        Expected behavior: Should query only the configured column.
        RED expectation: Will FAIL because current implementation ignores config.
        """
        config = self._create_base_config()
        config['agent_board']['poll_columns'] = ['blocked']  # Single custom column

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should query only the configured column
        daemon.client.get_tasks.assert_called_once_with(None, 'blocked')

    def test_multiple_custom_columns(self):
        """TEST: Multiple custom columns work correctly.

        Expected behavior: Should query all configured columns.
        RED expectation: Will FAIL because current implementation ignores config.
        """
        config = self._create_base_config()
        config['agent_board']['poll_columns'] = ['todo', 'doing', 'blocked']

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should query all 3 configured columns
        expected_calls = [
            call(None, 'todo'),
            call(None, 'doing'),
            call(None, 'blocked'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(
            daemon.client.get_tasks.call_count,
            3,
            "Should query all configured columns"
        )

    def test_config_loading_preserves_poll_columns(self):
        """TEST: Configuration loading properly stores poll_columns value.

        Expected behavior: Config should be accessible within daemon instance.
        RED expectation: May PASS if config loading works, even if not used.
        """
        config = self._create_base_config()
        config['agent_board']['poll_columns'] = ['review']
        config['agent_board']['role'] = 'reviewer'

        daemon = self._create_daemon_with_config(config)

        # ASSERTION: Config should be stored correctly
        self.assertEqual(
            daemon.config['agent_board']['poll_columns'],
            ['review'],
            "poll_columns config should be preserved"
        )
        self.assertEqual(
            daemon.config['agent_board']['role'],
            'reviewer',
            "role config should be preserved"
        )

    def test_integration_reviewer_only_picks_review_tasks(self):
        """INTEGRATION TEST: Reviewer polls review column and picks review tasks.

        This tests the complete flow: poll review -> find task -> claim task.
        RED expectation: Will FAIL because reviewer queries wrong columns.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        config['agent_board']['poll_columns'] = ['review']

        daemon = self._create_daemon_with_config(config)

        # Set up mock tasks: review column has a task, todo has different task
        review_task = {
            'id': 'task_review_123',
            'title': 'Code review task',
            'description': 'Review implementation for correctness',
            'column': 'review',
            'assignee': None
        }

        todo_task = {
            'id': 'task_todo_456',
            'title': 'Implementation task',
            'description': 'Implement feature X',
            'column': 'todo',
            'assignee': None
        }

        def mock_get_tasks_side_effect(assignee, column):
            """Mock function to return different tasks per column."""
            if column == 'review':
                return [review_task]
            elif column == 'todo':
                return [todo_task]
            elif column == 'doing':
                return []  # No tasks in progress
            else:
                return []

        daemon.client.get_tasks.side_effect = mock_get_tasks_side_effect

        # Mock task execution to complete successfully
        with patch.object(daemon, 'task_executor') as mock_executor:
            mock_future = MagicMock()
            mock_future.done.return_value = False
            mock_executor.submit.return_value = mock_future

            # Execute one poll cycle
            daemon._poll_and_execute_tasks()

        # ASSERTION: Should only query review column (per reviewer config)
        daemon.client.get_tasks.assert_called_with(None, 'review')

        # ASSERTION: Should claim the review task (not todo task)
        daemon.client.claim_task.assert_called_once_with('task_review_123', 'test-agent')

    def test_invalid_poll_columns_type_fallback(self):
        """TEST: Non-list poll_columns falls back to default safely.

        Expected behavior: Should handle invalid config gracefully.
        RED expectation: Will FAIL if validation logic doesn't exist yet.
        """
        config = self._create_base_config()
        config['agent_board']['poll_columns'] = "invalid_string"  # Not a list

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should fallback to default behavior for safety
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(
            daemon.client.get_tasks.call_count,
            2,
            "Invalid poll_columns type should fallback to default"
        )


if __name__ == '__main__':
    # Run tests with verbose output for debugging
    unittest.main(verbosity=2)