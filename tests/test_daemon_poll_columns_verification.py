#!/usr/bin/env python3
"""
VERIFICATION: Test daemon poll_columns role/config-based polling functionality.

This test suite verifies that TEST-01 requirements are MET:
- Rapper default: poll 'todo' + 'ready' columns
- Reviewer behavior: poll 'review' column only, not 'todo'/'ready'
- Backward compatibility: missing poll_columns defaults to ['todo', 'ready']

DISCOVERY: The poll_columns functionality is already implemented in daemon.py!
Lines 571-581 show the daemon reads poll_columns config and uses it to query columns.

This test file serves as:
1. Verification that the functionality works correctly
2. Regression prevention for future changes
3. Documentation of expected behavior

References:
- /app/agent-board-reviewer/requirements.md v1.1 AC-02
- /app/agent-board-reviewer/design.md v2.1 §3.1/3.2/6.1
- TEST-01 from requirements DAG
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from daemon import RapperDaemon


class TestDaemonPollColumnsVerification(unittest.TestCase):
    """Verification tests for poll_columns role-based functionality."""

    def _create_test_config(self, **overrides):
        """Create complete test configuration with all required fields."""
        base_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test-key',
                'agent_id': 'test-agent',
                'poll_interval': 30,
                'webhook_port': 19999,
                'poll_columns': ['todo', 'ready'],  # Default
                'route_completed_to': 'done'
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'}
        }

        # Deep merge overrides into base config
        config = {}
        for key, value in base_config.items():
            if key in overrides and isinstance(value, dict):
                config[key] = {**value, **overrides[key]}
            else:
                config[key] = overrides.get(key, value)

        return config

    def _create_daemon(self, config):
        """Create daemon with given config and mock dependencies."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(RapperDaemon, '_load_config', return_value=config):
                with patch('daemon.init_db'):
                    daemon = RapperDaemon('test_config.yaml', agent_id='test-agent')
                    daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                    # Mock external dependencies
                    daemon.client.get_tasks = MagicMock(return_value=[])
                    daemon._count_running_tasks = MagicMock(return_value=0)
                    daemon._load_picked_tasks = MagicMock(return_value=set())
                    daemon._cleanup_completed_futures = MagicMock()

                    return daemon

    def _extract_queried_columns(self, daemon):
        """Extract columns that were queried by daemon."""
        call_list = daemon.client.get_tasks.call_args_list
        columns = []
        for call_obj in call_list:
            if len(call_obj[0]) > 1:  # args tuple has column parameter
                column = call_obj[0][1]
                columns.append(column)
        return columns

    def test_verify_rapper_polls_todo_ready_by_default(self):
        """✅ VERIFY: Rapper with default config polls 'todo' and 'ready' columns."""
        config = self._create_test_config(agent_board={
            'role': 'rapper',
            'poll_columns': ['todo', 'ready']
        })

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Assertions
        self.assertIn('todo', queried_columns, "Rapper must poll 'todo' column")
        self.assertIn('ready', queried_columns, "Rapper must poll 'ready' column")
        print(f"✅ Rapper queried columns: {queried_columns}")

    def test_verify_reviewer_polls_only_review(self):
        """✅ VERIFY: Reviewer polls only 'review' column, never 'todo'/'ready'."""
        config = self._create_test_config(agent_board={
            'role': 'reviewer',
            'poll_columns': ['review'],
            'agent_id': 'reviewer-1'
        })

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Assertions
        self.assertIn('review', queried_columns, "Reviewer must poll 'review' column")
        self.assertNotIn('todo', queried_columns, "Reviewer must NOT poll 'todo' column")
        self.assertNotIn('ready', queried_columns, "Reviewer must NOT poll 'ready' column")
        print(f"✅ Reviewer queried columns: {queried_columns}")

    def test_verify_backward_compatibility_no_poll_columns(self):
        """✅ VERIFY: Missing poll_columns config defaults to ['todo', 'ready']."""
        config = self._create_test_config()
        # Remove poll_columns to test default behavior
        del config['agent_board']['poll_columns']

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Should default to rapper columns for backward compatibility
        self.assertIn('todo', queried_columns, "Should default to 'todo' column")
        self.assertIn('ready', queried_columns, "Should default to 'ready' column")
        print(f"✅ Backward compatibility queried columns: {queried_columns}")

    def test_verify_empty_poll_columns_fallback(self):
        """✅ VERIFY: Empty poll_columns list falls back to defaults."""
        config = self._create_test_config(agent_board={
            'poll_columns': []  # Empty list
        })

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Should fallback to defaults
        self.assertIn('todo', queried_columns, "Empty config should fallback to 'todo'")
        self.assertIn('ready', queried_columns, "Empty config should fallback to 'ready'")
        print(f"✅ Empty poll_columns fallback: {queried_columns}")

    def test_verify_custom_multiple_columns(self):
        """✅ VERIFY: Custom multiple columns configuration works."""
        config = self._create_test_config(agent_board={
            'poll_columns': ['todo', 'review', 'blocked']
        })

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Should query all configured columns
        self.assertIn('todo', queried_columns, "Should query 'todo' column")
        self.assertIn('review', queried_columns, "Should query 'review' column")
        self.assertIn('blocked', queried_columns, "Should query 'blocked' column")
        print(f"✅ Multiple custom columns: {queried_columns}")

    def test_verify_config_loading(self):
        """✅ VERIFY: Configuration is properly loaded and stored."""
        config = self._create_test_config(agent_board={
            'role': 'reviewer',
            'poll_columns': ['review']
        })

        daemon = self._create_daemon(config)

        # Verify config is accessible
        self.assertEqual(daemon.config['agent_board']['role'], 'reviewer')
        self.assertEqual(daemon.config['agent_board']['poll_columns'], ['review'])
        print(f"✅ Config loaded correctly: role={daemon.config['agent_board']['role']}, poll_columns={daemon.config['agent_board']['poll_columns']}")

    def test_implementation_inspection(self):
        """🔍 INSPECTION: Show exactly what the implementation does."""
        print("\n" + "="*60)
        print("IMPLEMENTATION INSPECTION")
        print("="*60)

        # Test different configurations
        test_cases = [
            ("Rapper Default", {'role': 'rapper', 'poll_columns': ['todo', 'ready']}),
            ("Reviewer", {'role': 'reviewer', 'poll_columns': ['review']}),
            ("Custom Multi", {'poll_columns': ['todo', 'blocked', 'review']}),
            ("Backward Compat", {}),  # No poll_columns
        ]

        for name, agent_board_config in test_cases:
            config = self._create_test_config(agent_board=agent_board_config)
            daemon = self._create_daemon(config)
            daemon._poll_and_execute_tasks()

            queried_columns = self._extract_queried_columns(daemon)
            configured_columns = daemon.config['agent_board'].get('poll_columns', 'DEFAULT')

            print(f"\n{name}:")
            print(f"  Configured: {configured_columns}")
            print(f"  Queried:    {queried_columns}")

        print("\n" + "="*60)

    def test_red_phase_reviewer_auto_defaults_to_review(self):
        """❌ RED PHASE: Reviewer role should auto-default poll_columns to ['review'].

        This test should FAIL because role-based auto-defaults aren't implemented.
        """
        config = self._create_test_config(agent_board={
            'role': 'reviewer'
            # No poll_columns specified - should auto-default based on role
        })

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Expected (will FAIL): reviewer role should auto-default to review column
        # Current: uses default ['todo', 'ready'] regardless of role
        self.assertIn('review', queried_columns, "Reviewer role should auto-default to 'review' column")
        self.assertNotIn('todo', queried_columns, "Reviewer role should NOT auto-default to 'todo' column")
        print(f"❌ RED: Reviewer auto-default queried columns: {queried_columns}")

    def test_red_phase_role_validation_warnings(self):
        """❌ RED PHASE: Invalid role/poll_columns combinations should log warnings.

        This test should FAIL because role validation isn't implemented.
        """
        # Test reviewer with invalid todo/ready columns
        config = self._create_test_config(agent_board={
            'role': 'reviewer',
            'poll_columns': ['todo', 'ready']  # Invalid for reviewer role
        })

        # Should log validation warning (will FAIL - no validation exists)
        with self.assertLogs('daemon.daemon', level='WARNING') as log_capture:
            daemon = self._create_daemon(config)

            # Look for role validation warning
            warning_found = any(
                'reviewer' in log.lower() and 'todo' in log.lower()
                for log in log_capture.output
            )
            self.assertTrue(warning_found, "Should warn about reviewer role with todo/ready columns")
            print(f"❌ RED: Role validation warning not found in logs")

    def test_red_phase_rapper_role_auto_defaults(self):
        """❌ RED PHASE: Rapper role should explicitly default to todo/ready when no poll_columns.

        This may PASS because it matches current behavior, but tests role-based logic.
        """
        config = self._create_test_config(agent_board={
            'role': 'rapper'
            # No poll_columns specified - should auto-default based on role
        })
        # Remove default poll_columns to test role-based defaults
        del config['agent_board']['poll_columns']

        daemon = self._create_daemon(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_queried_columns(daemon)

        # Should auto-default to rapper columns based on role
        self.assertIn('todo', queried_columns, "Rapper role should auto-default to 'todo' column")
        self.assertIn('ready', queried_columns, "Rapper role should auto-default to 'ready' column")
        print(f"✓ Rapper role auto-default queried columns: {queried_columns}")

    def test_red_phase_config_role_based_merge(self):
        """❌ RED PHASE: Config loading should apply role-based defaults.

        This test should FAIL because config doesn't auto-populate based on role.
        """
        config = self._create_test_config(agent_board={
            'role': 'reviewer'
            # No poll_columns specified
        })
        # Remove poll_columns to test role-based config merging
        del config['agent_board']['poll_columns']

        daemon = self._create_daemon(config)

        # Expected (will FAIL): config should be auto-populated with role defaults
        expected_columns = ['review']
        actual_columns = daemon.config['agent_board'].get('poll_columns')

        self.assertEqual(actual_columns, expected_columns,
                        f"Reviewer role should auto-populate poll_columns to {expected_columns}, got {actual_columns}")
        print(f"❌ RED: Config auto-population not implemented - got {actual_columns}")

    def test_red_phase_unknown_role_handling(self):
        """❌ RED PHASE: Unknown role should log warning and default safely.

        This test should FAIL because role validation doesn't exist.
        """
        config = self._create_test_config(agent_board={
            'role': 'unknown-role',
            'poll_columns': ['strange-column']
        })

        # Should log validation warning for unknown role (will FAIL)
        with self.assertLogs('daemon.daemon', level='WARNING') as log_capture:
            daemon = self._create_daemon(config)

            warning_found = any(
                'unknown-role' in log.lower() or 'invalid role' in log.lower()
                for log in log_capture.output
            )
            self.assertTrue(warning_found, "Should warn about unknown role value")
            print(f"❌ RED: Unknown role warning not found in logs")


if __name__ == '__main__':
    # Run with high verbosity to show implementation details
    unittest.main(verbosity=2, failfast=False)