#!/usr/bin/env python3
"""
TEST-SUPP-04 REWORK: Reviewer TaskRunner Claim Auth Path Tests

Tests that BUG-02 fix works correctly: reviewer daemon TaskRunner internal claim
uses correct Board config/API key and skips duplicate claims appropriately.

REQUIREMENTS:
- All tests use mocks only, no real processes
- Tests must finish within 10 seconds
- Tests verify the real implementation works correctly after fixes
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add the lib directory to sys.path so we can import the actual modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'lib'))


class TestReviewerTaskRunnerClaimAuth:
    """Test suite verifying reviewer TaskRunner claim authorization works correctly."""

    def test_claim_function_accepts_and_uses_provided_config(self):
        """
        FIXED: claim_board_task_if_provided() properly uses provided config
        instead of calling load_config() when config is provided.
        """
        from task_runner import claim_board_task_if_provided

        reviewer_config = {
            'agent_board': {
                'api_key': 'sk-reviewer-correct-key',
                'agent_id': 'reviewer-1',
                'url': 'http://localhost:3456'
            }
        }

        # Create mock task
        task = Mock()
        task.board_task_id = 'task_reviewer_owned'

        # Mock the AgentBoardClient - it's imported locally inside the function
        with patch('daemon.AgentBoardClient') as mock_client_class:
            mock_client = Mock()
            mock_client.claim_task.return_value = True
            mock_client_class.return_value = mock_client

            # Call the function with reviewer config
            result = claim_board_task_if_provided(task, config=reviewer_config)

            # Verify the function used the provided config
            mock_client_class.assert_called_once_with(
                'http://localhost:3456',
                'sk-reviewer-correct-key'
            )
            mock_client.claim_task.assert_called_once_with('task_reviewer_owned', 'reviewer-1')
            assert result is True

    def test_taskrunner_passes_config_to_claim_function(self):
        """
        FIXED: TaskRunner passes its stored config to claim_board_task_if_provided().
        """
        from task_runner import TaskRunner

        reviewer_config = {
            'agent_board': {
                'api_key': 'sk-reviewer-token',
                'agent_id': 'reviewer-1',
                'url': 'http://localhost:3456'
            }
        }

        # Create TaskRunner with reviewer config
        task_runner = TaskRunner(config=reviewer_config)

        # Verify config is stored
        assert task_runner.config == reviewer_config

        # Mock claim function to track what config gets passed
        with patch('task_runner.claim_board_task_if_provided') as mock_claim:
            mock_claim.return_value = True

            # Create real task object to avoid JSON serialization issues
            from task_runner import Task
            task = Task(
                id='test-task-123',
                name='test task',
                prompt='test prompt',
                workdir='/test',
                board_task_id='task_needs_reviewer_config'
            )

            # Call _run_task_sync to trigger claim
            with patch.object(task_runner, '_build_claude_command'), \
                 patch('task_runner.subprocess.Popen') as mock_popen, \
                 patch('builtins.open', mock=Mock()), \
                 patch('task_runner.write_audit_event'), \
                 patch('task_runner.time.time', return_value=1234567890), \
                 patch('task_runner.load_config', return_value={}):

                # Create a proper mock process that won't hang
                mock_process = Mock()
                mock_process.pid = 12345

                # Mock stdout with proper iteration behavior - add end marker
                mock_process.stdout = iter([
                    '{"type": "result", "result": "done"}\n'
                ])

                # Mock wait() method properly - it should not hang
                mock_process.wait = Mock(return_value=None)
                mock_process.returncode = 0
                mock_popen.return_value = mock_process

                task_runner._run_task_sync(task, skip_claim=False)

            # Verify claim function was called with correct config
            mock_claim.assert_called_once_with(task, config=reviewer_config)

    def test_daemon_passes_config_to_taskrunner_constructor(self):
        """
        FIXED: RapperDaemon passes its config to TaskRunner constructor.
        """
        from daemon import RapperDaemon

        reviewer_config = {
            'agent_board': {
                'api_key': 'sk-daemon-reviewer-key',
                'agent_id': 'reviewer-1',
                'url': 'http://localhost:3456',
                'webhook_port': 18789,  # Add required webhook_port
                'poll_interval': 30
            }
        }

        # Mock TaskRunner constructor to verify config is passed
        with patch('daemon.TaskRunner') as mock_taskrunner_class, \
             patch('daemon.AgentBoardClient'), \
             patch('daemon.init_db'), \
             patch.object(RapperDaemon, '_load_config', return_value=reviewer_config):

            mock_taskrunner = Mock()
            mock_taskrunner_class.return_value = mock_taskrunner

            # Create daemon (triggers TaskRunner creation)
            daemon = RapperDaemon('/fake/config.yaml')

            # Verify TaskRunner was created with config
            mock_taskrunner_class.assert_called_once_with(config=reviewer_config)

    def test_correct_api_key_allows_reviewer_task_claim_success(self):
        """
        FIXED: Using correct reviewer config allows successful task claiming.
        """
        from task_runner import claim_board_task_if_provided

        # Reviewer daemon config (correct)
        reviewer_config = {
            'agent_board': {
                'api_key': 'sk-reviewer-abc123',
                'agent_id': 'reviewer-1',
                'url': 'http://localhost:3456'
            }
        }

        # Create task that belongs to reviewer
        task = Mock()
        task.board_task_id = 'task_reviewer_owned'

        # Mock the AgentBoardClient - imported locally in the function
        with patch('daemon.AgentBoardClient') as mock_client_class:
            mock_client = Mock()
            mock_client.claim_task.return_value = True  # Success with correct key
            mock_client_class.return_value = mock_client

            # Call claim function with correct reviewer config
            claim_success = claim_board_task_if_provided(task, config=reviewer_config)

            # Should succeed with correct API key
            assert claim_success, (
                "Task claim should succeed when correct reviewer API key is provided!"
            )

            # Verify correct API key was used
            mock_client_class.assert_called_once_with(
                'http://localhost:3456',
                'sk-reviewer-abc123'
            )

    def test_reviewer_role_skips_duplicate_claims(self):
        """
        FIXED: Reviewer role auto-detects and skips internal claim when appropriate.
        """
        from task_runner import claim_board_task_if_provided

        reviewer_config = {
            'agent_board': {
                'role': 'reviewer',
                'agent_id': 'reviewer-1',
                'url': 'http://localhost:3456'
            }
        }

        task = Mock()
        task.board_task_id = 'task_already_claimed_by_daemon'

        # Mock environment variable that indicates daemon context
        with patch.dict('os.environ', {'RAPPER_DAEMON_CONTEXT': '1'}):
            # Mock AgentBoardClient (shouldn't be called due to skip logic)
            with patch('daemon.AgentBoardClient') as mock_client_class:
                # Call claim function with reviewer config in daemon context
                result = claim_board_task_if_provided(task, config=reviewer_config)

                # Should skip claim and return True (daemon context detected)
                assert result is True, "Should skip duplicate claim in daemon context"

                # AgentBoardClient should not be instantiated due to skip logic
                mock_client_class.assert_not_called()

    def test_claim_function_signature_accepts_config_parameter(self):
        """
        FIXED: claim_board_task_if_provided() function signature supports config parameter.
        """
        from task_runner import claim_board_task_if_provided
        import inspect

        # Check function signature
        sig = inspect.signature(claim_board_task_if_provided)
        params = list(sig.parameters.keys())

        # Should have both 'task' and 'config' parameters
        assert 'task' in params, "Function should accept 'task' parameter"
        assert 'config' in params, "Function should accept 'config' parameter"

        # Verify config parameter has default value
        config_param = sig.parameters['config']
        assert config_param.default is None, "Config parameter should default to None"

        # Test that function can actually be called with config
        task = Mock()
        task.board_task_id = None  # No board task ID to avoid actual claiming logic

        test_config = {'agent_board': {'test': 'config'}}

        # This should not raise TypeError
        try:
            result = claim_board_task_if_provided(task, config=test_config)
            # Should return True when no board_task_id is set
            assert result is True
        except TypeError as e:
            pytest.fail(f"Function should accept config parameter, but got TypeError: {e}")



if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])