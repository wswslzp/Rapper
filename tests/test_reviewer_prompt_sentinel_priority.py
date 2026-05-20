#!/usr/bin/env python3
"""
pytest tests for BUG-03: Reviewer prompt sentinel priority over structured result instructions.

Tests that reviewer sentinel verdict JSON remains the final output requirement
and is not overridden by generic structured result instructions.

This follows Test-First RED approach - tests written first to expose the bug,
then implementation fixes will make tests pass (GREEN).

BUG-03 Context:
- reviewer-1 claim succeeds but output lacks <<<REVIEW_VERDICT_JSON>>> sentinels
- daemon fail-closed rejects due to missing verdict block
- root cause: _add_structured_result_instructions appended after reviewer prompt

Expected RED behavior:
- Tests should fail with assertion error showing structured result instructions
  override reviewer sentinel requirements in final prompt
"""

import os
import sys
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import dataclass
from typing import Optional, Dict, Any

# Add lib to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from task_runner import TaskRunner


@dataclass
class MockConfig:
    """Mock config object for testing."""
    agent_board: Dict[str, Any]
    claude: Dict[str, Any]
    tasks: Dict[str, Any]
    reviewer: Optional[Dict[str, Any]] = None


class TestReviewerPromptSentinelPriority:
    """Tests for BUG-03: Reviewer prompt sentinel priority."""

    def setup_method(self):
        """Setup for each test method."""
        self.temp_dir = Path(tempfile.mkdtemp())

        # Create minimal valid config for reviewer role
        self.reviewer_config = MockConfig(
            agent_board={
                "role": "reviewer",
                "agent_id": "reviewer-1",
                "url": "http://localhost:3456",
                "api_key": "test-key"
            },
            claude={
                "settings_path": None,
                "append_system_prompt_path": None
            },
            tasks={
                "max_concurrent_tasks": 5
            },
            reviewer={
                "verdict_sentinel_start": "<<<REVIEW_VERDICT_JSON>>>",
                "verdict_sentinel_end": "<<<END_REVIEW_VERDICT_JSON>>>"
            }
        )

        # Create TaskRunner with reviewer config
        self.task_runner = TaskRunner(config=self.reviewer_config)

    def teardown_method(self):
        """Cleanup after each test method."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reviewer_prompt_contains_sentinel_markers(self):
        """Test that reviewer prompt contains required sentinel start/end markers."""
        original_prompt = "Review this implementation for correctness"
        task_id = "test_task_123"
        board_task_id = "task_abc123"
        workdir = "/test/workdir"

        # Process prompt through TaskRunner
        final_prompt = self.task_runner._process_prompt(
            original_prompt, task_id, board_task_id, workdir
        )

        # Verify sentinel markers are present
        assert "<<<REVIEW_VERDICT_JSON>>>" in final_prompt, \
            "Final prompt missing review verdict start sentinel"
        assert "<<<END_REVIEW_VERDICT_JSON>>>" in final_prompt, \
            "Final prompt missing review verdict end sentinel"

    def test_reviewer_prompt_ends_with_sentinel_not_structured_result(self):
        """
        GREEN TEST: Final prompt MUST end with reviewer sentinel block, with NO structured result instructions.

        This test verifies the bug fix - reviewer roles should NOT have generic structured result
        instructions appended, as the reviewer protocol provides its own output format requirements
        via sentinel verdict blocks.
        """
        original_prompt = "Review this implementation for correctness"
        task_id = "test_task_123"
        board_task_id = "task_abc123"
        workdir = "/test/workdir"

        # Process prompt through TaskRunner
        final_prompt = self.task_runner._process_prompt(
            original_prompt, task_id, board_task_id, workdir
        )

        # Split into lines for easier analysis
        lines = final_prompt.split('\n')

        # Find positions of key sections
        verdict_end_line = None
        structured_result_start_line = None

        for i, line in enumerate(lines):
            if "<<<END_REVIEW_VERDICT_JSON>>>" in line:
                verdict_end_line = i
            elif "🔥 CRITICAL: STRUCTURED RESULT REQUIRED 🔥" in line:
                structured_result_start_line = i

        # Verify reviewer sentinel exists
        assert verdict_end_line is not None, \
            "Could not find <<<END_REVIEW_VERDICT_JSON>>> in final prompt"

        # GREEN ASSERTION: Structured result instructions should NOT exist for reviewer roles
        # The reviewer protocol provides its own output format via sentinel verdict blocks
        assert structured_result_start_line is None, \
            f"BUG-03 FIX VERIFICATION FAILED: Found structured result instructions in reviewer prompt " \
            f"at line {structured_result_start_line}. Reviewer roles should use ONLY sentinel verdict format, " \
            f"not generic structured result instructions."

    def test_reviewer_prompt_sentinel_is_final_constraint(self):
        """
        RED TEST: The reviewer verdict block should be the absolute final output constraint.

        This tests that no other instructions appear after the verdict end sentinel,
        ensuring Claude sees the sentinel format as the ultimate requirement.
        """
        original_prompt = "Review this implementation for correctness"
        task_id = "test_task_123"
        board_task_id = "task_abc123"
        workdir = "/test/workdir"

        # Process prompt through TaskRunner
        final_prompt = self.task_runner._process_prompt(
            original_prompt, task_id, board_task_id, workdir
        )

        # Find the verdict end position
        verdict_end_pos = final_prompt.find("<<<END_REVIEW_VERDICT_JSON>>>")
        assert verdict_end_pos != -1, "Could not find verdict end sentinel"

        # Get everything after the verdict end sentinel
        after_verdict = final_prompt[verdict_end_pos + len("<<<END_REVIEW_VERDICT_JSON>>>"):].strip()

        # RED ASSERTION: This should fail because structured result instructions come after
        assert not after_verdict or after_verdict.isspace(), \
            f"BUG-03 DETECTED: Content exists after reviewer verdict end sentinel. " \
            f"This overrides the verdict format requirement. Content: {repr(after_verdict[:200])}"

    def test_non_reviewer_role_uses_structured_result_normally(self):
        """
        Control test: Non-reviewer roles should still use structured result instructions normally.

        This ensures the fix doesn't break normal task execution for implementer agents.
        """
        # Create non-reviewer config
        implementer_config = MockConfig(
            agent_board={
                "role": "implementer",
                "agent_id": "implementer-1",
                "url": "http://localhost:3456",
                "api_key": "test-key"
            },
            claude={
                "settings_path": None,
                "append_system_prompt_path": None
            },
            tasks={
                "max_concurrent_tasks": 5
            }
        )

        implementer_runner = TaskRunner(config=implementer_config)

        original_prompt = "Implement the auth feature"
        task_id = "test_task_456"

        final_prompt = implementer_runner._process_prompt(original_prompt, task_id)

        # Should contain structured result instructions
        assert "🔥 CRITICAL: STRUCTURED RESULT REQUIRED 🔥" in final_prompt, \
            "Non-reviewer prompt missing structured result instructions"

        # Should NOT contain reviewer sentinels
        assert "<<<REVIEW_VERDICT_JSON>>>" not in final_prompt, \
            "Non-reviewer prompt incorrectly contains reviewer sentinels"
        assert "<<<END_REVIEW_VERDICT_JSON>>>" not in final_prompt, \
            "Non-reviewer prompt incorrectly contains reviewer sentinels"

    def test_reviewer_custom_sentinels_respected(self):
        """Test that custom sentinel markers from config are respected."""
        # Custom sentinel config
        custom_config = MockConfig(
            agent_board={
                "role": "reviewer",
                "agent_id": "reviewer-1",
                "url": "http://localhost:3456",
                "api_key": "test-key"
            },
            claude={
                "settings_path": None,
                "append_system_prompt_path": None
            },
            tasks={
                "max_concurrent_tasks": 5
            },
            reviewer={
                "verdict_sentinel_start": "<<<CUSTOM_START>>>",
                "verdict_sentinel_end": "<<<CUSTOM_END>>>"
            }
        )

        custom_runner = TaskRunner(config=custom_config)

        original_prompt = "Review this implementation"
        task_id = "test_task_789"

        final_prompt = custom_runner._process_prompt(original_prompt, task_id)

        # Should use custom sentinels
        assert "<<<CUSTOM_START>>>" in final_prompt, \
            "Custom start sentinel not found in final prompt"
        assert "<<<CUSTOM_END>>>" in final_prompt, \
            "Custom end sentinel not found in final prompt"

        # Should not use default sentinels
        assert "<<<REVIEW_VERDICT_JSON>>>" not in final_prompt, \
            "Default sentinel found when custom sentinel configured"
        assert "<<<END_REVIEW_VERDICT_JSON>>>" not in final_prompt, \
            "Default sentinel found when custom sentinel configured"