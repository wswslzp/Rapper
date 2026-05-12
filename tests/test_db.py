import pytest
import sqlite3
import tempfile
import json
import pathlib
import datetime
import sys
import os
from unittest.mock import patch

# Add lib directory to path to import db module
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'lib'))
import db

def test_init_db_creates_table():
    """Test that init_db creates database file and tasks table."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, 'test.db')

        # Initialize database
        db.init_db(db_path)

        # Verify database file exists
        assert os.path.exists(db_path)

        # Verify tasks table exists
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        table_exists = cursor.fetchone() is not None
        conn.close()

        assert table_exists


def test_migration_from_json():
    """Test migration from JSON files to SQLite database."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Set up temporary home directory structure
        temp_home = pathlib.Path(temp_dir)
        rapper_dir = temp_home / '.rapper'
        tasks_dir = rapper_dir / 'tasks'
        tasks_dir.mkdir(parents=True)

        # Create 2 sample JSON task files
        task1_data = {
            'id': 'task-1',
            'name': 'Test Task 1',
            'status': 'completed',
            'pid': 12345,
            'result': 'Success',
            'created_at': '2026-05-13T10:00:00'
        }

        task2_data = {
            'id': 'task-2',
            'name': 'Test Task 2',
            'status': 'failed',
            'pid': 67890,
            'error': 'Error message',
            'structured_result': {'status': 'failed', 'errors': ['Test error']},
            'created_at': '2026-05-13T11:00:00'
        }

        # Write JSON files
        with open(tasks_dir / 'task-1.json', 'w') as f:
            json.dump(task1_data, f)
        with open(tasks_dir / 'task-2.json', 'w') as f:
            json.dump(task2_data, f)

        # Mock Path.home() to return our temp directory
        with patch.object(pathlib.Path, 'home', return_value=temp_home):
            # Initialize database (should trigger migration)
            db_path = tasks_dir.parent / 'tasks.db'
            db.init_db(str(db_path))

            # Verify 2 rows were imported
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM tasks")
            count = cursor.fetchone()[0]
            conn.close()

            assert count == 2

            # Verify JSON files were moved to archive
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            archive_dir = tasks_dir.parent / 'tasks-archive' / today
            assert archive_dir.exists()
            assert (archive_dir / 'task-1.json').exists()
            assert (archive_dir / 'task-2.json').exists()
            assert not (tasks_dir / 'task-1.json').exists()
            assert not (tasks_dir / 'task-2.json').exists()


def test_repeat_init_idempotent():
    """Test that repeated init_db calls are idempotent."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, 'test.db')

        # Initialize database twice
        db.init_db(db_path)
        db.init_db(db_path)

        # Verify table still exists and no duplicates
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM tasks")
        count = cursor.fetchone()[0]

        # Verify table structure is correct
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
        table_exists = cursor.fetchone() is not None
        conn.close()

        assert table_exists
        assert count == 0  # Should be empty since no data was inserted


def test_crud():
    """Test CRUD operations: save_task, load_task, list_tasks."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, 'test.db')
        db.init_db(db_path)

        # Create test tasks
        task1 = {
            'id': 'test-1',
            'name': 'First Task',
            'status': 'running',
            'pid': 111,
            'result': 'In progress',
            'structured_result': {'status': 'running'},
            'created_at': '2026-05-13T10:00:00'
        }

        task2 = {
            'id': 'test-2',
            'name': 'Second Task',
            'status': 'completed',
            'pid': 222,
            'result': 'Done',
            'error': None,
            'created_at': '2026-05-13T11:00:00'
        }

        # Save both tasks
        db.save_task(task1)
        db.save_task(task2)

        # Load each task and verify fields match
        loaded_task1 = db.load_task('test-1')
        loaded_task2 = db.load_task('test-2')

        assert loaded_task1['id'] == task1['id']
        assert loaded_task1['name'] == task1['name']
        assert loaded_task1['status'] == task1['status']
        assert loaded_task1['pid'] == task1['pid']
        assert loaded_task1['result'] == task1['result']
        assert loaded_task1['structured_result'] == task1['structured_result']

        assert loaded_task2['id'] == task2['id']
        assert loaded_task2['name'] == task2['name']
        assert loaded_task2['status'] == task2['status']

        # Update first task
        task1_updated = task1.copy()
        task1_updated['status'] = 'completed'
        task1_updated['result'] = 'Finished'
        db.save_task(task1_updated)

        # Verify update
        updated_task = db.load_task('test-1')
        assert updated_task['status'] == 'completed'
        assert updated_task['result'] == 'Finished'

        # Test list_tasks
        all_tasks = db.list_tasks()
        assert len(all_tasks) == 2

        # Test list_tasks with status filter
        completed_tasks = db.list_tasks('completed')
        assert len(completed_tasks) == 2  # Both tasks are now completed


def test_running_count():
    """Test get_running_count function."""
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = os.path.join(temp_dir, 'test.db')
        db.init_db(db_path)

        # Create tasks with different statuses
        tasks = [
            {'id': 'running-1', 'name': 'Running Task 1', 'status': 'running'},
            {'id': 'running-2', 'name': 'Running Task 2', 'status': 'running'},
            {'id': 'completed-1', 'name': 'Completed Task', 'status': 'completed'},
            {'id': 'failed-1', 'name': 'Failed Task', 'status': 'failed'}
        ]

        # Save all tasks
        for task in tasks:
            db.save_task(task)

        # Verify running count
        running_count = db.get_running_count()
        assert running_count == 2