#!/usr/bin/env python3
"""
Test for BUG-04: Reviewer verdict parser must ignore prose mentions of sentinel strings.

The bug is that after BUG-03 clean harness, reviewer output includes prose text that mentions
the sentinel strings (e.g., in backticks) before the actual JSON block. The current parser
incorrectly extracts content between the prose mentions instead of finding the real
sentinel-delimited JSON block.

Bug scenario:
- Output contains prose text mentioning `<<<REVIEW_VERDICT_JSON>>>` and `<<<END_REVIEW_VERDICT_JSON>>>`
- Later there's a real JSON block with proper sentinels containing `{"verdict":"approved", ...}`
- Parser should ignore prose mentions and extract the real JSON block
- Current behavior: extracts non-JSON text between prose mentions and fails with malformed JSON

Test requirements:
1. Output with prose sentinel mentions followed by real JSON block
2. Parser should return the real JSON block's verdict ("approved")
3. _get_specific_parse_error_message should not fail due to prose mentions

RED gate criteria:
- Test must fail with current implementation (assertion failure, not timeout/error)
- Must use assert statements, not return True/False
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import RapperDaemon


class TestReviewerVerdictParserProseSentinels(unittest.TestCase):
    """Test verdict parser handling of prose mentions of sentinel strings."""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

        # Create reviewer config
        self.reviewer_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-reviewer1',
                'agent_id': 'reviewer-1',
                'poll_interval': 30,
                'webhook_port': 18794,
                'role': 'reviewer',
                'poll_columns': ['review'],
            },
            'reviewer': {
                'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
                'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>',
            },
            'tasks': {'max_concurrent_tasks': 1},
            'logging': {'level': 'warning'}
        }

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_reviewer_daemon(self):
        """Create reviewer daemon with mocked client."""
        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(self.reviewer_config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load:
            mock_load.return_value = self.reviewer_config

            daemon = RapperDaemon(self.config_path, 'reviewer-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.update_task_metadata = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock task runner
            daemon.task_runner = MagicMock()

            return daemon

    def test_prose_mentions_before_real_json_should_extract_real_verdict(self):
        """Prose mentions of sentinels before real JSON block should be ignored."""
        daemon = self._create_reviewer_daemon()

        # Output with prose mentions that form a complete pair, followed by real JSON block
        # This triggers the bug: parser finds prose mentions first and extracts non-JSON content
        output_with_prose_mentions = """
Review analysis complete. The code has been thoroughly reviewed.

As specified in the docs, the output format should use <<<REVIEW_VERDICT_JSON>>>
to start the JSON block and then <<<END_REVIEW_VERDICT_JSON>>> to close it.

Now here's my actual verdict:

<<<REVIEW_VERDICT_JSON>>>
{
  "verdict": "approved",
  "summary": "Code review passed all checks",
  "findings": [],
  "stats": {
    "files_changed": 3,
    "tests_passed": 12,
    "tests_run": 12
  }
}
<<<END_REVIEW_VERDICT_JSON>>>
"""

        # Test direct parser method
        verdict = daemon._parse_review_verdict(output_with_prose_mentions)

        # Should successfully extract the real JSON block, not prose content
        assert verdict is not None, "Parser should find the real JSON block"
        assert verdict.get('verdict') == 'approved', f"Expected 'approved' verdict, got {verdict.get('verdict')}"
        assert verdict.get('summary') == 'Code review passed all checks', "Should extract correct summary"

    def test_prose_mentions_with_backticks_ignored(self):
        """Prose mentions in backticks should be ignored in favor of real sentinels."""
        daemon = self._create_reviewer_daemon()

        output_with_backtick_mentions = """
Analysis completed. As per the specification, I need to wrap my response
with the markers `<<<REVIEW_VERDICT_JSON>>>` and `<<<END_REVIEW_VERDICT_JSON>>>`.

Here's my actual verdict:

<<<REVIEW_VERDICT_JSON>>>
{
  "verdict": "approved",
  "summary": "Implementation meets requirements",
  "findings": []
}
<<<END_REVIEW_VERDICT_JSON>>>
"""

        verdict = daemon._parse_review_verdict(output_with_backtick_mentions)

        assert verdict is not None, "Parser should find real JSON block despite backtick mentions"
        assert verdict['verdict'] == 'approved', "Should extract correct verdict from real block"

    def test_multiple_prose_mentions_before_real_json(self):
        """Multiple prose mentions should all be ignored."""
        daemon = self._create_reviewer_daemon()

        output_with_multiple_mentions = """
The review process uses `<<<REVIEW_VERDICT_JSON>>>` to start and
`<<<END_REVIEW_VERDICT_JSON>>>` to end the verdict JSON.

Note: The format expects <<<REVIEW_VERDICT_JSON>>> followed by JSON,
then <<<END_REVIEW_VERDICT_JSON>>> to close.

Final verdict:

<<<REVIEW_VERDICT_JSON>>>
{"verdict": "rejected", "summary": "Issues found", "findings": [{"severity": "major"}]}
<<<END_REVIEW_VERDICT_JSON>>>
"""

        verdict = daemon._parse_review_verdict(output_with_multiple_mentions)

        assert verdict is not None, "Should parse real JSON despite multiple prose mentions"
        assert verdict['verdict'] == 'rejected', "Should extract correct verdict from real block"

    def test_prose_mentions_without_real_json_should_fail(self):
        """Only prose mentions without real sentinels should fail to parse."""
        daemon = self._create_reviewer_daemon()

        output_only_prose = """
The review uses `<<<REVIEW_VERDICT_JSON>>>` and `<<<END_REVIEW_VERDICT_JSON>>>`
markers but I forgot to include the actual verdict JSON.
"""

        verdict = daemon._parse_review_verdict(output_only_prose)

        # Should fail to find any real JSON block
        assert verdict is None, "Should fail when only prose mentions exist"

    def test_bug04_prose_sentinels_after_real_json_breaks_parser(self):
        """BUG-04: Prose sentinels after real JSON should be ignored, but parser uses last block."""
        daemon = self._create_reviewer_daemon()

        # This reproduces BUG-04 where prose mentions come AFTER real JSON
        # Current "last block" logic will incorrectly use prose instead of real JSON
        buggy_output = """
Here's my review verdict:

<<<REVIEW_VERDICT_JSON>>>
{"verdict":"approved", "summary":"Code review passed"}
<<<END_REVIEW_VERDICT_JSON>>>

Note: The above used the <<<REVIEW_VERDICT_JSON>>>
sentinel format as specified, ending with <<<END_REVIEW_VERDICT_JSON>>>
for proper formatting.
"""

        # Current implementation uses "last block" strategy
        # It will find 2 blocks:
        # 1. {"verdict":"approved", "summary":"Code review passed"}  (real JSON)
        # 2. "\nsentinel format as specified, ending with "  (prose, not valid JSON)
        # Since it uses the LAST block, it will try to parse the prose content and fail

        verdict = daemon._parse_review_verdict(buggy_output)

        # BUG-04: This assertion should FAIL with current implementation
        # Parser will use last block (prose) instead of first block (real JSON)
        assert verdict is not None, "Parser should use first real JSON block, not last prose mention"
        assert verdict.get('verdict') == 'approved', f"Should extract 'approved' from real JSON, got: {verdict}"

    def test_bug04_malformed_last_block_priority_issue(self):
        """BUG-04: Parser should choose parseable JSON over malformed last block."""
        daemon = self._create_reviewer_daemon()

        # Real verdict first, then broken prose mention that forms incomplete sentinel pair
        problematic_output = """
<<<REVIEW_VERDICT_JSON>>>
{"verdict":"rejected", "summary":"Issues found in implementation"}
<<<END_REVIEW_VERDICT_JSON>>>

The verdict format uses <<<REVIEW_VERDICT_JSON>>> to start
and should be followed by valid JSON, then <<<END_REVIEW_VERDICT_JSON>>> to close.
However, if the JSON is malformed like <<<REVIEW_VERDICT_JSON>>>
{broken json here}
<<<END_REVIEW_VERDICT_JSON>>> then parsing will fail.
"""

        verdict = daemon._parse_review_verdict(problematic_output)

        # The "last block" strategy will extract "{broken json here}" which is invalid JSON
        # This should fail parsing and return None, even though there's valid JSON earlier
        # This demonstrates why the "last block" strategy is problematic
        assert verdict is not None, "Should parse the valid JSON block, not fail on broken last block"
        assert verdict.get('verdict') == 'rejected', "Should extract verdict from first valid JSON block"

    def test_parse_error_message_handles_prose_mentions_gracefully(self):
        """_get_specific_parse_error_message should not fail due to prose mentions."""
        daemon = self._create_reviewer_daemon()

        output_with_prose_and_broken_json = """
Using `<<<REVIEW_VERDICT_JSON>>>` and `<<<END_REVIEW_VERDICT_JSON>>>` format:

<<<REVIEW_VERDICT_JSON>>>
{broken json here
<<<END_REVIEW_VERDICT_JSON>>>
"""

        error_msg = daemon._get_specific_parse_error_message(output_with_prose_and_broken_json)

        # Should get JSON parse error, not sentinel error
        assert 'malformed json' in error_msg.lower() or 'json' in error_msg.lower(), \
            f"Expected JSON parse error, got: {error_msg}"
        assert 'sentinel not found' not in error_msg.lower(), \
            "Should not complain about missing sentinels when real ones exist"

    def test_prose_before_real_approved_verdict_integration(self):
        """Integration test: prose mentions before approved verdict should route to 'done'."""
        daemon = self._create_reviewer_daemon()

        # Create task with result containing prose mentions + real approved verdict
        task = MagicMock()
        task.status = 'completed'
        task.error = None
        task.progress = []
        task.result = """
Clean harness after BUG-03. The output format uses `<<<REVIEW_VERDICT_JSON>>>`
and `<<<END_REVIEW_VERDICT_JSON>>>` markers around the JSON response.

Final review verdict:

<<<REVIEW_VERDICT_JSON>>>
{
  "verdict": "approved",
  "summary": "All tests pass, code meets standards",
  "findings": [],
  "stats": {"files_changed": 2, "tests_passed": 8, "tests_run": 8}
}
<<<END_REVIEW_VERDICT_JSON>>>
"""

        # Mock board task
        task.board_task = {
            'id': 'task_prose_approved',
            'implementedBy': 'rapper-1',
            'reviewState': 'reviewing'
        }

        board_task_id = 'task_prose_approved'

        with patch('time.time', return_value=1642345678.9):
            with patch.object(daemon, '_heartbeat_worker'):
                daemon._execute_task_in_background(board_task_id, task)

        # Should move to done (approved)
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'done', 'approved'
        )

        # Should set approved metadata
        metadata_call = daemon.client.update_task_metadata.call_args
        metadata = metadata_call[0][1]
        assert metadata['reviewState'] == 'approved', "Should mark as approved despite prose mentions"

        # Should add approval comment
        comment_call = daemon.client.add_comment.call_args
        comment_text = comment_call[0][2]
        assert '✅' in comment_text and 'APPROVED' in comment_text, "Should post approval comment"


if __name__ == '__main__':
    unittest.main()