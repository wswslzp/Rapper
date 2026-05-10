#!/usr/bin/env python3
"""
Tests for worktree isolation functionality.

Tests the _make_worktree_safe_prompt() function to ensure it properly
rewrites prompts for safe execution inside git worktrees.
Also tests auto_commit_worktree() for post-task auto-commit.
"""

import subprocess
import sys
import os
import tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.task_runner import _make_worktree_safe_prompt, auto_commit_worktree, Task


def test_replaces_absolute_path_with_relative():
    """Test that absolute paths to main repo are replaced with relative paths."""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    # Test regular file path
    prompt = "Please edit /app/myrepo/src/foo.py"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Extract the actual prompt part after the guard
    parts = result.split("--- END GUARD ---")
    assert len(parts) > 1, "Could not find guard separator"
    prompt_part = parts[1].strip()

    # In the prompt part, absolute path should be replaced
    assert "/app/myrepo/src/foo.py" not in prompt_part
    # Should contain the relative path
    assert "./src/foo.py" in prompt_part
    print("✓ test_replaces_absolute_path_with_relative passed")


def test_replaces_path_with_trailing_slash():
    """Test that paths ending with '/' are properly replaced."""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    prompt = "Look in directory /app/myrepo/ for files"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Extract the prompt part after the guard
    parts = result.split("--- END GUARD ---")
    assert len(parts) > 1, "Could not find guard separator"
    prompt_part = parts[1].strip()

    # Should not contain the absolute path with trailing slash in prompt part
    assert "/app/myrepo/" not in prompt_part
    # Should contain "./"
    assert "./" in prompt_part
    print("✓ test_replaces_path_with_trailing_slash passed")


def test_replaces_exact_repo_root():
    """Test that exact repo root (without trailing /) is replaced with '.'"""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    # Test prompt ending with repo root
    prompt = "Read all files in /app/myrepo"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Extract the prompt part after the guard
    parts = result.split("--- END GUARD ---")
    assert len(parts) > 1, "Could not find guard separator"
    prompt_part = parts[1].strip()

    # Should not contain the absolute path in prompt part
    assert "/app/myrepo" not in prompt_part
    # Should contain the specific replacement
    assert "files in ." in prompt_part
    print("✓ test_replaces_exact_repo_root passed")


def test_does_not_replace_unrelated_paths():
    """Test that paths not matching the repo root are not replaced."""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    prompt = "Check /app/other-project/file.py and /some/random/path"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Extract the prompt part after the guard
    parts = result.split("--- END GUARD ---")
    assert len(parts) > 1, "Could not find guard separator"
    prompt_part = parts[1].strip()

    # These paths should remain unchanged in the prompt part
    assert "/app/other-project/file.py" in prompt_part
    assert "/some/random/path" in prompt_part
    print("✓ test_does_not_replace_unrelated_paths passed")


def test_guard_prepended():
    """Test that the worktree isolation guard is prepended to the prompt."""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    prompt = "This is a test prompt"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Should start with the guard
    assert result.startswith("⚠️ WORKTREE ISOLATION GUARD ⚠️")
    # Should contain the original prompt after the guard
    assert "This is a test prompt" in result
    # Guard should contain isolation instructions
    assert "CRITICAL RULES" in result
    assert "Use ONLY relative paths" in result
    print("✓ test_guard_prepended passed")


def test_guard_contains_worktree_path():
    """Test that the guard includes the worktree path information."""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    prompt = "Test prompt"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Guard should mention the worktree path
    assert worktree_path in result
    # Guard should mention the main repo path
    assert repo_workdir in result
    print("✓ test_guard_contains_worktree_path passed")


def test_complex_replacement_scenario():
    """Test a complex scenario with multiple path types."""
    repo_workdir = "/app/myrepo"
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    prompt = """
    Please do the following:
    1. Edit /app/myrepo/src/main.py
    2. Check files in /app/myrepo/
    3. Don't touch /app/other-project/file.py
    4. Update /app/myrepo and commit
    """

    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Extract the prompt part after the guard
    parts = result.split("--- END GUARD ---")
    assert len(parts) > 1, "Could not find guard separator"
    prompt_part = parts[1].strip()

    # Absolute repo paths should be replaced in prompt part
    assert "/app/myrepo/src/main.py" not in prompt_part
    assert "./src/main.py" in prompt_part

    assert "/app/myrepo/" not in prompt_part
    assert "./" in prompt_part

    # Unrelated paths should remain
    assert "/app/other-project/file.py" in prompt_part

    # Single repo root should become "."
    lines = prompt_part.split('\n')
    found_update_line = False
    for line in lines:
        if "Update" in line and "commit" in line:
            # This should be "Update . and commit" now
            assert "/app/myrepo" not in line
            assert "Update ." in line
            found_update_line = True
            break
    assert found_update_line, "Could not find the 'Update' line to verify replacement"

    print("✓ test_complex_replacement_scenario passed")


def test_repo_workdir_with_trailing_slash():
    """Test behavior when repo_workdir has trailing slash (should be normalized)."""
    repo_workdir = "/app/myrepo/"  # Note trailing slash
    worktree_path = "/app/myrepo/.claude/worktrees/test-task"

    prompt = "Edit /app/myrepo/file.py and check /app/myrepo/"
    result = _make_worktree_safe_prompt(prompt, repo_workdir, worktree_path)

    # Extract the prompt part after the guard
    parts = result.split("--- END GUARD ---")
    assert len(parts) > 1, "Could not find guard separator"
    prompt_part = parts[1].strip()

    # Should still work correctly in prompt part
    assert "/app/myrepo/file.py" not in prompt_part
    assert "./file.py" in prompt_part
    assert "/app/myrepo/" not in prompt_part
    print("✓ test_repo_workdir_with_trailing_slash passed")


def _make_test_git_repo() -> tuple[str, str]:
    """Helper: create a temp git repo with a worktree. Returns (repo_dir, worktree_dir)."""
    tmpdir = tempfile.mkdtemp()
    repo_dir = os.path.join(tmpdir, "repo")
    os.makedirs(repo_dir)

    # Init repo
    subprocess.run(["git", "init", repo_dir], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo_dir, "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", repo_dir, "config", "user.name", "Test"], check=True)

    # Initial commit
    test_file = os.path.join(repo_dir, "test.txt")
    with open(test_file, "w") as f:
        f.write("original\n")
    subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m", "init"], check=True, capture_output=True)

    # Create worktree
    wt_dir = os.path.join(tmpdir, "worktrees", "test-task")
    os.makedirs(os.path.dirname(wt_dir), exist_ok=True)
    subprocess.run(["git", "-C", repo_dir, "worktree", "add", wt_dir, "-b", "rapper/test-task"],
                   check=True, capture_output=True)

    return repo_dir, wt_dir


def test_auto_commit_worktree_commits_changes():
    """Test that auto_commit_worktree commits uncommitted changes in a worktree."""
    repo_dir, wt_dir = _make_test_git_repo()

    try:
        # Modify a file in the worktree (simulating what Claude does)
        test_file = os.path.join(wt_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("modified by claude\n")

        # Verify uncommitted changes exist
        result = subprocess.run(["git", "-C", wt_dir, "status", "--porcelain"],
                                capture_output=True, text=True)
        assert result.stdout.strip(), "Should have uncommitted changes before auto_commit"

        # Verify only init commit exists
        result = subprocess.run(["git", "-C", wt_dir, "rev-list", "--count", "HEAD"],
                                capture_output=True, text=True)
        assert result.stdout.strip() == "1", "Should have only 1 commit before auto_commit"

        # Create a mock Task
        task = Task(
            id="test-123",
            name="test-task",
            prompt="test",
            workdir=wt_dir,
            worktree_path=wt_dir,
            branch_name="rapper/test-task",
        )

        # Run auto_commit_worktree
        success = auto_commit_worktree(task)
        assert success, "auto_commit_worktree should return True on success"

        # Verify the worktree is now clean
        result = subprocess.run(["git", "-C", wt_dir, "status", "--porcelain"],
                                capture_output=True, text=True)
        assert not result.stdout.strip(), "Worktree should be clean after auto_commit"

        # Verify a new commit was created
        result = subprocess.run(["git", "-C", wt_dir, "rev-list", "--count", "HEAD"],
                                capture_output=True, text=True)
        assert result.stdout.strip() == "2", "Should have 2 commits after auto_commit"

        # Verify commit message contains task name
        result = subprocess.run(["git", "-C", wt_dir, "log", "-1", "--format=%s"],
                                capture_output=True, text=True)
        assert "test-task" in result.stdout, f"Commit msg should mention task name: {result.stdout}"

        print("✓ test_auto_commit_worktree_commits_changes passed")
    finally:
        import shutil
        shutil.rmtree(os.path.dirname(os.path.dirname(wt_dir)), ignore_errors=True)


def test_auto_commit_worktree_clean_is_noop():
    """Test that auto_commit_worktree returns True (success) on a clean worktree."""
    repo_dir, wt_dir = _make_test_git_repo()

    try:
        # No changes — worktree should be clean
        task = Task(
            id="test-456",
            name="test-clean",
            prompt="test",
            workdir=wt_dir,
            worktree_path=wt_dir,
            branch_name="rapper/test-task",
        )

        success = auto_commit_worktree(task)
        assert success, "auto_commit_worktree on clean worktree should return True"

        # Should still have only 1 commit
        result = subprocess.run(["git", "-C", wt_dir, "rev-list", "--count", "HEAD"],
                                capture_output=True, text=True)
        assert result.stdout.strip() == "1", "Should still have 1 commit (no new commit made)"

        print("✓ test_auto_commit_worktree_clean_is_noop passed")
    finally:
        import shutil
        shutil.rmtree(os.path.dirname(os.path.dirname(wt_dir)), ignore_errors=True)


def test_auto_commit_worktree_invalid_path():
    """Test that auto_commit_worktree returns False for invalid/missing worktree path."""
    task = Task(
        id="test-789",
        name="test-invalid",
        prompt="test",
        workdir="/nonexistent/path",
        worktree_path="/nonexistent/path",
        branch_name="rapper/test-invalid",
    )

    success = auto_commit_worktree(task)
    assert not success, "auto_commit_worktree on invalid path should return False"
    print("✓ test_auto_commit_worktree_invalid_path passed")


if __name__ == "__main__":
    """Run all tests."""
    print("Running worktree isolation tests...\n")

    try:
        test_replaces_absolute_path_with_relative()
        test_replaces_path_with_trailing_slash()
        test_replaces_exact_repo_root()
        test_does_not_replace_unrelated_paths()
        test_guard_prepended()
        test_guard_contains_worktree_path()
        test_complex_replacement_scenario()
        test_repo_workdir_with_trailing_slash()
        test_auto_commit_worktree_commits_changes()
        test_auto_commit_worktree_clean_is_noop()
        test_auto_commit_worktree_invalid_path()

        print("\n🎉 All worktree isolation tests passed!")

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)