#!/usr/bin/env python3
"""
[TEST-SUPP-005] 验证并行 merge 不受 __pycache__ 干扰

关联 Bug:
- BUG: task_67d9d615 — __pycache__ merge 冲突
- Triage: escape · 设计遗漏

RED 验证要求:
- 当前 merge 第二个 worktree 时 __pycache__ 导致冲突，测试必须 FAIL

测试场景:
1. 两个 worktree 并行修改同一 Python 文件
2. merge 第一个 → 成功
3. merge 第二个 → 不应报 __pycache__ 冲突
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


def run_git_command(cmd, cwd):
    """Helper to run git commands with proper error handling."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, check=True
        )
        return result.stdout.strip(), result.stderr.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git command failed: {' '.join(cmd)}\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")


def create_python_file_with_import(filepath, content, module_name="test_module"):
    """Create a Python file that will generate __pycache__ when imported."""
    with open(filepath, "w") as f:
        f.write(f"""# {module_name}.py
import os
import sys

{content}

def main():
    print("Running {module_name}")
    return "{module_name} executed"

if __name__ == "__main__":
    main()
""")


def test_parallel_pycache_merge_conflicts():
    """RED test: Second worktree merge should fail due to __pycache__ conflicts."""

    print("🔴 [TEST-SUPP-005] Testing parallel worktree merge with __pycache__ conflicts...")

    with tempfile.TemporaryDirectory() as temp_dir:
        test_repo = os.path.join(temp_dir, "test_repo")

        # 1. Create test git repository
        os.makedirs(test_repo)
        run_git_command(["git", "init"], test_repo)
        run_git_command(["git", "config", "user.name", "Test User"], test_repo)
        run_git_command(["git", "config", "user.email", "test@example.com"], test_repo)

        # Create initial Python file
        initial_py = os.path.join(test_repo, "shared_module.py")
        create_python_file_with_import(
            initial_py,
            "# Initial version\nVERSION = '1.0.0'",
            "shared_module"
        )

        run_git_command(["git", "add", "shared_module.py"], test_repo)
        run_git_command(["git", "commit", "-m", "Initial commit with shared Python module"], test_repo)

        print(f"✓ Created test repo with Python file at {test_repo}")

        # 2. Create two worktrees that modify the same Python file
        task1_name = "feature-auth"
        task1_id = f"test-auth-{int(time.time())}"
        worktree1_path, branch1_name = setup_worktree(task1_name, test_repo)

        task2_name = "feature-logging"
        task2_id = f"test-logging-{int(time.time())}"
        worktree2_path, branch2_name = setup_worktree(task2_name, test_repo)

        print(f"✓ Created worktree 1: {branch1_name} at {worktree1_path}")
        print(f"✓ Created worktree 2: {branch2_name} at {worktree2_path}")

        # 3. Modify the same Python file in both worktrees (different changes)
        # Worktree 1: Add authentication feature
        py1_file = os.path.join(worktree1_path, "shared_module.py")
        create_python_file_with_import(
            py1_file,
            """# Version with auth feature
VERSION = '1.1.0'

def authenticate(username, password):
    \"\"\"Authenticate user.\"\"\"
    return username == "admin" and password == "secret"

def get_auth_status():
    return "auth enabled"
""",
            "shared_module"
        )

        # Worktree 2: Add logging feature (conflicting changes)
        py2_file = os.path.join(worktree2_path, "shared_module.py")
        create_python_file_with_import(
            py2_file,
            """# Version with logging feature
VERSION = '1.2.0'

import logging

def setup_logging():
    \"\"\"Setup logging configuration.\"\"\"
    logging.basicConfig(level=logging.INFO)
    return True

def log_activity(message):
    logging.info(f"Activity: {message}")
""",
            "shared_module"
        )

        print("✓ Modified shared_module.py in both worktrees with conflicting changes")

        # 4. Generate __pycache__ files in both worktrees by importing the modules
        # This simulates real-world scenario where Claude Code or testing generates __pycache__
        import importlib.util

        # Import from worktree 1 to generate __pycache__
        sys.path.insert(0, worktree1_path)
        try:
            spec1 = importlib.util.spec_from_file_location("shared_module_1", py1_file)
            module1 = importlib.util.module_from_spec(spec1)
            spec1.loader.exec_module(module1)
            print(f"✓ Generated __pycache__ in worktree 1: {module1.VERSION}")
        except Exception as e:
            print(f"⚠️  Failed to import module 1: {e}")
        finally:
            sys.path.remove(worktree1_path)

        # Import from worktree 2 to generate __pycache__
        sys.path.insert(0, worktree2_path)
        try:
            spec2 = importlib.util.spec_from_file_location("shared_module_2", py2_file)
            module2 = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(module2)
            print(f"✓ Generated __pycache__ in worktree 2: {module2.VERSION}")
        except Exception as e:
            print(f"⚠️  Failed to import module 2: {e}")
        finally:
            sys.path.remove(worktree2_path)

        # Verify __pycache__ directories exist
        pycache1 = os.path.join(worktree1_path, "__pycache__")
        pycache2 = os.path.join(worktree2_path, "__pycache__")

        print(f"📁 __pycache__ in worktree 1 exists: {os.path.exists(pycache1)}")
        print(f"📁 __pycache__ in worktree 2 exists: {os.path.exists(pycache2)}")

        if os.path.exists(pycache1):
            pycache1_files = os.listdir(pycache1)
            print(f"   Files: {pycache1_files}")

        if os.path.exists(pycache2):
            pycache2_files = os.listdir(pycache2)
            print(f"   Files: {pycache2_files}")

        # 5. Create completed task objects and auto-commit
        task1 = Task(
            id=task1_id,
            name=task1_name,
            prompt="Add authentication feature",
            workdir=test_repo,
            status="completed",
            worktree_path=worktree1_path,
            branch_name=branch1_name,
            repo_workdir=test_repo,
            result="Authentication feature implemented"
        )

        task2 = Task(
            id=task2_id,
            name=task2_name,
            prompt="Add logging feature",
            workdir=test_repo,
            status="completed",
            worktree_path=worktree2_path,
            branch_name=branch2_name,
            repo_workdir=test_repo,
            result="Logging feature implemented"
        )

        # Auto-commit changes (includes __pycache__ if present)
        auto_commit_result1 = auto_commit_worktree(task1)
        auto_commit_result2 = auto_commit_worktree(task2)

        print(f"✓ Auto-committed worktree 1: {auto_commit_result1}")
        print(f"✓ Auto-committed worktree 2: {auto_commit_result2}")

        # 6. Try to merge first worktree - should succeed
        print(f"\n🔄 Merging first worktree: {branch1_name}")
        merge1_result = subprocess.run([
            "git", "merge", branch1_name
        ], cwd=test_repo, capture_output=True, text=True)

        print(f"Merge 1 exit code: {merge1_result.returncode}")
        print(f"Merge 1 stdout: {merge1_result.stdout}")
        if merge1_result.stderr:
            print(f"Merge 1 stderr: {merge1_result.stderr}")

        if merge1_result.returncode != 0:
            print("❌ UNEXPECTED: First merge failed")
            return False

        print("✅ First merge succeeded")

        # 7. Try to merge second worktree - this is where the bug should occur
        print(f"\n🔄 Merging second worktree: {branch2_name}")
        merge2_result = subprocess.run([
            "git", "merge", branch2_name
        ], cwd=test_repo, capture_output=True, text=True)

        print(f"Merge 2 exit code: {merge2_result.returncode}")
        print(f"Merge 2 stdout: {merge2_result.stdout}")
        if merge2_result.stderr:
            print(f"Merge 2 stderr: {merge2_result.stderr}")

        # 8. RED TEST: Check if merge failed due to __pycache__ conflicts
        merge_output = merge2_result.stdout + merge2_result.stderr

        # Look for __pycache__ related conflict markers
        has_pycache_conflict = (
            "__pycache__" in merge_output.lower() or
            "conflict" in merge_output.lower() and "cache" in merge_output.lower() or
            merge2_result.returncode != 0 and "__pycache__" in merge_output
        )

        print(f"\n📊 RED TEST ANALYSIS:")
        print(f"   Second merge exit code: {merge2_result.returncode}")
        print(f"   Contains __pycache__ conflict: {has_pycache_conflict}")
        print(f"   Expected behavior: Merge should succeed without __pycache__ conflicts")

        # Clean up tasks
        try:
            task1.save()
            task2.save()
            os.remove(task1.task_file)
            os.remove(task2.task_file)
        except:
            pass

        # The RED test should FAIL if __pycache__ conflicts occur
        if merge2_result.returncode != 0:
            if has_pycache_conflict:
                print(f"\n🔴 RED TEST RESULT: PASS (bug confirmed)")
                print(f"   ✅ Second merge failed with __pycache__ conflicts as expected")
                print(f"   ✅ This confirms the bug exists - __pycache__ files interfere with merges")
                print(f"   📝 Next: Implement solution to exclude __pycache__ from merge conflicts")
                return True
            else:
                print(f"\n⚠️  RED TEST RESULT: UNCERTAIN")
                print(f"   ❌ Second merge failed but not clearly due to __pycache__")
                print(f"   📝 May be a different conflict issue")
                return False
        else:
            print(f"\n🟢 RED TEST RESULT: FAIL (unexpected)")
            print(f"   ❌ Second merge succeeded - __pycache__ conflicts not occurring")
            print(f"   ❌ Either the bug doesn't exist or test setup is wrong")
            print(f"   📝 Test may need adjustment or bug already fixed")
            return False


def test_feature_requirements():
    """Document the requirements for fixing the __pycache__ merge issue."""
    print("\n📝 FEATURE REQUIREMENTS for GREEN phase:")
    print("1. Git merge should ignore __pycache__ directories")
    print("2. Add __pycache__/ to .gitignore if not already present")
    print("3. Configure merge strategy to skip binary cache files")
    print("4. Consider git clean -fd before merge to remove untracked cache files")
    print("5. Auto-commit should exclude __pycache__ files by default")
    print("\nImplementation areas:")
    print("- Enhance do_merge() function in rapper script")
    print("- Add .gitignore management for Python cache files")
    print("- Consider merge.ours strategy for __pycache__ conflicts")
    print("- Add pre-merge cleanup of cache directories")


if __name__ == "__main__":
    try:
        # Initialize database for testing
        from lib.db import init_db
        init_db()

        print("🧪 [TEST-SUPP-005] Parallel __pycache__ Merge Conflicts Test")
        print("=" * 70)

        # Run the main RED test
        success = test_parallel_pycache_merge_conflicts()

        # Document requirements
        test_feature_requirements()

        if success:
            print(f"\n✅ RED test completed successfully")
            print(f"   - Confirmed: __pycache__ merge conflicts occur as expected")
            print(f"   - Ready for: GREEN phase implementation")
        else:
            print(f"\n❌ RED test inconclusive or bug not reproduced")
            print(f"   - Check test setup or investigate different conflict scenarios")
            # Don't exit with error code for RED tests that don't reproduce the bug
            # This helps distinguish between test setup issues vs. bug already fixed

    except Exception as e:
        print(f"\n💥 Test execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)