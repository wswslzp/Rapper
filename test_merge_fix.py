#!/usr/bin/env python3
"""
Test script to reproduce and verify the merge issue fix.

This script simulates the worktree merge scenario:
1. Creates a test git repo
2. Creates a worktree with some files (simulating Rapper output)
3. Tests the merge functionality
"""

import os
import subprocess
import tempfile
import shutil
from pathlib import Path

def run_cmd(cmd, cwd=None, capture=True):
    """Run command and return result"""
    result = subprocess.run(cmd, shell=True, cwd=cwd,
                          capture_output=capture, text=True)
    if capture:
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    return result.returncode

def test_worktree_merge():
    """Test the worktree merge scenario"""

    # Create temp directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "test_repo")

        print(f"🧪 Testing worktree merge in {repo_dir}")

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

        # 2. Create worktree (simulating rapper --worktree)
        branch_name = "rapper/test-feature"
        worktree_path = os.path.join(tmpdir, "worktree")

        run_cmd(f"git worktree add -b {branch_name} {worktree_path}", cwd=repo_dir)
        print(f"✅ Created worktree: {worktree_path}")

        # 3. Create files in worktree (simulating Claude Write tool output)
        test_file = os.path.join(worktree_path, "new_feature.py")
        with open(test_file, "w") as f:
            f.write("""#!/usr/bin/env python3
def hello():
    print("Hello from new feature!")

if __name__ == "__main__":
    hello()
""")

        # Also create a subdirectory with files
        subdir = os.path.join(worktree_path, "lib")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "utils.py"), "w") as f:
            f.write("def utility_function():\n    return 'utility'\n")

        print(f"✅ Created files in worktree (simulating Claude Write tool)")

        # 4. Check git status in worktree (this should show untracked files)
        rc, stdout, stderr = run_cmd("git status --porcelain", cwd=worktree_path)
        print(f"📋 Git status in worktree:\n{stdout}")

        if not stdout:
            print("❌ ERROR: No untracked files detected!")
            return False

        # 5. Test the auto-commit logic (from do_merge function)
        print("\n🔧 Testing auto-commit logic...")

        # Check if worktree is dirty
        dirty = stdout.strip()
        if dirty:
            print(f"✅ Worktree has uncommitted changes, auto-committing...")
            run_cmd("git add -A", cwd=worktree_path)
            commit_msg = f"feat({branch_name.split('/')[-1]}): auto-commit by rapper --merge"
            run_cmd(f"git commit -m '{commit_msg}'", cwd=worktree_path)
            print(f"✅ Auto-committed with message: {commit_msg}")
        else:
            print("❌ ERROR: Worktree appears clean but should have changes!")
            return False

        # 6. Test merge
        print("\n🔀 Testing merge...")
        rc, stdout, stderr = run_cmd(f"git merge {branch_name}", cwd=repo_dir)

        if rc == 0:
            print(f"✅ Merge successful!")
            print(f"📄 Merge output: {stdout}")
        else:
            print(f"❌ Merge failed: {stderr}")
            return False

        # 7. Verify files exist in main repo
        main_test_file = os.path.join(repo_dir, "new_feature.py")
        main_utils_file = os.path.join(repo_dir, "lib", "utils.py")

        if os.path.exists(main_test_file) and os.path.exists(main_utils_file):
            print("✅ Files successfully merged to main repo!")
            return True
        else:
            print("❌ ERROR: Files not found in main repo after merge!")
            return False

def test_edge_cases():
    """Test edge cases that might cause issues"""
    print("\n🧪 Testing edge cases...")

    # Test git status --porcelain with various file states
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "edge_test")
        os.makedirs(repo_dir)
        run_cmd("git init", cwd=repo_dir)
        run_cmd("git config user.name 'Test User'", cwd=repo_dir)
        run_cmd("git config user.email 'test@example.com'", cwd=repo_dir)

        # Test with just untracked files
        with open(os.path.join(repo_dir, "untracked.txt"), "w") as f:
            f.write("test")

        rc, stdout, stderr = run_cmd("git status --porcelain", cwd=repo_dir)
        print(f"📋 Untracked files status: '{stdout}'")

        if stdout.strip():
            print("✅ git status --porcelain correctly detects untracked files")
        else:
            print("❌ ERROR: git status --porcelain doesn't detect untracked files")
            return False

        return True

if __name__ == "__main__":
    print("🎤 Rapper Merge Fix Test\n")

    # Test main scenario
    success = test_worktree_merge()
    if not success:
        print("\n❌ Main test failed!")
        exit(1)

    # Test edge cases
    success = test_edge_cases()
    if not success:
        print("\n❌ Edge case test failed!")
        exit(1)

    print("\n🎉 All tests passed!")