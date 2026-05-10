#!/usr/bin/env python3
"""
Test enhanced merge functionality using the proper Task object structure.
"""

import os
import subprocess
import tempfile
import json
import sys
from pathlib import Path

# Add the rapper lib directory to the path
sys.path.insert(0, "/app/rapper/lib")
from task_runner import Task, TASK_DIR

def run_cmd(cmd, cwd=None, capture=True):
    """Run command and return result"""
    result = subprocess.run(cmd, shell=True, cwd=cwd,
                          capture_output=capture, text=True)
    if capture:
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    return result.returncode

def test_enhanced_merge():
    """Test the enhanced merge functionality"""
    print("🎤 Testing Enhanced Merge with Task Runner")

    # Create temp directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "test_repo")

        print(f"🧪 Testing in {repo_dir}")

        # 1. Initialize git repo
        os.makedirs(repo_dir)
        run_cmd("git init", cwd=repo_dir)
        run_cmd("git config user.name 'Test User'", cwd=repo_dir)
        run_cmd("git config user.email 'test@example.com'", cwd=repo_dir)

        # Create initial commit
        with open(os.path.join(repo_dir, "README.md"), "w") as f:
            f.write("# Test Repo\n")
        run_cmd("git add README.md", cwd=repo_dir)
        run_cmd("git commit -m 'Initial commit'", cwd=repo_dir)

        # 2. Create worktree
        branch_name = "rapper/test-merge-feature"
        worktree_path = os.path.join(tmpdir, "worktree")

        run_cmd(f"git worktree add -b {branch_name} {worktree_path}", cwd=repo_dir)
        print(f"✅ Created worktree: {worktree_path}")

        # 3. Create files in worktree (simulating Claude Write tool output)
        test_file = os.path.join(worktree_path, "enhanced_feature.py")
        with open(test_file, "w") as f:
            f.write("""#!/usr/bin/env python3
def enhanced_feature():
    '''Enhanced feature with better diagnostics'''
    print("Hello from enhanced feature!")
    return True

if __name__ == "__main__":
    enhanced_feature()
""")

        # Also create a subdirectory with files
        subdir = os.path.join(worktree_path, "utils")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "helpers.py"), "w") as f:
            f.write("""def helper_function(x):
    '''Helper function for testing'''
    return x * 2

def another_helper():
    return "Enhanced diagnostics working!"
""")

        print(f"✅ Created files in worktree (simulating Claude Write tool)")

        # 4. Create a proper Task object
        task_id = f"enhanced-merge-test-{int(os.urandom(4).hex(), 16)}"

        # Create the task using the Task class structure
        task = Task(
            id=task_id,
            name="enhanced-merge-test",
            prompt="Test enhanced merge functionality",
            workdir=repo_dir,
            status="completed",
            branch_name=branch_name,
            worktree_path=worktree_path,
            repo_workdir=repo_dir
        )

        # Save the task to the tasks directory
        print(f"✅ Creating task: {task_id}")
        task.save()

        # 5. Test the enhanced merge
        print("\n🔧 Testing enhanced merge...")

        rapper_script = "/app/rapper/rapper"
        merge_cmd = [rapper_script, "--merge", task_id]

        print(f"Running: {' '.join(merge_cmd)}")

        try:
            result = subprocess.run(merge_cmd,
                                  capture_output=True, text=True,
                                  cwd=repo_dir, timeout=60)

            print(f"Exit code: {result.returncode}")
            print(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                print(f"STDERR:\n{result.stderr}")

            if result.returncode == 0:
                print("✅ Merge command completed successfully!")

                # 6. Verify files exist in main repo
                main_test_file = os.path.join(repo_dir, "enhanced_feature.py")
                main_utils_file = os.path.join(repo_dir, "utils", "helpers.py")

                if os.path.exists(main_test_file) and os.path.exists(main_utils_file):
                    print("✅ Files successfully merged to main repo!")
                    print("✅ Enhanced merge functionality working correctly!")

                    # Show the git log to confirm commits
                    rc, log_output, _ = run_cmd("git log --oneline -n 3", cwd=repo_dir)
                    print(f"\n📋 Git log:\n{log_output}")

                    return True
                else:
                    print("❌ ERROR: Files not found in main repo after merge!")
                    print(f"Looking for: {main_test_file}")
                    print(f"Looking for: {main_utils_file}")
                    return False
            else:
                print("❌ ERROR: Merge command failed!")
                return False

        except subprocess.TimeoutExpired:
            print("❌ ERROR: Merge command timed out!")
            return False
        except Exception as e:
            print(f"❌ ERROR: Exception running merge: {e}")
            return False

        finally:
            # Clean up the task file
            try:
                task_file = TASK_DIR / f"{task_id}.json"
                if task_file.exists():
                    task_file.unlink()
                    print(f"🧹 Cleaned up task file: {task_id}")
            except Exception as e:
                print(f"Warning: Could not clean up task file: {e}")

def test_empty_worktree_diagnostics():
    """Test the enhanced diagnostics for empty worktree case"""
    print("\n🧪 Testing Empty Worktree Diagnostics")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "empty_test")

        # Setup repo
        os.makedirs(repo_dir)
        run_cmd("git init", cwd=repo_dir)
        run_cmd("git config user.name 'Test User'", cwd=repo_dir)
        run_cmd("git config user.email 'test@example.com'", cwd=repo_dir)

        with open(os.path.join(repo_dir, "README.md"), "w") as f:
            f.write("# Empty Test Repo\n")
        run_cmd("git add README.md", cwd=repo_dir)
        run_cmd("git commit -m 'Initial commit'", cwd=repo_dir)

        # Create worktree but don't add any files
        branch_name = "rapper/empty-test"
        worktree_path = os.path.join(tmpdir, "empty_worktree")

        run_cmd(f"git worktree add -b {branch_name} {worktree_path}", cwd=repo_dir)
        print(f"✅ Created empty worktree: {worktree_path}")

        # Create task for empty worktree
        task_id = f"empty-test-{int(os.urandom(4).hex(), 16)}"

        task = Task(
            id=task_id,
            name="empty-test",
            prompt="Test empty worktree diagnostics",
            workdir=repo_dir,
            status="completed",
            branch_name=branch_name,
            worktree_path=worktree_path,
            repo_workdir=repo_dir
        )

        task.save()
        print(f"✅ Created empty worktree task: {task_id}")

        # Test merge - this should show enhanced diagnostics
        print("\n🔧 Testing enhanced diagnostics for empty worktree...")

        merge_cmd = ["/app/rapper/rapper", "--merge", task_id]

        try:
            result = subprocess.run(merge_cmd,
                                  capture_output=True, text=True,
                                  cwd=repo_dir, timeout=60)

            print(f"Exit code: {result.returncode}")
            print(f"STDOUT:\n{result.stdout}")
            if result.stderr:
                print(f"STDERR:\n{result.stderr}")

            # Check if we got the enhanced diagnostics
            if "Already up to date" in result.stdout:
                print("✅ Successfully reproduced 'Already up to date' case")
                if "Diagnostic Information" in result.stdout:
                    print("✅ Enhanced diagnostics are working!")
                    return True
                else:
                    print("❌ Enhanced diagnostics not found in output")
                    return False
            else:
                print("❌ Did not reproduce the 'Already up to date' case")
                return False

        except Exception as e:
            print(f"❌ ERROR: {e}")
            return False

        finally:
            # Clean up
            try:
                task_file = TASK_DIR / f"{task_id}.json"
                if task_file.exists():
                    task_file.unlink()
            except:
                pass

if __name__ == "__main__":
    print("🎤 Enhanced Merge Testing with Task Runner\n")

    # Ensure tasks directory exists
    TASK_DIR.mkdir(parents=True, exist_ok=True)

    # Test 1: Normal merge with files
    success1 = test_enhanced_merge()

    # Test 2: Empty worktree diagnostics
    success2 = test_empty_worktree_diagnostics()

    if success1 and success2:
        print("\n🎉 All tests passed! Enhanced merge functionality is working!")
    else:
        print(f"\n❌ Tests failed: Normal={success1}, Diagnostics={success2}")
        exit(1)