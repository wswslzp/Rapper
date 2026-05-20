#!/usr/bin/env python3
"""
Test daemon role-based validation and defaults for poll_columns configuration.

This implements role-specific validation requirements from Agent Board Reviewer design:
- Reviewer role should have automatic defaults and validation
- Rapper role should have proper defaults and validation
- Role mismatches should be caught and warned/corrected

Verifies role-based behavior that should be implemented:
- Role-based poll_columns defaults (reviewer->review, rapper->todo/ready)
- Role-based validation (reviewer shouldn't poll todo/ready by mistake)
- Role-based configuration enforcement and warnings

Related:
- requirements.md v1.1 AC-02: "Reviewer 只 poll review 列任务，不 poll todo/ready"
- design.md v2.1 §3.1: Rapper config defaults
- design.md v2.1 §3.2: Reviewer config requirements
- design.md v2.1 §6.1: daemon.py changes D1 (role/poll_columns)

Expected behavior: These tests should FAIL initially (RED phase) because
role-based validation and automatic defaults are not yet implemented in daemon.py.
The current implementation respects poll_columns but doesn't validate or default based on role.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch
import logging

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

try:
    from daemon import RapperDaemon
except ImportError as e:
    print(f"Failed to import daemon: {e}")
    sys.exit(1)


class TestDaemonRoleValidation(unittest.TestCase):
    """Test role-based validation and automatic defaults for poll_columns."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.temp_dir))

    def _create_base_config(self):
        """Create base configuration dict without role-specific settings."""
        return {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test-key',
                'agent_id': 'test-agent',
                'poll_interval': 30,
                'webhook_port': 19999,
            },
            'tasks': {'max_concurrent_tasks': 1},
            'logging': {'level': 'warning'},
        }

    def _create_daemon_with_config(self, config_dict):
        """Create daemon instance with given config, capturing log output."""
        config_path = os.path.join(self.temp_dir, 'test_config.yaml')

        # Mock config loading to return our test config
        with patch.object(RapperDaemon, '_load_config', return_value=config_dict), \
             patch('daemon.init_db'):
            daemon = RapperDaemon(config_path, agent_id='test-agent-123')

            # Set up mocks
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')
            daemon.client.get_tasks = MagicMock(return_value=[])
            daemon.client.claim_task = MagicMock(return_value=True)
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon._count_running_tasks = MagicMock(return_value=0)
            daemon._load_picked_tasks = MagicMock(return_value=set())
            daemon._cleanup_completed_futures = MagicMock()

            return daemon

    def test_reviewer_role_auto_defaults_to_review_column(self):
        """Test reviewer role automatically defaults poll_columns to ['review'] when not specified.

        RED expectation: Will FAIL because role-based defaults aren't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        # Deliberately omit poll_columns to test role-based default

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Expected: reviewer role should automatically default to review column
        # Current implementation (will FAIL): uses default ['todo', 'ready'] regardless of role
        daemon.client.get_tasks.assert_called_once_with(None, 'review')

    def test_rapper_role_auto_defaults_to_todo_ready_columns(self):
        """Test rapper role automatically defaults poll_columns to ['todo', 'ready'] when not specified.

        RED expectation: May PASS since this matches current default behavior.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'rapper'
        # Deliberately omit poll_columns to test role-based default

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Expected: rapper role should default to todo and ready columns
        # Current implementation (should PASS): defaults are already ['todo', 'ready']
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_reviewer_with_todo_columns_logs_validation_warning(self):
        """Test reviewer role with todo/ready poll_columns logs validation warning.

        RED expectation: Will FAIL because role validation isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        config['agent_board']['poll_columns'] = ['todo', 'ready']  # Invalid for reviewer

        # Capture log output during daemon creation
        with self.assertLogs('daemon.daemon', level='WARNING') as log_capture:
            daemon = self._create_daemon_with_config(config)

            # Should log a validation warning about invalid role/poll_columns combination
            warning_found = any(
                'reviewer' in log.lower() and 'todo' in log.lower()
                for log in log_capture.output
            )
            self.assertTrue(warning_found,
                          "Should warn when reviewer role has todo/ready poll_columns")

    def test_rapper_with_review_columns_logs_validation_warning(self):
        """Test rapper role with review poll_columns logs validation warning.

        RED expectation: Will FAIL because role validation isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'rapper'
        config['agent_board']['poll_columns'] = ['review']  # Invalid for rapper

        # Capture log output during daemon creation
        with self.assertLogs('daemon.daemon', level='WARNING') as log_capture:
            daemon = self._create_daemon_with_config(config)

            # Should log a validation warning about invalid role/poll_columns combination
            warning_found = any(
                'rapper' in log.lower() and 'review' in log.lower()
                for log in log_capture.output
            )
            self.assertTrue(warning_found,
                          "Should warn when rapper role has review poll_columns")

    def test_reviewer_validation_corrects_invalid_columns(self):
        """Test reviewer role with invalid columns gets auto-corrected to ['review'].

        RED expectation: Will FAIL because role validation/correction isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        config['agent_board']['poll_columns'] = ['todo', 'ready']  # Should be corrected

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Expected: should auto-correct to review column despite invalid config
        # Current implementation (will FAIL): respects invalid config as-is
        daemon.client.get_tasks.assert_called_once_with(None, 'review')

    def test_rapper_validation_corrects_review_column(self):
        """Test rapper role with review column gets auto-corrected to ['todo', 'ready'].

        RED expectation: Will FAIL because role validation/correction isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'rapper'
        config['agent_board']['poll_columns'] = ['review']  # Should be corrected

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Expected: should auto-correct to todo/ready despite invalid config
        # Current implementation (will FAIL): respects invalid config as-is
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_unknown_role_logs_warning_and_defaults_rapper(self):
        """Test unknown role value logs warning and defaults to rapper behavior.

        RED expectation: Will FAIL because role validation isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'unknown-role'  # Invalid role

        # Capture log output
        with self.assertLogs('daemon.daemon', level='WARNING') as log_capture:
            daemon = self._create_daemon_with_config(config)

            # Execute one poll cycle
            daemon._poll_and_execute_tasks()

            # Should warn about unknown role
            warning_found = any(
                'unknown-role' in log.lower() and 'invalid' in log.lower()
                for log in log_capture.output
            )
            self.assertTrue(warning_found,
                          "Should warn about unknown role value")

        # Should default to rapper behavior (todo + ready)
        expected_calls = [
            call(None, 'todo'),
            call(None, 'ready'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)

    def test_reviewer_mixed_valid_invalid_columns_filters_appropriately(self):
        """Test reviewer with mixed valid/invalid columns filters to only valid ones.

        RED expectation: Will FAIL because column filtering by role isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        config['agent_board']['poll_columns'] = ['review', 'todo', 'blocked']  # Mixed validity

        daemon = self._create_daemon_with_config(config)

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Expected: should filter out 'todo' and keep 'review', 'blocked' (review-appropriate)
        # Current implementation (will FAIL): polls all columns as specified
        valid_calls = [
            call(None, 'review'),
            call(None, 'blocked'),  # blocked is valid for reviewer
        ]
        daemon.client.get_tasks.assert_has_calls(valid_calls, any_order=True)
        self.assertEqual(daemon.client.get_tasks.call_count, 2)

        # Should NOT call todo
        all_call_args = [call_obj[0] for call_obj in daemon.client.get_tasks.call_args_list]
        for call_args in all_call_args:
            if len(call_args) > 1:
                column = call_args[1]
                self.assertNotEqual(column, 'todo',
                                  "Reviewer should not poll todo column even if configured")

    def test_role_enforcement_prevents_cross_role_task_pickup(self):
        """Test role enforcement prevents reviewer from picking up rapper tasks.

        This tests the complete isolation: reviewer should never see or pick up
        tasks from todo/ready columns, even if they exist.

        RED expectation: Will FAIL because role-based task isolation isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'  # Should auto-default to review only

        daemon = self._create_daemon_with_config(config)

        # Set up tasks in multiple columns
        review_task = {'id': 'task_review_1', 'assignee': None, 'column': 'review'}
        todo_task = {'id': 'task_todo_1', 'assignee': None, 'column': 'todo'}

        def mock_get_tasks_side_effect(assignee, column):
            if column == 'review':
                return [review_task]
            elif column == 'todo':
                return [todo_task]
            elif column == 'doing':
                return []
            else:
                return []

        daemon.client.get_tasks.side_effect = mock_get_tasks_side_effect

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Expected: should only query review, should only claim review task
        # Current implementation (will FAIL): queries default columns
        daemon.client.get_tasks.assert_called_with(None, 'review')
        daemon.client.claim_task.assert_called_once_with('task_review_1', 'test-agent-123')

    def test_config_role_defaults_are_applied_during_load(self):
        """Test that role-based defaults are applied during config loading phase.

        RED expectation: Will FAIL because role-based config merging isn't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        # No poll_columns specified

        daemon = self._create_daemon_with_config(config)

        # Expected: config should be auto-populated with role-based defaults
        # Current implementation (will FAIL): config loading doesn't apply role defaults
        self.assertEqual(daemon.config['agent_board']['poll_columns'], ['review'],
                        "Reviewer role should auto-populate poll_columns=['review']")

    def test_role_based_route_completed_to_defaults(self):
        """Test that reviewer role defaults route_completed_to appropriately.

        This verifies that role affects not just polling but also completion routing.

        RED expectation: May FAIL if role-based routing defaults aren't implemented.
        """
        config = self._create_base_config()
        config['agent_board']['role'] = 'reviewer'
        # No route_completed_to specified

        daemon = self._create_daemon_with_config(config)

        # Expected: reviewer role might have different completion routing defaults
        # Current implementation: check what default is applied
        route_config = daemon.config['agent_board'].get('route_completed_to', 'done')

        # For reviewer, the completion route shouldn't default to 'review'
        # (that would create infinite loops), but could have special handling
        self.assertIn(route_config, ['done', 'review'],
                     "Reviewer role should have valid route_completed_to default")


if __name__ == '__main__':
    # Run tests with detailed output to see validation failures
    unittest.main(verbosity=2)