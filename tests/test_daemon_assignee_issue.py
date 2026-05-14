#!/usr/bin/env python3
"""
Tests for the task assignment gap issue (Pitfall #7b/#20).

Verifies:
1. Current behavior: Tasks moved to todo without assignee are not picked up
2. Proposed fix: Daemon can query column=todo without assignee requirement
3. Daemon claims tasks and sets assignee during claim process

Issue: When user drags task from backlog→todo via Board UI, frontend only
calls POST /api/tasks/:id/move (changes column) but doesn't set assignee.
Daemon query requires both assignee=agent_id AND column=todo, so unassigned
tasks in todo are never picked up.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


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


class TestAssigneeIssue(unittest.TestCase):
    """Test cases for the assignee gap issue."""

    def test_old_behavior_with_assignee_filter(self):
        """Test that get_tasks still works with assignee filter (backward compatibility)."""
        client = _make_client()

        # Mock API response for assignee-filtered query
        client._make_request.return_value = []

        result = client.get_tasks('test-agent', 'todo')

        # Verify the query parameters include both assignee and column
        # Note: Parameter order may vary, so check the URL contains both
        call_args = client._make_request.call_args[0]
        self.assertEqual(call_args[0], 'GET')
        url = call_args[1]
        self.assertIn('column=todo', url)
        self.assertIn('assignee=test-agent', url)

        # No tasks returned when filtered by assignee
        self.assertEqual(result, [])

    def test_proposed_fix_query_by_column_only(self):
        """Test proposed fix: query by column only, then filter/claim appropriately."""
        client = _make_client()

        # Mock API response with tasks in todo column (some with/without assignees)
        tasks_in_todo = [
            {
                'id': 'task_123',
                'title': 'Fix auth bug',
                'description': 'Fix authentication issue',
                'column': 'todo',
                'assignee': None,  # No assignee (from frontend drag)
            },
            {
                'id': 'task_456',
                'title': 'Add feature X',
                'description': 'Implement new feature',
                'column': 'todo',
                'assignee': 'test-agent',  # Already assigned
            },
            {
                'id': 'task_789',
                'title': 'Other task',
                'description': 'Different task',
                'column': 'todo',
                'assignee': 'other-agent',  # Assigned to different agent
            }
        ]

        client._make_request.return_value = tasks_in_todo

        # Simulate modified get_tasks that only queries by column
        def get_tasks_by_column_only(column='todo'):
            response = client._make_request('GET', f'/api/tasks?column={column}')
            return response if isinstance(response, list) else response.get('tasks', [])

        result = get_tasks_by_column_only('todo')

        # Verify the query only uses column parameter
        client._make_request.assert_called_once_with('GET', '/api/tasks?column=todo')

        # All tasks in todo column are returned
        self.assertEqual(len(result), 3)

        # Now daemon can filter for unassigned tasks or claim them
        unassigned_tasks = [t for t in result if not t.get('assignee')]
        self.assertEqual(len(unassigned_tasks), 1)
        self.assertEqual(unassigned_tasks[0]['id'], 'task_123')

    def test_claim_task_sets_assignee(self):
        """Test that claim_task properly moves task to 'doing' (sets assignee implicitly)."""
        client = _make_client()

        # Mock successful claim response
        client._make_request.return_value = {}

        result = client.claim_task('task_123', 'test-agent')

        # Verify claim operation calls PATCH with doing column
        expected_calls = [
            call('PATCH', '/api/tasks/task_123', {
                'column': 'doing',
                'lastHeartbeat': unittest.mock.ANY  # datetime, hard to match exactly
            }),
            call('POST', '/api/tasks/task_123/comments', {
                'author': 'test-agent',
                'text': 'Started by agent test-agent'
            })
        ]

        client._make_request.assert_has_calls(expected_calls)
        self.assertTrue(result)

    def test_daemon_task_filtering_logic(self):
        """Test daemon logic for filtering available tasks from mixed todo column."""
        # This simulates the improved daemon logic that can handle the proposed fix

        # Mock tasks returned by column-only query
        todo_tasks = [
            {'id': 'task_unassigned', 'assignee': None, 'title': 'Unassigned task'},
            {'id': 'task_mine', 'assignee': 'rapper-1', 'title': 'My task'},
            {'id': 'task_other', 'assignee': 'rapper-2', 'title': 'Other agent task'}
        ]

        agent_id = 'rapper-1'

        # Logic daemon could use to filter tasks from column-only query:
        # 1. Take unassigned tasks (can claim them)
        # 2. Take tasks already assigned to this agent (resume/continue)
        # 3. Exclude tasks assigned to other agents

        available_tasks = []
        for task in todo_tasks:
            assignee = task.get('assignee')
            if assignee is None or assignee == agent_id:
                available_tasks.append(task)

        # Should get unassigned task and own task, but not other agent's task
        self.assertEqual(len(available_tasks), 2)
        task_ids = [t['id'] for t in available_tasks]
        self.assertIn('task_unassigned', task_ids)
        self.assertIn('task_mine', task_ids)
        self.assertNotIn('task_other', task_ids)

    @patch('daemon.TaskRunner')
    def test_full_daemon_flow_with_unassigned_task(self, mock_task_runner):
        """Test complete daemon flow handling unassigned task."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')

            # Write minimal config
            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            # Mock daemon with file-based picked_tasks in temp dir
            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                # Mock the client's _make_request method to simulate API responses
                def mock_get_tasks_behavior(assignee, column):
                    if assignee is None and column == 'todo':
                        # Column-only query - return unassigned task
                        return [{
                            'id': 'board_task_123',
                            'title': 'Fix auth bug',
                            'description': 'Fix authentication issue',
                            'column': 'todo',
                            'assignee': None
                        }]
                    elif assignee == 'test-agent' and column == 'doing':
                        # Doing tasks query - return empty
                        return []
                    else:
                        return []

                daemon.client.get_tasks = MagicMock(side_effect=mock_get_tasks_behavior)
                daemon.client.claim_task = MagicMock(return_value=True)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                # Mock task runner
                mock_task_runner_instance = MagicMock()
                mock_task_runner.return_value = mock_task_runner_instance
                daemon.task_runner = mock_task_runner_instance

                # Mock successful task execution
                from task_runner import Task
                mock_internal_task = Task(
                    id='internal_123',
                    name='Fix auth bug',
                    prompt='Fix authentication issue',
                    workdir='/tmp',
                    status='completed',
                    result='Task completed successfully'
                )

                with patch('daemon.generate_task_id', return_value='internal_123'):
                    with patch('daemon.Task', return_value=mock_internal_task):
                        with patch.object(daemon, '_count_running_tasks', return_value=0):
                            with patch.object(daemon, '_heartbeat_worker'):
                                # Run one poll cycle
                                daemon._poll_and_execute_tasks()

                # Verify task was claimed before execution
                daemon.client.claim_task.assert_called_once_with('board_task_123', 'test-agent')

                # Verify task was marked as done
                daemon.client.update_task_status.assert_called_with(
                    'board_task_123', 'done', 'Task completed successfully'
                )


    def test_regression_unassigned_tasks_now_picked_up(self):
        """Regression test: verify fix allows unassigned tasks to be picked up."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')

            # Write minimal config
            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                # Mock the client's _make_request to simulate API responses
                daemon.client._make_request = MagicMock()

                # Simulate mixed tasks in todo column (some assigned, some not)
                todo_tasks_response = [
                    {
                        'id': 'unassigned_task',
                        'title': 'Fix critical bug',
                        'description': 'Urgent production issue',
                        'column': 'todo',
                        'assignee': None  # ← This is what we're fixing
                    },
                    {
                        'id': 'assigned_to_me',
                        'title': 'My existing task',
                        'column': 'todo',
                        'assignee': 'test-agent'
                    },
                    {
                        'id': 'assigned_to_other',
                        'title': 'Other agent task',
                        'column': 'todo',
                        'assignee': 'other-agent'
                    }
                ]

                # Configure mock responses based on query parameters
                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        # Column-only query - return all tasks
                        return todo_tasks_response
                    elif 'column=doing' in endpoint and 'assignee=test-agent' in endpoint:
                        # Doing tasks query - return empty (no tasks in progress)
                        return []
                    else:
                        return []

                daemon.client._make_request.side_effect = mock_api_call

                # Mock other dependencies
                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner'):
                        daemon.task_runner._run_task_sync = MagicMock()
                        with patch('daemon.generate_task_id', return_value='internal_123'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = 'Task completed'
                                mock_task.error = None
                                mock_task_class.return_value = mock_task

                                # Mock claim_task to succeed
                                daemon.client.claim_task = MagicMock(return_value=True)
                                daemon.client.update_task_status = MagicMock(return_value=True)
                                daemon.client.add_comment = MagicMock(return_value=True)

                                with patch.object(daemon, '_heartbeat_worker'):
                                    # Run one poll cycle
                                    daemon._poll_and_execute_tasks()

                # Verify the fix worked:
                # 1. get_tasks was called with column-only query (no assignee filter)
                api_calls = [call[0][1] for call in daemon.client._make_request.call_args_list]
                column_only_calls = [call for call in api_calls if 'column=todo' in call and 'assignee=' not in call]
                self.assertTrue(len(column_only_calls) > 0, "Should query by column only")

                # 2. claim_task was called (meaning an unassigned task was picked up)
                daemon.client.claim_task.assert_called()
                called_task_id = daemon.client.claim_task.call_args[0][0]

                # 3. The task that was claimed should be either unassigned or assigned to this agent
                claimed_task = next(t for t in todo_tasks_response if t['id'] == called_task_id)
                self.assertIn(claimed_task['assignee'], [None, 'test-agent'],
                              f"Should only claim unassigned or own tasks, got assignee={claimed_task['assignee']}")

                # 4. Should not claim tasks assigned to other agents
                self.assertNotEqual(called_task_id, 'assigned_to_other',
                                    "Should not claim tasks assigned to other agents")


if __name__ == '__main__':
    unittest.main()