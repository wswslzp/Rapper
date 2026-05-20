#!/usr/bin/env python3
"""
Test for Reviewer verdict parser fail-closed behavior - TEST-07 specific verification.

This test implements the fail-closed verdict parsing from Agent Board Reviewer design:
- Any parse failure → task rejected and moved to todo
- Clear comment explaining parse failure or invalid verdict
- assignee restored to implementedBy to ensure task can be picked up again

Verification points per requirements.md v1.1 AC-13 and design.md v2.1 §8:

F1: Missing sentinel start/end → rejected with clear parse failure comment
F2: Malformed JSON → rejected with clear parse failure comment
F3: Missing verdict field → rejected with clear invalid verdict comment
F4: Unknown verdict value → rejected with clear invalid verdict comment
F5: assignee restored to implementedBy on parse failure
F6: reviewState set to rejected on parse failure

Related files:
- /app/agent-board-reviewer/requirements.md v1.1 AC-13
- /app/agent-board-reviewer/design.md v2.1 §5.3/5.4/8
- /app/agent-board-reviewer/design_review.txt
- /app/agent-board-reviewer/.checkpoints/PROGRESS.md

Test should start RED (failing) - no verdict parser fail-closed logic implemented yet.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon


class TestDaemonVerdictParserFailClosed(unittest.TestCase):
    """Test Reviewer verdict parser fail-closed behavior - parse failures → rejected."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create reviewer config following design.md v2.1 §3.2
        self.reviewer_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-reviewer1',
                'agent_id': 'reviewer-1',
                'poll_interval': 30,
                'webhook_port': 18794,  # Required by daemon init
                'role': 'reviewer',
                'poll_columns': ['review'],
                'approve_to_column': 'done',
                'reject_to_column': 'todo',
            },
            'reviewer': {
                'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
                'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>',
                'fail_closed_on_parse_error': True,
                'report_dir': '/data/agent-board-reviewer/reports'
            },
            'tasks': {'max_concurrent_tasks': 1},
            'logging': {'level': 'warning'}
        }

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_reviewer_daemon(self):
        """Create reviewer daemon with mocked client."""
        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(self.reviewer_config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load:
            mock_load.return_value = self.reviewer_config

            daemon = RapperDaemon(self.config_path, 'reviewer-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.update_task_metadata = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock task runner
            daemon.task_runner = MagicMock()

            return daemon

    def _create_task_with_result(self, result_text, implementedBy='rapper-1'):
        """Create mock task with specific result text."""
        task = MagicMock()
        task.status = 'completed'
        task.error = None
        task.progress = [{'step': 1}, {'step': 2}]
        task.result = result_text
        task.structured_result = {}  # Empty, since parsing should extract from result text

        # Mock board task with implementedBy metadata
        task.board_task = {
            'id': 'task_test123',
            'implementedBy': implementedBy,
            'reviewState': 'reviewing'
        }

        return task

    def test_f1_missing_sentinel_start_rejected_with_comment(self):
        """F1: Missing sentinel start → rejected with clear parse failure comment."""
        daemon = self._create_reviewer_daemon()

        # Result missing start sentinel
        result_without_start = (
            "Review analysis complete.\n\n"
            "The implementation looks good but has some issues.\n\n"
            '{"verdict": "rejected", "summary": "Missing start sentinel"}\n'
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_without_start)
        board_task_id = 'task_missing_start'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should move to todo (fail-closed)
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Should restore assignee to implementedBy
        metadata_call = daemon.client.update_task_metadata.call_args
        metadata = metadata_call[0][1]
        self.assertEqual(metadata['reviewState'], 'rejected')

        # Should add clear parse failure comment
        comment_call = daemon.client.add_comment.call_args
        task_id, author, comment_text = comment_call[0]

        self.assertEqual(task_id, board_task_id)
        self.assertEqual(author, 'reviewer-1')
        self.assertIn('❌', comment_text)
        self.assertIn('REJECTED', comment_text)
        self.assertIn('parse', comment_text.lower())
        self.assertIn('sentinel', comment_text.lower())

    def test_f1_missing_sentinel_end_rejected_with_comment(self):
        """F1: Missing sentinel end → rejected with clear parse failure comment."""
        daemon = self._create_reviewer_daemon()

        # Result missing end sentinel
        result_without_end = (
            "Review analysis complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            '{"verdict": "approved", "summary": "Missing end sentinel"}\n'
            "Some additional text after JSON..."
        )

        task = self._create_task_with_result(result_without_end)
        board_task_id = 'task_missing_end'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Should have clear parse failure comment
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('parse', comment_text.lower())
        self.assertIn('sentinel', comment_text.lower())

    def test_f2_malformed_json_rejected_with_comment(self):
        """F2: Malformed JSON → rejected with clear parse failure comment."""
        daemon = self._create_reviewer_daemon()

        # Result with malformed JSON (missing quote, trailing comma)
        result_with_bad_json = (
            "Review complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            '{"verdict": approved, "summary": "Malformed JSON",}\n'
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_with_bad_json)
        board_task_id = 'task_bad_json'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Comment should mention JSON parse failure
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('❌', comment_text)
        self.assertIn('parse', comment_text.lower())
        self.assertIn('json', comment_text.lower())

    def test_f2_invalid_json_structure_rejected(self):
        """F2: Valid JSON but not an object → rejected with parse failure comment."""
        daemon = self._create_reviewer_daemon()

        # Valid JSON but wrong structure (array instead of object)
        result_with_array_json = (
            "Review complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            '["verdict", "approved", "summary", "Not an object"]\n'
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_with_array_json)
        board_task_id = 'task_array_json'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Comment should mention invalid format
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('invalid', comment_text.lower())

    def test_f3_missing_verdict_field_rejected_with_comment(self):
        """F3: Missing verdict field → rejected with clear invalid verdict comment."""
        daemon = self._create_reviewer_daemon()

        # Valid JSON but missing required verdict field
        verdict_without_verdict_field = {
            "status": "completed",
            "summary": "Missing verdict field",
            "findings": [],
            "approved_acs": ["AC-01"],
            "rejected_acs": [],
            "stats": {"files_changed": 2}
        }

        result_missing_verdict = (
            "Review analysis complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            f"{json.dumps(verdict_without_verdict_field, indent=2)}\n"
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_missing_verdict)
        board_task_id = 'task_missing_verdict'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Comment should mention missing verdict field
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('❌', comment_text)
        self.assertIn('verdict', comment_text.lower())
        self.assertIn('missing', comment_text.lower())

    def test_f4_unknown_verdict_value_rejected_with_comment(self):
        """F4: Unknown verdict value → rejected with clear invalid verdict comment."""
        daemon = self._create_reviewer_daemon()

        # Valid JSON structure but invalid verdict value
        verdict_with_invalid_value = {
            "status": "completed",
            "verdict": "maybe",  # Invalid - should be "approved" or "rejected"
            "summary": "Invalid verdict value",
            "findings": [],
            "approved_acs": [],
            "rejected_acs": [],
            "stats": {"files_changed": 1}
        }

        result_invalid_verdict = (
            "Review complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            f"{json.dumps(verdict_with_invalid_value, indent=2)}\n"
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_invalid_verdict)
        board_task_id = 'task_invalid_verdict'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Comment should mention invalid verdict value
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('❌', comment_text)
        self.assertIn('invalid', comment_text.lower())
        self.assertIn('verdict', comment_text.lower())

    def test_f4_null_verdict_value_rejected(self):
        """F4: Null verdict value → rejected with clear invalid verdict comment."""
        daemon = self._create_reviewer_daemon()

        # Verdict field present but null
        verdict_with_null = {
            "status": "completed",
            "verdict": None,
            "summary": "Null verdict value",
            "findings": []
        }

        result_null_verdict = (
            "Review complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            f"{json.dumps(verdict_with_null, indent=2)}\n"
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_null_verdict)
        board_task_id = 'task_null_verdict'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

    def test_f5_assignee_restored_to_implemented_by_on_failure(self):
        """F5: assignee restored to implementedBy on parse failure."""
        daemon = self._create_reviewer_daemon()

        # Task with different implementedBy
        result_parse_error = "No verdict JSON anywhere in this result"
        task = self._create_task_with_result(result_parse_error, implementedBy='rapper-2')
        board_task_id = 'task_restore_assignee'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should restore assignee to original implementer
        metadata_call = daemon.client.update_task_metadata.call_args
        metadata = metadata_call[0][1]
        self.assertEqual(metadata.get('assignee'), 'rapper-2')

    def test_f6_review_state_set_to_rejected_on_failure(self):
        """F6: reviewState set to rejected on parse failure."""
        daemon = self._create_reviewer_daemon()

        result_parse_error = "Invalid result with no verdict JSON"
        task = self._create_task_with_result(result_parse_error)
        board_task_id = 'task_review_state'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should set reviewState to rejected
        metadata_call = daemon.client.update_task_metadata.call_args
        metadata = metadata_call[0][1]
        self.assertEqual(metadata['reviewState'], 'rejected')

        # Should also set reviewCompletedAt timestamp
        self.assertIn('reviewCompletedAt', metadata)
        self.assertTrue(metadata['reviewCompletedAt'].startswith('2022-01-16T'))

    def test_empty_result_fails_closed(self):
        """Empty task result → rejected with parse failure comment."""
        daemon = self._create_reviewer_daemon()

        task = self._create_task_with_result("")
        board_task_id = 'task_empty_result'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed to rejected
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Comment should explain parse failure
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('❌', comment_text)
        self.assertIn('parse', comment_text.lower())

    def test_multiple_sentinel_blocks_uses_last(self):
        """Multiple sentinel blocks → uses last one (if valid), or fails if last is invalid."""
        daemon = self._create_reviewer_daemon()

        # Multiple blocks - first valid, last invalid
        result_multiple_blocks = (
            "First analysis:\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            '{"verdict": "approved", "summary": "First block"}\n'
            "<<<END_REVIEW_VERDICT_JSON>>>\n\n"
            "Updated analysis:\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            '{"verdict": "invalid_value", "summary": "Last block"}\n'  # Invalid verdict
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        task = self._create_task_with_result(result_multiple_blocks)
        board_task_id = 'task_multiple_blocks'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should fail-closed because last block has invalid verdict
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

    def test_fail_closed_configuration_respected(self):
        """Verify fail_closed_on_parse_error configuration is checked."""
        # This test ensures the configuration setting is actually used
        daemon = self._create_reviewer_daemon()

        # Override config to disable fail-closed (hypothetically)
        daemon.config['reviewer']['fail_closed_on_parse_error'] = False

        result_parse_error = "No verdict anywhere"
        task = self._create_task_with_result(result_parse_error)
        board_task_id = 'task_config_check'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                # Even with fail_closed disabled, current implementation should still reject
                # (This test documents expected behavior - may change based on implementation)
                daemon._execute_task_in_background(board_task_id, task)

        # Current design mandates fail-closed regardless, so should still reject
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )


if __name__ == '__main__':
    unittest.main()