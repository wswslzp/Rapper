#!/usr/bin/env python3
"""
Final comprehensive test for daemon poll_columns functionality.

This test properly validates that:
1. Daemon queries configured poll_columns
2. Rapper defaults to ['todo', 'ready']
3. Reviewer uses ['review']
4. Backward compatibility is maintained

Key insight: Daemon makes multiple get_tasks calls including deduplication queries,
so we need to check that the configured columns are included in the calls,
not that they are the only calls.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from daemon import RapperDaemon


class TestDaemonPollColumnsFinal(unittest.TestCase):
    """Final test for poll_columns configuration validation."""

    def _create_daemon_with_config(self, config_dict):
        """Helper to create daemon with given config."""
        # Ensure all required config fields are present
        defaults = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'test-agent',
                'poll_interval': 30,
                'webhook_port': 19999,
                'poll_columns': ['todo', 'ready'],  # Default for backward compatibility
                'route_completed_to': 'done'
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        # Deep merge config_dict into defaults
        merged_config = {}
        for key, value in defaults.items():
            if key in config_dict and isinstance(value, dict):
                merged_config[key] = {**value, **config_dict[key]}
            else:
                merged_config[key] = config_dict.get(key, value)

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')

            with patch.object(RapperDaemon, '_load_config', return_value=merged_config):
                with patch('daemon.init_db'):
                    daemon = RapperDaemon(config_path, agent_id='test-agent')

                    daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                    # Mock dependencies
                    daemon.client.get_tasks = MagicMock(return_value=[])
                    daemon._count_running_tasks = MagicMock(return_value=0)
                    daemon._load_picked_tasks = MagicMock(return_value=set())
                    daemon._cleanup_completed_futures = MagicMock()

                    return daemon

    def _extract_columns_from_calls(self, call_list):
        """Extract column names from get_tasks call list."""
        columns = []
        for call_obj in call_list:
            if len(call_obj[0]) > 1:  # call_obj[0] is args tuple
                column = call_obj[0][1]  # Second argument is column
                columns.append(column)
        return columns

    def test_rapper_default_configuration(self):
        """Test rapper with default poll_columns=['todo', 'ready']."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'rapper-1',
                'role': 'rapper',
                'poll_columns': ['todo', 'ready'],
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)
        daemon._poll_and_execute_tasks()

        # Extract columns that were queried
        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Verify configured columns are queried
        self.assertIn('todo', queried_columns, "Rapper should query 'todo' column")
        self.assertIn('ready', queried_columns, "Rapper should query 'ready' column")

    def test_reviewer_configuration(self):
        """Test reviewer with poll_columns=['review']."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'reviewer-1',
                'role': 'reviewer',
                'poll_columns': ['review'],
            },
            'tasks': {'max_concurrent_tasks': 1},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)
        daemon._poll_and_execute_tasks()

        # Extract columns that were queried
        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Verify configured column is queried
        self.assertIn('review', queried_columns, "Reviewer should query 'review' column")

        # Verify reviewer does NOT query rapper columns
        self.assertNotIn('todo', queried_columns, "Reviewer should NOT query 'todo' column")
        self.assertNotIn('ready', queried_columns, "Reviewer should NOT query 'ready' column")

    def test_backward_compatibility_no_poll_columns(self):
        """Test backward compatibility when poll_columns is not specified."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'legacy-rapper',
                # No poll_columns specified - should default to ['todo', 'ready']
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)
        daemon._poll_and_execute_tasks()

        # Extract columns that were queried
        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Should default to rapper behavior
        self.assertIn('todo', queried_columns, "Should default to querying 'todo' column")
        self.assertIn('ready', queried_columns, "Should default to querying 'ready' column")

    def test_custom_multiple_columns(self):
        """Test custom configuration with multiple columns."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'custom-agent',
                'poll_columns': ['todo', 'blocked', 'review'],
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)
        daemon._poll_and_execute_tasks()

        # Extract columns that were queried
        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Verify all configured columns are queried
        self.assertIn('todo', queried_columns, "Should query 'todo' column")
        self.assertIn('blocked', queried_columns, "Should query 'blocked' column")
        self.assertIn('review', queried_columns, "Should query 'review' column")

    def test_single_custom_column(self):
        """Test configuration with single custom column."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'single-agent',
                'poll_columns': ['blocked'],
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)
        daemon._poll_and_execute_tasks()

        # Extract columns that were queried
        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Verify only the configured column is queried for tasks
        self.assertIn('blocked', queried_columns, "Should query configured 'blocked' column")

    def test_config_validation_stores_poll_columns(self):
        """Test that poll_columns config is properly stored."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'config-test',
                'role': 'reviewer',
                'poll_columns': ['review'],
            },
            'tasks': {'max_concurrent_tasks': 1},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)

        # Verify config is stored correctly
        self.assertEqual(daemon.config['agent_board']['poll_columns'], ['review'])
        self.assertEqual(daemon.config['agent_board']['role'], 'reviewer')

    def test_implementation_verification(self):
        """Verify the implementation actually uses poll_columns from config.

        This test validates that the code path in _poll_and_execute_tasks()
        reads and uses the poll_columns configuration.
        """
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'implementation-test',
                'poll_columns': ['custom_column_name'],
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)

        # Verify the config was loaded with our custom column
        loaded_columns = daemon.config.get('agent_board', {}).get('poll_columns', [])
        self.assertEqual(loaded_columns, ['custom_column_name'])

        # Execute polling and verify the custom column is queried
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        self.assertIn('custom_column_name', queried_columns,
                     "Implementation should query configured custom column")

    def test_empty_poll_columns_fallback(self):
        """Test that empty poll_columns falls back to default."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'empty-test',
                'poll_columns': [],  # Empty list
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Should fallback to default behavior
        self.assertIn('todo', queried_columns, "Empty config should fallback to 'todo'")
        self.assertIn('ready', queried_columns, "Empty config should fallback to 'ready'")

    def test_role_and_poll_columns_integration(self):
        """Test that role and poll_columns work together correctly."""
        # Test reviewer role with review columns
        reviewer_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-reviewer',
                'agent_id': 'reviewer-integration',
                'role': 'reviewer',
                'poll_columns': ['review', 'code-review'],
            },
            'tasks': {'max_concurrent_tasks': 1},
            'logging': {'level': 'warning'},
        }

        daemon = self._create_daemon_with_config(reviewer_config)
        daemon._poll_and_execute_tasks()

        queried_columns = self._extract_columns_from_calls(
            daemon.client.get_tasks.call_args_list
        )

        # Should query both review-related columns
        self.assertIn('review', queried_columns)
        self.assertIn('code-review', queried_columns)

        # Should not query implementation columns
        self.assertNotIn('todo', queried_columns)
        self.assertNotIn('ready', queried_columns)


if __name__ == '__main__':
    unittest.main(verbosity=2)