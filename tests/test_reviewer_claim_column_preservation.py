#!/usr/bin/env python3
"""
Test-First 补充测试 for BUG task_af5bec5e9ffdcc39

BUG: Reviewer claim 后 task 不应从 review 回退到 doing

测试目标：验证 Reviewer claim 一个 column=review 的 task 后，
task 必须保持在 review 列，不能移动到 doing 列。

预期 RED 失败原因：
当前 claim_task() 实现总是设置 column='doing'，违反了 Reviewer 状态机。

正确行为应该是：
1. Rapper doing -> Rapper complete -> review
2. Reviewer claim/reviewing -> 仍然 review (关键测试点)
3. Reviewer PASS -> done
4. Reviewer FAIL/REJECT -> todo + assignee 恢复原 Rapper

相关代码：
- lib/daemon.py:156 claim_task() 方法
- lib/daemon.py:712-714 reviewer claim 处理
- requirements.md v1.1 状态机设计
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


def _make_reviewer_config():
    """创建 Reviewer 配置。"""
    return {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test-reviewer',
            'agent_id': 'reviewer-1',
            'poll_interval': 30,
            'webhook_port': 19999,
            'role': 'reviewer',
            'poll_columns': ['review'],
            'claim_from_column': 'review',
            'claim_to_column': 'review',  # BUG: 应该保持 review，不是 doing
            'reject_to_column': 'todo',
            'approve_to_column': 'done',
        },
        'tasks': {'max_concurrent_tasks': 5},
        'logging': {'level': 'warning'},
        'reviewer': {
            'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
            'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>',
            'fail_closed_on_parse_error': True,
        }
    }


def _make_review_task():
    """创建待 Review 的 task。"""
    return {
        'id': 'task_bug_af5bec5e',
        'title': 'Fix auth bug after Rapper implementation',
        'description': 'Auth implementation completed by rapper-2, needs review',
        'column': 'review',  # 关键：task 在 review 列
        'assignee': 'rapper-2',  # 由 rapper 完成并放入 review
        'implementedBy': 'rapper-2',  # 原实现者
        'reviewState': 'pending',  # 等待 reviewer
        'reviewStartedAt': None,
        'reviewedBy': None,
    }


class TestReviewerClaimColumnPreservation(unittest.TestCase):
    """测试 Reviewer claim 后必须保持 task 在 review 列。"""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_reviewer_daemon(self):
        """创建配置好的 Reviewer daemon."""
        import yaml
        config = _make_reviewer_config()

        with open(self.config_path, 'w') as f:
            yaml.dump(config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config

            daemon = RapperDaemon(self.config_path, 'reviewer-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods
            daemon.client.get_tasks = MagicMock()
            daemon.client.claim_task = MagicMock(return_value=True)
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.update_task_metadata = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock task runner
            daemon.task_runner = MagicMock()
            daemon.task_runner._run_task_sync = MagicMock()

            return daemon

    def test_reviewer_claim_preserves_review_column(self):
        """
        核心测试：Reviewer claim 一个 column=review 的 task 后，
        column 必须保持为 review，不能变成 doing。

        这是针对 BUG task_af5bec5e9ffdcc39 的主要测试。
        当前实现下应该 RED 失败。
        """
        daemon = self._create_reviewer_daemon()
        review_task = _make_review_task()
        daemon.client.get_tasks.return_value = [review_task]

        # Mock claim to capture what column is sent
        claimed_column = None
        def mock_claim_task(task_id, agent_id, retries=3, target_column=None):
            nonlocal claimed_column
            # Capture the target_column that would be sent to the API
            claimed_column = target_column if target_column is not None else 'doing'
            return True

        daemon.client.claim_task.side_effect = mock_claim_task

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        # ASSERTION: Reviewer claim 应该保持 task 在 review 列
        # 当前实现会 RED 失败，因为 claim_task 总是设置 column='doing'
        assert claimed_column == 'review', (
            f"BUG: Reviewer claim 后 task 应该保持在 review 列，"
            f"但实际被移动到 {claimed_column} 列。"
            f"Reviewer 工作状态应该通过 assignee=reviewer-* 和 reviewState=reviewing 表达，"
            f"而不是改变 column。"
        )

    def test_reviewer_claim_task_api_call_inspection(self):
        """
        直接测试 AgentBoardClient.claim_task() 方法的API调用。

        测试 claim_task() 在指定 target_column='review' 时发送正确的
        PATCH 请求来保持 review 列。
        """
        from daemon import AgentBoardClient

        client = AgentBoardClient('http://localhost:3456', 'test-key')

        # Mock _make_request to capture all API calls
        patch_requests = []
        def mock_make_request(method, endpoint, data=None):
            patch_requests.append((method, endpoint, data))
            return {}  # Return empty dict for all calls

        client._make_request = mock_make_request

        # Call claim_task as reviewer would with target_column='review'
        result = client.claim_task('task_review_123', 'reviewer-1', target_column='review')

        # 验证发送了正确的 PATCH 请求
        patch_requests_with_review = []
        for method, endpoint, data in patch_requests:
            if method == 'PATCH' and data and data.get('column') == 'review':
                patch_requests_with_review.append((method, endpoint, data))

        # ASSERTION: 应该发送 column=review 的 PATCH 请求
        assert len(patch_requests_with_review) == 1, (
            f"Expected 1 PATCH request with column=review, "
            f"but found {len(patch_requests_with_review)} review updates: {patch_requests_with_review}."
        )

        # 验证没有 column=doing 的请求
        doing_updates = []
        for method, endpoint, data in patch_requests:
            if method == 'PATCH' and data and data.get('column') == 'doing':
                doing_updates.append((method, endpoint, data))

        # ASSERTION: 不应该有 column=doing 的更新
        assert len(doing_updates) == 0, (
            f"When claiming with target_column='review', should not send column=doing updates. "
            f"Found {len(doing_updates)} doing updates: {doing_updates}."
        )

    def test_reviewer_work_state_expressed_via_assignee_and_metadata(self):
        """
        验证 Reviewer 工作状态应该通过 assignee=reviewer-* 和
        reviewState=reviewing 表达，而不是改变 column。
        """
        daemon = self._create_reviewer_daemon()
        review_task = _make_review_task()
        daemon.client.get_tasks.return_value = [review_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        # 验证设置了正确的 reviewer metadata
        daemon.client.update_task_metadata.assert_called()
        metadata_call = daemon.client.update_task_metadata.call_args[0][1]

        # ASSERTION: Reviewer 状态应该通过 metadata 表达
        assert metadata_call.get('reviewedBy') == 'reviewer-1', (
            f"Reviewer claim 应该设置 reviewedBy=reviewer-1"
        )
        assert metadata_call.get('reviewState') == 'reviewing', (
            f"Reviewer claim 应该设置 reviewState=reviewing"
        )
        assert metadata_call.get('implementedBy') == 'rapper-2', (
            f"Reviewer claim 应该保留 implementedBy=rapper-2"
        )

    def test_reviewer_pass_verdict_moves_review_to_done(self):
        """
        测试 Reviewer PASS 判定应该将 task 从 review 移动到 done。
        这验证了状态机的正确路径：review -> done
        """
        daemon = self._create_reviewer_daemon()

        # 模拟 PASS 判定结果
        verdict_json = {
            'status': 'completed',
            'verdict': 'approved',
            'summary': 'Implementation looks good, all ACs met',
            'findings': [],
            'approved_acs': ['AC-01', 'AC-02', 'AC-03']
        }

        # 模拟完成的 review task
        mock_task = MagicMock()
        mock_task.status = 'completed'
        mock_task.result = f"Review completed\n\n<<<REVIEW_VERDICT_JSON>>>\n{json.dumps(verdict_json)}\n<<<END_REVIEW_VERDICT_JSON>>>"
        mock_task.error = None
        mock_task.progress = []
        mock_task.structured_result = {'verdict': 'approved'}

        board_task_id = 'task_pass_test'

        with patch.object(daemon, '_heartbeat_worker'):
            with patch.object(daemon, '_parse_review_verdict', return_value=verdict_json):
                daemon._execute_task_in_background(board_task_id, mock_task)

        # ASSERTION: PASS 判定应该移动到 done
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'done', 'approved'
        )

    def test_reviewer_reject_verdict_moves_review_to_todo_with_original_assignee(self):
        """
        测试 Reviewer REJECT 判定应该将 task 从 review 移动到 todo，
        并恢复 assignee 为原 implementedBy。
        这验证了状态机的正确路径：review -> todo + 恢复 assignee

        注意：这个测试需要正确的 board_task context 才能工作。
        """
        daemon = self._create_reviewer_daemon()

        # 模拟 REJECT 判定结果
        verdict_json = {
            'status': 'completed',
            'verdict': 'rejected',
            'summary': 'Missing AC-03 implementation',
            'findings': [
                {
                    'severity': 'critical',
                    'category': 'ac-coverage',
                    'summary': 'AC-03 not implemented'
                }
            ]
        }

        # 模拟完成的 review task
        mock_task = MagicMock()
        mock_task.status = 'completed'
        mock_task.result = f"Review found issues\n\n<<<REVIEW_VERDICT_JSON>>>\n{json.dumps(verdict_json)}\n<<<END_REVIEW_VERDICT_JSON>>>"
        mock_task.error = None
        mock_task.progress = []
        mock_task.structured_result = {'verdict': 'rejected'}

        board_task_id = 'task_reject_test'

        # 需要提供正确的 board_task context 给 _execute_task_in_background
        # 这个方法会查找 board_task 来获取 implementedBy 字段
        review_task = _make_review_task()
        review_task['id'] = board_task_id  # 匹配测试ID

        # Mock get_tasks to return our review task when daemon looks it up
        def mock_get_tasks(assignee, column):
            if column in ['doing', 'review']:
                return [review_task]
            return []

        daemon.client.get_tasks.side_effect = mock_get_tasks

        with patch.object(daemon, '_heartbeat_worker'):
            with patch.object(daemon, '_parse_review_verdict', return_value=verdict_json):
                daemon._execute_task_in_background(board_task_id, mock_task)

        # ASSERTION: REJECT 判定应该移动到 todo
        daemon.client.update_task_status.assert_called_with(
            board_task_id, 'todo', 'rejected'
        )

        # ASSERTION: 应该恢复 assignee 为原 implementedBy
        daemon.client.update_task_metadata.assert_called()
        restore_call = daemon.client.update_task_metadata.call_args[0][1]
        assert restore_call.get('assignee') == 'rapper-2', (
            f"REJECT 后应该恢复 assignee 为原 implementedBy=rapper-2"
        )
        assert restore_call.get('reviewState') == 'rejected', (
            f"REJECT 后 reviewState 应该为 rejected"
        )


if __name__ == '__main__':
    unittest.main()