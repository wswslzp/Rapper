#!/usr/bin/env python3
"""
Test for Reviewer APPROVED verdict flow - TEST-06 specific verification.

This test implements the core APPROVED path from Agent Board Reviewer design:
- Input: Reviewer completes task with APPROVED verdict
- Output: Task moves to `done` column with review report comment
- Metadata: reviewState=approved, reviewCompletedAt timestamp

Verification points per requirements.md v1.1 AC-03/AC-05 and design.md v2.1 §4.3/5.4/5.5:

V1: Reviewer APPROVED verdict moves task from doing→done
V2: Comment includes APPROVED summary with ✅ marker
V3: Comment includes statistics (tests, files changed, etc.)
V4: Comment includes report_path reference
V5: Task metadata updated with reviewState=approved
V6: Task metadata updated with reviewCompletedAt timestamp

Related files:
- /app/agent-board-reviewer/requirements.md v1.1
- /app/agent-board-reviewer/design.md v2.1
- /app/agent-board-reviewer/design_review.txt
- /app/agent-board-reviewer/.checkpoints/PROGRESS.md

Test should start RED (failing) - no reviewer logic implemented yet.
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


class TestDaemonReviewerApprovedFlow(unittest.TestCase):
    """Test Reviewer APPROVED verdict flow - moves task to done with complete report."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create reviewer config
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

    def _create_approved_verdict_task(self):
        """Create mock task with APPROVED verdict in structured result."""
        # This follows design.md v2.1 §5.3 Verdict JSON Schema
        verdict_json = {
            "status": "completed",
            "verdict": "approved",
            "summary": "All acceptance criteria met, tests passing",
            "findings": [],
            "approved_acs": ["AC-01", "AC-02", "AC-03"],
            "rejected_acs": [],
            "stats": {
                "files_changed": 5,
                "lines_added": 120,
                "lines_removed": 30,
                "tests_run": 12,
                "tests_passed": 12,
                "tests_failed": 0
            },
            "report_path": "/data/agent-board-reviewer/reports/task_abc123-reviewer-1-20260516.md"
        }

        # Mock task object
        task = MagicMock()
        task.status = 'completed'
        task.error = None
        task.progress = [{'step': 1}, {'step': 2}, {'step': 3}]  # 3 progress steps
        task.result = (
            "Review completed successfully.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            f"{json.dumps(verdict_json, indent=2)}\n"
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )
        task.structured_result = verdict_json  # Also set in structured_result for compatibility

        return task, verdict_json

    def test_v1_approved_verdict_moves_task_to_done(self):
        """V1: Reviewer APPROVED verdict moves task from doing→done."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_abc123'

        # Mock time for consistent testing
        test_time = 1642345678.9
        with patch('time.time', return_value=test_time):
            with patch.object(daemon, '_heartbeat_worker'):
                # This should trigger reviewer completion logic
                daemon._execute_task_in_background(board_task_id, task)

        # V1: Verify task moved to done column with approved status
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'done', 'approved'
        )

    def test_v2_approved_comment_includes_summary_with_checkmark(self):
        """V2: Comment includes APPROVED summary with ✅ marker."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_summary_test'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # V2: Verify approval comment was added
        daemon.client.add_comment.assert_called()
        comment_call = daemon.client.add_comment.call_args
        task_id, author, comment_text = comment_call[0]

        self.assertEqual(task_id, board_task_id)
        self.assertEqual(author, 'reviewer-1')
        self.assertIn('✅', comment_text)
        self.assertIn('APPROVED', comment_text)
        self.assertIn('All acceptance criteria met, tests passing', comment_text)

    def test_v3_approved_comment_includes_statistics(self):
        """V3: Comment includes statistics (tests, files changed, etc.)."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_stats_test'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # V3: Verify statistics are included in comment
        comment_call = daemon.client.add_comment.call_args
        _, _, comment_text = comment_call[0]

        # Should include stats from verdict.stats
        self.assertIn('files 5', comment_text)  # files_changed: 5
        self.assertIn('+120/-30', comment_text)  # lines added/removed
        self.assertIn('tests 12/12 pass', comment_text)  # tests passed/run

    def test_v4_approved_comment_includes_report_path(self):
        """V4: Comment includes report_path reference."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_report_test'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # V4: Verify report path is included in comment
        comment_call = daemon.client.add_comment.call_args
        _, _, comment_text = comment_call[0]

        expected_report_path = "/data/agent-board-reviewer/reports/task_abc123-reviewer-1-20260516.md"
        self.assertIn(expected_report_path, comment_text)

    def test_v5_review_state_updated_to_approved(self):
        """V5: Task metadata updated with reviewState=approved."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_state_test'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # V5: Verify metadata update with reviewState=approved
        daemon.client.update_task_metadata.assert_called()
        metadata_call = daemon.client.update_task_metadata.call_args
        task_id, metadata = metadata_call[0]

        self.assertEqual(task_id, board_task_id)
        self.assertEqual(metadata['reviewState'], 'approved')

    def test_v6_review_completed_at_timestamp_set(self):
        """V6: Task metadata updated with reviewCompletedAt timestamp."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_timestamp_test'

        test_time = 1642345678.9
        expected_iso_time = datetime.fromtimestamp(test_time).isoformat()

        with patch('time.time', return_value=test_time):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # V6: Verify reviewCompletedAt timestamp is set
        metadata_call = daemon.client.update_task_metadata.call_args
        _, metadata = metadata_call[0]

        self.assertIn('reviewCompletedAt', metadata)
        # Should be ISO format timestamp
        self.assertTrue(metadata['reviewCompletedAt'].startswith('2022-01-16T'))

    def test_full_approved_flow_integration(self):
        """Integration test: Full APPROVED flow with all components."""
        daemon = self._create_reviewer_daemon()
        task, verdict = self._create_approved_verdict_task()
        board_task_id = 'task_integration_test'

        test_time = 1642345678.9
        with patch('time.time', return_value=test_time):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should call all three board operations:

        # 1. Move to done
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'done', 'approved'
        )

        # 2. Update metadata
        metadata_call = daemon.client.update_task_metadata.call_args
        metadata = metadata_call[0][1]
        self.assertEqual(metadata['reviewState'], 'approved')
        self.assertIn('reviewCompletedAt', metadata)

        # 3. Add comment
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        self.assertIn('✅', comment_text)
        self.assertIn('APPROVED', comment_text)
        self.assertIn('tests 12/12 pass', comment_text)
        self.assertIn('task_abc123-reviewer-1-20260516.md', comment_text)


if __name__ == '__main__':
    unittest.main()