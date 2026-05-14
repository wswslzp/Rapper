#!/usr/bin/env python3
"""
[TEST-SUPP-003] Tests for parallel Rapper execution validation

Tests to prevent data corruption from concurrent tasks in the same repo without --worktree.
This is the RED phase - tests should FAIL initially since no parallel detection exists.

Scenarios:
1. Same repo + no --worktree → REJECT with error
2. Different repos → ALLOW parallel execution
3. Same repo + --worktree → ALLOW parallel execution
"""

import subprocess
import sys
import os
import tempfile
import shutil
import time
from pathlib import Path

# Add the parent directory to Python path so we can import from lib/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _create_test_repo(name: str) -> str:
    """Helper: Create a temporary git repository for testing."""
    tmpdir = tempfile.mkdtemp()
    repo_dir = os.path.join(tmpdir, name)
    os.makedirs(repo_dir)

    # Initialize git repo
    subprocess.run(["git", "init", repo_dir], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo_dir, "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", repo_dir, "config", "user.name", "Test User"], check=True)

    # Create initial commit so repo has a valid HEAD
    test_file = os.path.join(repo_dir, "README.md")
    with open(test_file, "w") as f:
        f.write(f"# Test repository: {name}\n")
    subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m", "Initial commit"],
                   check=True, capture_output=True)

    return repo_dir


def _simulate_rapper_background_call(name: str, workdir: str, use_worktree: bool = False) -> tuple[int, str]:
    """
    Simulate calling rapper --background by directly invoking the bash script.

    Returns (exit_code, output) - exit_code 0 means success, non-zero means rejection.
    This is our integration test for the actual rapper script logic.
    """
    rapper_script = "/app/rapper/rapper"
    cmd = [rapper_script, "--background", name, "-p", f"echo 'Test task {name}'", "--workdir", workdir]

    if use_worktree:
        cmd.append("--worktree")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10  # Quick timeout for testing
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return 1, "TIMEOUT: Task launch took too long"
    except Exception as e:
        return 1, f"ERROR: {str(e)}"


def test_same_repo_no_worktree_rejected():
    """
    TRACER BULLET: Test that starting two tasks in the same repo without --worktree is rejected.

    This is the core safety test - prevent data corruption from concurrent execution.
    Should FAIL initially because no parallel detection exists.
    """
    repo_dir = _create_test_repo("same-repo-test")

    try:
        # Start first background task
        exit_code1, output1 = _simulate_rapper_background_call("first-task", repo_dir)

        if exit_code1 != 0:
            print(f"   ⚠️  First task failed to start: {output1}")
            return  # Can't test parallel rejection if first task doesn't start

        print(f"✓ First task started successfully")

        # Give first task time to be recorded
        time.sleep(2)

        # Try to start second task in same repo - this should be REJECTED
        exit_code2, output2 = _simulate_rapper_background_call("second-task", repo_dir)

        if exit_code2 == 0:
            # Parallel task was allowed - this means RED phase test should fail
            print("   ❌ CRITICAL: Second task was allowed in same repo without --worktree!")
            print(f"   This indicates NO parallel detection exists (expected for RED phase)")
            return False  # Test "failed" as expected in RED phase
        else:
            # Task was rejected - check if it's for the right reason
            output_lower = output2.lower()
            parallel_keywords = ["parallel", "concurrent", "conflict", "already running", "worktree"]

            if any(keyword in output_lower for keyword in parallel_keywords):
                print(f"✓ Parallel execution correctly rejected: {output2}")
                return True  # Rejection logic already exists
            else:
                print(f"⚠️  Task rejected for unexpected reason: {output2}")
                return False  # Wrong kind of failure

    finally:
        # Cleanup: kill any background tasks
        subprocess.run(["pkill", "-f", "first-task"], capture_output=True)
        subprocess.run(["pkill", "-f", "second-task"], capture_output=True)
        shutil.rmtree(os.path.dirname(repo_dir), ignore_errors=True)


def test_different_repos_allowed():
    """
    Test that parallel execution is allowed when tasks run in different repositories.
    """
    repo1_dir = _create_test_repo("repo1")
    repo2_dir = _create_test_repo("repo2")

    try:
        # Start task in first repo
        exit_code1, output1 = _simulate_rapper_background_call("task-repo1", repo1_dir)

        if exit_code1 != 0:
            print(f"   ⚠️  First task failed: {output1}")
            return False

        print(f"✓ First task started in repo1")
        time.sleep(1)

        # Start task in second repo - should be allowed
        exit_code2, output2 = _simulate_rapper_background_call("task-repo2", repo2_dir)

        if exit_code2 == 0:
            print(f"✓ Second task allowed in different repo")
            return True
        else:
            print(f"❌ Different repos were incorrectly rejected: {output2}")
            return False

    finally:
        subprocess.run(["pkill", "-f", "task-repo1"], capture_output=True)
        subprocess.run(["pkill", "-f", "task-repo2"], capture_output=True)
        shutil.rmtree(os.path.dirname(repo1_dir), ignore_errors=True)
        shutil.rmtree(os.path.dirname(repo2_dir), ignore_errors=True)


def test_same_repo_with_worktree_allowed():
    """
    Test that parallel execution is allowed when using --worktree isolation.
    """
    repo_dir = _create_test_repo("worktree-test")

    try:
        # Start first task without worktree
        exit_code1, output1 = _simulate_rapper_background_call("main-task", repo_dir)

        if exit_code1 != 0:
            print(f"   ⚠️  Main task failed: {output1}")
            return False

        print(f"✓ Main task started")
        time.sleep(1)

        # Start second task with worktree - should be allowed
        exit_code2, output2 = _simulate_rapper_background_call("worktree-task", repo_dir, use_worktree=True)

        if exit_code2 == 0:
            print(f"✓ Worktree task allowed in same repo")
            return True
        else:
            print(f"❌ Worktree task was rejected: {output2}")
            return False

    finally:
        subprocess.run(["pkill", "-f", "main-task"], capture_output=True)
        subprocess.run(["pkill", "-f", "worktree-task"], capture_output=True)
        shutil.rmtree(os.path.dirname(repo_dir), ignore_errors=True)


def test_cli_interface_validation():
    """
    Test the rapper CLI interface directly to ensure it properly validates parallel execution.

    This tests the actual entry point where validation should occur.
    """
    repo_dir = _create_test_repo("cli-test")

    try:
        # Test the rapper script's argument parsing and validation
        rapper_script = "/app/rapper/rapper"

        # Check if rapper script exists and is executable
        if not os.path.exists(rapper_script):
            print(f"❌ Rapper script not found at {rapper_script}")
            return False

        if not os.access(rapper_script, os.X_OK):
            print(f"❌ Rapper script not executable: {rapper_script}")
            return False

        print(f"✓ Rapper script found and executable")

        # Test basic help functionality to ensure script works
        result = subprocess.run([rapper_script, "--help"], capture_output=True, text=True, timeout=5)
        if "background" not in result.stdout:
            print(f"❌ Rapper help output doesn't mention background tasks")
            return False

        print(f"✓ Rapper CLI interface functional")
        return True

    except Exception as e:
        print(f"❌ CLI interface test failed: {e}")
        return False

    finally:
        shutil.rmtree(os.path.dirname(repo_dir), ignore_errors=True)


if __name__ == "__main__":
    """
    Run all parallel execution validation tests.

    These tests should FAIL initially (RED phase) since no parallel detection exists.
    """
    print("🔴 Running parallel execution validation tests (RED phase)...\n")
    print("Note: These tests SHOULD FAIL initially - that's the point of TDD RED phase!\n")

    test_results = {}

    print("1. Testing CLI interface...")
    test_results["cli_interface"] = test_cli_interface_validation()
    print()

    print("2. Testing same repo conflict detection...")
    test_results["same_repo_conflict"] = test_same_repo_no_worktree_rejected()
    print()

    print("3. Testing different repos allowed...")
    test_results["different_repos"] = test_different_repos_allowed()
    print()

    print("4. Testing worktree isolation...")
    test_results["worktree_isolation"] = test_same_repo_with_worktree_allowed()
    print()

    # Analyze results
    passed_tests = [name for name, result in test_results.items() if result]
    failed_tests = [name for name, result in test_results.items() if not result]

    print("🔴 RED PHASE SUMMARY:")
    print(f"   - Tests passed: {len(passed_tests)} {passed_tests}")
    print(f"   - Tests failed: {len(failed_tests)} {failed_tests}")

    if "same_repo_conflict" in failed_tests:
        print("\n✓ GOOD: Same repo conflict test failed - parallel detection not implemented yet")
        print("   This is expected for RED phase!")
    else:
        print("\n⚠️  UNEXPECTED: Same repo conflict test passed - parallel detection may already exist")

    if len(failed_tests) > 0:
        print(f"\n✓ RED phase working correctly - {len(failed_tests)} tests appropriately failing")
        print("   Ready for GREEN phase implementation!")
    else:
        print("\n⚠️  All tests passed - parallel detection might already be implemented")

    print("\nNext step: Implement minimal parallel detection logic in do_background() function")