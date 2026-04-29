#!/usr/bin/env python3
"""
Tests for worktree isolation functionality.

Tests the _make_worktree_safe_prompt() function to ensure it properly
rewrites prompts for safe execution inside git worktrees.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.task_runner import _make_worktree_safe_prompt


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

        print("\n🎉 All worktree isolation tests passed!")

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Unexpected error: {e}")
        sys.exit(1)