#!/usr/bin/env python3
"""
Simple integration test to verify the progress reporting implementation.
"""

import sys
import os
import tempfile
from pathlib import Path

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))

def test_imports():
    """Test that all modules import correctly with the new changes."""
    try:
        from task_runner import load_config, post_board_comment, Task, generate_task_id
        print("✅ All imports successful")
        return True
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False

def test_load_config():
    """Test that load_config works with defaults."""
    try:
        from task_runner import load_config

        # Test with no config file
        config = load_config()

        # Verify defaults are set
        assert config["progress_reporting"]["enabled"] == True
        assert config["progress_reporting"]["report_every_n_tools"] == 5
        assert config["progress_reporting"]["board_url"] == "http://localhost:3456"
        assert config["agent_board"]["api_key"] == ""

        print("✅ load_config() works correctly with defaults")
        return True
    except Exception as e:
        print(f"❌ load_config() error: {e}")
        return False

def test_post_board_comment():
    """Test that post_board_comment function handles edge cases."""
    try:
        from task_runner import post_board_comment

        config = {
            "progress_reporting": {"board_url": "http://localhost:3456"},
            "agent_board": {"api_key": ""}
        }

        # Test with empty board_task_id (should return False)
        result = post_board_comment("", "test message", config)
        assert result == False

        result = post_board_comment(None, "test message", config)
        assert result == False

        print("✅ post_board_comment() handles edge cases correctly")
        return True
    except Exception as e:
        print(f"❌ post_board_comment() error: {e}")
        return False

def test_task_with_board_task_id():
    """Test that Task class works with board_task_id field."""
    try:
        from task_runner import Task, generate_task_id

        task_id = generate_task_id()
        board_task_id = "task_test123"

        # Create a task with board_task_id
        task = Task(
            id=task_id,
            name="test-task",
            prompt="Test task",
            workdir="/tmp",
            board_task_id=board_task_id
        )

        # Verify board_task_id is set
        assert task.board_task_id == board_task_id

        print("✅ Task class handles board_task_id correctly")
        return True
    except Exception as e:
        print(f"❌ Task class error: {e}")
        return False

def main():
    """Run all integration tests."""
    print("Running integration tests for progress reporting...")

    tests = [
        test_imports,
        test_load_config,
        test_post_board_comment,
        test_task_with_board_task_id,
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1
        else:
            print(f"Test failed: {test.__name__}")

    print(f"\nResults: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All integration tests passed!")
        return True
    else:
        print("❌ Some tests failed")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)