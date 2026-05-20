#!/usr/bin/env python3
"""
Test-First RED test for E2E-03 blocker: reviewer daemon live execution path
must wrap original task description in Reviewer Prompt Protocol, not pass
original task directly to Claude.

BACKGROUND / FAILURE EVIDENCE:
E2E-03 rerun showed rapper-1 `route_completed_to=review` worked, but reviewer-1
failed to approve:
- Harness: `task_7a27729d4fb60fe8`
- rapper-1 routed to review: `/home/zliao/.rapper/logs/daemon-rapper-1.log`: "routed to review"
- reviewer-1 failed closed: `journalctl --user -u abr-reviewer1-e2e03-gray-20260519000623.service`: "No complete review verdict blocks found in output"
- reviewer internal log: `/home/zliao/.rapper/tasks/20260519-001853-ogqk.log` shows reviewer executed original task prompt: "Create a small markdown report...", NOT Reviewer Prompt Protocol
- Output had no `<<<REVIEW_VERDICT_JSON>>>` / `<<<END_REVIEW_VERDICT_JSON>>>`

TEST REQUIREMENTS:
- Use reviewer config (agent_board.role=reviewer, claude.append_system_prompt_path, reviewer.verdict_sentinel_start/end)
- Construct live daemon/TaskRunner execution path
- Assert final prompt passed to TaskRunner/Claude contains Reviewer Prompt Protocol features:
  - review-only / reviewer role constraints (not execute original task)
  - `<<<REVIEW_VERDICT_JSON>>>`
  - `<<<END_REVIEW_VERDICT_JSON>>>`
- Assert final prompt is NOT just original task description
- Assert final prompt does not tell reviewer to create report files (implementation tasks)

RED GATE:
- pytest can collect/run
- Current code shows normal assertion failure (not timeout/INTERNALERROR/collection error)
- Tests use assert, not return True/False

PROHIBITED:
- No modification of lib/daemon.py / lib/task_runner.py / config / prompt files
- No implementation fixes
- No start/stop/kill/restart service
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon
from task_runner import Task, TaskRunner, generate_task_id


def _make_reviewer_config():
    """Create reviewer configuration that should trigger Reviewer Prompt Protocol."""
    return {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test-reviewer',
            'agent_id': 'reviewer-1',
            'role': 'reviewer',  # KEY: This should trigger reviewer protocol
            'poll_columns': ['review'],
            'poll_interval': 30,
            'webhook_port': 19999,
        },
        'claude': {
            'append_system_prompt_path': '/tmp/reviewer_system_prompt.txt',
            'settings_path': '/tmp/reviewer_settings.json'
        },
        'reviewer': {
            'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
            'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>',
            'fail_closed_on_parse_error': True,
        },
        'tasks': {'max_concurrent_tasks': 5},
        'logging': {'level': 'warning'},
    }


def _make_original_task_description():
    """Create original task description that should NOT be passed directly to Claude."""
    return "Create a small markdown report summarizing the recent changes to the authentication system"


def _make_mock_board_task():
    """Create mock board task from review column."""
    return {
        'id': 'task_live_test_123',
        'title': 'Review auth changes',
        'description': _make_original_task_description(),
        'column': 'review',
        'assignee': None,
        'implementedBy': 'rapper-1',
        'reviewState': 'pending',
        'workdir': '/app/test-project'
    }


class MockTask:
    """Mock Task object for testing prompt injection."""
    def __init__(self, task_id=None, prompt=None):
        self.id = task_id or generate_task_id()
        self.prompt = prompt or _make_original_task_description()
        self.workdir = '/app/test-project'
        self.status = 'pending'
        self.board_task_id = 'task_live_test_123'
        self.progress = []


class TestReviewerDaemonLivePromptInjection(unittest.TestCase):
    """Test that reviewer daemon live execution path injects Reviewer Prompt Protocol."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create mock system prompt file
        self.system_prompt_path = '/tmp/reviewer_system_prompt.txt'
        with open(self.system_prompt_path, 'w') as f:
            f.write("You are a code reviewer agent.")

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        # Clean up mock files
        for path in [self.system_prompt_path]:
            if os.path.exists(path):
                os.unlink(path)

    def _create_reviewer_daemon_with_config(self, config_dict):
        """Create reviewer daemon with given config and mocked dependencies."""
        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(config_dict, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config_dict

            daemon = RapperDaemon(self.config_path, 'reviewer-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods
            daemon.client.get_tasks = MagicMock()
            daemon.client.claim_task = MagicMock(return_value=True)
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.update_task_metadata = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            return daemon

    def test_reviewer_daemon_live_execution_applies_reviewer_prompt_protocol(self):
        """Test that reviewer daemon live execution path injects Reviewer Prompt Protocol."""
        # Setup: Create reviewer daemon with reviewer role config
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        # Create original task description (what should NOT be passed directly to Claude)
        original_task_prompt = _make_original_task_description()

        # Create mock internal task as daemon would
        board_task = _make_mock_board_task()
        internal_task = Task(
            id=generate_task_id(),
            name=board_task.get('title', f"board-{board_task['id']}"),
            prompt=board_task.get('description', ''),  # This is the original prompt
            workdir=board_task.get('workdir') or '/app/test',
            status='pending',
            board_task_id=board_task['id']
        )

        # Mock TaskRunner to capture the final prompt passed to Claude
        captured_claude_prompt = None
        original_run_task_sync = daemon.task_runner._run_task_sync

        def capture_claude_prompt(task, *args, **kwargs):
            nonlocal captured_claude_prompt
            captured_claude_prompt = task.prompt
            # Don't actually run Claude - just capture the prompt
            task.status = 'completed'
            task.result = 'Mock completed'
            return task

        daemon.task_runner._run_task_sync = capture_claude_prompt

        # Execute: Run the live daemon execution path
        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task['id'], internal_task)

        # Assert: The final prompt should contain Reviewer Prompt Protocol features
        self.assertIsNotNone(captured_claude_prompt,
            "TaskRunner._run_task_sync should have been called with a prompt")

        # RED ASSERTION 1: Final prompt should contain reviewer role constraints
        # (not execute original task, only review)
        assert "You are Agent Board Reviewer" in captured_claude_prompt, \
            f"Expected reviewer protocol preamble in final prompt. Got: {captured_claude_prompt[:500]}..."

        assert "You MUST NOT modify source code" in captured_claude_prompt, \
            f"Expected reviewer constraint in final prompt. Got: {captured_claude_prompt[:500]}..."

        # RED ASSERTION 2: Final prompt should contain verdict sentinels
        assert "<<<REVIEW_VERDICT_JSON>>>" in captured_claude_prompt, \
            f"Expected verdict start sentinel in final prompt. Got: {captured_claude_prompt[:500]}..."

        assert "<<<END_REVIEW_VERDICT_JSON>>>" in captured_claude_prompt, \
            f"Expected verdict end sentinel in final prompt. Got: {captured_claude_prompt[:500]}..."

        # RED ASSERTION 3: Final prompt should NOT be just the original task description
        assert captured_claude_prompt != original_task_prompt, \
            f"Final prompt should NOT be just original task description. Expected reviewer protocol wrapper, got: {captured_claude_prompt[:200]}..."

        # RED ASSERTION 4: Original task should be embedded as context, not as instruction
        assert "ORIGINAL TASK:" in captured_claude_prompt, \
            f"Expected original task to be embedded as context under 'ORIGINAL TASK:' section. Got: {captured_claude_prompt[:1000]}..."

        # RED ASSERTION 5: Final prompt should not tell reviewer to create implementation files
        # (The original task "Create a small markdown report" should not be the main instruction)
        lines_after_original_task = captured_claude_prompt.split("ORIGINAL TASK:")[1] if "ORIGINAL TASK:" in captured_claude_prompt else ""
        lines_before_original_task = captured_claude_prompt.split("ORIGINAL TASK:")[0] if "ORIGINAL TASK:" in captured_claude_prompt else captured_claude_prompt

        # The main instruction should be about reviewing, not creating reports
        assert "verify completed implementation" in lines_before_original_task.lower() or "review" in lines_before_original_task.lower(), \
            f"Expected main instruction to be about reviewing/verification, not implementation. Got: {lines_before_original_task[:300]}..."

    def test_reviewer_task_runner_prompt_processing_applies_protocol(self):
        """Test TaskRunner._process_prompt directly applies reviewer protocol for reviewer role."""
        # Setup: Create TaskRunner with reviewer config
        config = _make_reviewer_config()
        task_runner = TaskRunner(config=config)

        original_prompt = _make_original_task_description()
        task_id = generate_task_id()
        board_task_id = 'task_test_protocol'
        workdir = '/app/test'

        # Execute: Process the prompt through TaskRunner (this should apply reviewer protocol)
        final_prompt = task_runner._process_prompt(
            prompt=original_prompt,
            task_id=task_id,
            board_task_id=board_task_id,
            workdir=workdir
        )

        # RED ASSERTION: TaskRunner._process_prompt should apply reviewer protocol
        assert "You are Agent Board Reviewer reviewer-1" in final_prompt, \
            f"Expected _process_prompt to apply reviewer protocol. Got: {final_prompt[:300]}..."

        assert "<<<REVIEW_VERDICT_JSON>>>" in final_prompt, \
            f"Expected verdict sentinels from _process_prompt. Got: {final_prompt[:500]}..."

        assert final_prompt != original_prompt, \
            f"_process_prompt should transform original prompt with reviewer protocol. Got: {final_prompt[:200]}..."

    def test_non_reviewer_role_does_not_apply_protocol(self):
        """Test that non-reviewer roles do not get reviewer protocol (control test)."""
        # Setup: Create config with rapper role (not reviewer)
        config = _make_reviewer_config()
        config['agent_board']['role'] = 'rapper'  # NOT reviewer

        task_runner = TaskRunner(config=config)
        original_prompt = _make_original_task_description()

        # Execute: Process prompt with rapper role
        final_prompt = task_runner._process_prompt(
            prompt=original_prompt,
            task_id=generate_task_id(),
            board_task_id='task_rapper_test'
        )

        # Assert: Should NOT apply reviewer protocol for non-reviewer roles
        assert "You are Agent Board Reviewer" not in final_prompt, \
            f"Non-reviewer role should NOT get reviewer protocol. Got: {final_prompt[:300]}..."

        assert "<<<REVIEW_VERDICT_JSON>>>" not in final_prompt, \
            f"Non-reviewer role should NOT get verdict sentinels. Got: {final_prompt[:500]}..."

    def test_missing_reviewer_config_does_not_break_processing(self):
        """Test that missing reviewer config sections don't break prompt processing."""
        # Setup: Config with reviewer role but missing reviewer section
        config = {
            'agent_board': {
                'role': 'reviewer',
                'agent_id': 'reviewer-1'
            }
            # Missing 'reviewer' section with sentinels
        }

        task_runner = TaskRunner(config=config)
        original_prompt = _make_original_task_description()

        # Execute: Should not crash even with incomplete config
        final_prompt = task_runner._process_prompt(
            prompt=original_prompt,
            task_id=generate_task_id(),
        )

        # Assert: Should still apply reviewer protocol with defaults
        assert "You are Agent Board Reviewer reviewer-1" in final_prompt, \
            f"Should apply reviewer protocol even with incomplete config. Got: {final_prompt[:300]}..."


if __name__ == '__main__':
    unittest.main()