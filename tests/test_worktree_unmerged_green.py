#!/usr/bin/env python3
"""
[TEST-SUPP-006] GREEN: 验证 unmerged worktree 被正确检测

这是 GREEN 测试 - 应该通过，证明功能已正确实现

测试场景:
1. 完成一个 worktree 任务但不 merge
2. rapper --tasks 应显示 "N unmerged"
3. 验证输出包含正确的 merge 指令
"""

import os
import shutil
import subprocess
import tempfile
import time
import sys
from pathlib import Path

# Add lib to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.task_runner import TaskRunner, Task, setup_worktree, auto_commit_worktree

def test_unmerged_worktree_detection_green():
    """GREEN test: rapper --tasks should show unmerged worktree information."""

    print("🟢 [TEST-SUPP-006] GREEN: Testing unmerged worktree detection...")

    with tempfile.TemporaryDirectory() as temp_dir:
        test_repo = os.path.join(temp_dir, "test_repo")

        # 1. Create a test git repository
        os.makedirs(test_repo)
        subprocess.run(["git", "init"], cwd=test_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=test_repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=test_repo, check=True)

        # Create initial commit
        test_file = os.path.join(test_repo, "test.txt")
        with open(test_file, "w") as f:
            f.write("Initial content\n")
        subprocess.run(["git", "add", "test.txt"], cwd=test_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=test_repo, check=True)

        # 2. Create a worktree task (simulate completion)
        task_name = "test-green-unmerged"
        task_id = f"test-green-{int(time.time())}"

        # Setup worktree
        worktree_path, branch_name = setup_worktree(task_name, test_repo)

        # Simulate task completion by making changes in worktree
        worktree_file = os.path.join(worktree_path, "feature.txt")
        with open(worktree_file, "w") as f:
            f.write("New feature implemented\n")

        # Create a completed task with worktree
        task = Task(
            id=task_id,
            name=task_name,
            prompt="Implement test feature",
            workdir=test_repo,
            status="completed",
            worktree_path=worktree_path,
            branch_name=branch_name,
            repo_workdir=test_repo,
            result="Feature implementation completed"
        )

        # Auto-commit the changes (simulates normal task completion)
        auto_commit_worktree(task)
        task.save()

        # 3. Test the enhanced rapper --tasks output (should show unmerged info)
        rapper_dir = os.environ.get("RAPPER_DIR", "/app/rapper")
        tasks_output = subprocess.run([
            f"{rapper_dir}/.venv/bin/python3",
            f"{rapper_dir}/lib/task_runner.py",
            "list"
        ], capture_output=True, text=True, cwd=test_repo)

        print(f"Enhanced rapper --tasks output:")
        print(f"stdout:\n{tasks_output.stdout}")
        if tasks_output.stderr:
            print(f"stderr: {tasks_output.stderr}")

        tasks_text = tasks_output.stdout.lower()

        # 4. GREEN TEST: Verify unmerged information IS shown
        success = True
        test_results = []

        # Test 1: Contains "unmerged" keyword
        has_unmerged_keyword = "unmerged" in tasks_text
        test_results.append(("Contains 'unmerged' keyword", has_unmerged_keyword))
        if not has_unmerged_keyword:
            success = False

        # Test 2: Shows count (e.g., "1 unmerged")
        has_count = "1 unmerged" in tasks_text
        test_results.append(("Shows '1 unmerged' count", has_count))
        if not has_count:
            success = False

        # Test 3: Contains merge command suggestion
        has_merge_command = "rapper --merge" in tasks_output.stdout
        test_results.append(("Contains 'rapper --merge' command", has_merge_command))
        if not has_merge_command:
            success = False

        # Test 4: Shows branch name or task info
        has_branch_info = branch_name in tasks_output.stdout or task_id in tasks_output.stdout
        test_results.append(("Shows branch/task info", has_branch_info))
        if not has_branch_info:
            success = False

        # Test 5: Contains commit info
        has_commit_info = "commit" in tasks_text
        test_results.append(("Shows commit information", has_commit_info))
        if not has_commit_info:
            success = False

        # Print test results
        print(f"\n📊 Test Results:")
        for test_name, result in test_results:
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"  {status}: {test_name}")

        # Cleanup task from database
        try:
            os.remove(task.task_file)
        except FileNotFoundError:
            pass

        # Final result
        if success:
            print(f"\n🟢 GREEN TEST RESULT: PASS")
            print(f"   ✅ rapper --tasks correctly shows unmerged worktree info")
            print(f"   ✅ All expected features are present")
            return True
        else:
            print(f"\n🔴 GREEN TEST RESULT: FAIL")
            print(f"   ❌ Some expected features are missing")
            print(f"   ❌ Implementation needs more work")
            return False


def test_multiple_unmerged_worktrees():
    """Test that multiple unmerged worktrees are correctly detected and displayed."""

    print("\n🟢 [BONUS] Testing multiple unmerged worktrees...")

    with tempfile.TemporaryDirectory() as temp_dir:
        test_repo = os.path.join(temp_dir, "test_repo")

        # Setup repo
        os.makedirs(test_repo)
        subprocess.run(["git", "init"], cwd=test_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=test_repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=test_repo, check=True)

        test_file = os.path.join(test_repo, "test.txt")
        with open(test_file, "w") as f:
            f.write("Initial content\n")
        subprocess.run(["git", "add", "test.txt"], cwd=test_repo, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=test_repo, check=True)

        # Create 2 unmerged worktree tasks
        tasks = []
        for i in range(2):
            task_name = f"test-multi-{i}"
            task_id = f"test-multi-{i}-{int(time.time())}"

            worktree_path, branch_name = setup_worktree(task_name, test_repo)

            # Make changes
            worktree_file = os.path.join(worktree_path, f"feature_{i}.txt")
            with open(worktree_file, "w") as f:
                f.write(f"Feature {i} implemented\n")

            task = Task(
                id=task_id,
                name=task_name,
                prompt=f"Implement feature {i}",
                workdir=test_repo,
                status="completed",
                worktree_path=worktree_path,
                branch_name=branch_name,
                repo_workdir=test_repo,
                result=f"Feature {i} completed"
            )
            auto_commit_worktree(task)
            task.save()
            tasks.append(task)

        # Test output
        rapper_dir = os.environ.get("RAPPER_DIR", "/app/rapper")
        tasks_output = subprocess.run([
            f"{rapper_dir}/.venv/bin/python3",
            f"{rapper_dir}/lib/task_runner.py",
            "list"
        ], capture_output=True, text=True, cwd=test_repo)

        print(f"Multiple unmerged worktrees output:")
        print(tasks_output.stdout)

        # Check for "2 unmerged"
        success = "2 unmerged" in tasks_output.stdout.lower()

        # Cleanup
        for task in tasks:
            try:
                os.remove(task.task_file)
            except FileNotFoundError:
                pass

        if success:
            print(f"✅ Multiple unmerged worktrees correctly detected")
            return True
        else:
            print(f"❌ Multiple unmerged worktrees not properly shown")
            return False


if __name__ == "__main__":
    try:
        # Initialize database for testing
        from lib.db import init_db
        init_db()

        print("🧪 [TEST-SUPP-006] GREEN: Unmerged Worktree Detection Test")
        print("=" * 65)

        # Run main GREEN test
        main_success = test_unmerged_worktree_detection_green()

        # Run bonus test for multiple worktrees
        multi_success = test_multiple_unmerged_worktrees()

        overall_success = main_success and multi_success

        if overall_success:
            print(f"\n🎉 ALL GREEN TESTS PASSED")
            print(f"   ✅ Single unmerged worktree detection: WORKING")
            print(f"   ✅ Multiple unmerged worktree detection: WORKING")
            print(f"   ✅ Feature implementation: COMPLETE")
        else:
            print(f"\n❌ SOME GREEN TESTS FAILED")
            print(f"   {'✅' if main_success else '❌'} Single unmerged worktree")
            print(f"   {'✅' if multi_success else '❌'} Multiple unmerged worktrees")
            print(f"   ❌ Implementation needs fixes")
            sys.exit(1)

    except Exception as e:
        print(f"\n💥 GREEN test execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)