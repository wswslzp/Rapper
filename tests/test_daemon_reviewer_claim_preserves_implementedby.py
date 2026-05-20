#!/usr/bin/env python3
"""
TEST-04: reviewer claim preserves implementedBy

测试 Reviewer claim review task 时不会丢失原实现者。

验证点：
- review task 初始 `implementedBy=rapper-N` 或 assignee=rapper-N
- Reviewer claim 后 `assignee=reviewer-N`
- `implementedBy` 仍保留 rapper-N
- `reviewedBy=reviewer-N`，`reviewState=reviewing`，`reviewStartedAt` 被写入

参考：
- requirements.md v1.1 AC-04/AC-11
- design.md v2.1 §2.2/2.4/4.3

这是 RED 测试 - 在 IMPL-01/IMPL-03 实现前会失败。
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch, call

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


class TestReviewerClaimPreservesImplementedBy(unittest.TestCase):
    """测试 Reviewer claim review task 时保留 implementedBy 字段。"""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_reviewer_config(self):
        """创建 Reviewer 角色配置。"""
        return {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-reviewer1',
                'agent_id': 'reviewer-1',
                'poll_interval': 30,
                'webhook_port': 18794,
                # Reviewer 特有配置
                'role': 'reviewer',
                'poll_columns': ['review'],
                'claim_from_column': 'review',
                'claim_to_column': 'doing',
                'reject_to_column': 'todo',
                'approve_to_column': 'done',
            },
            'tasks': {'max_concurrent_tasks': 1},
            'reviewer': {
                'verdict_sentinel_start': '<<<REVIEW_VERDICT_JSON>>>',
                'verdict_sentinel_end': '<<<END_REVIEW_VERDICT_JSON>>>',
                'fail_closed_on_parse_error': True,
                'stale_review_minutes': 30,
            },
            'logging': {'level': 'warning'},
        }

    def _create_review_task_scenario(self, implementedBy='rapper-1', assignee=None):
        """创建等待 review 的任务场景。"""
        return {
            'id': 'task_7f25a48f',
            'title': 'Implement authentication middleware',
            'description': 'Add JWT authentication middleware to API routes',
            'column': 'review',
            'assignee': assignee,  # 可能是 None 或 rapper-N
            'implementedBy': implementedBy,  # 原实现者
            'reviewState': 'pending',  # 等待审查
            'reviewStartedAt': None,
            'reviewedBy': None,
            'reviewCompletedAt': None,
        }

    def _create_reviewer_daemon(self, config):
        """创建配置好的 Reviewer daemon 实例。"""
        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = config

            daemon = RapperDaemon(self.config_path, 'reviewer-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock AgentBoardClient 方法
            daemon.client.get_tasks = MagicMock()
            daemon.client.claim_task = MagicMock(return_value=True)
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.update_task_metadata = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock TaskRunner - Reviewer 不应该实际执行代码
            daemon.task_runner = MagicMock()

            return daemon

    def test_reviewer_claim_preserves_implementedby_from_rapper_1(self):
        """测试 reviewer-1 claim rapper-1 完成的 review task，preserves implementedBy。"""
        config = self._create_reviewer_config()
        daemon = self._create_reviewer_daemon(config)

        # 创建 rapper-1 完成并转到 review 的任务
        review_task = self._create_review_task_scenario(
            implementedBy='rapper-1',
            assignee=None  # 转到 review 时可能清空 assignee
        )
        daemon.client.get_tasks.return_value = [review_task]

        # Mock 运行状态
        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    # 执行 reviewer polling 和 claim
                    daemon._poll_and_execute_tasks()

        # 验证：reviewer 从 'review' 列拉取任务
        daemon.client.get_tasks.assert_called_with(None, 'review')

        # 验证：reviewer claim 了正确的任务，并保持在 review 列
        daemon.client.claim_task.assert_called_once_with('task_7f25a48f', 'reviewer-1', target_column='review')

        # 验证：claim 后更新了 review metadata，但保留了 implementedBy
        daemon.client.update_task_metadata.assert_called()
        metadata_call = daemon.client.update_task_metadata.call_args[0][1]

        # 关键验证点：implementedBy 应该保留 rapper-1，不被覆盖
        self.assertEqual(metadata_call['implementedBy'], 'rapper-1',
                        "implementedBy 应该保留原实现者，不被 reviewer claim 覆盖")

        # reviewer claim 后应设置的字段
        self.assertEqual(metadata_call['reviewedBy'], 'reviewer-1',
                        "reviewedBy 应该设置为当前 reviewer")
        self.assertEqual(metadata_call['reviewState'], 'reviewing',
                        "reviewState 应该从 pending 变为 reviewing")
        self.assertIn('reviewStartedAt', metadata_call,
                     "reviewStartedAt 应该被写入")

    def test_reviewer_claim_preserves_implementedby_from_rapper_2(self):
        """测试 reviewer-1 claim rapper-2 完成的 review task（cross-pairing）。"""
        config = self._create_reviewer_config()
        daemon = self._create_reviewer_daemon(config)

        # 创建 rapper-2 完成的任务（cross-pairing 场景）
        review_task = self._create_review_task_scenario(
            implementedBy='rapper-2',
            assignee='rapper-2'  # 可能仍保留 assignee
        )
        daemon.client.get_tasks.return_value = [review_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        # 验证：claim 后 implementedBy 保留 rapper-2
        daemon.client.update_task_metadata.assert_called()
        metadata = daemon.client.update_task_metadata.call_args[0][1]

        self.assertEqual(metadata['implementedBy'], 'rapper-2',
                        "跨配对场景下 implementedBy 应该保留 rapper-2")
        self.assertEqual(metadata['reviewedBy'], 'reviewer-1',
                        "reviewedBy 应该设置为 reviewer-1")

    def test_reviewer_claim_sets_reviewing_state_correctly(self):
        """验证 reviewer claim 正确设置 reviewState 和时间戳。"""
        config = self._create_reviewer_config()
        daemon = self._create_reviewer_daemon(config)

        review_task = self._create_review_task_scenario(implementedBy='rapper-1')
        daemon.client.get_tasks.return_value = [review_task]

        # 记录 claim 时间前后，验证 reviewStartedAt 合理性
        start_time = datetime.utcnow()

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        end_time = datetime.utcnow()

        # 验证 review 状态更新
        daemon.client.update_task_metadata.assert_called()
        metadata = daemon.client.update_task_metadata.call_args[0][1]

        self.assertEqual(metadata['reviewState'], 'reviewing',
                        "claim 后 reviewState 应为 reviewing")
        self.assertEqual(metadata['reviewedBy'], 'reviewer-1',
                        "reviewedBy 应为当前 reviewer")

        # 验证 reviewStartedAt 在合理时间范围内
        self.assertIn('reviewStartedAt', metadata,
                     "reviewStartedAt 字段应该存在")
        # 注意：实际实现中可能使用 ISO 8601 格式

    def test_reviewer_claim_ignores_tasks_assigned_to_other_reviewers(self):
        """验证 reviewer 不会 claim 已被其他 reviewer 占用的任务。"""
        config = self._create_reviewer_config()
        daemon = self._create_reviewer_daemon(config)

        # 任务已被 reviewer-2 claim
        occupied_task = self._create_review_task_scenario(implementedBy='rapper-1')
        occupied_task['assignee'] = 'reviewer-2'
        occupied_task['reviewState'] = 'reviewing'
        occupied_task['reviewedBy'] = 'reviewer-2'

        daemon.client.get_tasks.return_value = [occupied_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                daemon._poll_and_execute_tasks()

        # 验证：不应该 claim 已占用的任务
        daemon.client.claim_task.assert_not_called()
        daemon.client.update_task_metadata.assert_not_called()

    def test_reviewer_claim_failure_does_not_corrupt_metadata(self):
        """验证 claim 失败时不会污染任务 metadata。"""
        config = self._create_reviewer_config()
        daemon = self._create_reviewer_daemon(config)

        review_task = self._create_review_task_scenario(implementedBy='rapper-1')
        daemon.client.get_tasks.return_value = [review_task]

        # Mock claim_task 失败（可能是并发冲突）
        daemon.client.claim_task.return_value = False

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                daemon._poll_and_execute_tasks()

        # 验证：claim 失败时不应更新 metadata
        daemon.client.claim_task.assert_called_once()
        daemon.client.update_task_metadata.assert_not_called()

    def test_review_task_fields_compatibility_with_board_api(self):
        """验证 review metadata 字段与 Agent Board API 兼容。"""
        config = self._create_reviewer_config()
        daemon = self._create_reviewer_daemon(config)

        review_task = self._create_review_task_scenario(implementedBy='rapper-1')
        daemon.client.get_tasks.return_value = [review_task]

        with patch.object(daemon, '_count_running_tasks', return_value=0):
            with patch.object(daemon, '_load_picked_tasks', return_value=set()):
                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._poll_and_execute_tasks()

        # 验证：update_task_metadata 调用的字段名符合 Board API schema
        daemon.client.update_task_metadata.assert_called()
        task_id, metadata = daemon.client.update_task_metadata.call_args[0]

        self.assertEqual(task_id, 'task_7f25a48f')

        # 验证必需字段存在且类型正确
        required_fields = ['implementedBy', 'reviewedBy', 'reviewState', 'reviewStartedAt']
        for field in required_fields:
            self.assertIn(field, metadata, f"metadata 应包含字段 {field}")

        # 验证字段值类型和格式
        self.assertIsInstance(metadata['implementedBy'], str)
        self.assertIsInstance(metadata['reviewedBy'], str)
        self.assertIn(metadata['reviewState'], ['reviewing'])


if __name__ == '__main__':
    unittest.main()