#!/usr/bin/env python3
"""
Test daemon reviewer-specific polling behavior.

This supplements test_daemon_poll_columns_role_config.py with focused tests
on reviewer role behavior and edge cases.

Verifies:
- Reviewer never polls rapper columns (todo/ready)
- Reviewer role configuration validation
- Reviewer task selection from review column only

RED phase expectations:
These tests should FAIL initially because the daemon doesn't implement
role-based polling yet.

References:
- /app/agent-board-reviewer/requirements.md v1.1 AC-02
- /app/agent-board-reviewer/design.md v2.1 §3.2
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lib'))

try:
    from daemon import RapperDaemon
except ImportError as e:
    print(f"Failed to import daemon: {e}")
    sys.exit(1)


class TestDaemonReviewerPollBehavior(unittest.TestCase):
    """Test reviewer-specific polling behavior to ensure role separation."""

    def _create_reviewer_config(self):
        """Create reviewer-specific configuration."""
        return {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-reviewer-test',
                'agent_id': 'reviewer-1',
                'poll_interval': 30,
                'webhook_port': 18794,
                'role': 'reviewer',
                'poll_columns': ['review'],
                'route_completed_to': 'done'  # Reviewers route to done after review
            },
            'tasks': {
                'max_concurrent_tasks': 1  # Reviewers typically handle one at a time
            },
            'logging': {
                'level': 'warning'
            }
        }

    def _create_reviewer_daemon(self, custom_config=None):
        """Create daemon instance configured as reviewer."""
        config = custom_config or self._create_reviewer_config()

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'reviewer_config.yaml')

            with patch.object(RapperDaemon, '_load_config', return_value=config):
                with patch('daemon.init_db'):
                    daemon = RapperDaemon(config_path, agent_id='reviewer-1')

                    # Set up test paths
                    daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                    # Mock external dependencies
                    daemon.client.get_tasks = MagicMock(return_value=[])
                    daemon.client.claim_task = MagicMock(return_value=True)
                    daemon.client.update_task_status = MagicMock(return_value=True)
                    daemon._count_running_tasks = MagicMock(return_value=0)
                    daemon._load_picked_tasks = MagicMock(return_value=set())
                    daemon._cleanup_completed_futures = MagicMock()

                    return daemon

    def test_reviewer_never_polls_todo_column(self):
        """TEST: Reviewer daemon NEVER polls 'todo' column regardless of tasks present.

        This ensures strict role separation - reviewers don't compete with rappers.
        RED expectation: Will FAIL because current implementation polls todo.
        """
        daemon = self._create_reviewer_daemon()

        # Execute multiple poll cycles to ensure consistency
        for _ in range(3):
            daemon._poll_and_execute_tasks()

        # ASSERTION: todo column should NEVER be queried
        all_calls = daemon.client.get_tasks.call_args_list
        for call_obj in all_calls:
            if len(call_obj[0]) > 1:  # call_obj[0] is the args tuple
                column = call_obj[0][1]  # Second argument is column
                self.assertNotEqual(
                    column, 'todo',
                    "Reviewer daemon must NEVER poll 'todo' column"
                )

    def test_reviewer_never_polls_ready_column(self):
        """TEST: Reviewer daemon NEVER polls 'ready' column.

        Ready column is for tasks ready for implementation, not review.
        RED expectation: Will FAIL because current implementation polls ready.
        """
        daemon = self._create_reviewer_daemon()

        # Execute poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: ready column should NEVER be queried
        all_calls = daemon.client.get_tasks.call_args_list
        for call_obj in all_calls:
            if len(call_obj[0]) > 1:
                column = call_obj[0][1]
                self.assertNotEqual(
                    column, 'ready',
                    "Reviewer daemon must NEVER poll 'ready' column"
                )

    def test_reviewer_exclusively_polls_review_column(self):
        """TEST: Reviewer with default config polls ONLY review column.

        This is the core reviewer behavior - exclusive focus on review tasks.
        RED expectation: Will FAIL because current implementation doesn't use config.
        """
        daemon = self._create_reviewer_daemon()

        # Execute poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should query exactly once with review column
        daemon.client.get_tasks.assert_called_once_with(None, 'review')

    def test_reviewer_with_multiple_review_columns(self):
        """TEST: Reviewer can be configured to poll multiple review-related columns.

        Edge case: Some orgs might have 'review' and 'code-review' columns.
        RED expectation: Will FAIL because poll_columns config not implemented.
        """
        config = self._create_reviewer_config()
        config['agent_board']['poll_columns'] = ['review', 'code-review']

        daemon = self._create_reviewer_daemon(config)

        # Execute poll cycle
        daemon._poll_and_execute_tasks()

        # ASSERTION: Should query both review-related columns
        expected_calls = [
            call(None, 'review'),
            call(None, 'code-review'),
        ]
        daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
        self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_reviewer_ignores_tasks_in_wrong_columns(self):
        """INTEGRATION TEST: Reviewer ignores tasks in todo/ready even if present.

        Simulates scenario where Board has tasks in various columns.
        RED expectation: Will FAIL because reviewer polls wrong columns.
        """
        daemon = self._create_reviewer_daemon()

        # Mock scenario: tasks exist in multiple columns
        def mock_get_tasks_behavior(assignee, column):
            if column == 'todo':
                return [{'id': 'todo_task', 'title': 'Implementation task', 'assignee': None}]
            elif column == 'ready':
                return [{'id': 'ready_task', 'title': 'Ready task', 'assignee': None}]
            elif column == 'review':
                return [{'id': 'review_task', 'title': 'Review task', 'assignee': None}]
            elif column == 'doing':
                return []
            else:
                return []

        daemon.client.get_tasks.side_effect = mock_get_tasks_behavior

        # Mock task execution
        with patch.object(daemon, 'task_executor') as mock_executor:
            mock_future = MagicMock()
            mock_future.done.return_value = False
            mock_executor.submit.return_value = mock_future

            # Execute poll cycle
            daemon._poll_and_execute_tasks()

        # ASSERTION: Should only query review column
        daemon.client.get_tasks.assert_called_with(None, 'review')

        # ASSERTION: Should only claim review task (not todo/ready tasks)
        daemon.client.claim_task.assert_called_once_with('review_task', 'reviewer-1')

    def test_reviewer_config_validation(self):
        """TEST: Reviewer configuration is properly loaded and stored.

        Validates that reviewer-specific config values are accessible.
        RED expectation: May PASS if config loading works, even if not used.
        """
        daemon = self._create_reviewer_daemon()

        # ASSERTION: Role should be configured as reviewer
        self.assertEqual(
            daemon.config['agent_board']['role'],
            'reviewer',
            "Daemon should be configured with reviewer role"
        )

        # ASSERTION: Poll columns should be review-only
        self.assertEqual(
            daemon.config['agent_board']['poll_columns'],
            ['review'],
            "Reviewer should be configured to poll only review column"
        )

        # ASSERTION: Max concurrent should be low for reviewers
        self.assertEqual(
            daemon.config['tasks']['max_concurrent_tasks'],
            1,
            "Reviewer should have low concurrency for focused review"
        )

    def test_reviewer_empty_review_column_no_crash(self):
        """TEST: Reviewer handles empty review column gracefully.

        Edge case: No review tasks available.
        RED expectation: Should PASS if basic polling logic works.
        """
        daemon = self._create_reviewer_daemon()

        # Mock empty review column
        daemon.client.get_tasks.return_value = []

        # Execute poll cycle - should not crash
        try:
            daemon._poll_and_execute_tasks()
        except Exception as e:
            self.fail(f"Reviewer should handle empty review column gracefully: {e}")

        # ASSERTION: Should still query review column
        daemon.client.get_tasks.assert_called_with(None, 'review')

        # ASSERTION: Should not claim any tasks
        daemon.client.claim_task.assert_not_called()

    def test_reviewer_mixed_assignee_tasks_in_review_column(self):
        """TEST: Reviewer correctly filters claimable tasks from review column.

        Scenario: Review column contains mix of unassigned and assigned tasks.
        RED expectation: May PASS if existing claim filtering logic works.
        """
        daemon = self._create_reviewer_daemon()

        # Mock review column with mixed assignee states
        review_tasks = [
            {'id': 'assigned_task', 'title': 'Assigned review', 'assignee': 'other-reviewer'},
            {'id': 'unassigned_task', 'title': 'Unassigned review', 'assignee': None},
            {'id': 'my_task', 'title': 'My review', 'assignee': 'reviewer-1'},
        ]

        def mock_get_tasks_behavior(assignee, column):
            if column == 'review':
                return review_tasks
            elif column == 'doing':
                return []
            else:
                return []

        daemon.client.get_tasks.side_effect = mock_get_tasks_behavior

        # Mock task execution
        with patch.object(daemon, 'task_executor') as mock_executor:
            mock_future = MagicMock()
            mock_future.done.return_value = False
            mock_executor.submit.return_value = mock_future

            # Execute poll cycle
            daemon._poll_and_execute_tasks()

        # ASSERTION: Should query review column
        daemon.client.get_tasks.assert_called_with(None, 'review')

        # ASSERTION: Should claim a claimable task (unassigned or assigned to self)
        self.assertEqual(daemon.client.claim_task.call_count, 1)
        claimed_task_id = daemon.client.claim_task.call_args[0][0]
        self.assertIn(
            claimed_task_id,
            ['unassigned_task', 'my_task'],
            "Should only claim unassigned tasks or tasks assigned to self"
        )
        self.assertNotEqual(
            claimed_task_id,
            'assigned_task',
            "Should not claim tasks assigned to other reviewers"
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)