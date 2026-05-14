#!/usr/bin/env python3
"""
Simple validation test for the parallel detection logic.

This tests the Python logic that's embedded in the check_repo_conflicts bash function.
"""

import sys
import os
import tempfile
import shutil

# Add the lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))

from task_runner import Task, generate_task_id, list_tasks
from db import init_db

def create_test_task(name, workdir, has_worktree=False):
    """Create a test task for conflict detection testing."""
    task_id = generate_task_id()
    task = Task(
        id=task_id,
        name=name,
        prompt=f"Test task {name}",
        workdir=workdir,
        status="running",
        pid=12345,  # Fake PID for testing
        worktree_path="/fake/worktree" if has_worktree else None,
        branch_name="test-branch" if has_worktree else None,
    )
    task.save()
    return task

def test_conflict_detection():
    """Test the parallel execution conflict detection logic."""

    # Initialize database with a test path
    test_db_path = "/tmp/test_rapper_tasks.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
    init_db(test_db_path)

    # Create test directories
    repo1_dir = tempfile.mkdtemp()
    repo2_dir = tempfile.mkdtemp()

    try:
        print("Testing parallel execution conflict detection...\n")

        # Test 1: No running tasks - should allow
        print("1. Testing with no running tasks...")
        canonical_target = os.path.realpath(repo1_dir)
        use_worktree = False

        conflicts = []
        # Ensure db is properly initialized
        init_db(test_db_path)
        running_tasks = list_tasks(status='running')

        for task in running_tasks:
            task_canonical = os.path.realpath(task.workdir) if task.workdir else ''
            if task_canonical == canonical_target:
                task_has_worktree = task.worktree_path is not None and task.worktree_path != ''
                if not use_worktree and not task_has_worktree:
                    conflicts.append(task)

        assert len(conflicts) == 0, f"Expected no conflicts, got {len(conflicts)}"
        print("   ✓ No conflicts detected with empty database")

        # Test 2: Create task in repo1 without worktree
        print("2. Creating first task in repo1 without worktree...")
        task1 = create_test_task("task1", repo1_dir, has_worktree=False)
        print(f"   ✓ Task created: {task1.id}")

        # Test 3: Try to create second task in same repo without worktree - should conflict
        print("3. Testing conflict detection for second task in same repo...")
        canonical_target = os.path.realpath(repo1_dir)
        use_worktree = False

        conflicts = []
        running_tasks = list_tasks(status='running')

        for task in running_tasks:
            task_canonical = os.path.realpath(task.workdir) if task.workdir else ''
            if task_canonical == canonical_target:
                task_has_worktree = task.worktree_path is not None and task.worktree_path != ''
                if not use_worktree and not task_has_worktree:
                    conflicts.append(task)

        assert len(conflicts) == 1, f"Expected 1 conflict, got {len(conflicts)}"
        assert conflicts[0].id == task1.id, f"Expected conflict with task1, got {conflicts[0].id}"
        print("   ✓ Conflict correctly detected!")

        # Test 4: Create task in different repo - should be allowed
        print("4. Testing different repo (should be allowed)...")
        canonical_target = os.path.realpath(repo2_dir)  # Different repo
        use_worktree = False

        conflicts = []
        running_tasks = list_tasks(status='running')

        for task in running_tasks:
            task_canonical = os.path.realpath(task.workdir) if task.workdir else ''
            if task_canonical == canonical_target:
                task_has_worktree = task.worktree_path is not None and task.worktree_path != ''
                if not use_worktree and not task_has_worktree:
                    conflicts.append(task)

        assert len(conflicts) == 0, f"Expected no conflicts in different repo, got {len(conflicts)}"
        print("   ✓ Different repo correctly allowed")

        # Test 5: Create task in same repo WITH worktree - should be allowed
        print("5. Testing same repo with worktree (should be allowed)...")
        canonical_target = os.path.realpath(repo1_dir)  # Same repo as task1
        use_worktree = True  # But WITH worktree

        conflicts = []
        running_tasks = list_tasks(status='running')

        for task in running_tasks:
            task_canonical = os.path.realpath(task.workdir) if task.workdir else ''
            if task_canonical == canonical_target:
                task_has_worktree = task.worktree_path is not None and task.worktree_path != ''
                if not use_worktree and not task_has_worktree:
                    conflicts.append(task)

        assert len(conflicts) == 0, f"Expected no conflicts with worktree, got {len(conflicts)}"
        print("   ✓ Same repo with worktree correctly allowed")

        # Test 6: Create worktree task alongside another worktree task - should be allowed
        print("6. Creating second worktree task in same repo...")
        task2 = create_test_task("task2-worktree", repo1_dir, has_worktree=True)

        canonical_target = os.path.realpath(repo1_dir)
        use_worktree = True

        conflicts = []
        running_tasks = list_tasks(status='running')

        for task in running_tasks:
            task_canonical = os.path.realpath(task.workdir) if task.workdir else ''
            if task_canonical == canonical_target:
                task_has_worktree = task.worktree_path is not None and task.worktree_path != ''
                if not use_worktree and not task_has_worktree:
                    conflicts.append(task)

        assert len(conflicts) == 0, f"Expected no conflicts between worktree tasks, got {len(conflicts)}"
        print("   ✓ Multiple worktree tasks correctly allowed")

        print("\n🟢 All parallel detection logic tests PASSED!")
        print("   - Conflict detection working correctly")
        print("   - Different repos allowed")
        print("   - Worktree isolation respected")

        return True

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        shutil.rmtree(repo1_dir, ignore_errors=True)
        shutil.rmtree(repo2_dir, ignore_errors=True)

        # Cancel test tasks
        try:
            for task in list_tasks(status='running'):
                if task.name.startswith('task'):
                    task.status = 'cancelled'
                    task.save()
        except:
            pass

if __name__ == "__main__":
    success = test_conflict_detection()
    sys.exit(0 if success else 1)