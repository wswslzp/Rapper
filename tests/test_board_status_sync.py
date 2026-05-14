#!/usr/bin/env python3
"""
[TEST-SUPP-004] Verify Rapper completion automatically syncs Board status.

Test scenarios:
1. Rapper completes → Board task column becomes 'done'
2. Board has completion comment (duration, steps)
3. Rapper fails → Board column becomes 'failed'

Prerequisites: KANBAN-006 board_move_task / board_add_comment tools available

DISCOVERY: The functionality was ALREADY IMPLEMENTED in commit 6274ec3 (BUG-P14 fix).
Tests PASS confirming Board sync works correctly. Original requirement appears outdated.
"""

import json
import os
import tempfile
import time
import unittest
from unittest.mock import Mock, patch, MagicMock, call
import sys

# Add lib to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

from daemon import RapperDaemon, AgentBoardClient
from task_runner import Task, generate_task_id


class TestBoardStatusSync(unittest.TestCase):
    """Test automatic Board status synchronization when Rapper tasks complete."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create minimal config
        config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'poll_interval': 30,
                'webhook_port': 18789,
                'agent_id': 'test-agent'
            },
            'tasks': {
                'max_concurrent_tasks': 5
            }
        }

        with open(self.config_path, 'w') as f:
            import yaml
            yaml.dump(config, f)

        # Mock the tasks directory
        self.tasks_dir = os.path.join(self.temp_dir, 'tasks')
        os.makedirs(self.tasks_dir, exist_ok=True)

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_daemon_with_mocks(self, mock_client_class, mock_task_runner_class, task_list):
        """Helper to create daemon with consistent mocking."""
        # Setup mocks
        mock_client = Mock(spec=AgentBoardClient)
        mock_client_class.return_value = mock_client

        # Mock get_tasks to return different results for different calls
        # Call 1: todo tasks - return our test task
        # Call 2: ready tasks - return empty list
        # Call 3: doing tasks - return empty list (no conflicts)
        mock_client.get_tasks.side_effect = [
            task_list,  # todo tasks
            [],         # ready tasks
            []          # doing tasks (exclude from consideration)
        ]

        mock_client.claim_task.return_value = True
        mock_client.update_task_status.return_value = True
        mock_client.add_comment.return_value = True

        mock_task_runner = Mock()
        mock_task_runner_class.return_value = mock_task_runner

        # Create daemon with proper mocking
        with patch('os.path.expanduser', return_value=self.tasks_dir):
            daemon = RapperDaemon(self.config_path, 'test-agent')
            daemon._count_running_tasks = Mock(return_value=0)
            daemon._load_picked_tasks = Mock(return_value=set())  # No previously picked tasks

        return daemon, mock_client, mock_task_runner

    @patch('daemon.init_db')  # Mock database initialization
    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_task_completion_moves_board_to_done_column(self, mock_client_class, mock_task_runner_class, mock_init_db):
        """Test that successful task completion moves Board task to 'done' column."""
        task_list = [{
            'id': 'task_afd30123',
            'title': 'Test Implementation Task',
            'description': 'Implement feature X'
        }]

        daemon, mock_client, mock_task_runner = self._create_daemon_with_mocks(
            mock_client_class, mock_task_runner_class, task_list
        )

        # Mock successful task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'completed'
            task.result = 'Feature X implemented successfully'
            task.progress = [
                {'tool': 'read', 'params': {}},
                {'tool': 'edit', 'params': {}},
                {'tool': 'write', 'params': {}}
            ]
            task.structured_result = {
                'status': 'completed',
                'output_path': 'src/feature_x.py',
                'pr_url': 'https://github.com/user/repo/pull/123'
            }

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # CRITICAL: Verify Board task status was updated to 'done'
        mock_client.update_task_status.assert_called_once_with(
            'task_afd30123',
            'done',
            'Feature X implemented successfully'
        )

    @patch('daemon.init_db')
    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_task_failure_moves_board_to_failed_column(self, mock_client_class, mock_task_runner_class, mock_init_db):
        """Test that task failure moves Board task to 'failed' column."""
        task_list = [{
            'id': 'task_afd30123',
            'title': 'Failing Task',
            'description': 'This task will fail'
        }]

        daemon, mock_client, mock_task_runner = self._create_daemon_with_mocks(
            mock_client_class, mock_task_runner_class, task_list
        )

        # Mock failed task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'failed'
            task.error = 'Build failed: compilation error in src/main.py'
            task.progress = [
                {'tool': 'read', 'params': {}},
                {'tool': 'bash', 'params': {'command': 'npm test'}}
            ]

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # CRITICAL: Verify Board task status was updated to 'failed'
        mock_client.update_task_status.assert_called_once_with(
            'task_afd30123',
            'failed',
            'Build failed: compilation error in src/main.py'
        )

    @patch('daemon.init_db')
    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_completion_comment_includes_duration_and_steps(self, mock_client_class, mock_task_runner_class, mock_init_db):
        """Test that completion comments include execution time and step count."""
        task_list = [{
            'id': 'task_afd30123',
            'title': 'Timed Task',
            'description': 'Task with timing info'
        }]

        daemon, mock_client, mock_task_runner = self._create_daemon_with_mocks(
            mock_client_class, mock_task_runner_class, task_list
        )

        # Mock successful task execution with specific timing
        def mock_run_task_sync(task, **kwargs):
            task.status = 'completed'
            task.result = 'Task completed successfully'
            task.progress = [
                {'tool': 'read', 'params': {'file': 'config.json'}},
                {'tool': 'edit', 'params': {'file': 'src/main.py'}},
                {'tool': 'bash', 'params': {'command': 'npm test'}},
                {'tool': 'write', 'params': {'file': 'output.log'}}
            ]
            task.structured_result = {
                'status': 'completed',
                'output_path': 'output.log'
            }

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Verify status update called
        mock_client.update_task_status.assert_called_once()

        # CRITICAL: Verify completion comment was posted with duration and steps
        mock_client.add_comment.assert_called_once()
        comment_call = mock_client.add_comment.call_args

        # Check comment parameters
        self.assertEqual(comment_call[0][0], 'task_afd30123')  # task_id
        self.assertEqual(comment_call[0][1], 'test-agent')     # author

        # Check comment content includes required elements
        comment_text = comment_call[0][2]
        self.assertIn('✅ 任务完成', comment_text, "Comment should have completion indicator")
        self.assertIn('耗时：', comment_text, "Comment should include duration")
        self.assertIn('步数：4', comment_text, "Comment should include step count")
        self.assertIn('输出：output.log', comment_text, "Comment should include output path")

    @patch('daemon.init_db')
    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_failure_comment_includes_duration_and_error_details(self, mock_client_class, mock_task_runner_class, mock_init_db):
        """Test that failure comments include execution time and error details."""
        task_list = [{
            'id': 'task_afd30123',
            'title': 'Error Task',
            'description': 'Task that will encounter errors'
        }]

        daemon, mock_client, mock_task_runner = self._create_daemon_with_mocks(
            mock_client_class, mock_task_runner_class, task_list
        )

        # Mock failed task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'failed'
            task.error = 'TypeError: cannot read property of undefined'
            task.progress = [
                {'tool': 'read', 'params': {}},
                {'tool': 'bash', 'params': {'command': 'node script.js'}}
            ]

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Verify status update called
        mock_client.update_task_status.assert_called_once()

        # CRITICAL: Verify failure comment was posted with duration and error
        mock_client.add_comment.assert_called_once()
        comment_call = mock_client.add_comment.call_args

        # Check comment parameters
        self.assertEqual(comment_call[0][0], 'task_afd30123')  # task_id
        self.assertEqual(comment_call[0][1], 'test-agent')     # author

        # Check comment content includes required elements
        comment_text = comment_call[0][2]
        self.assertIn('❌ 任务失败', comment_text, "Comment should have failure indicator")
        self.assertIn('耗时：', comment_text, "Comment should include duration")
        self.assertIn('步数：2', comment_text, "Comment should include step count")
        self.assertIn('原因：TypeError: cannot read property', comment_text, "Comment should include error details")

    @patch('daemon.init_db')
    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_board_sync_failure_does_not_crash_daemon(self, mock_client_class, mock_task_runner_class, mock_init_db):
        """Test that Board sync failures don't crash the daemon."""
        task_list = [{
            'id': 'task_afd30123',
            'title': 'Sync Test Task',
            'description': 'Task to test sync failure handling'
        }]

        daemon, mock_client, mock_task_runner = self._create_daemon_with_mocks(
            mock_client_class, mock_task_runner_class, task_list
        )

        # SIMULATE BOARD API FAILURE
        mock_client.update_task_status.return_value = False
        mock_client.add_comment.side_effect = Exception("Network timeout")

        # Mock successful task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'completed'
            task.result = 'Task completed successfully'
            task.progress = [{'tool': 'bash', 'params': {}}]

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle - should not crash despite Board API failures
        try:
            daemon._poll_and_execute_tasks()
            daemon_survived = True
        except Exception as e:
            daemon_survived = False

        self.assertTrue(daemon_survived, "Daemon should survive Board API failures")

        # Verify attempted sync calls were made despite failures
        mock_client.update_task_status.assert_called_once()
        mock_client.add_comment.assert_called_once()


if __name__ == '__main__':
    # Run tests with verbose output
    unittest.main(verbosity=2)