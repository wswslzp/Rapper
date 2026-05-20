#!/usr/bin/env python3
"""Regression tests for Agent Board daemon display names.

The Board UI lists `agent.name`; reviewer daemons used to all register as
`rapper-<hostname>-reviewer` because daemon.py truncated agent_id to 8 chars.
For reviewer-1/2/3 that made all online reviewers visually identical.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon


def _minimal_config(**agent_board_overrides):
    agent_board = {
        'url': 'http://localhost:3456',
        'api_key': 'sk-test',
        'agent_id': 'reviewer-1',
        'role': 'reviewer',
        'poll_interval': 30,
        'webhook_port': 19999,
    }
    agent_board.update(agent_board_overrides)
    return {
        'agent_board': agent_board,
        'tasks': {'max_concurrent_tasks': 1},
    }


class DaemonAgentDisplayNameTest(unittest.TestCase):
    def _make_daemon(self, config):
        with tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False) as f:
            yaml.safe_dump(config, f)
            config_path = f.name
        self.addCleanup(lambda: os.path.exists(config_path) and os.unlink(config_path))
        with patch('daemon.init_db'):
            return RapperDaemon(config_path, config['agent_board']['agent_id'])

    def test_reviewer_default_display_name_includes_full_agent_id(self):
        daemon = self._make_daemon(_minimal_config(agent_id='reviewer-2'))

        self.assertEqual(daemon.agent_info.name, 'rapper-zliaotestingai-reviewer-2')

    def test_agent_display_name_can_be_configured_explicitly(self):
        daemon = self._make_daemon(_minimal_config(
            agent_id='reviewer-3',
            display_name='Reviewer 3 / Docs Gate',
        ))

        self.assertEqual(daemon.agent_info.name, 'Reviewer 3 / Docs Gate')


if __name__ == '__main__':
    unittest.main()
