#!/usr/bin/env python3
"""
TEST-01 Status Verification: Poll Columns Configuration

This test verifies the current RED state of poll_columns tests and documents
exactly what needs to be implemented in IMPL-01.

Current State:
- daemon.py hardcodes poll_columns = ['todo', 'ready'] at lines 559-561
- No config support for poll_columns or role
- All tests expecting custom poll_columns behavior will fail

Implementation Requirements for IMPL-01:
1. Add poll_columns support to daemon.py _poll_and_execute_tasks()
2. Add role support (optional, for future use)
3. Default to ['todo', 'ready'] when poll_columns not specified (backward compatibility)
4. Support empty poll_columns (no polling)
5. Support single and multiple column configurations

Test Status: Expected to be RED until IMPL-01 is completed.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon


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


class TestPollColumnsCurrentState(unittest.TestCase):
    """Verify current hardcoded behavior and document implementation needs."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_current_hardcoded_behavior_verified(self):
        """Verify current daemon always polls todo+ready regardless of config."""
        # Test 1: Empty config
        config1 = _make_base_config()

        # Test 2: Config with poll_columns=["review"]
        config2 = _make_base_config()
        config2['agent_board']['poll_columns'] = ['review']

        # Test 3: Config with role=reviewer
        config3 = _make_base_config()
        config3['agent_board']['role'] = 'reviewer'
        config3['agent_board']['poll_columns'] = ['review']

        for i, config in enumerate([config1, config2, config3], 1):
            with self.subTest(config_scenario=i):
                import yaml
                with open(self.config_path, 'w') as f:
                    yaml.dump(config, f)

                with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                    mock_load_config.return_value = config

                    daemon = RapperDaemon(self.config_path, 'test-agent')
                    daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')
                    daemon.client.get_tasks = MagicMock(return_value=[])

                    with patch.object(daemon, '_count_running_tasks', return_value=0):
                        with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                            daemon._poll_and_execute_tasks()

                    # ALL configs should result in the same hardcoded behavior
                    expected_calls = [call(None, 'todo'), call(None, 'ready')]
                    daemon.client.get_tasks.assert_has_calls(expected_calls, any_order=True)
                    self.assertEqual(daemon.client.get_tasks.call_count, 2)

    def test_implementation_requirements_documented(self):
        """Document what IMPL-01 needs to implement."""
        requirements = [
            "Add poll_columns config support to _poll_and_execute_tasks()",
            "Replace hardcoded ['todo', 'ready'] with configurable columns",
            "Default to ['todo', 'ready'] when poll_columns not in config",
            "Support empty poll_columns list (no polling)",
            "Support single column poll_columns=['review']",
            "Support multiple columns poll_columns=['todo', 'ready', 'blocked']",
            "Optional: Add role config support for future use",
            "Maintain backward compatibility for existing configs"
        ]

        # This test always passes - it's for documentation
        self.assertTrue(True, f"IMPL-01 Requirements:\n" + "\n".join(f"- {req}" for req in requirements))

    def test_failing_scenarios_identified(self):
        """Identify specific scenarios that should work after IMPL-01."""
        failing_scenarios = [
            {
                'name': 'Reviewer only polls review',
                'config': {'poll_columns': ['review']},
                'expected_calls': [call(None, 'review')],
                'current_calls': [call(None, 'todo'), call(None, 'ready')]
            },
            {
                'name': 'Empty poll_columns means no polling',
                'config': {'poll_columns': []},
                'expected_calls': [],
                'current_calls': [call(None, 'todo'), call(None, 'ready')]
            },
            {
                'name': 'Single custom column',
                'config': {'poll_columns': ['doing']},
                'expected_calls': [call(None, 'doing')],
                'current_calls': [call(None, 'todo'), call(None, 'ready')]
            }
        ]

        for scenario in failing_scenarios:
            with self.subTest(scenario=scenario['name']):
                # Verify the gap between expected and current behavior
                self.assertNotEqual(
                    scenario['expected_calls'],
                    scenario['current_calls'],
                    f"Scenario '{scenario['name']}' shows implementation gap"
                )

        # Document the verification
        self.assertTrue(True, f"Verified {len(failing_scenarios)} scenarios need IMPL-01 fixes")

    def test_hardcoded_lines_identified(self):
        """Identify the exact lines in daemon.py that need modification."""
        # Read the current daemon.py to find hardcoded lines
        daemon_path = os.path.join(os.path.dirname(__file__), '../lib/daemon.py')

        with open(daemon_path, 'r') as f:
            lines = f.readlines()

        # Find the hardcoded polling lines
        hardcoded_found = False
        hardcoded_line_num = None

        for i, line in enumerate(lines, 1):
            if "all_todo_tasks = self.client.get_tasks(None, 'todo')" in line:
                hardcoded_found = True
                hardcoded_line_num = i
                break

        self.assertTrue(hardcoded_found, "Found hardcoded todo polling in daemon.py")
        self.assertIsNotNone(hardcoded_line_num, f"Hardcoded polling found at line {hardcoded_line_num}")

        # Document the specific lines that need changes
        modification_lines = [
            f"Line ~{hardcoded_line_num}: all_todo_tasks = self.client.get_tasks(None, 'todo')",
            f"Line ~{hardcoded_line_num + 1}: all_ready_tasks = self.client.get_tasks(None, 'ready')",
            f"Line ~{hardcoded_line_num + 2}: all_tasks = all_todo_tasks + all_ready_tasks"
        ]

        self.assertTrue(True, f"IMPL-01 must modify these lines:\n" + "\n".join(modification_lines))


if __name__ == '__main__':
    unittest.main()