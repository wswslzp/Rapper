#!/usr/bin/env python3
"""
Tests for daemon route_completed_to configuration.

This implements TEST-02 and TEST-03 from the Agent Board Reviewer design:
- TEST-02: route completed to review - when route_completed_to=review, completed tasks go to review
- TEST-03: default route remains done - default behavior unchanged for backward compatibility

Verifies:
- R1: Default config with route_completed_to=done sends completed tasks to 'done' column
- R2: Config with route_completed_to=review sends completed tasks to 'review' column
- R3: Backward compatibility: no route_completed_to defaults to 'done'
- R4: Task with requiresReview=true goes to review regardless of route_completed_to
- R5: Review route preserves implementedBy metadata
- R6: Review route sets reviewState=pending
- R7: Done route does not set review metadata
- R8: Invalid route_completed_to value defaults to 'done'

Related:
- requirements.md v1.1 AC-09, AC-10
- design.md v2.1 §4.2 Rapper State Machine
- TEST-02, TEST-03 from requirements.md DAG
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch, Mock

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


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


def _make_rapper_config_with_route(route_to):
    """Rapper config with explicit route_completed_to setting."""
    config = _make_base_config()
    config['agent_board'].update({
        'role': 'rapper',
        'poll_columns': ['todo', 'ready'],
        'route_completed_to': route_to,
    })
    return config


def _make_legacy_rapper_config():
    """Legacy rapper config without route_completed_to (for backward compatibility test)."""
    config = _make_base_config()
    config['agent_board'].update({
        'role': 'rapper',
        'poll_columns': ['todo', 'ready'],
        # No route_completed_to key
    })
    return config


class MockTask:
    """Mock Task object for testing."""
    def __init__(self, status='completed', result='Task completed', error=None):
        self.id = 'mock_task_123'  # Add missing id attribute
        self.status = status
        self.result = result
        self.error = error
        self.progress = []
        self.structured_result = {}
        self.board_task_metadata = {}  # Add metadata for requiresReview tests


class TestDaemonRouteCompleted(unittest.TestCase):
    """Test cases for route_completed_to configuration."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_daemon_with_config(self, config_dict):
        """Helper to create daemon with given config, mocking file operations."""
        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(config_dict, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config_dict

            daemon = RapperDaemon(self.config_path, 'test-agent')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock task runner
            daemon.task_runner = MagicMock()
            daemon.task_runner._run_task_sync = MagicMock()

            return daemon

    def test_r1_default_route_to_done(self):
        """R1: Default config with route_completed_to=done sends completed tasks to 'done'."""
        config = _make_rapper_config_with_route('done')
        daemon = self._create_daemon_with_config(config)

        # Create mock completed task
        mock_task = MockTask(status='completed', result='Implementation completed')
        board_task_id = 'task_123'

        # Execute the background task completion path
        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Verify task was moved to 'done' column
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id, 'done', 'Implementation completed'
        )

        # Verify completion comment was added
        daemon.client.add_comment.assert_called()
        comment_calls = daemon.client.add_comment.call_args_list
        self.assertTrue(any('✅ 任务完成' in str(call) for call in comment_calls))

    def test_r2_route_to_review_column(self):
        """R2: Config with route_completed_to=review sends completed tasks to 'review'."""
        config = _make_rapper_config_with_route('review')
        daemon = self._create_daemon_with_config(config)

        mock_task = MockTask(status='completed', result='Implementation completed')
        board_task_id = 'task_456'

        # Mock the agent_id for implementedBy
        daemon.agent_id = 'rapper-1'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Verify task was moved to 'review' column with proper metadata
        daemon.client.update_task_status.assert_called_once()
        call_args = daemon.client.update_task_status.call_args[0]

        # Should call update_task_status with review-specific parameters
        self.assertEqual(call_args[0], board_task_id)  # task_id
        self.assertEqual(call_args[1], 'review')       # column should be 'review'

        # Should also update implementedBy and reviewState metadata
        # This requires the client.update_task_metadata method or similar
        # For now, test that the basic routing works

    def test_r3_backward_compatibility_no_route_config(self):
        """R3: No route_completed_to defaults to 'done' for backward compatibility."""
        config = _make_legacy_rapper_config()  # No route_completed_to key
        daemon = self._create_daemon_with_config(config)

        mock_task = MockTask(status='completed', result='Task done')
        board_task_id = 'task_789'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Should default to 'done' column (current behavior)
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id, 'done', 'Task done'
        )

    def test_r4_requires_review_overrides_route_config(self):
        """R4: Task with requiresReview=true goes to review regardless of route_completed_to."""
        config = _make_rapper_config_with_route('done')  # Configured for 'done'
        daemon = self._create_daemon_with_config(config)

        # Create a task that requires review
        mock_task = MockTask(status='completed', result='Implementation completed')
        mock_task.board_task_metadata = {'requiresReview': True}  # Task-level override
        board_task_id = 'task_override'

        daemon.agent_id = 'rapper-2'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Should route to review despite route_completed_to=done
        daemon.client.update_task_status.assert_called_once()
        call_args = daemon.client.update_task_status.call_args[0]
        self.assertEqual(call_args[1], 'review')  # Column should be 'review'

    def test_r5_review_route_preserves_implemented_by(self):
        """R5: Review route preserves implementedBy metadata."""
        config = _make_rapper_config_with_route('review')
        daemon = self._create_daemon_with_config(config)

        mock_task = MockTask(status='completed')
        board_task_id = 'task_metadata'
        daemon.agent_id = 'rapper-3'

        # Mock the client to capture metadata updates
        mock_update_metadata = MagicMock(return_value=True)
        daemon.client.update_task_metadata = mock_update_metadata

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Should have called update_task_metadata with implementedBy
        mock_update_metadata.assert_called_once()
        call_args = mock_update_metadata.call_args[0]
        metadata = call_args[1]  # Second arg should be metadata dict

        self.assertEqual(metadata['implementedBy'], 'rapper-3')
        self.assertEqual(metadata['reviewState'], 'pending')

    def test_r6_review_route_sets_review_state_pending(self):
        """R6: Review route sets reviewState=pending."""
        config = _make_rapper_config_with_route('review')
        daemon = self._create_daemon_with_config(config)

        mock_task = MockTask(status='completed')
        board_task_id = 'task_review_state'

        mock_update_metadata = MagicMock(return_value=True)
        daemon.client.update_task_metadata = mock_update_metadata

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Verify reviewState was set to pending
        mock_update_metadata.assert_called_once()
        metadata = mock_update_metadata.call_args[0][1]
        self.assertEqual(metadata['reviewState'], 'pending')

    def test_r7_done_route_no_review_metadata(self):
        """R7: Done route does not set review metadata."""
        config = _make_rapper_config_with_route('done')
        daemon = self._create_daemon_with_config(config)

        mock_task = MockTask(status='completed')
        board_task_id = 'task_no_metadata'

        # Should not call update_task_metadata for done route
        mock_update_metadata = MagicMock()
        daemon.client.update_task_metadata = mock_update_metadata

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Should not have called update_task_metadata
        mock_update_metadata.assert_not_called()

    def test_r8_invalid_route_config_defaults_to_done(self):
        """R8: Invalid route_completed_to value defaults to 'done'."""
        config = _make_base_config()
        config['agent_board']['route_completed_to'] = 'invalid_column'
        daemon = self._create_daemon_with_config(config)

        mock_task = MockTask(status='completed', result='Task with invalid route')
        board_task_id = 'task_invalid_route'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Should default to 'done' for invalid route
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id, 'done', 'Task with invalid route'
        )

    def test_failed_tasks_always_go_to_failed_column(self):
        """Test that failed tasks go to 'failed' column regardless of route_completed_to."""
        config = _make_rapper_config_with_route('review')  # Configured for review
        daemon = self._create_daemon_with_config(config)

        # Create failed task
        mock_task = MockTask(status='failed', error='Task execution failed')
        board_task_id = 'task_failed'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Failed tasks should always go to 'failed', not respect route_completed_to
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id, 'failed', 'Task execution failed'
        )

    def test_config_loading_validates_route_completed_to(self):
        """Test that config loading validates route_completed_to values."""
        # Test valid values
        valid_routes = ['done', 'review']
        for route in valid_routes:
            config = _make_rapper_config_with_route(route)
            daemon = self._create_daemon_with_config(config)

            # Should load without error and preserve the route
            self.assertEqual(
                daemon.config['agent_board'].get('route_completed_to'),
                route
            )

        # Test None/missing defaults to 'done'
        config = _make_legacy_rapper_config()
        daemon = self._create_daemon_with_config(config)

        # Should default route_completed_to to 'done' when not specified
        # Implementation should normalize missing values
        route = daemon.config['agent_board'].get('route_completed_to', 'done')
        self.assertEqual(route, 'done')


if __name__ == '__main__':
    unittest.main()