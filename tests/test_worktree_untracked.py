#!/usr/bin/env python3
"""RED test: verify auto_commit_worktree includes untracked new files.

Bug: auto_commit_worktree() in lib/task_runner.py uses git add -u
which excludes untracked new files. This test creates a temp git repo
with a worktree, adds an untracked file, calls auto_commit_worktree(),
and asserts the untracked file IS in the commit.

Expected: FAIL (RED) on current code if it uses git add -u.
"""

import subprocess
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.task_runner import auto_commit_worktree, Task


def _make_test_git_repo():
    """Helper: create a temp git repo with a worktree.
    Returns (repo_dir, worktree_dir, tmpdir_root).
    """
    tmpdir = tempfile.mkdtemp()
    repo_dir = os.path.join(tmpdir, "repo")
    os.makedirs(repo_dir)

    # Init repo
    subprocess.run(["git", "init", repo_dir], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", repo_dir, "config", "user.email", "test@test.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", repo_dir, "config", "user.name", "Test"],
        check=True,
    )

    # Initial commit
    test_file = os.path.join(repo_dir, "README.md")
    with open(test_file, "w") as f:
        f.write("# Test Repo\n")
    subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", repo_dir, "commit", "-m", "initial commit"],
        check=True,
        capture_output=True,
    )

    # Create worktree on a new branch
    wt_dir = os.path.join(tmpdir, "worktrees", "test-task")
    os.makedirs(os.path.dirname(wt_dir), exist_ok=True)
    subprocess.run(
        [
            "git", "-C", repo_dir, "worktree", "add",
            wt_dir, "-b", "rapper/test-untracked",
        ],
        check=True,
        capture_output=True,
    )

    return repo_dir, wt_dir, tmpdir


def test_untracked_file_in_commit():
    """Create untracked file, auto-commit, assert it IS in the commit.

    This MUST FAIL if auto_commit_worktree uses git add -u
    (which excludes untracked files).
    """
    repo_dir, wt_dir, tmpdir = _make_test_git_repo()

    try:
        # Add a NEW untracked file (never tracked before)
        new_file = os.path.join(wt_dir, "new_module.py")
        with open(new_file, "w") as f:
            f.write("print('hello from new file')\n")

        # Confirm it's untracked
        status = subprocess.run(
            ["git", "-C", wt_dir, "status", "--porcelain"],
            capture_output=True, text=True,
        )
        assert "?? new_module.py" in status.stdout, (
            f"Expected untracked file, got:\n{status.stdout}"
        )

        # Create Task with worktree_path set
        task = Task(
            id="test-untracked-001",
            name="test-untracked-files",
            prompt="test",
            workdir=wt_dir,
            worktree_path=wt_dir,
            branch_name="rapper/test-untracked",
        )

        # Run auto_commit_worktree
        success = auto_commit_worktree(task)
        assert success, "auto_commit_worktree should return True"

        # --- THE KEY ASSERTION ---
        # The untracked file MUST be in the commit.
        # If git add -u was used, this FAILS (RED).
        diff_result = subprocess.run(
            ["git", "-C", wt_dir, "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True,
        )
        committed = diff_result.stdout.strip().split("\n")
        assert "new_module.py" in committed, (
            f"BUG: untracked file NOT in commit!\n"
            f"Files committed: {committed}\n"
            f"(git add -u excludes untracked files — should use git add -A)"
        )

        print("✓ test_untracked_file_in_commit PASSED — untracked file IS in commit")

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    test_untracked_file_in_commit()