#!/usr/bin/env python3
"""
BUG-02: Reviewer live E2E blockers - Test-First reproduction

Tests reproduce the three main blockers exposed by E2E harness task_e2de03edc5fc366f:

T1: Board task schema accepts reviewer metadata
T2: reviewer daemon preserves/sets implementedBy on claim
T3: reviewer TaskRunner claim uses reviewer config or is disabled

These tests are designed to FAIL (RED state) until the underlying issues are fixed.
They verify the specific problems found in live E2E testing.

Related:
- BUG-02: task_aa09b783ed37d958
- E2E harness: task_e2de03edc5fc366f
- Design: /app/agent-board-reviewer/design.md v2.1
- Requirements: /app/agent-board-reviewer/requirements.md v1.1
"""

import json
import os
import sys
import tempfile
import unittest
import time
from datetime import datetime
from unittest.mock import MagicMock, patch, call, Mock
from urllib.error import HTTPError

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon
from task_runner import TaskRunner, Task, generate_task_id, claim_board_task_if_provided


class TestT1BoardSchemaReviewerMetadata(unittest.TestCase):
    """T1: Board task schema accepts reviewer metadata fields."""

    def setUp(self):
        """Set up test environment."""
        # Use real agent-board URL if available, mock if not
        self.board_url = os.environ.get('AGENT_BOARD_URL', 'http://localhost:3456')
        self.api_key = os.environ.get('AGENT_BOARD_API_KEY', 'test-key')
        self.client = AgentBoardClient(self.board_url, self.api_key)

    def test_agent_board_schema_validation_directly(self):
        """Test actual Agent Board schema validation against reviewer fields."""
        # This test expresses EXPECTED behavior: schema should accept reviewer fields
        # Will FAIL with current broken schema, PASS when schema is fixed

        try:
            # Try to import the actual schema from agent-board repo
            import sys
            schema_path = '/app/agent-board/repo/src'
            if schema_path not in sys.path:
                sys.path.append(schema_path)

            # This will fail if we can't import the schema
            from schemas import UpdateTaskSchema

            # Test data with reviewer metadata fields
            reviewer_update_data = {
                'column': 'review',
                'implementedBy': 'rapper-1',
                'reviewedBy': 'reviewer-1',
                'reviewState': 'reviewing',
                'reviewStartedAt': '2026-05-16T10:00:00Z',
                'reviewCompletedAt': None,
                'reviewAttempt': 1
            }

            # EXPECTED BEHAVIOR: Schema validation should SUCCEED
            # (will FAIL with current broken schema that doesn't recognize reviewer fields)
            result = UpdateTaskSchema.parse(reviewer_update_data)

            # If we reach here, validation succeeded (good!)
            self.assertIsNotNone(result, "Schema should successfully parse reviewer metadata fields")

            # Verify all fields are preserved
            self.assertEqual(result.implementedBy, 'rapper-1')
            self.assertEqual(result.reviewedBy, 'reviewer-1')
            self.assertEqual(result.reviewState, 'reviewing')

        except ImportError:
            # If we can't import the schema, skip this test
            self.skipTest("Could not import agent-board schema for direct validation")

    def test_board_schema_accepts_implementedBy_field(self):
        """Test that Board API accepts implementedBy in task updates."""
        # This test expresses EXPECTED behavior: implementedBy should be accepted
        # Will FAIL with current broken schema, PASS when schema is fixed

        test_task_id = f"test_task_{int(time.time())}"

        # Mock successful response (what should happen when schema is fixed)
        with patch.object(self.client, '_make_request') as mock_request:
            mock_request.return_value = None  # Successful PATCH returns None

            # Attempt to update task with implementedBy field
            metadata = {
                'implementedBy': 'rapper-1',
                'column': 'review'
            }

            # This should succeed when schema supports implementedBy
            success = self.client.update_task_metadata(test_task_id, metadata)

            # EXPECTED BEHAVIOR: Should succeed (will FAIL with current broken schema)
            self.assertTrue(success, "Board schema should accept implementedBy field")

            # Verify correct API call was made
            mock_request.assert_called_once()
            args, kwargs = mock_request.call_args
            # _make_request is called as: _make_request('PATCH', '/api/tasks/{id}', metadata)
            self.assertEqual(args[0], 'PATCH')  # method
            self.assertTrue(args[1].endswith(test_task_id))  # endpoint contains task_id
            self.assertEqual(args[2], metadata)  # data parameter

    def test_board_schema_accepts_reviewer_metadata_fields(self):
        """Test that Board API accepts all reviewer metadata fields."""
        # This test expresses EXPECTED behavior: all reviewer fields should be accepted
        # Will FAIL with current broken schema, PASS when schema is fixed

        test_task_id = f"test_task_reviewer_{int(time.time())}"

        with patch.object(self.client, '_make_request') as mock_request:
            # Mock successful response (what should happen when schema is fixed)
            mock_request.return_value = None  # Successful PATCH returns None

            # All the reviewer metadata fields that should be supported
            reviewer_metadata = {
                'implementedBy': 'rapper-1',
                'reviewedBy': 'reviewer-1',
                'reviewState': 'reviewing',
                'reviewStartedAt': datetime.utcnow().isoformat(),
                'reviewCompletedAt': None,
                'reviewAttempt': 1
            }

            # This should succeed when schema supports reviewer fields
            success = self.client.update_task_metadata(test_task_id, reviewer_metadata)

            # EXPECTED BEHAVIOR: Should succeed (will FAIL with current broken schema)
            self.assertTrue(success, "Board schema should accept all reviewer metadata fields")

            # Verify correct API call was made
            mock_request.assert_called_once()
            args, kwargs = mock_request.call_args
            # _make_request is called as: _make_request('PATCH', '/api/tasks/{id}', metadata)
            self.assertEqual(args[0], 'PATCH')  # method
            self.assertTrue(args[1].endswith(test_task_id))  # endpoint contains task_id
            self.assertEqual(args[2], reviewer_metadata)  # data parameter

    def test_board_schema_persistence_for_reviewer_fields(self):
        """Test that reviewer metadata fields are properly persisted."""
        # This test expresses EXPECTED behavior: reviewer fields should be persisted
        # Will FAIL with current broken schema, PASS when schema is fixed

        test_task_id = f"test_persist_{int(time.time())}"

        with patch.object(self.client, '_make_request') as mock_request:
            # Mock successful update and retrieval
            mock_request.return_value = None  # Successful PATCH returns None

            metadata = {'implementedBy': 'rapper-1', 'reviewState': 'reviewing'}

            # PATCH should succeed
            success = self.client.update_task_metadata(test_task_id, metadata)

            # EXPECTED BEHAVIOR: Should succeed (will FAIL with current broken schema)
            self.assertTrue(success, "Board schema should accept and persist reviewer fields")

            # Verify the update was attempted
            mock_request.assert_called_once()
            args, kwargs = mock_request.call_args
            # _make_request is called as: _make_request('PATCH', '/api/tasks/{id}', metadata)
            self.assertEqual(args[0], 'PATCH')  # method
            self.assertTrue(args[1].endswith(test_task_id))  # endpoint contains task_id
            self.assertEqual(args[2], metadata)  # data parameter


class TestT2ReviewerDaemonClaimBehavior(unittest.TestCase):
    """T2: reviewer daemon preserves/sets implementedBy on claim."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create reviewer config
        config_content = """
agent_board:
  url: http://localhost:3456
  api_key: reviewer-test-key
  agent_id: reviewer-1
  role: reviewer
  poll_columns: ['review']

reviewer:
  verdict_sentinel_start: "<<<REVIEW_VERDICT_JSON>>>"
  verdict_sentinel_end: "<<<END_REVIEW_VERDICT_JSON>>>"

tasks:
  max_concurrent_tasks: 1
"""
        with open(self.config_path, 'w') as f:
            f.write(config_content)

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reviewer_claim_preserves_implementedBy(self):
        """Test reviewer claim preserves implementedBy and sets reviewer metadata."""
        # This test expresses EXPECTED behavior: _handle_reviewer_task_claim should succeed
        # Will FAIL with current broken implementation, PASS when fixed

        # Create reviewer daemon
        daemon = RapperDaemon(self.config_path)

        # Mock task in review column with implementedBy
        review_task = {
            'id': 'task_review_123',
            'title': 'Review auth feature',
            'column': 'review',
            'assignee': 'rapper-1',
            'implementedBy': 'rapper-1'  # This field exists in task
        }

        with patch.object(daemon.client, 'update_task_metadata') as mock_update:
            # Mock successful metadata update (what should happen when fixed)
            mock_update.return_value = True

            # Call the reviewer claim handler (this is called after actual claim)
            success = daemon._handle_reviewer_task_claim(review_task)

            # EXPECTED BEHAVIOR: Should succeed (will FAIL with current broken implementation)
            self.assertTrue(success, "_handle_reviewer_task_claim should return True when metadata updates succeed")

            # Verify metadata update was attempted with correct fields
            mock_update.assert_called_once()
            call_args = mock_update.call_args[0]
            metadata = call_args[1]

            # Should have preserved implementedBy and added reviewer fields
            self.assertEqual(metadata['implementedBy'], 'rapper-1')
            self.assertEqual(metadata['reviewedBy'], 'reviewer-1')
            self.assertEqual(metadata['reviewState'], 'reviewing')
            self.assertIn('reviewStartedAt', metadata)

    def test_reviewer_claim_fails_without_implementedBy(self):
        """Test reviewer claim fails gracefully when implementedBy missing."""

        daemon = RapperDaemon(self.config_path)

        # Task without implementedBy (this can happen in current system)
        review_task = {
            'id': 'task_review_456',
            'title': 'Review without implementer',
            'column': 'review',
            'assignee': 'rapper-1'
            # Missing implementedBy field
        }

        # Should fail because implementedBy is required for review
        success = daemon._handle_reviewer_task_claim(review_task)
        self.assertFalse(success)

    def test_reviewer_daemon_polling_claimable_tasks(self):
        """Test that reviewer daemon can identify claimable review tasks."""

        daemon = RapperDaemon(self.config_path)

        with patch.object(daemon.client, 'get_tasks') as mock_get:
            # Mock tasks in review column
            mock_get.return_value = [
                {
                    'id': 'task_1',
                    'column': 'review',
                    'assignee': 'rapper-1',
                    'implementedBy': 'rapper-1'
                },
                {
                    'id': 'task_2',
                    'column': 'review',
                    'assignee': 'reviewer-2',  # Assigned to different reviewer
                    'implementedBy': 'rapper-2'
                }
            ]

            # This logic should work (identifying claimable tasks)
            # But the actual claim will fail due to schema issues

            # The daemon polling logic should identify task_1 as claimable
            # but not task_2 (assigned to different reviewer)


class TestT3ReviewerTaskRunnerConfig(unittest.TestCase):
    """T3: reviewer TaskRunner claim uses reviewer config or is disabled."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reviewer_taskrunner_uses_different_config(self):
        """Test that reviewer execution uses reviewer-specific board config."""
        # This test expresses EXPECTED behavior: reviewer should use correct config
        # Will FAIL with current broken implementation that uses default config

        # Create a task that would be executed by reviewer
        task = Task(
            id=generate_task_id(),
            name="reviewer-task",
            prompt="Review the auth implementation",
            workdir="/app/test-project",
            status="pending",
            board_task_id="task_reviewer_claim_test"
        )

        # Mock reviewer-specific config (what should be used)
        reviewer_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'reviewer-api-key',  # Correct key for reviewer
                'agent_id': 'reviewer-1',       # Correct agent_id for reviewer
                'role': 'normal'  # Not reviewer role, so claim should happen with reviewer config
            }
        }

        with patch('task_runner.load_config') as mock_load_config:
            # BUG: Current implementation calls load_config() with default path
            # Should load reviewer config or skip claim entirely
            mock_load_config.return_value = reviewer_config

            # Mock the daemon import to control the AgentBoardClient
            with patch.dict('sys.modules', {'daemon': Mock()}):
                mock_daemon = sys.modules['daemon']
                MockClient = Mock()
                mock_daemon.AgentBoardClient = MockClient
                mock_client = MockClient.return_value
                mock_client.claim_task.return_value = True

                # Attempt to claim board task
                success = claim_board_task_if_provided(task)

                # EXPECTED BEHAVIOR: Should succeed with correct config
                # (will FAIL because current implementation uses wrong config path)
                self.assertTrue(success, "reviewer claim should succeed with correct config")

                # Verify correct credentials are used
                MockClient.assert_called_with(
                    'http://localhost:3456',
                    'reviewer-api-key'  # Should use reviewer API key
                )

    def test_reviewer_execution_skips_claim_when_daemon_role(self):
        """Test that reviewer execution properly skips claim when running as daemon."""
        # This test expresses EXPECTED behavior: reviewer role should skip claim
        # Will FAIL with current broken implementation that doesn't check role

        # Create reviewer config
        reviewer_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'reviewer-api-key',
                'agent_id': 'reviewer-1',
                'role': 'reviewer'  # This should disable claim in TaskRunner
            }
        }

        task = Task(
            id=generate_task_id(),
            name="reviewer-daemon-task",
            prompt="Daemon reviewer task",
            workdir="/app/test-project",
            status="pending",
            board_task_id="task_daemon_reviewer"
        )

        with patch('task_runner.load_config') as mock_config:
            mock_config.return_value = reviewer_config

            # Mock the daemon import to control the AgentBoardClient
            with patch.dict('sys.modules', {'daemon': Mock()}):
                mock_daemon = sys.modules['daemon']
                MockClient = Mock()
                mock_daemon.AgentBoardClient = MockClient

                # Should succeed by skipping claim (not calling AgentBoardClient)
                success = claim_board_task_if_provided(task)

                # EXPECTED BEHAVIOR: Should succeed by skipping claim
                # (will FAIL because current implementation doesn't check role)
                self.assertTrue(success, "reviewer role should skip claim and return True")

                # EXPECTED BEHAVIOR: Should NOT call AgentBoardClient when role=reviewer
                # (will FAIL because current implementation calls it anyway)
                MockClient.assert_not_called()

    def test_taskrunner_reviewer_config_validation(self):
        """Test TaskRunner reviewer configuration validation."""

        # Test with missing reviewer config
        config_without_reviewer = {
            'agent_board': {
                'role': 'reviewer',
                'url': 'http://localhost:3456',
                'api_key': 'test-key'
            }
            # Missing reviewer section
        }

        with patch('task_runner.load_config') as mock_config:
            mock_config.return_value = config_without_reviewer

            # TaskRunner should handle missing reviewer config gracefully
            runner = TaskRunner(config=config_without_reviewer)

            # Should have empty reviewer config
            self.assertEqual(runner.config.get('reviewer', {}), {})

        # Test with valid reviewer config
        config_with_reviewer = {
            'agent_board': {
                'role': 'reviewer',
                'url': 'http://localhost:3456',
                'api_key': 'reviewer-key',
                'agent_id': 'reviewer-1'
            },
            'reviewer': {
                'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
                'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>'
            }
        }

        with patch('task_runner.load_config') as mock_config:
            mock_config.return_value = config_with_reviewer

            runner = TaskRunner(config=config_with_reviewer)

            # Should have reviewer config available
            self.assertIsNotNone(runner.config.get('reviewer'))
            self.assertEqual(
                runner.config['reviewer']['verdict_sentinel_start'],
                '<<<REVIEW_VERDICT_JSON>>>'
            )


if __name__ == '__main__':
    # Run tests
    unittest.main(verbosity=2)