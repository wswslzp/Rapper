#!/usr/bin/env python3
"""
Test daemon final comment functionality (BUG-P14 fix).

Tests that the daemon posts final status comments to Board tasks
when tasks complete successfully or fail.
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


class TestDaemonFinalComments(unittest.TestCase):
    """Test daemon final comment functionality for BUG-P14."""

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

    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_completion_comment_posted_on_success(self, mock_client_class, mock_task_runner_class):
        """Test that final completion comment is posted when task succeeds."""
        # Setup mocks
        mock_client = Mock(spec=AgentBoardClient)
        mock_client_class.return_value = mock_client
        mock_client.get_tasks.return_value = [{
            'id': 'board-task-123',
            'title': 'Test Task',
            'description': 'Test task description'
        }]
        mock_client.claim_task.return_value = True
        mock_client.update_task_status.return_value = True
        mock_client.add_comment.return_value = True

        mock_task_runner = Mock()
        mock_task_runner_class.return_value = mock_task_runner

        # Create daemon
        with patch('os.path.expanduser', return_value=self.tasks_dir):
            daemon = RapperDaemon(self.config_path, 'test-agent')
            daemon._count_running_tasks = Mock(return_value=0)  # No running tasks

        # Mock successful task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'completed'
            task.result = 'Task completed successfully'
            task.progress = [
                {'tool': 'read', 'params': {}},
                {'tool': 'edit', 'params': {}},
                {'tool': 'write', 'params': {}}
            ]
            task.structured_result = {
                'status': 'completed',
                'output_path': '/tmp/output.txt',
                'pr_url': 'https://github.com/user/repo/pull/123'
            }

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Verify final comment was posted
        mock_client.add_comment.assert_called_once()
        call_args = mock_client.add_comment.call_args

        # Check comment parameters
        self.assertEqual(call_args[0][0], 'board-task-123')  # task_id
        self.assertEqual(call_args[0][1], 'test-agent')      # author

        # Check comment content
        comment_text = call_args[0][2]
        self.assertIn('✅ 任务完成', comment_text)
        self.assertIn('耗时：', comment_text)
        self.assertIn('步数：3', comment_text)  # 3 progress items
        self.assertIn('输出：/tmp/output.txt', comment_text)

    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_failure_comment_posted_on_error(self, mock_client_class, mock_task_runner_class):
        """Test that final failure comment is posted when task fails."""
        # Setup mocks
        mock_client = Mock(spec=AgentBoardClient)
        mock_client_class.return_value = mock_client
        mock_client.get_tasks.return_value = [{
            'id': 'board-task-456',
            'title': 'Failing Task',
            'description': 'This task will fail'
        }]
        mock_client.claim_task.return_value = True
        mock_client.update_task_status.return_value = True
        mock_client.add_comment.return_value = True

        mock_task_runner = Mock()
        mock_task_runner_class.return_value = mock_task_runner

        # Create daemon
        with patch('os.path.expanduser', return_value=self.tasks_dir):
            daemon = RapperDaemon(self.config_path, 'test-agent')
            daemon._count_running_tasks = Mock(return_value=0)

        # Mock failed task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'failed'
            task.error = 'Something went wrong during execution'
            task.progress = [
                {'tool': 'read', 'params': {}},
                {'tool': 'edit', 'params': {}}
            ]

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Verify final comment was posted
        mock_client.add_comment.assert_called_once()
        call_args = mock_client.add_comment.call_args

        # Check comment parameters
        self.assertEqual(call_args[0][0], 'board-task-456')  # task_id
        self.assertEqual(call_args[0][1], 'test-agent')      # author

        # Check comment content
        comment_text = call_args[0][2]
        self.assertIn('❌ 任务失败', comment_text)
        self.assertIn('耗时：', comment_text)
        self.assertIn('步数：2', comment_text)  # 2 progress items
        self.assertIn('原因：Something went wrong', comment_text)

    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_exception_comment_posted_on_execution_error(self, mock_client_class, mock_task_runner_class):
        """Test that final comment is posted when task execution throws exception."""
        # Setup mocks
        mock_client = Mock(spec=AgentBoardClient)
        mock_client_class.return_value = mock_client
        mock_client.get_tasks.return_value = [{
            'id': 'board-task-789',
            'title': 'Exception Task',
            'description': 'This task will raise an exception'
        }]
        mock_client.claim_task.return_value = True
        mock_client.update_task_status.return_value = True
        mock_client.add_comment.return_value = True

        mock_task_runner = Mock()
        mock_task_runner_class.return_value = mock_task_runner

        # Create daemon
        with patch('os.path.expanduser', return_value=self.tasks_dir):
            daemon = RapperDaemon(self.config_path, 'test-agent')
            daemon._count_running_tasks = Mock(return_value=0)

        # Mock task execution exception
        def mock_run_task_sync(task, **kwargs):
            task.progress = [{'tool': 'read', 'params': {}}]  # One step before crash
            raise RuntimeError("Unexpected execution error")

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle
        daemon._poll_and_execute_tasks()

        # Verify final comment was posted
        mock_client.add_comment.assert_called_once()
        call_args = mock_client.add_comment.call_args

        # Check comment parameters
        self.assertEqual(call_args[0][0], 'board-task-789')  # task_id
        self.assertEqual(call_args[0][1], 'test-agent')      # author

        # Check comment content
        comment_text = call_args[0][2]
        self.assertIn('❌ 任务失败', comment_text)
        self.assertIn('耗时：', comment_text)
        self.assertIn('步数：1', comment_text)  # 1 progress item
        self.assertIn('原因：Unexpected execution error', comment_text)

    @patch('daemon.TaskRunner')
    @patch('daemon.AgentBoardClient')
    def test_comment_failure_does_not_crash_daemon(self, mock_client_class, mock_task_runner_class):
        """Test that comment posting failure doesn't crash the daemon."""
        # Setup mocks
        mock_client = Mock(spec=AgentBoardClient)
        mock_client_class.return_value = mock_client
        mock_client.get_tasks.return_value = [{
            'id': 'board-task-999',
            'title': 'Test Task',
            'description': 'Test task description'
        }]
        mock_client.claim_task.return_value = True
        mock_client.update_task_status.return_value = True
        mock_client.add_comment.side_effect = Exception("Network error")  # Comment fails

        mock_task_runner = Mock()
        mock_task_runner_class.return_value = mock_task_runner

        # Create daemon
        with patch('os.path.expanduser', return_value=self.tasks_dir):
            daemon = RapperDaemon(self.config_path, 'test-agent')
            daemon._count_running_tasks = Mock(return_value=0)

        # Mock successful task execution
        def mock_run_task_sync(task, **kwargs):
            task.status = 'completed'
            task.result = 'Task completed successfully'
            task.progress = []

        mock_task_runner._run_task_sync = mock_run_task_sync

        # Execute one poll cycle - should not crash despite comment failure
        try:
            daemon._poll_and_execute_tasks()
            # If we reach this point, the daemon didn't crash
            success = True
        except Exception:
            success = False

        self.assertTrue(success, "Daemon should not crash when comment posting fails")
        # Status should still be updated even if comment fails
        mock_client.update_task_status.assert_called_once_with('board-task-999', 'done', 'Task completed successfully')


if __name__ == '__main__':
    unittest.main()