#!/usr/bin/env python3
"""
Test daemon respects poll_columns config for role-based polling.

This implements TEST-01 from the Agent Board Reviewer design:
"daemon respects poll_columns - rapper polls todo/ready, reviewer polls review"

Verifies:
- T1: Rapper config with poll_columns=["todo", "ready"] polls both columns
- T2: Reviewer config with poll_columns=["review"] polls only review column
- T3: Backward compatibility: no poll_columns defaults to ["todo", "ready"]
- T4: Empty poll_columns falls back to default ['todo', 'ready']
- T5: Single column configuration works correctly
- T6: Multiple custom columns work correctly

Related:
- requirements.md v1.1 AC-02
- design.md v2.1 §3.1/3.2/6.1
- TEST-01 from requirements.md DAG

Expected behavior: These tests should FAIL initially (RED phase) because
the poll_columns functionality is not yet implemented in daemon.py.
The current implementation hardcodes polling 'todo' and 'ready' columns.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon


def _make_base_config():
    """Base config dict for RapperDaemon."""
    return {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test',
            'agent_id': 'test-agent',
            'poll_interval': 30,
            'webhook_port': 19999,
        },
        'tasks': {'max_concurrent_tasks': 5},
        'logging': {'level': 'warning'},
    }


def _make_rapper_config():
    """Rapper config with explicit poll_columns."""
    config = _make_base_config()
    config['agent_board'].update({
        'role': 'rapper',
        'poll_columns': ['todo', 'ready'],
    })
    return config


def _make_reviewer_config():
    """Reviewer config with poll_columns=["review"]."""
    config = _make_base_config()
    config['agent_board'].update({
        'role': 'reviewer',
        'poll_columns': ['review'],
    })
    return config


class TestDaemonPollColumns(unittest.TestCase):
    """Test cases for poll_columns configuration and role-based polling."""

    def _create_daemon_with_config(self, config_dict, temp_dir):
        """Helper to create daemon with given config."""
        config_path = os.path.join(temp_dir, 'config.yaml')

        # Write config to file
        import yaml
        with open(config_path, 'w') as f:
            yaml.dump(config_dict, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config_dict

            daemon = RapperDaemon(config_path, 'test-agent')
            daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

            # Mock client methods to track calls
            daemon.client.get_tasks = MagicMock(return_value=[])
            daemon.client.claim_task = MagicMock(return_value=True)
            daemon.client.update_task_status = MagicMock(return_value=True)

            return daemon

    def test_t1_rapper_polls_todo_and_ready(self):
        """T1: Rapper config with poll_columns=['todo', 'ready'] polls both columns."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_rapper_config()
            daemon = self._create_daemon_with_config(config, temp_dir)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    # Run one poll cycle
                    daemon._poll_and_execute_tasks()

            # Expected: should poll the columns specified in poll_columns config
            # Current implementation (will FAIL): polls hardcoded 'todo' and 'ready'
            expected_calls = [
                call(None, 'todo'),
                call(None, 'ready'),
            ]
            daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
            self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_t2_reviewer_polls_only_review(self):
        """T2: Reviewer config with poll_columns=['review'] polls only review column."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_reviewer_config()
            daemon = self._create_daemon_with_config(config, temp_dir)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    # Run one poll cycle
                    daemon._poll_and_execute_tasks()

            # Expected: should poll only 'review' column per poll_columns config
            # Current implementation (will FAIL): polls hardcoded 'todo' and 'ready'
            daemon.client.get_tasks.assert_called_once_with(None, 'review')

            # Also ensure todo and ready are NOT queried
            all_calls = [call_obj[0] for call_obj in daemon.client.get_tasks.call_args_list]
            for call_args in all_calls:
                if len(call_args) > 1:
                    column = call_args[1]  # Second argument is column
                    self.assertNotIn(column, ['todo', 'ready'],
                                   f"Reviewer should not poll {column} column")

    def test_t3_backward_compatibility_no_poll_columns(self):
        """T3: No poll_columns config defaults to ['todo', 'ready'] for backward compatibility."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_base_config()  # No poll_columns key
            daemon = self._create_daemon_with_config(config, temp_dir)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    # Run one poll cycle
                    daemon._poll_and_execute_tasks()

            # Expected: should default to polling both todo and ready (current behavior)
            # Current implementation (will PASS): this is the current hardcoded behavior
            expected_calls = [
                call(None, 'todo'),
                call(None, 'ready'),
            ]
            daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
            self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_t4_empty_poll_columns_fallback_default(self):
        """T4: Empty poll_columns falls back to default ['todo', 'ready'] for backward compatibility."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_base_config()
            config['agent_board']['poll_columns'] = []  # Empty list
            daemon = self._create_daemon_with_config(config, temp_dir)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    # Run one poll cycle
                    daemon._poll_and_execute_tasks()

            # Expected: should fallback to default ['todo', 'ready'] per design spec
            expected_calls = [
                call(None, 'todo'),
                call(None, 'ready'),
            ]
            daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
            self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_t5_single_column_configuration(self):
        """T5: Single column configuration works correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_base_config()
            config['agent_board']['poll_columns'] = ['doing']  # Single column
            daemon = self._create_daemon_with_config(config, temp_dir)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    # Run one poll cycle
                    daemon._poll_and_execute_tasks()

            # Expected: should call get_tasks exactly once with 'doing'
            # Current implementation (will FAIL): polls hardcoded 'todo' and 'ready'
            daemon.client.get_tasks.assert_called_once_with(None, 'doing')

    def test_t6_multiple_custom_columns(self):
        """T6: Multiple custom columns work correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_base_config()
            config['agent_board']['poll_columns'] = ['todo', 'ready', 'blocked']  # Three columns
            daemon = self._create_daemon_with_config(config, temp_dir)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    # Run one poll cycle
                    daemon._poll_and_execute_tasks()

            # Expected: should call get_tasks for all three columns
            # Current implementation (will FAIL): polls only hardcoded 'todo' and 'ready'
            expected_calls = [
                call(None, 'todo'),
                call(None, 'ready'),
                call(None, 'blocked'),
            ]
            daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
            self.assertEqual(daemon.client.get_tasks.call_count, 3)

    def test_config_loading_stores_poll_columns(self):
        """Test that poll_columns config is properly stored in daemon instance."""
        with tempfile.TemporaryDirectory() as temp_dir:
            reviewer_config = _make_reviewer_config()
            daemon = self._create_daemon_with_config(reviewer_config, temp_dir)

            # Expected: config should be stored with poll_columns
            # Current implementation: config is stored but poll_columns is not used
            self.assertEqual(daemon.config['agent_board']['role'], 'reviewer')
            self.assertEqual(daemon.config['agent_board']['poll_columns'], ['review'])

    def test_role_reviewer_with_review_columns_combination(self):
        """Test role=reviewer with poll_columns=['review'] combination."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_reviewer_config()
            daemon = self._create_daemon_with_config(config, temp_dir)

            # Verify config loaded correctly
            self.assertEqual(daemon.config['agent_board']['role'], 'reviewer')
            self.assertEqual(daemon.config['agent_board']['poll_columns'], ['review'])

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    daemon._poll_and_execute_tasks()

            # Expected: should poll only review column
            # Current implementation (will FAIL): polls hardcoded 'todo' and 'ready'
            daemon.client.get_tasks.assert_called_once_with(None, 'review')

    def test_integration_reviewer_picks_review_task(self):
        """Integration test: reviewer polls review column and picks review task."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = _make_reviewer_config()  # polls only review
            daemon = self._create_daemon_with_config(config, temp_dir)

            # Mock review column has tasks, todo column has tasks
            review_task = {
                'id': 'review_task_123',
                'title': 'Review task',
                'description': 'Task to review',
                'column': 'review',
                'assignee': None
            }

            def mock_get_tasks_behavior(assignee, column):
                if column == 'review':
                    return [review_task]
                elif column == 'doing':
                    return []
                else:
                    return []  # No tasks in other columns

            daemon.client.get_tasks = MagicMock(side_effect=mock_get_tasks_behavior)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    with patch('daemon.generate_task_id', return_value='internal_123'):
                        with patch('daemon.Task') as mock_task_class:
                            # Fully-populated Task double.  The integration test only
                            # verifies poll/claim/submit scheduling; it must not enter
                            # real TaskRunner/Claude execution or start background work.
                            mock_task = MagicMock()
                            mock_task.id = 'internal_123'
                            mock_task.board_task_id = 'review_task_123'
                            mock_task.status = 'pending'
                            mock_task.result = None
                            mock_task.error = None
                            mock_task.progress = []
                            mock_task.structured_result = {}
                            mock_task_class.return_value = mock_task

                            submitted_future = MagicMock()
                            daemon.task_executor.submit = MagicMock(return_value=submitted_future)

                            daemon._poll_and_execute_tasks()

            # Expected: should query the configured review column, then do a
            # separate 'doing' query for deduplication.  It must not query rapper
            # pickup columns when configured as reviewer.
            daemon.client.get_tasks.assert_any_call(None, 'review')
            queried_columns = [args[1] for args, _kwargs in daemon.client.get_tasks.call_args_list]
            self.assertIn('review', queried_columns)
            self.assertNotIn('todo', queried_columns)
            self.assertNotIn('ready', queried_columns)
            daemon.client.claim_task.assert_called_once_with('review_task_123', 'test-agent', target_column='review')
            daemon.task_executor.submit.assert_called_once_with(
                daemon._execute_task_in_background,
                'review_task_123',
                mock_task,
            )


if __name__ == '__main__':
    unittest.main()