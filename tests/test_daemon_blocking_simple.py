#!/usr/bin/env python3
"""
[TEST-SUPP-002] Test for daemon blocking with historical todo tasks.

This RED test demonstrates the bug where large numbers of historical todo
tasks block the daemon from picking up new tasks in a timely manner.

RED requirement: This test MUST FAIL under current code to demonstrate the bug.

Bug: task_120fd217 — 大量历史 todo 残留阻塞 Daemon
Issue: Daemon processes ALL todo tasks linearly without time-based prioritization,
       causing new urgent tasks to be delayed by historical task processing.
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch
from datetime import datetime, timedelta

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


def _make_minimal_config():
    """Minimal config dict for RapperDaemon."""
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


def _create_historical_tasks(count=15, base_time=None):
    """Create a list of historical todo tasks for testing.

    Args:
        count: Number of historical tasks to create
        base_time: Base timestamp for task creation (defaults to 7 days ago)

    Returns:
        List of historical task dicts
    """
    if base_time is None:
        base_time = datetime.utcnow() - timedelta(days=7)

    historical_tasks = []
    for i in range(count):
        task_time = base_time + timedelta(minutes=i*10)  # Spread tasks over time
        task = {
            'id': f'historical_task_{i:03d}',
            'title': f'Historical Task #{i+1}',
            'description': f'Old task created on {task_time.strftime("%Y-%m-%d %H:%M")}',
            'column': 'todo',
            'assignee': None,  # Unassigned historical tasks
            'created_at': task_time.isoformat() + 'Z',
            'updated_at': task_time.isoformat() + 'Z'
        }
        historical_tasks.append(task)

    return historical_tasks


def _create_new_task(task_id='new_urgent_task', time_offset_minutes=0):
    """Create a new urgent task that should be picked up quickly.

    Args:
        task_id: ID for the new task
        time_offset_minutes: Minutes before current time (0 = now)

    Returns:
        New task dict
    """
    now = datetime.utcnow() - timedelta(minutes=time_offset_minutes)
    return {
        'id': task_id,
        'title': 'URGENT: Fix Critical Production Bug',
        'description': 'Critical production issue that needs immediate attention',
        'column': 'todo',
        'assignee': None,
        'created_at': now.isoformat() + 'Z',
        'updated_at': now.isoformat() + 'Z',
        'priority': 'high'  # This should be processed first
    }


class TestDaemonBlocking(unittest.TestCase):
    """Test daemon blocking behavior with historical todo tasks."""

    def test_daemon_blocked_by_historical_tasks_RED(self):
        """
        RED TEST: Daemon gets blocked by 15 historical todo tasks and fails to
        pick up new urgent task within reasonable time (60s simulation).

        This test MUST FAIL under current code to demonstrate the bug.

        Test scenario:
        1. Board has 15 historical todo tasks (created 7 days ago)
        2. New urgent task is added to todo column
        3. Daemon should pick up the new urgent task quickly (within 60s)
        4. BUG: Daemon processes tasks linearly and gets blocked by historical tasks
        """
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

                # Create test data: 15 historical tasks + 1 new urgent task
                historical_tasks = _create_historical_tasks(15)
                new_urgent_task = _create_new_task()

                # Combine all tasks (historical tasks come first in the list)
                # This simulates the API returning historical tasks before the new one
                all_todo_tasks = historical_tasks + [new_urgent_task]

                # Track which task gets picked (to verify the bug)
                picked_task_id = None
                original_claim_task = None

                def mock_claim_task(task_id, agent_id, retries=3):
                    nonlocal picked_task_id
                    picked_task_id = task_id
                    return True  # Simulate successful claim

                # Mock API responses
                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        # Return all tasks (historical + new) - historical tasks first
                        return all_todo_tasks
                    elif 'column=ready' in endpoint:
                        # No ready tasks
                        return []
                    elif 'column=doing' in endpoint:
                        # No doing tasks
                        return []
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)
                daemon.client.claim_task = MagicMock(side_effect=mock_claim_task)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                # Mock task runner to simulate slow task processing
                # This simulates the daemon getting stuck processing historical tasks
                def mock_run_task_sync(task, timeout=None, max_turns=None):
                    # Simulate slow processing for historical tasks
                    if task.name.startswith('Historical Task'):
                        time.sleep(0.1)  # Simulate some processing time
                    task.status = 'completed'
                    task.result = f'Completed {task.name}'

                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner') as mock_task_runner:
                        mock_task_runner._run_task_sync = MagicMock(side_effect=mock_run_task_sync)

                        with patch('daemon.generate_task_id', return_value='internal_123'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = 'Task completed'
                                mock_task.error = None
                                mock_task_class.return_value = mock_task

                                with patch.object(daemon, '_heartbeat_worker'):
                                    # Simulate time pressure: start timing
                                    start_time = time.time()

                                    # Run one poll cycle
                                    daemon._poll_and_execute_tasks()

                                    elapsed_time = time.time() - start_time

                # ASSERTIONS: This test should FAIL under current code
                # Current behavior: picks first task in list (historical task)
                # Desired behavior: should pick urgent new task first

                self.assertIsNotNone(picked_task_id,
                                   "Daemon should have picked up a task")

                # BUG ASSERTION: This should FAIL because daemon picks historical tasks first
                self.assertEqual(picked_task_id, 'new_urgent_task',
                    f"EXPECTED FAILURE: Daemon should pick urgent new task first, "
                    f"but picked {picked_task_id}. This demonstrates the blocking bug - "
                    f"daemon processes tasks linearly without prioritizing recent/urgent tasks.")

                # Additional assertions to document the problematic behavior
                if picked_task_id != 'new_urgent_task':
                    # If the bug exists, verify it picked a historical task
                    self.assertTrue(picked_task_id.startswith('historical_task_'),
                                  f"Bug confirmed: picked historical task {picked_task_id} instead of urgent task")

                # Performance assertion: should complete quickly even with many tasks
                self.assertLess(elapsed_time, 1.0,
                              f"Task pickup should be fast even with {len(historical_tasks)} historical tasks, "
                              f"but took {elapsed_time:.2f}s")

    def test_many_historical_tasks_blocks_new_task_processing(self):
        """
        RED TEST: Verify that processing many historical tasks blocks new task pickup.

        This test specifically checks the linear processing bottleneck where
        the daemon processes available_tasks[0] regardless of task age/priority.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')

            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                # Create many historical tasks (simulate the problematic scenario)
                historical_tasks = _create_historical_tasks(25)
                new_task_1 = _create_new_task('new_task_1', time_offset_minutes=5)
                new_task_2 = _create_new_task('new_task_2', time_offset_minutes=0)  # Most recent

                # Order matters: historical tasks come first in API response
                all_tasks = historical_tasks + [new_task_1, new_task_2]

                # Track which tasks are processed in order
                processed_tasks = []

                def mock_claim_task(task_id, agent_id, retries=3):
                    processed_tasks.append(task_id)
                    # Only claim the first task (simulating single-task processing per poll)
                    return len(processed_tasks) == 1

                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        return all_tasks
                    elif 'column=ready' in endpoint:
                        return []
                    elif 'column=doing' in endpoint:
                        return []
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)
                daemon.client.claim_task = MagicMock(side_effect=mock_claim_task)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner'):
                        daemon.task_runner._run_task_sync = MagicMock()
                        with patch('daemon.generate_task_id', return_value='internal_123'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = 'Task completed'
                                mock_task_class.return_value = mock_task

                                with patch.object(daemon, '_heartbeat_worker'):
                                    # Run one poll cycle
                                    daemon._poll_and_execute_tasks()

                # FAILING ASSERTION: Daemon should process newest task first
                self.assertTrue(len(processed_tasks) > 0, "Should have processed at least one task")

                first_processed = processed_tasks[0]

                # BUG: This will FAIL because daemon processes available_tasks[0]
                # which is the first historical task, not the newest/most urgent
                self.assertIn(first_processed, ['new_task_1', 'new_task_2'],
                    f"EXPECTED FAILURE: Should process new/urgent tasks first, "
                    f"but processed {first_processed}. This shows the daemon "
                    f"blindly picks available_tasks[0] without prioritization.")

                # Document the actual problematic behavior
                if first_processed.startswith('historical_task_'):
                    print(f"\n⚠️  BUG CONFIRMED: Daemon processed old task {first_processed} "
                          f"instead of recent urgent tasks. {len(historical_tasks)} historical "
                          f"tasks are blocking new task processing.")


if __name__ == '__main__':
    # Run with verbose output to show the failing assertions
    unittest.main(verbosity=2)