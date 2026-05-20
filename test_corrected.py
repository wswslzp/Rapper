#!/usr/bin/env python3
"""
Corrected version of the failing test with proper patch path
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))

from task_runner import claim_board_task_if_provided, Task, generate_task_id
from unittest.mock import Mock, patch
import task_runner

def test_reviewer_execution_skips_claim_when_daemon_role():
    """Test that reviewer execution properly skips claim when running as daemon."""

    # Create reviewer config
    reviewer_config = {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'reviewer-api-key',
            'agent_id': 'reviewer-1',
            'role': 'reviewer'  # This should disable claim in TaskRunner
        }
    }

    task = Task(
        id=generate_task_id(),
        name="reviewer-daemon-task",
        prompt="Daemon reviewer task",
        workdir="/app/test-project",
        status="pending",
        board_task_id="task_daemon_reviewer"
    )

    # CORRECTED: Use proper patch path
    with patch.object(task_runner, 'load_config') as mock_config:
        mock_config.return_value = reviewer_config

        # Mock the daemon import to control the AgentBoardClient
        with patch.dict('sys.modules', {'daemon': Mock()}):
            mock_daemon = sys.modules['daemon']
            MockClient = Mock()
            mock_daemon.AgentBoardClient = MockClient

            # Should succeed by skipping claim (not calling AgentBoardClient)
            success = claim_board_task_if_provided(task)

            print(f"Success: {success} (should be True)")
            print(f"MockClient called: {MockClient.called} (should be False)")
            print(f"MockClient call count: {MockClient.call_count} (should be 0)")

            if success and not MockClient.called:
                print("✅ TEST PASSED: Reviewer role skips claim correctly")
                return True
            else:
                print("❌ TEST FAILED: Reviewer role did not skip claim")
                return False

def test_reviewer_taskrunner_uses_different_config():
    """Test that reviewer execution uses reviewer-specific board config."""

    # Create reviewer config
    reviewer_config = {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'reviewer-api-key',  # Correct key for reviewer
            'agent_id': 'reviewer-1',       # Correct agent_id for reviewer
            'role': 'normal-rapper'  # NOT reviewer, so claim should happen
        }
    }

    task = Task(
        id=generate_task_id(),
        name="reviewer-task",
        prompt="Review the auth implementation",
        workdir="/app/test-project",
        status="pending",
        board_task_id="task_reviewer_claim_test"
    )

    # CORRECTED: Use proper patch path
    with patch.object(task_runner, 'load_config') as mock_config:
        mock_config.return_value = reviewer_config

        # Mock the daemon import to control the AgentBoardClient
        with patch.dict('sys.modules', {'daemon': Mock()}):
            mock_daemon = sys.modules['daemon']
            MockClient = Mock()
            mock_daemon.AgentBoardClient = MockClient

            # Attempt to claim board task - should use reviewer API key
            success = claim_board_task_if_provided(task)

            print(f"Success: {success}")
            print(f"MockClient call count: {MockClient.call_count}")
            if MockClient.call_count > 0:
                args, kwargs = MockClient.call_args_list[0]
                print(f"MockClient called with: url={args[0]}, api_key={args[1]}")

                expected_url = 'http://localhost:3456'
                expected_api_key = 'reviewer-api-key'

                if args[0] == expected_url and args[1] == expected_api_key:
                    print("✅ TEST PASSED: Correct reviewer config used")
                    return True
                else:
                    print(f"❌ TEST FAILED: Wrong config used. Expected: {expected_url}, {expected_api_key}")
                    return False
            else:
                print("❌ TEST FAILED: MockClient was not called")
                return False

if __name__ == '__main__':
    print("Running corrected tests...\n")

    print("=== Test 1: Reviewer role skip ===")
    test1_result = test_reviewer_execution_skips_claim_when_daemon_role()

    print("\n=== Test 2: Reviewer config usage ===")
    test2_result = test_reviewer_taskrunner_uses_different_config()

    print(f"\n=== Results ===")
    print(f"Test 1 (role skip): {'PASS' if test1_result else 'FAIL'}")
    print(f"Test 2 (config usage): {'PASS' if test2_result else 'FAIL'}")

    if test1_result and test2_result:
        print("🎉 All corrected tests passed!")
    else:
        print("❌ Some tests failed")