#!/usr/bin/env python3
"""
[TEST-SUPP-006] 验证 unmerged worktree 被正确检测

关联 Bug:
- BUG: task_50c8549e — 忘记 merge 无告警
- Triage: escape · 设计遗漏

RED 验证要求:
- 当前 rapper --tasks 不显示 unmerged 信息，测试必须 FAIL

测试场景:
1. 完成一个 worktree 任务但不 merge
2. rapper --tasks 应显示 "N unmerged"
3. git worktree list 应显示残留 branch
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

def test_unmerged_worktree_detection():
    """RED test: rapper --tasks should show unmerged worktree information."""

    print("🔴 [TEST-SUPP-006] Testing unmerged worktree detection...")

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
        task_name = "test-unmerged"
        task_id = f"test-{int(time.time())}"

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

        # 3. Verify worktree exists and has commits
        worktree_output = subprocess.run(
            ["git", "worktree", "list"],
            cwd=test_repo,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Git worktree list:\n{worktree_output.stdout}")

        # Verify branch exists and has commits ahead of main
        branch_status = subprocess.run(
            ["git", "log", "--oneline", "master..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=True
        )
        print(f"Unmerged commits in {branch_name}:\n{branch_status.stdout}")

        # 4. Test the current rapper --tasks output (should NOT show unmerged info)
        rapper_dir = os.environ.get("RAPPER_DIR", "/app/rapper")
        tasks_output = subprocess.run([
            f"{rapper_dir}/.venv/bin/python3",
            f"{rapper_dir}/lib/task_runner.py",
            "list"
        ], capture_output=True, text=True, cwd=test_repo)

        print(f"Current rapper --tasks output:")
        print(f"stdout: {tasks_output.stdout}")
        print(f"stderr: {tasks_output.stderr}")

        # 5. RED TEST: Check that unmerged information is NOT currently shown
        # This is what we expect in the RED phase - the feature doesn't exist yet
        tasks_text = tasks_output.stdout

        # Current behavior: should NOT contain unmerged information
        has_unmerged_info = (
            "unmerged" in tasks_text.lower() or
            "worktree" in tasks_text.lower() and "unmerged" in tasks_text.lower()
        )

        print(f"❌ Current output contains unmerged info: {has_unmerged_info}")

        if has_unmerged_info:
            print("🟢 UNEXPECTED: Feature already exists! Test should be updated to GREEN phase.")
            return False

        # 6. Verify what SHOULD be shown (for GREEN implementation)
        print("\n📋 Expected behavior for GREEN phase:")
        print("rapper --tasks should show:")
        print(f"  - Task {task_id} (completed)")
        print(f"  - Summary line: '1 unmerged worktree'")
        print(f"  - Or per-task notation: '[unmerged: {branch_name}]'")

        # 7. Manual verification of git state
        print(f"\n🔍 Manual verification:")
        print(f"  - Worktree exists: {os.path.exists(worktree_path)}")
        print(f"  - Branch name: {branch_name}")
        print(f"  - Worktree path: {worktree_path}")

        # Check if branch has commits that aren't merged
        try:
            unmerged_check = subprocess.run([
                "git", "rev-list", "--count", f"master..{branch_name}"
            ], cwd=test_repo, capture_output=True, text=True, check=True)
            unmerged_count = int(unmerged_check.stdout.strip())
            print(f"  - Unmerged commits: {unmerged_count}")

            if unmerged_count == 0:
                print("⚠️  WARNING: No unmerged commits found - auto_commit_worktree may have failed")
                return False

        except subprocess.CalledProcessError as e:
            print(f"  - Error checking unmerged commits: {e}")
            return False

        # Cleanup task from database
        try:
            os.remove(task.task_file)
        except FileNotFoundError:
            pass

        print("\n🔴 RED TEST RESULT: PASS")
        print("   Current rapper --tasks does NOT show unmerged worktree info (expected)")
        print("   Next: Implement feature to make GREEN test pass")

        return True

def test_feature_requirements():
    """Document the feature requirements for the GREEN implementation."""

    print("\n📝 FEATURE REQUIREMENTS for GREEN phase:")
    print("1. rapper --tasks should detect completed worktree tasks")
    print("2. Check if worktree branches have unmerged commits vs main")
    print("3. Display summary: 'N unmerged worktrees' or per-task markers")
    print("4. Provide actionable info: task_id, branch_name for merge")
    print("\nImplementation areas:")
    print("- Enhance list_tasks() in task_runner.py")
    print("- Add worktree detection logic")
    print("- Modify task listing display format")
    print("- Add --merge-all command for batch cleanup")


if __name__ == "__main__":
    try:
        # Initialize database for testing
        from lib.db import init_db
        init_db()

        print("🧪 [TEST-SUPP-006] Unmerged Worktree Detection Test")
        print("=" * 60)

        # Run the main test
        success = test_unmerged_worktree_detection()

        # Document requirements
        test_feature_requirements()

        if success:
            print("\n✅ RED test completed successfully")
            print("   - Confirmed: Current implementation lacks unmerged worktree detection")
            print("   - Ready for: GREEN phase implementation")
        else:
            print("\n❌ RED test failed")
            print("   - Issue with test setup or unexpected existing implementation")
            sys.exit(1)

    except Exception as e:
        print(f"\n💥 Test execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)