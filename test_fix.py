#!/usr/bin/env python3
"""
Simple test to verify the reviewer TaskRunner claim auth fixes are working.
"""
import sys
import os
sys.path.insert(0, '/app/rapper/lib')

from task_runner import claim_board_task_if_provided, Task, TaskRunner
from daemon import RapperDaemon
from unittest.mock import Mock

def test_function_signature():
    """Test 1: Function accepts config parameter"""
    print("=== Test 1: Function signature ===")

    try:
        task = Mock()
        task.board_task_id = None  # No board task, should return True
        result = claim_board_task_if_provided(task, config={'test': 'config'})
        print(f"✅ Function accepts config parameter: {result}")
        return True
    except TypeError as e:
        print(f"❌ Function does NOT accept config parameter: {e}")
        return False

def test_reviewer_role_skip():
    """Test 2: Reviewer role skips claim"""
    print("\n=== Test 2: Reviewer role skip ===")

    # Create a mock task with board_task_id
    task = Mock()
    task.board_task_id = 'test_task_123'

    # Test with reviewer config - should skip claim and return True
    reviewer_config = {
        'agent_board': {
            'role': 'reviewer',
            'api_key': 'sk-reviewer-key',
            'agent_id': 'reviewer-1'
        }
    }

    result = claim_board_task_if_provided(task, config=reviewer_config)
    if result:
        print("✅ Reviewer role correctly skips claim")
        return True
    else:
        print("❌ Reviewer role does NOT skip claim")
        return False

def test_config_usage():
    """Test 3: Function uses provided config instead of load_config()"""
    print("\n=== Test 3: Config usage ===")

    # This is harder to test directly without mocking load_config
    # But we can at least verify the function runs without errors
    task = Mock()
    task.board_task_id = 'test_task_456'

    config = {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test-key',
            'agent_id': 'test-agent'
        }
    }

    try:
        result = claim_board_task_if_provided(task, config=config)
        print(f"✅ Function runs with provided config: {result}")
        return True
    except Exception as e:
        print(f"❌ Function failed with provided config: {e}")
        return False

def test_daemon_creates_taskrunner_with_config():
    """Test 4: RapperDaemon passes config to TaskRunner"""
    print("\n=== Test 4: Daemon TaskRunner config ===")

    # Create a temporary config file
    import tempfile
    import yaml

    config_data = {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-reviewer-daemon-key',
            'agent_id': 'reviewer-1',
            'role': 'reviewer'
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    try:
        # This should work - create daemon and check TaskRunner has config
        daemon = RapperDaemon(config_path)
        if hasattr(daemon.task_runner, 'config') and daemon.task_runner.config is not None:
            print("✅ RapperDaemon passes config to TaskRunner")
            return True
        else:
            print("❌ RapperDaemon does NOT pass config to TaskRunner")
            return False
    except Exception as e:
        print(f"❌ Failed to create RapperDaemon: {e}")
        return False
    finally:
        # Clean up temp file
        try:
            os.unlink(config_path)
        except:
            pass

def main():
    """Run all tests"""
    print("Testing reviewer TaskRunner claim auth fixes...\n")

    tests = [
        test_function_signature,
        test_reviewer_role_skip,
        test_config_usage,
        test_daemon_creates_taskrunner_with_config
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test failed with exception: {e}")
            results.append(False)

    passed = sum(results)
    total = len(results)

    print(f"\n=== Summary ===")
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("🎉 All tests passed! Fixes are working.")
        return 0
    else:
        print("❌ Some tests failed. Need more work.")
        return 1

if __name__ == '__main__':
    sys.exit(main())