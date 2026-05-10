#!/usr/bin/env python3
"""
Tests for the Daemon infinite re-pickup bug fix (Method A).

Verifies that:
1. `AgentBoardClient.claim_task()` calls PATCH todo→doing with retries.
2. `_poll_and_execute_tasks()` uses claim_task() BEFORE executing.
3. Tasks already in 'doing' are excluded from polling (restart-safe dedup).
4. File-based dedup (_save/_load_picked_task) still works as secondary guard.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_client(base_url="http://localhost:3456", api_key="sk-test"):
    """Return an AgentBoardClient with a mocked _make_request."""
    client = AgentBoardClient(base_url, api_key)
    client._make_request = MagicMock()
    return client


def _make_minimal_config():
    """Minimal config dict for RapperDaemon without loading yaml."""
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


# ──────────────────────────────────────────────────────────────────────────────
# AgentBoardClient.claim_task tests
# ──────────────────────────────────────────────────────────────────────────────

class TestClaimTask(unittest.TestCase):

    def test_claim_task_patches_column_to_doing(self):
        """claim_task() must PATCH column=doing before execution."""
        client = _make_client()
        client._make_request.return_value = {}

        result = client.claim_task('task_abc', 'rapper-2')

        self.assertTrue(result)
        # First call must be PATCH with column=doing
        patch_call = client._make_request.call_args_list[0]
        method, endpoint, payload = patch_call[0]
        self.assertEqual(method, 'PATCH')
        self.assertIn('task_abc', endpoint)
        self.assertEqual(payload['column'], 'doing')

    def test_claim_task_includes_heartbeat(self):
        """claim_task() should set lastHeartbeat in the PATCH payload."""
        client = _make_client()
        client._make_request.return_value = {}

        client.claim_task('task_abc', 'rapper-2')

        patch_payload = client._make_request.call_args_list[0][0][2]
        self.assertIn('lastHeartbeat', patch_payload)

    def test_claim_task_posts_breadcrumb_comment(self):
        """claim_task() should leave a comment indicating which agent claimed the task."""
        client = _make_client()
        client._make_request.return_value = {}

        client.claim_task('task_abc', 'rapper-2')

        calls = client._make_request.call_args_list
        # Should have at least 2 calls: PATCH + POST comment
        self.assertGreaterEqual(len(calls), 2)
        comment_call = calls[1]
        method, endpoint = comment_call[0][0], comment_call[0][1]
        self.assertEqual(method, 'POST')
        self.assertIn('comments', endpoint)
        comment_payload = comment_call[0][2]
        self.assertEqual(comment_payload['author'], 'rapper-2')

    def test_claim_task_retries_on_network_error(self):
        """claim_task() must retry PATCH up to `retries` times on URLError."""
        from urllib.error import URLError
        client = _make_client()
        # Fail twice, succeed on third attempt
        client._make_request.side_effect = [
            URLError("connection refused"),
            URLError("connection refused"),
            {},  # PATCH succeeds
            {},  # comment POST
        ]

        result = client.claim_task('task_abc', 'rapper-2', retries=3)

        self.assertTrue(result)
        # Should have been called 3 times for PATCH (2 failures + 1 success) + 1 comment
        self.assertEqual(client._make_request.call_count, 4)

    def test_claim_task_returns_false_after_all_retries_exhausted(self):
        """claim_task() returns False when all retry attempts fail."""
        from urllib.error import URLError
        client = _make_client()
        client._make_request.side_effect = URLError("connection refused")

        result = client.claim_task('task_abc', 'rapper-2', retries=3)

        self.assertFalse(result)
        self.assertEqual(client._make_request.call_count, 3)

    def test_claim_task_comment_failure_is_non_fatal(self):
        """claim_task() returns True even if the comment POST fails."""
        from urllib.error import URLError
        client = _make_client()
        # PATCH succeeds, comment POST fails
        client._make_request.side_effect = [{}, URLError("comment failed")]

        result = client.claim_task('task_abc', 'rapper-2')

        self.assertTrue(result)


# ──────────────────────────────────────────────────────────────────────────────
# RapperDaemon._poll_and_execute_tasks tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPollClaimsBeforeExecution(unittest.TestCase):
    """Verify that claim_task is called BEFORE task execution."""

    def _make_daemon(self, tmpdir):
        """Build a RapperDaemon with all I/O mocked out."""
        config_path = os.path.join(tmpdir, 'config.yaml')
        # Write a minimal config so _load_config() works
        import yaml
        with open(config_path, 'w') as f:
            yaml.dump(_make_minimal_config(), f)

        daemon = RapperDaemon.__new__(RapperDaemon)
        daemon.config_path = config_path
        daemon.config = _make_minimal_config()
        daemon.agent_id = 'test-agent'
        daemon.running = False
        daemon.shutdown_event = threading.Event()
        daemon.current_task = None
        daemon.picked_tasks_file = os.path.join(tmpdir, 'daemon_picked.json')
        daemon.webhook_server = None
        daemon.webhook_thread = None

        import logging
        daemon.logger = logging.getLogger('test.daemon')

        # Mock the board client
        daemon.client = _make_client()

        # Mock the task runner so we don't actually run Claude
        daemon.task_runner = MagicMock()

        return daemon

    def test_claim_called_before_execution(self):
        """claim_task() must be called before task_runner._run_task_sync()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = self._make_daemon(tmpdir)

            todo_task = {
                'id': 'task_test001',
                'title': 'Test task',
                'description': 'Do something',
            }
            # get_tasks returns one todo task, empty doing list
            daemon.client.get_tasks = MagicMock(side_effect=lambda assignee, col: (
                [todo_task] if col == 'todo' else []
            ))
            daemon.client.claim_task = MagicMock(return_value=True)

            call_order = []
            daemon.client.claim_task.side_effect = lambda *a, **kw: call_order.append('claim') or True
            daemon.task_runner._run_task_sync.side_effect = lambda *a, **kw: call_order.append('execute')

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                daemon._poll_and_execute_tasks()

            self.assertIn('claim', call_order)
            self.assertIn('execute', call_order)
            self.assertLess(
                call_order.index('claim'),
                call_order.index('execute'),
                "claim_task() must be called BEFORE _run_task_sync()"
            )

    def test_doing_tasks_excluded_from_polling(self):
        """Tasks already in 'doing' (from prior run) must not be re-executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = self._make_daemon(tmpdir)

            already_doing = {'id': 'task_already', 'title': 'Already running', 'description': ''}
            # todo also lists the task (edge-case where board has it in both — shouldn't happen,
            # but simulating: todo returns it, doing also returns it)
            daemon.client.get_tasks = MagicMock(side_effect=lambda assignee, col: (
                [already_doing] if col == 'todo' else [already_doing]
            ))
            daemon.client.claim_task = MagicMock(return_value=True)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                daemon._poll_and_execute_tasks()

            # Since task is in 'doing', it should be excluded — no claim, no execute
            daemon.client.claim_task.assert_not_called()
            daemon.task_runner._run_task_sync.assert_not_called()

    def test_fresh_todo_task_not_in_doing_is_claimed(self):
        """A task in todo but NOT in doing should be claimed and executed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = self._make_daemon(tmpdir)

            new_task = {'id': 'task_new', 'title': 'New task', 'description': 'Fresh work'}
            daemon.client.get_tasks = MagicMock(side_effect=lambda assignee, col: (
                [new_task] if col == 'todo' else []  # doing is empty
            ))
            daemon.client.claim_task = MagicMock(return_value=True)
            daemon.client.update_task_status = MagicMock(return_value=True)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                daemon._poll_and_execute_tasks()

            daemon.client.claim_task.assert_called_once_with('task_new', 'test-agent')

    def test_file_dedup_prevents_same_process_repickup(self):
        """File-based dedup prevents re-picking a task in the same process lifetime."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = self._make_daemon(tmpdir)

            task = {'id': 'task_dup', 'title': 'Dup task', 'description': ''}
            # Doing is always empty (simulate claim PATCH failed after first pick)
            daemon.client.get_tasks = MagicMock(side_effect=lambda assignee, col: (
                [task] if col == 'todo' else []
            ))
            daemon.client.claim_task = MagicMock(return_value=False)  # claim always fails
            daemon.client.update_task_status = MagicMock(return_value=True)

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                # First poll: picks and saves to file
                daemon._poll_and_execute_tasks()
                # Second poll: file-based dedup should block re-pickup
                daemon._poll_and_execute_tasks()

            # _run_task_sync should only be called once (first poll)
            self.assertEqual(daemon.task_runner._run_task_sync.call_count, 1)

    def test_column_queried_is_todo_not_ready(self):
        """Poll must query column=todo, not column=ready (API semantics fix)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daemon = self._make_daemon(tmpdir)

            daemon.client.get_tasks = MagicMock(return_value=[])

            with patch.object(daemon, '_count_running_tasks', return_value=0):
                daemon._poll_and_execute_tasks()

            # First call to get_tasks should use 'todo'
            first_call_args = daemon.client.get_tasks.call_args_list[0][0]
            self.assertEqual(first_call_args[1], 'todo',
                             "Poll should query column=todo, not column=ready")


if __name__ == '__main__':
    unittest.main()
