#!/usr/bin/env python3
"""
Test script for Pitfall #31 fix: Historical todo tasks blocking daemon pickup

This script verifies that the picked_tasks cleanup mechanisms work correctly:
1. Periodic cleanup removes terminal tasks from picked_tasks file
2. Immediate cleanup removes completed tasks on execution finish
"""

import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock

# Add lib to path
import sys
sys.path.insert(0, os.path.dirname(__file__) + '/lib')
from daemon import RapperDaemon

class TestPitfall31Fix(unittest.TestCase):
    def setUp(self):
        """Setup test daemon with mocked config."""
        # Create temporary config file
        self.temp_config = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yaml')
        config_content = """
agent_board:
  url: http://localhost:3456
  api_key: test-key
  agent_id: test-agent
  poll_interval: 30
  webhook_port: 18789
tasks:
  max_concurrent_tasks: 5
"""
        self.temp_config.write(config_content)
        self.temp_config.close()

        # Mock the database init
        with patch('daemon.init_db'):
            self.daemon = RapperDaemon(self.temp_config.name, "test-agent")

        # Create temporary picked_tasks file directory
        self.temp_dir = tempfile.mkdtemp()
        self.daemon.picked_tasks_file = os.path.join(self.temp_dir, 'daemon_picked.json')

    def tearDown(self):
        """Cleanup test files."""
        import shutil
        os.unlink(self.temp_config.name)
        shutil.rmtree(self.temp_dir)

    def test_cleanup_completed_picked_tasks(self):
        """Test periodic cleanup removes terminal tasks from picked_tasks file."""
        # Setup: Create picked_tasks file with mixed task states
        picked_tasks = ["task_1", "task_2", "task_3", "task_4", "task_5"]
        with open(self.daemon.picked_tasks_file, 'w') as f:
            json.dump(picked_tasks, f)

        # Mock Board API responses
        done_tasks = [{'id': 'task_1'}, {'id': 'task_2'}]  # completed
        failed_tasks = [{'id': 'task_3'}]  # failed
        # task_4 and task_5 remain in todo/doing - should stay in picked_tasks

        with patch.object(self.daemon.client, 'get_tasks') as mock_get_tasks:
            def side_effect(assignee, column):
                if column == 'done':
                    return done_tasks
                elif column == 'failed':
                    return failed_tasks
                return []
            mock_get_tasks.side_effect = side_effect

            # Run cleanup
            self.daemon._cleanup_completed_picked_tasks()

            # Verify: terminal tasks (task_1, task_2, task_3) removed, others remain
            with open(self.daemon.picked_tasks_file, 'r') as f:
                remaining_tasks = set(json.load(f))

            expected_remaining = {'task_4', 'task_5'}
            self.assertEqual(remaining_tasks, expected_remaining)

    def test_remove_from_picked_tasks(self):
        """Test immediate cleanup removes specific task from picked_tasks file."""
        # Setup: Create picked_tasks file
        picked_tasks = ["task_1", "task_2", "task_3"]
        with open(self.daemon.picked_tasks_file, 'w') as f:
            json.dump(picked_tasks, f)

        # Remove one task
        self.daemon._remove_from_picked_tasks("task_2")

        # Verify: specific task removed
        with open(self.daemon.picked_tasks_file, 'r') as f:
            remaining_tasks = set(json.load(f))

        expected_remaining = {'task_1', 'task_3'}
        self.assertEqual(remaining_tasks, expected_remaining)

    def test_remove_from_picked_tasks_nonexistent(self):
        """Test removing non-existent task doesn't crash."""
        # Setup: Create picked_tasks file
        picked_tasks = ["task_1", "task_2"]
        with open(self.daemon.picked_tasks_file, 'w') as f:
            json.dump(picked_tasks, f)

        # Remove non-existent task (should not crash)
        self.daemon._remove_from_picked_tasks("nonexistent_task")

        # Verify: original tasks remain
        with open(self.daemon.picked_tasks_file, 'r') as f:
            remaining_tasks = set(json.load(f))

        expected_remaining = {'task_1', 'task_2'}
        self.assertEqual(remaining_tasks, expected_remaining)

    def test_periodic_cleanup_call_frequency(self):
        """Test periodic cleanup is called every 10 poll cycles."""
        with patch.object(self.daemon, '_cleanup_completed_picked_tasks') as mock_cleanup, \
             patch.object(self.daemon, 'client') as mock_client, \
             patch.object(self.daemon, '_count_running_tasks', return_value=0), \
             patch.object(self.daemon.task_executor, 'submit') as mock_submit:

            # Mock client responses to avoid actual API calls
            mock_client.get_tasks.return_value = []

            # Simulate 15 poll cycles
            for i in range(15):
                try:
                    self.daemon._poll_and_execute_tasks()
                except Exception:
                    pass  # Ignore other test-related errors

            # Verify cleanup was called on cycles 10 only (1-indexed counting)
            self.assertEqual(mock_cleanup.call_count, 1)

if __name__ == '__main__':
    # Run diagnostic command from issue description
    print("=== Pitfall #31 Fix Test ===\n")

    print("ISSUE: Historical todo tasks blocking daemon pickup")
    print("ROOT CAUSE: picked_tasks deduplication file grows indefinitely")
    print("SOLUTION: Periodic + immediate cleanup of terminal task IDs\n")

    print("Diagnostic command (check current Board state):")
    print("curl -H 'X-API-Key: ...' 'http://localhost:3456/api/tasks?assignee=rapper-1&column=todo' \\")
    print("  | python3 -c 'import sys,json; ts=json.load(sys.stdin); print(len(ts), \"todo tasks\")'")
    print("(If > 10 tasks → high risk for bloat)\n")

    print("Running unit tests...")
    unittest.main(verbosity=2)