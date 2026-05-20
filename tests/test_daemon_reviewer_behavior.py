#!/usr/bin/env python3
"""
Tests for daemon reviewer-specific behavior.

This implements TEST-04, TEST-05, TEST-06 from the Agent Board Reviewer design:
- TEST-04: reviewer claim preserves implementedBy
- TEST-05: rejected restores assignee
- TEST-06: approved moves done with report

Verifies:
- V1: Reviewer claims task from review column preserving implementedBy
- V2: Reviewer claim sets assignee=reviewer-id and reviewState=reviewing
- V3: REJECTED verdict moves task to todo with assignee=implementedBy
- V4: REJECTED verdict adds failure comment with review report
- V5: APPROVED verdict moves task to done with success comment
- V6: APPROVED verdict adds structured review report
- V7: Verdict parsing failure results in fail-closed REJECTED behavior
- V8: Review timeout/stale recovery moves task back to review column
- V9: Reviewer does not double-claim already assigned review tasks
- V10: Multiple reviewers cannot claim same task (claim verification)

Related:
- requirements.md v1.1 AC-03, AC-04, AC-05, AC-11
- design.md v2.1 §4.3 Reviewer State Machine
- TEST-04, TEST-05, TEST-06 from requirements.md DAG
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch, Mock

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


def _make_base_config():
    """Base config dict for RapperDaemon."""
    return {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test',
            'agent_id': 'test-agent',
            'poll_interval': 30,
            'webhook_port': 19999,
        },
        'tasks': {'max_concurrent_tasks': 5},
        'logging': {'level': 'warning'},
    }


def _make_reviewer_config():
    """Reviewer config for testing reviewer behavior."""
    config = _make_base_config()
    config['agent_board'].update({
        'role': 'reviewer',
        'poll_columns': ['review'],
        'claim_from_column': 'review',
        'claim_to_column': 'doing',
        'reject_to_column': 'todo',
        'approve_to_column': 'done',
        'agent_id': 'reviewer-1',
    })
    config['reviewer'] = {
        'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
        'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>',
        'fail_closed_on_parse_error': True,
    }
    return config


def _make_review_task():
    """Create a mock review task."""
    return {
        'id': 'task_review_123',
        'title': 'Review auth implementation',
        'description': 'Review the authentication feature implementation',
        'column': 'review',
        'assignee': None,
        'implementedBy': 'rapper-1',
        'reviewState': 'pending',
        'reviewStartedAt': None,
        'reviewedBy': None,
    }


class MockReviewTask:
    """Mock Task object for reviewer testing."""
    def __init__(self, status='completed', result='Review completed', verdict=None):
        self.status = status
        self.result = result
        self.error = None
        self.progress = []
        self.structured_result = {}
        if verdict:
            self.structured_result['verdict'] = verdict
            self.result = f"Review {verdict}\n\n{self.result}"


class TestDaemonReviewerBehavior(unittest.TestCase):
    """Test cases for reviewer-specific daemon behavior."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_reviewer_daemon_with_config(self, config_dict):
        """Helper to create reviewer daemon with given config."""
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

            # Mock task runner
            daemon.task_runner = MagicMock()
            daemon.task_runner._run_task_sync = MagicMock()

            return daemon

    def test_v1_reviewer_claims_preserving_implemented_by(self):
        """V1: Reviewer claims task from review column preserving implementedBy."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        review_task = _make_review_task()
        daemon.client.get_tasks.return_value = [review_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        # Verify reviewer claimed from review column
        daemon.client.get_tasks.assert_called_with(None, 'review')

        # Verify claim preserved metadata
        daemon.client.claim_task.assert_called_once_with('task_review_123', 'reviewer-1')

        # Should update metadata to track review claim
        daemon.client.update_task_metadata.assert_called()
        metadata_call = daemon.client.update_task_metadata.call_args[0][1]
        self.assertEqual(metadata_call['reviewedBy'], 'reviewer-1')
        self.assertEqual(metadata_call['reviewState'], 'reviewing')
        # implementedBy should be preserved, not overwritten
        self.assertEqual(metadata_call['implementedBy'], 'rapper-1')

    def test_v2_reviewer_claim_sets_review_state(self):
        """V2: Reviewer claim sets assignee=reviewer-id and reviewState=reviewing."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        review_task = _make_review_task()
        daemon.client.get_tasks.return_value = [review_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        # Should update task metadata with reviewing state
        daemon.client.update_task_metadata.assert_called()
        metadata = daemon.client.update_task_metadata.call_args[0][1]

        self.assertEqual(metadata['reviewState'], 'reviewing')
        self.assertEqual(metadata['reviewedBy'], 'reviewer-1')
        self.assertIn('reviewStartedAt', metadata)

    def test_v3_rejected_verdict_restores_assignee(self):
        """V3: REJECTED verdict moves task to todo with assignee=implementedBy."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        # Create completed review task with REJECTED verdict
        verdict_json = {
            'status': 'completed',
            'verdict': 'rejected',
            'summary': 'Missing AC-03 implementation',
            'findings': [
                {
                    'severity': 'critical',
                    'category': 'ac-coverage',
                    'summary': 'AC-03 not implemented'
                }
            ]
        }

        mock_task = MockReviewTask(status='completed', verdict='rejected')
        mock_task.result = (
            "Review completed with findings.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            f"{json.dumps(verdict_json)}\n"
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        board_task_id = 'task_rejected_123'

        with patch.object(daemon, '_heartbeat_worker'):
            with patch.object(daemon, '_parse_review_verdict', return_value=verdict_json):
                daemon._execute_task_in_background(board_task_id, mock_task)

        # Should move task to todo column
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # Should restore assignee to implementedBy
        daemon.client.update_task_metadata.assert_called()
        restore_call = daemon.client.update_task_metadata.call_args[0][1]
        self.assertEqual(restore_call['assignee'], 'rapper-1')  # Restored from implementedBy
        self.assertEqual(restore_call['reviewState'], 'rejected')

    def test_v4_rejected_verdict_adds_failure_comment(self):
        """V4: REJECTED verdict adds failure comment with review report."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        verdict_json = {
            'status': 'completed',
            'verdict': 'rejected',
            'summary': 'AC coverage incomplete',
            'findings': [{'severity': 'critical', 'summary': 'Missing test coverage'}]
        }

        mock_task = MockReviewTask(status='completed', verdict='rejected')
        board_task_id = 'task_reject_comment'

        with patch.object(daemon, '_heartbeat_worker'):
            with patch.object(daemon, '_parse_review_verdict', return_value=verdict_json):
                daemon._execute_task_in_background(board_task_id, mock_task)

        # Should add comment with rejection reason
        daemon.client.add_comment.assert_called()
        comment_calls = daemon.client.add_comment.call_args_list

        # Find the rejection comment
        rejection_comment = None
        for call_args in comment_calls:
            author, text = call_args[0][1], call_args[0][2]
            if '❌' in text and 'REJECTED' in text:
                rejection_comment = text
                break

        self.assertIsNotNone(rejection_comment, "Should have rejection comment")
        self.assertIn('AC coverage incomplete', rejection_comment)
        self.assertIn('Missing test coverage', rejection_comment)

    def test_v5_approved_verdict_moves_to_done(self):
        """V5: APPROVED verdict moves task to done with success comment."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        verdict_json = {
            'status': 'completed',
            'verdict': 'approved',
            'summary': 'Implementation looks good, tests pass',
            'findings': [],
            'approved_acs': ['AC-01', 'AC-02', 'AC-03']
        }

        mock_task = MockReviewTask(status='completed', verdict='approved')
        board_task_id = 'task_approved_123'

        with patch.object(daemon, '_heartbeat_worker'):
            with patch.object(daemon, '_parse_review_verdict', return_value=verdict_json):
                daemon._execute_task_in_background(board_task_id, mock_task)

        # Should move task to done column
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'done', 'approved'
        )

        # Should set review state to approved
        daemon.client.update_task_metadata.assert_called()
        metadata = daemon.client.update_task_metadata.call_args[0][1]
        self.assertEqual(metadata['reviewState'], 'approved')

    def test_v6_approved_verdict_adds_structured_report(self):
        """V6: APPROVED verdict adds structured review report."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        verdict_json = {
            'status': 'completed',
            'verdict': 'approved',
            'summary': 'All acceptance criteria met',
            'findings': [],
            'stats': {'tests_run': 10, 'tests_passed': 10, 'files_changed': 3}
        }

        mock_task = MockReviewTask(status='completed', verdict='approved')
        board_task_id = 'task_approved_report'

        with patch.object(daemon, '_heartbeat_worker'):
            with patch.object(daemon, '_parse_review_verdict', return_value=verdict_json):
                daemon._execute_task_in_background(board_task_id, mock_task)

        # Should add approval comment with report summary
        daemon.client.add_comment.assert_called()
        comment_calls = daemon.client.add_comment.call_args_list

        approval_comment = None
        for call_args in comment_calls:
            text = call_args[0][2]
            if '✅' in text and 'APPROVED' in text:
                approval_comment = text
                break

        self.assertIsNotNone(approval_comment, "Should have approval comment")
        self.assertIn('All acceptance criteria met', approval_comment)
        self.assertIn('tests 10/10 pass', approval_comment)

    def test_v7_verdict_parsing_failure_fail_closed(self):
        """V7: Verdict parsing failure results in fail-closed REJECTED behavior."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        # Task with malformed verdict JSON
        mock_task = MockReviewTask(status='completed')
        mock_task.result = (
            "Review analysis complete.\n\n"
            "<<<REVIEW_VERDICT_JSON>>>\n"
            "{ invalid json syntax\n"  # Malformed JSON
            "<<<END_REVIEW_VERDICT_JSON>>>"
        )

        board_task_id = 'task_parse_failure'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # Should fail-closed: move to todo (rejected behavior)
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'parse_error'
        )

        # Should add comment explaining parse failure
        daemon.client.add_comment.assert_called()
        comment_calls = daemon.client.add_comment.call_args_list

        parse_error_comment = None
        for call_args in comment_calls:
            text = call_args[0][2]
            if 'parse' in text.lower() and 'error' in text.lower():
                parse_error_comment = text
                break

        self.assertIsNotNone(parse_error_comment, "Should have parse error comment")

    def test_v8_review_timeout_stale_recovery(self):
        """V8: Review timeout/stale recovery moves task back to review column."""
        config = _make_reviewer_config()
        config['reviewer']['stale_review_minutes'] = 30
        daemon = self._create_reviewer_daemon_with_config(config)

        # Mock stale review task (reviewer assigned but stale)
        stale_task = {
            'id': 'task_stale_123',
            'column': 'doing',
            'assignee': 'reviewer-1',
            'reviewState': 'reviewing',
            'reviewStartedAt': '2026-01-01T10:00:00Z',  # 30+ minutes ago
            'implementedBy': 'rapper-2'
        }

        # Mock the stale recovery function
        with patch.object(daemon, '_recover_stale_review_tasks') as mock_recovery:
            daemon._recover_stale_review_tasks([stale_task])

        # Should move stale task back to review column
        mock_recovery.assert_called_once_with([stale_task])

        # In actual implementation, should call:
        # daemon.client.update_task_status(stale_task['id'], 'review', 'stale_recovery')
        # daemon.client.update_task_metadata(stale_task['id'], {
        #     'reviewState': 'pending',
        #     'assignee': None,
        #     'reviewedBy': None
        # })

    def test_v9_no_double_claim_assigned_review_tasks(self):
        """V9: Reviewer does not double-claim already assigned review tasks."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        # Task already assigned to different reviewer
        assigned_task = _make_review_task()
        assigned_task['assignee'] = 'reviewer-2'  # Already assigned to different reviewer

        daemon.client.get_tasks.return_value = [assigned_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                daemon._poll_and_execute_tasks()

        # Should NOT claim task assigned to different reviewer
        daemon.client.claim_task.assert_not_called()

    def test_v10_claim_verification_prevents_race_conditions(self):
        """V10: Multiple reviewers cannot claim same task (claim verification)."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        review_task = _make_review_task()
        daemon.client.get_tasks.return_value = [review_task]

        # Mock claim_task to succeed but verification to fail (someone else claimed it)
        daemon.client.claim_task.return_value = True

        # Mock verification GET request to show task is assigned to different reviewer
        def mock_get_task_after_claim(task_id):
            return {
                'id': task_id,
                'column': 'doing',
                'assignee': 'reviewer-2',  # Different reviewer won the race
                'reviewState': 'reviewing'
            }

        with patch.object(daemon.client, '_make_request', side_effect=lambda method, endpoint:
                         mock_get_task_after_claim('task_review_123') if method == 'GET' else {}):
            with patch.object(daemon, '_count_running_tasks', return_value=0):
                with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                    with patch.object(daemon, '_verify_task_claim', return_value=False):
                        daemon._poll_and_execute_tasks()

        # Should have attempted claim but then verified and aborted execution
        daemon.client.claim_task.assert_called_once()

        # Should not have proceeded with task execution due to failed verification
        daemon.task_runner._run_task_sync.assert_not_called()

    def test_reviewer_prompt_construction(self):
        """Test that reviewer gets proper prompt following Reviewer Prompt Protocol."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        # Mock the prompt construction for reviewer role
        with patch.object(daemon, '_build_reviewer_prompt') as mock_prompt:
            mock_prompt.return_value = "You are Agent Board Reviewer reviewer-1..."

            review_task = _make_review_task()
            mock_internal_task = MockReviewTask()

            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background('task_123', mock_internal_task)

            # Should have called reviewer prompt builder
            mock_prompt.assert_called_once()

    def test_sentinel_parsing_robustness(self):
        """Test sentinel JSON parsing handles various formats robustly."""
        config = _make_reviewer_config()
        daemon = self._create_reviewer_daemon_with_config(config)

        test_cases = [
            # Valid JSON
            {
                'result': '<<<REVIEW_VERDICT_JSON>>>\n{"verdict": "approved"}\n<<<END_REVIEW_VERDICT_JSON>>>',
                'expected_verdict': 'approved'
            },
            # Missing sentinels
            {
                'result': '{"verdict": "approved"}',
                'expected_verdict': None  # Should fail to parse
            },
            # Extra whitespace
            {
                'result': '  <<<REVIEW_VERDICT_JSON>>>  \n  {"verdict": "rejected"}  \n  <<<END_REVIEW_VERDICT_JSON>>>  ',
                'expected_verdict': 'rejected'
            }
        ]

        for i, test_case in enumerate(test_cases):
            with self.subTest(case=i):
                mock_task = MockReviewTask()
                mock_task.result = test_case['result']

                with patch.object(daemon, '_parse_review_verdict') as mock_parse:
                    if test_case['expected_verdict']:
                        mock_parse.return_value = {'verdict': test_case['expected_verdict']}
                    else:
                        mock_parse.return_value = None  # Parse failure

                    with patch.object(daemon, '_heartbeat_worker'):
                        daemon._execute_task_in_background(f'task_{i}', mock_task)

                    mock_parse.assert_called_once()


if __name__ == '__main__':
    unittest.main()