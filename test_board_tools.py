#!/usr/bin/env python3
"""
Test script for board tools integration.
Tests both the direct API and task_runner integration.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))

def test_board_tools_import():
    """Test that board tools can be imported."""
    print("Testing board tools import...")
    try:
        from board_tools import board_move_task, board_add_comment, board_get_task, board_my_tasks, board_create_task, get_board_config
        print("✅ Board tools imported successfully")
        return True
    except Exception as e:
        print(f"❌ Board tools import failed: {e}")
        return False

def test_config_loading():
    """Test configuration loading."""
    print("Testing configuration loading...")
    try:
        from board_tools import get_board_config
        config = get_board_config()
        expected_keys = ['enabled', 'api_url', 'api_key', 'agent_id']

        for key in expected_keys:
            if key not in config:
                print(f"❌ Missing config key: {key}")
                return False

        print(f"✅ Configuration loaded: {config}")
        return True
    except Exception as e:
        print(f"❌ Configuration loading failed: {e}")
        return False

def test_task_runner_integration():
    """Test task_runner integration."""
    print("Testing task_runner integration...")
    try:
        from task_runner import _get_board_tools_instructions, load_config

        # Test config loading
        config = load_config()
        if 'board_tools' not in config:
            print("❌ board_tools not in task_runner config")
            return False

        # Test instructions generation
        instructions = _get_board_tools_instructions()
        if 'AGENT BOARD TOOLS' not in instructions:
            print("❌ Board tools instructions not generated")
            return False

        print("✅ Task runner integration working")
        print(f"Instructions preview: {instructions[:150]}...")
        return True
    except Exception as e:
        print(f"❌ Task runner integration failed: {e}")
        return False

def test_api_connection():
    """Test API connection (optional, may fail if Agent Board not running)."""
    print("Testing API connection...")
    try:
        from board_tools import board_my_tasks
        result = board_my_tasks(limit=1)
        print(f"✅ API connection successful: {result[:100]}...")
        return True
    except Exception as e:
        print(f"⚠️  API connection failed (may be expected): {e}")
        return False  # Don't treat this as a failure

def main():
    """Run all tests."""
    print("=== Board Tools Integration Test ===\n")

    tests = [
        test_board_tools_import,
        test_config_loading,
        test_task_runner_integration,
        test_api_connection,
    ]

    passed = 0
    total = len(tests) - 1  # Don't count API test as required

    for test in tests:
        result = test()
        if result and test != test_api_connection:  # API test is optional
            passed += 1
        print()

    print(f"=== Results: {passed}/{total} core tests passed ===")

    if passed == total:
        print("🎉 All core tests passed! Board tools integration is working.")
        return True
    else:
        print("❌ Some tests failed. Check the output above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)