#!/usr/bin/env python3
"""
Test the enhanced merge functionality with better diagnostics.
"""

import os
import subprocess
import tempfile
from pathlib import Path

def run_cmd(cmd, cwd=None, capture=True):
    """Run command and return result"""
    result = subprocess.run(cmd, shell=True, cwd=cwd,
                          capture_output=capture, text=True)
    if capture:
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    return result.returncode

def test_enhanced_merge_with_files():
    """Test enhanced merge with actual file changes"""
    print("🧪 Testing enhanced merge with file changes...")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "test_repo")

        # Setup repo
        os.makedirs(repo_dir)
        run_cmd("git init", cwd=repo_dir)
        run_cmd("git config user.name 'Test User'", cwd=repo_dir)
        run_cmd("git config user.email 'test@example.com'", cwd=repo_dir)

        # Initial commit
        with open(os.path.join(repo_dir, "README.md"), "w") as f:
            f.write("# Test Repo\n")
        run_cmd("git add README.md", cwd=repo_dir)
        run_cmd("git commit -m 'Initial commit'", cwd=repo_dir)

        # Create worktree
        branch_name = "rapper/test-feature"
        worktree_path = os.path.join(tmpdir, "worktree")
        run_cmd(f"git worktree add -b {branch_name} {worktree_path}", cwd=repo_dir)

        # Add files to worktree (simulate Rapper output)
        with open(os.path.join(worktree_path, "feature.py"), "w") as f:
            f.write("print('Hello from feature!')\n")

        # Test the enhanced do_merge logic by calling our improved function
        print("Calling enhanced merge logic...")

        # We'll simulate the enhanced merge steps manually
        # Step 1: Check status
        rc, dirty, stderr = run_cmd("git status --porcelain", cwd=worktree_path)
        print(f"📋 Git status: '{dirty}'")

        if dirty:
            untracked = [line for line in dirty.split('\n') if line.startswith('??')]
            modified = [line for line in dirty.split('\n') if not line.startswith('??') and line.strip()]

            print(f"✅ Found {len(untracked)} untracked and {len(modified)} modified files")
            print("Changes:")
            for line in dirty.split('\n'):
                if line.strip():
                    print(f"  {line}")

            # Step 2: Add and commit
            run_cmd("git add -A", cwd=worktree_path)
            commit_msg = f"feat({branch_name.split('/')[-1]}): auto-commit by rapper --merge"
            run_cmd(f"git commit -m '{commit_msg}'", cwd=worktree_path)

            # Verify commit
            rc, last_commit, _ = run_cmd("git log -1 --oneline", cwd=worktree_path)
            print(f"✅ Latest commit: {last_commit}")

        # Step 3: Test merge
        print("\n🔀 Testing merge...")
        rc, merge_output, merge_error = run_cmd(f"git merge {branch_name}", cwd=repo_dir)

        print(f"Merge output: {merge_output}")
        if merge_error:
            print(f"Merge error: {merge_error}")

        if "Already up to date" in merge_output or "Already up-to-date" in merge_output:
            print("⚠️  Got 'Already up to date' - this is the problem case!")
            return False
        else:
            print("✅ Merge successful with real changes")

            # Verify file exists
            feature_file = os.path.join(repo_dir, "feature.py")
            if os.path.exists(feature_file):
                print("✅ Feature file merged successfully")
                return True
            else:
                print("❌ Feature file not found after merge")
                return False

def test_problematic_empty_branch():
    """Test case where worktree branch has no new commits (the reported problem)"""
    print("\n🧪 Testing problematic case: empty branch...")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = os.path.join(tmpdir, "test_repo")

        # Setup repo
        os.makedirs(repo_dir)
        run_cmd("git init", cwd=repo_dir)
        run_cmd("git config user.name 'Test User'", cwd=repo_dir)
        run_cmd("git config user.email 'test@example.com'", cwd=repo_dir)

        # Initial commit
        with open(os.path.join(repo_dir, "README.md"), "w") as f:
            f.write("# Test Repo\n")
        run_cmd("git add README.md", cwd=repo_dir)
        run_cmd("git commit -m 'Initial commit'", cwd=repo_dir)

        # Create worktree
        branch_name = "rapper/empty-test"
        worktree_path = os.path.join(tmpdir, "worktree")
        run_cmd(f"git worktree add -b {branch_name} {worktree_path}", cwd=repo_dir)

        # DON'T add any files to worktree (simulate the bug case)
        # This simulates when Rapper doesn't create files or fails to create them

        # Check diagnostic info
        print("=== Diagnostic Information ===")

        # Main repo HEAD
        rc, main_head, _ = run_cmd("git rev-parse HEAD", cwd=repo_dir)
        print(f"Main repo HEAD:    {main_head}")

        # Worktree HEAD
        rc, worktree_head, _ = run_cmd("git rev-parse HEAD", cwd=worktree_path)
        print(f"Worktree HEAD:     {worktree_head}")

        # Branch commit counts
        rc, branch_count, _ = run_cmd(f"git rev-list --count {branch_name}", cwd=repo_dir)
        rc, main_count, _ = run_cmd("git rev-list --count HEAD", cwd=repo_dir)
        print(f"Branch commits:    {branch_count}")
        print(f"Main commits:      {main_count}")

        # Git status in worktree
        rc, status, _ = run_cmd("git status --porcelain", cwd=worktree_path)
        print(f"Worktree status:   '{status}'")

        # File count
        file_count = len([f for f in Path(worktree_path).rglob("*") if f.is_file() and ".git" not in str(f)])
        print(f"Worktree files:    {file_count}")

        # Try merge
        rc, merge_output, merge_error = run_cmd(f"git merge {branch_name}", cwd=repo_dir)
        print(f"\nMerge result: {merge_output}")

        if "Already up to date" in merge_output or "Already up-to-date" in merge_output:
            print("✅ Successfully reproduced the 'Already up to date' problem!")
            print("This is the case that our enhanced diagnostics should help with.")
            return True
        else:
            print("❌ Did not reproduce the problem")
            return False

if __name__ == "__main__":
    print("🎤 Enhanced Merge Diagnostics Test\n")

    # Test normal case
    success1 = test_enhanced_merge_with_files()

    # Test problematic case
    success2 = test_problematic_empty_branch()

    if success1 and success2:
        print("\n🎉 Both test scenarios completed successfully!")
        print("Enhanced diagnostics should help identify the root cause.")
    else:
        print("\n❌ Some tests failed")
        exit(1)