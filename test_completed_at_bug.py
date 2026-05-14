#!/usr/bin/env python3
"""
Comprehensive test suite for timestamp functionality.

This test verifies that created_at and completed_at timestamps are properly
set when tasks are created, updated, and completed.
"""

import tempfile
import os
import time
from datetime import datetime
from lib.db import init_db, save_task, load_task
from lib.task_runner import Task

def test_task_creation_timestamps():
    """Test that created_at is set when a task is first saved"""

    # Use a temporary database to avoid interfering with real data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        # Initialize database
        init_db(tmp_db_path)

        # Create a task
        task = Task(
            id="test-create-20260513-123456",
            name="test-creation-timestamps",
            prompt="test prompt",
            workdir="/tmp",
            status="pending"
        )

        # Verify created_at is initially None
        assert task.created_at is None, "created_at should be None before first save"

        # Save task
        task.save()

        # Verify created_at was set
        assert task.created_at is not None, "created_at should be set after save"

        # Load from database and verify
        loaded_data = load_task(task.id)
        assert loaded_data.get('created_at') is not None, "created_at should be in database"

        print("✅ Task creation timestamp test passed")
        return True

    finally:
        if os.path.exists(tmp_db_path):
            os.unlink(tmp_db_path)

def test_task_completion_timestamps():
    """Test that completed_at is set when task status becomes terminal"""

    # Use a temporary database to avoid interfering with real data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        # Initialize database
        init_db(tmp_db_path)

        # Test each terminal status
        terminal_statuses = ['completed', 'failed', 'cancelled']

        for i, status in enumerate(terminal_statuses):
            task = Task(
                id=f"test-terminal-{status}-{i}",
                name=f"test-{status}-timestamps",
                prompt="test prompt",
                workdir="/tmp",
                status="running"
            )

            # Save initial running state
            task.save()

            # Verify completed_at is not set yet
            assert task.completed_at is None, f"completed_at should be None for running task"

            # Mark as terminal status and save
            task.status = status
            task.result = f"Task {status}"
            task.save()

            # Verify completed_at was set
            assert task.completed_at is not None, f"completed_at should be set for {status} task"

            # Load from database and verify
            loaded_data = load_task(task.id)
            assert loaded_data.get('completed_at') is not None, f"completed_at should be in database for {status} task"

        print("✅ Task completion timestamp tests passed for all terminal statuses")
        return True

    finally:
        if os.path.exists(tmp_db_path):
            os.unlink(tmp_db_path)

def test_timestamp_immutability():
    """Test that timestamps are not overwritten once set"""

    # Use a temporary database to avoid interfering with real data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        # Initialize database
        init_db(tmp_db_path)

        task = Task(
            id="test-immutable-20260513",
            name="test-timestamp-immutability",
            prompt="test prompt",
            workdir="/tmp",
            status="pending"
        )

        # Save to set created_at
        task.save()
        first_created_at = task.created_at

        # Save again and verify created_at didn't change
        time.sleep(0.1)  # Slight delay to ensure different timestamp if logic is wrong
        task.save()
        assert task.created_at == first_created_at, "created_at should not change on subsequent saves"

        # Complete the task
        task.status = "completed"
        task.save()
        first_completed_at = task.completed_at

        # Save again and verify completed_at didn't change
        time.sleep(0.1)
        task.save()
        assert task.completed_at == first_completed_at, "completed_at should not change on subsequent saves"

        print("✅ Timestamp immutability test passed")
        return True

    finally:
        if os.path.exists(tmp_db_path):
            os.unlink(tmp_db_path)

def test_db_safety_net():
    """Test that db.save_task provides safety net for timestamp setting"""

    # Use a temporary database to avoid interfering with real data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        # Initialize database
        init_db(tmp_db_path)

        # Create task data without timestamps (simulating old code calling db.save_task directly)
        task_data = {
            'id': 'test-db-safety-20260513',
            'name': 'test-db-safety-net',
            'status': 'completed',
            'prompt': 'test prompt',
            'workdir': '/tmp',
            'result': 'Task completed',
            'created_at': None,  # Explicitly None
            'completed_at': None  # Explicitly None
        }

        # Save directly via db.save_task (bypassing Task.save)
        save_task(task_data)

        # Load and verify timestamps were auto-set by db.save_task
        loaded_data = load_task(task_data['id'])
        assert loaded_data.get('created_at') is not None, "db.save_task should auto-set created_at"
        assert loaded_data.get('completed_at') is not None, "db.save_task should auto-set completed_at for terminal status"

        print("✅ Database safety net test passed")
        return True

    finally:
        if os.path.exists(tmp_db_path):
            os.unlink(tmp_db_path)

def test_original_bug_reproduction():
    """Test that reproduces the original bug to show it's now fixed"""

    # Use a temporary database to avoid interfering with real data
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp_db:
        tmp_db_path = tmp_db.name

    try:
        # Initialize database
        init_db(tmp_db_path)

        # Create a task
        task = Task(
            id="test-20260513-123456-abcd",
            name="test-completed-at-bug",
            prompt="test prompt",
            workdir="/tmp",
            status="running"
        )

        # Save initial state
        task.save()

        # Mark task as completed and save
        current_time = time.time()
        task.status = "completed"
        task.end_time = current_time
        task.result = "Task completed successfully"
        task.save()

        # Load task from database
        loaded_task_data = load_task(task.id)

        print("=== ORIGINAL BUG TEST (SHOULD NOW BE FIXED) ===")
        print(f"Task ID: {task.id}")
        print(f"Task status: {loaded_task_data.get('status')}")
        print(f"Task end_time: {task.end_time}")
        print(f"Task completed_at (from DB): {loaded_task_data.get('completed_at')}")
        print(f"Task created_at (from DB): {loaded_task_data.get('created_at')}")

        # The fix: completed_at should now be set
        assert loaded_task_data.get('completed_at') is not None, "BUG FIX: completed_at should now be set"
        assert loaded_task_data.get('created_at') is not None, "BUG FIX: created_at should now be set"
        print("✅ Original bug is now fixed: timestamps are properly set")

        return True

    finally:
        # Cleanup
        if os.path.exists(tmp_db_path):
            os.unlink(tmp_db_path)

def run_all_tests():
    """Run all timestamp tests"""
    tests = [
        test_task_creation_timestamps,
        test_task_completion_timestamps,
        test_timestamp_immutability,
        test_db_safety_net,
        test_original_bug_reproduction
    ]

    passed = 0
    failed = 0

    print("Running comprehensive timestamp tests...\n")

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            failed += 1

    print(f"\n📊 TEST RESULTS: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All timestamp tests passed!")
        return True
    else:
        print("💥 Some tests failed!")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)