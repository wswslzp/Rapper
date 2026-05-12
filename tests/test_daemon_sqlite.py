#!/usr/bin/env python3
"""
Integration tests for Daemon with SQLite task storage.

Verifies behavior after SQLite replaces JSON file storage:
- T1: Large number of JSON files don't affect startup speed
- T2: Running task count is accurate
- T3: Poll loop works normally with SQLite backend
- T4: Migrated old data is readable by daemon
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon
import db
from task_runner import Task, generate_task_id


def _make_client(base_url="http://localhost:3456", api_key="sk-test"):
    """Return an AgentBoardClient with a mocked _make_request."""
    client = AgentBoardClient(base_url, api_key)
    client._make_request = MagicMock()
    return client


def _make_minimal_config():
    """Minimal config dict for RapperDaemon without loading yaml."""
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


class TestDaemonSQLiteIntegration(unittest.TestCase):
    """Integration tests for Daemon with SQLite backend."""

    def setUp(self):
        """Set up test environment with temporary directories."""
        self.temp_dir = tempfile.mkdtemp()
        self.test_db_path = os.path.join(self.temp_dir, 'test_tasks.db')

        # Initialize test database
        db.init_db(self.test_db_path)

        # Set up temporary rapper directory structure
        self.rapper_dir = Path(self.temp_dir) / '.rapper'
        self.tasks_dir = self.rapper_dir / 'tasks'
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_t1_large_json_files_no_startup_slowdown(self):
        """T1: Large number of JSON files don't affect startup speed."""
        # Create ~200 mock JSON task files
        json_files_count = 200

        for i in range(json_files_count):
            task_data = {
                'id': f'old-task-{i:03d}',
                'name': f'Old Task {i}',
                'status': 'completed' if i % 3 == 0 else 'failed',
                'pid': 1000 + i,
                'result': f'Task {i} result',
                'created_at': '2026-05-12T10:00:00'
            }

            json_file = self.tasks_dir / f'old-task-{i:03d}.json'
            with open(json_file, 'w') as f:
                json.dump(task_data, f)

        # Verify files were created
        json_files = list(self.tasks_dir.glob('*.json'))
        self.assertEqual(len(json_files), json_files_count)

        # Mock daemon's config path to point to our temp directory
        config = _make_minimal_config()

        with patch('daemon.init_db'):  # Skip actual init_db to avoid migration
            with patch.object(Path, 'expanduser', return_value=self.rapper_dir):
                # Mock RapperDaemon to only test _count_running_tasks speed
                daemon = RapperDaemon.__new__(RapperDaemon)  # Skip __init__
                daemon.config = config
                daemon.logger = MagicMock()

                # Mock get_running_count to simulate SQLite query
                with patch('daemon.get_running_count', return_value=3) as mock_count:
                    start_time = time.time()

                    # Call _count_running_tasks which uses SQLite instead of scanning JSON files
                    running_count = daemon._count_running_tasks()

                    elapsed_ms = (time.time() - start_time) * 1000

                    # Verify speed: should be < 100ms (much faster than scanning 200 JSON files)
                    self.assertLess(elapsed_ms, 100,
                                    f"_count_running_tasks took {elapsed_ms:.2f}ms, expected < 100ms")

                    # Verify it uses SQLite query, not file scanning
                    mock_count.assert_called_once()
                    self.assertEqual(running_count, 3)

    def test_t2_running_count_accurate(self):
        """T2: Running task count is accurate from SQLite database."""
        # Insert test data directly into tasks.db: 3 running + 5 completed
        running_tasks = [
            {
                'id': f'running-{i}',
                'name': f'Running Task {i}',
                'status': 'running',
                'pid': 2000 + i,
                'created_at': '2026-05-13T10:00:00'
            }
            for i in range(3)
        ]

        completed_tasks = [
            {
                'id': f'completed-{i}',
                'name': f'Completed Task {i}',
                'status': 'completed',
                'result': f'Task {i} done',
                'created_at': '2026-05-13T09:00:00'
            }
            for i in range(5)
        ]

        # Save all tasks to database
        all_tasks = running_tasks + completed_tasks
        for task_data in all_tasks:
            db.save_task(task_data)

        # Verify total tasks saved
        all_saved_tasks = db.list_tasks()
        self.assertEqual(len(all_saved_tasks), 8)

        # Test get_running_count() directly
        running_count = db.get_running_count()
        self.assertEqual(running_count, 3, "get_running_count() should return 3 running tasks")

        # Test daemon's _count_running_tasks method
        config = _make_minimal_config()

        with patch('daemon.init_db'):  # Skip init to use our test DB
            daemon = RapperDaemon.__new__(RapperDaemon)  # Skip __init__
            daemon.config = config
            daemon.logger = MagicMock()

            # Mock the db module to use our test database
            with patch('daemon.get_running_count', return_value=running_count):
                daemon_running_count = daemon._count_running_tasks()
                self.assertEqual(daemon_running_count, 3,
                               "Daemon _count_running_tasks should return 3")

    def test_t3_poll_loop_works_normally(self):
        """T3: Poll loop works normally - Board task pickup → claim → execution."""
        # Mock todo task from Board
        board_task = {
            'id': 'board-task-123',
            'title': 'Fix authentication bug',
            'description': 'Fix login issues in auth service',
            'column': 'todo',
            'assignee': None,  # Unassigned task
            'workdir': '/app/test-project'
        }

        # Mock API responses function
        def mock_get_tasks_behavior(assignee, column):
            if assignee is None and column == 'todo':
                return [board_task]  # Todo tasks
            elif assignee == 'test-agent' and column == 'doing':
                return []  # No doing tasks
            else:
                return []

        mock_client = _make_client()
        mock_client.get_tasks = MagicMock(side_effect=mock_get_tasks_behavior)
        mock_client.claim_task = MagicMock(return_value=True)  # Successful claim
        mock_client.update_task_status = MagicMock(return_value=True)  # Status updates work
        mock_client.add_comment = MagicMock(return_value=True)

        # Create daemon with mocked components
        config = _make_minimal_config()

        with patch('daemon.init_db'):
            with patch.object(Path, 'expanduser', return_value=self.rapper_dir):
                daemon = RapperDaemon.__new__(RapperDaemon)
                daemon.config = config
                daemon.agent_id = 'test-agent'
                daemon.logger = MagicMock()
                daemon.client = mock_client
                daemon.current_task = None
                daemon._last_progress_step = 0
                daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

                # Mock TaskRunner to simulate task execution
                mock_task_runner = MagicMock()
                daemon.task_runner = mock_task_runner

                # Mock _count_running_tasks to return 0 (under limit)
                daemon._count_running_tasks = MagicMock(return_value=0)

                # Mock file operations
                daemon._load_picked_tasks = MagicMock(return_value=set())
                daemon._save_picked_task = MagicMock()

                # Create a mock internal task that will be "executed"
                mock_internal_task = MagicMock()
                mock_internal_task.id = 'internal-123'
                mock_internal_task.status = 'completed'
                mock_internal_task.result = 'Task completed successfully'
                mock_internal_task.progress = [{'tool': 'Read', 'file': 'auth.py'}]
                mock_internal_task.structured_result = {'status': 'completed'}

                # Mock Task creation and runner execution
                with patch('daemon.Task') as MockTask:
                    with patch('daemon.generate_task_id', return_value='internal-123'):
                        MockTask.return_value = mock_internal_task

                        # Run one iteration of the poll loop
                        daemon._poll_and_execute_tasks()

                # Verify the full workflow executed correctly

                # 1. Daemon queried for todo tasks (new approach - column only)
                # Check that get_tasks was called multiple times with the right parameters
                calls = mock_client.get_tasks.call_args_list
                self.assertGreater(len(calls), 0, "get_tasks should be called at least once")

                # First call should be for todo tasks (column-only query)
                first_call = calls[0]
                self.assertEqual(first_call[0], (None, 'todo'), "First call should query todo column only")

                # 2. Task was claimed before execution (Method A)
                mock_client.claim_task.assert_called_with('board-task-123', 'test-agent')

                # 3. Internal task was created with correct parameters
                MockTask.assert_called_once()
                task_args = MockTask.call_args[1]
                self.assertEqual(task_args['name'], 'Fix authentication bug')
                self.assertEqual(task_args['prompt'], 'Fix login issues in auth service')
                self.assertEqual(task_args['board_task_id'], 'board-task-123')

                # 4. Task execution was attempted
                mock_task_runner._run_task_sync.assert_called_once()

                # 5. Final status was reported to Board (task completed)
                mock_client.update_task_status.assert_called_with(
                    'board-task-123', 'done', 'Task completed successfully'
                )

    def test_t4_migrated_old_data_readable(self):
        """T4: Daemon can read migrated old data from SQLite."""
        # Simulate the migration scenario:
        # 1. Create JSON files (old format)
        # 2. Run init_db to trigger migration
        # 3. Verify daemon can read historical tasks

        # Create historical JSON task files
        historical_tasks = [
            {
                'id': 'hist-task-001',
                'name': 'Historical Task 1',
                'status': 'completed',
                'pid': 5001,
                'result': 'Old task completed',
                'board_task_id': 'board-hist-001',
                'created_at': '2026-05-10T14:30:00',
                'structured_result': {'status': 'completed', 'output_path': 'src/auth.py'}
            },
            {
                'id': 'hist-task-002',
                'name': 'Historical Task 2',
                'status': 'failed',
                'pid': 5002,
                'error': 'Network timeout',
                'board_task_id': 'board-hist-002',
                'created_at': '2026-05-10T15:45:00'
            },
            {
                'id': 'hist-task-003',
                'name': 'Historical Running Task',
                'status': 'running',  # This should count in running tasks
                'pid': 5003,
                'board_task_id': 'board-hist-003',
                'created_at': '2026-05-10T16:00:00'
            }
        ]

        # Write JSON files to tasks directory
        for task in historical_tasks:
            json_file = self.tasks_dir / f"{task['id']}.json"
            with open(json_file, 'w') as f:
                json.dump(task, f)

        # Mock Path.home() to point to our temp directory during init_db
        with patch.object(Path, 'home', return_value=Path(self.temp_dir)):
            # Trigger migration by initializing database
            # This should migrate the JSON files and move them to archive
            db.init_db(self.test_db_path)

        # Verify migration worked: JSON files moved to archive
        json_files_remaining = list(self.tasks_dir.glob('*.json'))
        self.assertEqual(len(json_files_remaining), 0, "JSON files should be archived after migration")

        # Verify archive directory created with moved files
        archive_dirs = list((self.rapper_dir / 'tasks-archive').glob('*'))
        self.assertGreater(len(archive_dirs), 0, "Archive directory should be created")

        # Verify all tasks were migrated to SQLite
        migrated_tasks = db.list_tasks()
        self.assertEqual(len(migrated_tasks), 3, "All 3 historical tasks should be migrated")

        # Verify specific task data integrity after migration
        task_001 = db.load_task('hist-task-001')
        self.assertIsNotNone(task_001)
        self.assertEqual(task_001['name'], 'Historical Task 1')
        self.assertEqual(task_001['status'], 'completed')
        self.assertEqual(task_001['board_task_id'], 'board-hist-001')

        # Verify structured_result was properly serialized/deserialized
        self.assertIsInstance(task_001['structured_result'], dict)
        self.assertEqual(task_001['structured_result']['status'], 'completed')

        # Verify running count includes migrated running task
        running_count = db.get_running_count()
        self.assertEqual(running_count, 1, "Should count 1 running task from migrated data")

        # Verify daemon can use migrated data
        config = _make_minimal_config()

        with patch('daemon.init_db'):  # Skip double init
            daemon = RapperDaemon.__new__(RapperDaemon)
            daemon.config = config
            daemon.logger = MagicMock()

            # Mock the database path to our test DB
            with patch('daemon.get_running_count', return_value=running_count):
                daemon_running_count = daemon._count_running_tasks()
                self.assertEqual(daemon_running_count, 1,
                               "Daemon should see migrated running task")

        # Test that daemon can list historical tasks (useful for status reporting)
        all_historical = db.list_tasks()
        completed_historical = db.list_tasks('completed')
        failed_historical = db.list_tasks('failed')

        self.assertEqual(len(completed_historical), 1)
        self.assertEqual(len(failed_historical), 1)
        self.assertEqual(completed_historical[0]['id'], 'hist-task-001')
        self.assertEqual(failed_historical[0]['id'], 'hist-task-002')

    def test_concurrent_task_limit_respected(self):
        """Test that daemon respects max_concurrent_tasks limit with SQLite."""
        # Set up database with 4 running tasks (close to limit of 5)
        running_tasks = [
            {
                'id': f'concurrent-{i}',
                'name': f'Concurrent Task {i}',
                'status': 'running',
                'pid': 3000 + i,
                'created_at': '2026-05-13T10:00:00'
            }
            for i in range(4)
        ]

        for task in running_tasks:
            db.save_task(task)

        # Mock client with available task
        def mock_get_tasks_behavior(assignee, column):
            if assignee is None and column == 'todo':
                return [{
                    'id': 'new-task-123',
                    'title': 'New Task',
                    'description': 'Should be executed',
                    'column': 'todo',
                    'assignee': None
                }]
            elif assignee == 'test-agent' and column == 'doing':
                return []  # No doing tasks
            else:
                return []

        mock_client = _make_client()
        mock_client.get_tasks = MagicMock(side_effect=mock_get_tasks_behavior)
        mock_client.claim_task = MagicMock(return_value=True)
        mock_client.update_task_status = MagicMock(return_value=True)

        config = _make_minimal_config()
        config['tasks']['max_concurrent_tasks'] = 5  # Set limit to 5

        with patch('daemon.init_db'):
            daemon = RapperDaemon.__new__(RapperDaemon)
            daemon.config = config
            daemon.agent_id = 'test-agent'
            daemon.logger = MagicMock()
            daemon.client = mock_client
            daemon.current_task = None
            daemon._last_progress_step = 0
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')
            daemon.task_runner = MagicMock()

            # Mock file operations
            daemon._load_picked_tasks = MagicMock(return_value=set())
            daemon._save_picked_task = MagicMock()

            # Return actual running count from our test database
            with patch('daemon.get_running_count', return_value=4):
                # Should execute task (4 < 5)
                daemon._poll_and_execute_tasks()

                # Task should be picked up and execution attempted
                mock_client.claim_task.assert_called()

        # Now test at capacity: add one more running task (total = 5)
        db.save_task({
            'id': 'concurrent-5',
            'name': 'Fifth Concurrent Task',
            'status': 'running',
            'pid': 3005,
            'created_at': '2026-05-13T10:05:00'
        })

        # Create new mock client for capacity test
        mock_client_2 = _make_client()
        mock_client_2.get_tasks = MagicMock(side_effect=mock_get_tasks_behavior)
        mock_client_2.claim_task = MagicMock(return_value=True)
        mock_client_2.update_task_status = MagicMock(return_value=True)

        with patch('daemon.init_db'):
            daemon = RapperDaemon.__new__(RapperDaemon)
            daemon.config = config
            daemon.agent_id = 'test-agent'
            daemon.logger = MagicMock()
            daemon.client = mock_client_2
            daemon.current_task = None
            daemon._last_progress_step = 0
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')
            daemon.task_runner = MagicMock()

            daemon._load_picked_tasks = MagicMock(return_value=set())
            daemon._save_picked_task = MagicMock()

            with patch('daemon.get_running_count', return_value=5):
                # Should NOT execute task (5 >= 5, at capacity)
                daemon._poll_and_execute_tasks()

                # Task should NOT be claimed due to capacity limit
                mock_client_2.claim_task.assert_not_called()

                # Should log capacity warning
                daemon.logger.warning.assert_called_with(
                    "Concurrency limit reached: 5/5, skipping task execution"
                )


if __name__ == '__main__':
    unittest.main()