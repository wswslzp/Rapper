#!/usr/bin/env python3
"""
Test for AC-10: route_completed_to=review functionality.

This test specifically validates the requirements from AC-10:
- When agent_board.route_completed_to=review is set, completed tasks should go to 'review' column
- implementedBy should be set to the rapper's agent_id
- reviewState should be set to 'pending'

This is a RED test - it will fail until IMPL-02 is implemented.

Related:
- requirements.md v1.1 AC-10
- design.md v2.1 §4.2 Rapper State Machine
- design.md v2.1 §2.2 Task Field Extensions
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon
from task_runner import Task


class TestAC10RouteCompletedToReview(unittest.TestCase):
    """Test AC-10: route_completed_to=review functionality."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_rapper_with_review_config(self):
        """Create a rapper daemon configured with route_completed_to=review."""
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'rapper-1',
                'poll_interval': 30,
                'webhook_port': 19999,
                'role': 'rapper',
                'poll_columns': ['todo', 'ready'],
                'route_completed_to': 'review'  # KEY: This is what we're testing
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config

            daemon = RapperDaemon(self.config_path, 'rapper-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods that we need to verify
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.update_task_metadata = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock task runner to avoid actually running Claude
            daemon.task_runner = MagicMock()
            daemon.task_runner._run_task_sync = MagicMock()

            return daemon

    def _create_completed_task(self):
        """Create a properly structured completed Task object."""
        task = Task(
            id='test_task_123',
            name='test-task',
            prompt='Test task for AC-10',
            workdir='/test/workdir',
            status='completed',
            result='Task completed successfully',
            structured_result={'status': 'completed', 'output_path': 'output.txt'},
            progress=[]
        )
        return task

    def test_ac10_route_completed_to_review_updates_column(self):
        """
        AC-10 Test 1: When route_completed_to=review, completed task moves to 'review' column.

        This test verifies the core routing behavior - that the daemon respects the
        route_completed_to config and sends completed tasks to the review column
        instead of the default 'done' column.

        Expected to FAIL until IMPL-02 implements the routing logic in daemon.py.
        """
        daemon = self._create_rapper_with_review_config()
        task = self._create_completed_task()
        board_task_id = 'task_ac10_review_column'

        # Execute the background task completion path
        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, task)

        # VERIFY: Task should be moved to 'review' column, not 'done'
        daemon.client.update_task_status.assert_called_once()
        call_args = daemon.client.update_task_status.call_args[0]

        self.assertEqual(call_args[0], board_task_id, "Task ID should match")
        self.assertEqual(call_args[1], 'review', "Column should be 'review' when route_completed_to=review")
        self.assertIn('Task completed successfully', call_args[2], "Status message should be preserved")

    def test_ac10_route_completed_to_review_sets_implemented_by(self):
        """
        AC-10 Test 2: When route_completed_to=review, implementedBy should be set to rapper agent_id.

        This test verifies that when a task is routed to review, the implementedBy
        metadata field is set to track who implemented the task. This is required
        for the REJECT → todo assignee restoration flow.

        Expected to FAIL until IMPL-02 implements metadata updates in daemon.py.
        """
        daemon = self._create_rapper_with_review_config()
        task = self._create_completed_task()
        board_task_id = 'task_ac10_implemented_by'

        # Set the agent_id explicitly to test implementedBy tracking
        daemon.agent_id = 'rapper-1'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, task)

        # VERIFY: implementedBy should be set to rapper agent_id
        daemon.client.update_task_metadata.assert_called_once()
        call_args = daemon.client.update_task_metadata.call_args[0]

        self.assertEqual(call_args[0], board_task_id, "Task ID should match")

        metadata = call_args[1]
        self.assertEqual(metadata['implementedBy'], 'rapper-1',
                        "implementedBy should be set to the rapper's agent_id")

    def test_ac10_route_completed_to_review_sets_review_state_pending(self):
        """
        AC-10 Test 3: When route_completed_to=review, reviewState should be set to 'pending'.

        This test verifies that the reviewState metadata is properly initialized
        to 'pending' when a task enters the review column. This is required for
        the reviewer state machine to track review progress.

        Expected to FAIL until IMPL-02 implements metadata updates in daemon.py.
        """
        daemon = self._create_rapper_with_review_config()
        task = self._create_completed_task()
        board_task_id = 'task_ac10_review_state'

        daemon.agent_id = 'rapper-1'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, task)

        # VERIFY: reviewState should be set to 'pending'
        daemon.client.update_task_metadata.assert_called_once()
        call_args = daemon.client.update_task_metadata.call_args[0]

        metadata = call_args[1]
        self.assertEqual(metadata['reviewState'], 'pending',
                        "reviewState should be set to 'pending' for tasks entering review")

    def test_ac10_route_completed_to_review_full_metadata_set(self):
        """
        AC-10 Test 4: Complete metadata verification for route_completed_to=review.

        This test verifies all metadata fields are properly set when routing to review:
        - implementedBy = rapper agent_id
        - reviewState = 'pending'
        - Column update to 'review'
        - Completion comment still added

        Expected to FAIL until IMPL-02 implements both routing and metadata logic.
        """
        daemon = self._create_rapper_with_review_config()
        task = self._create_completed_task()
        board_task_id = 'task_ac10_full_metadata'

        daemon.agent_id = 'rapper-1'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, task)

        # VERIFY Column Update
        daemon.client.update_task_status.assert_called_once()
        status_call = daemon.client.update_task_status.call_args[0]
        self.assertEqual(status_call[1], 'review', "Task should be moved to review column")

        # VERIFY Metadata Update
        daemon.client.update_task_metadata.assert_called_once()
        metadata_call = daemon.client.update_task_metadata.call_args[0]
        metadata = metadata_call[1]

        self.assertEqual(metadata['implementedBy'], 'rapper-1')
        self.assertEqual(metadata['reviewState'], 'pending')

        # VERIFY Completion Comment (should still be added)
        daemon.client.add_comment.assert_called()
        comment_calls = daemon.client.add_comment.call_args_list
        self.assertTrue(any('✅ 任务完成' in str(call) or 'Task completed' in str(call) for call in comment_calls),
                        "Completion comment should still be added when routing to review")

    def test_ac10_default_route_still_goes_to_done(self):
        """
        AC-10 Test 5: Verify backward compatibility - default route_completed_to should still go to 'done'.

        This test ensures we don't break existing behavior. When route_completed_to is not set
        or is set to 'done', tasks should still go to the done column as before.

        This test should PASS even before IMPL-02, as it tests existing behavior.
        """
        # Create config without route_completed_to (should default to 'done')
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-test',
                'agent_id': 'rapper-1',
                'poll_interval': 30,
                'webhook_port': 19999,
                'role': 'rapper',
                'poll_columns': ['todo', 'ready'],
                # No route_completed_to - should default to 'done'
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config

            daemon = RapperDaemon(self.config_path, 'rapper-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)
            daemon.task_runner = MagicMock()

        task = self._create_completed_task()
        board_task_id = 'task_ac10_default_done'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, task)

        # VERIFY: Should still go to 'done' by default
        daemon.client.update_task_status.assert_called_once()
        call_args = daemon.client.update_task_status.call_args[0]
        self.assertEqual(call_args[1], 'done', "Default routing should still go to 'done'")


if __name__ == '__main__':
    # Run with verbose output to show which specific tests are failing
    unittest.main(verbosity=2)