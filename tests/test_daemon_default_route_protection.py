#!/usr/bin/env python3
"""
保护性测试：确保 Rapper 默认配置下的向后兼容性

这是对 requirements.md v1.1 AC-09 的验证测试：
"默认配置下 Rapper 完成仍进入 `done`，现有工作流不受影响"

验证点：
1. 未配置 `route_completed_to` 时，completed task 仍 `update_task_status(..., 'done')`
2. 不应意外进入 `review`
3. 旧有 completion comment 行为不破坏

Purpose: 回归保护 - 在实现 Agent Board Reviewer 功能时确保不破坏现有 Rapper 行为

Related:
- requirements.md v1.1 AC-09
- design.md v2.1 §9.2 向后兼容
- TEST-03: default route remains done
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch, Mock

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


class MockTask:
    """Mock Task object for testing."""
    def __init__(self, status='completed', result='Task completed', error=None, task_id=None):
        self.id = task_id or f"mock_task_{int(time.time() * 1000)}"
        self.status = status
        self.result = result
        self.error = error
        self.progress = []
        self.structured_result = {}
        # Add other Task attributes that might be accessed
        self.name = "Mock Task"
        self.prompt = "Mock prompt"
        self.workdir = "/tmp"


class TestDaemonDefaultRouteProtection(unittest.TestCase):
    """保护性测试：验证默认配置下的向后兼容性"""

    def setUp(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, 'config.yaml')

    def tearDown(self):
        """Clean up test environment."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_daemon_with_legacy_config(self):
        """创建使用传统配置的daemon（无route_completed_to配置）"""
        # 模拟典型的现有 rapper 配置，不包含任何新的reviewer相关字段
        legacy_config = {
            'agent_board': {
                'url': 'http://localhost:3456',
                'api_key': 'sk-rapper1',
                'agent_id': 'rapper-1',
                'poll_interval': 30,
                'webhook_port': 18789,
                'poll_columns': ['todo', 'ready']  # 现有默认值
                # 注意：明确不包含 route_completed_to, role 等新字段
            },
            'tasks': {'max_concurrent_tasks': 5},
            'logging': {'level': 'warning'},
        }

        import yaml
        with open(self.config_path, 'w') as f:
            yaml.dump(legacy_config, f)

        with patch('daemon.RapperDaemon._load_config') as mock_load_config:
            mock_load_config.return_value = legacy_config

            daemon = RapperDaemon(self.config_path, 'rapper-1')
            daemon.picked_tasks_file = os.path.join(self.temp_dir, 'picked.json')

            # Mock client methods - 这是我们要验证的关键调用
            daemon.client.update_task_status = MagicMock(return_value=True)
            daemon.client.add_comment = MagicMock(return_value=True)

            # Mock task runner
            daemon.task_runner = MagicMock()
            daemon.task_runner._run_task_sync = MagicMock()

            return daemon

    def test_default_completed_task_goes_to_done_column(self):
        """
        核心回归保护测试：默认配置下完成的任务必须进入 'done' 列

        这是 AC-09 的直接验证：无 `route_completed_to=review` 时
        `_execute_task_in_background` 调用 `update_task_status(..., "done")`
        """
        daemon = self._create_daemon_with_legacy_config()

        # 创建已完成的任务
        mock_task = MockTask(
            status='completed',
            result='Implementation completed successfully'
        )
        board_task_id = 'task_legacy_12345'

        # 执行任务完成逻辑
        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # 关键断言：必须调用 update_task_status 且状态为 'done'
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id,
            'done',  # 这是关键 - 必须是 'done' 而不是 'review'
            'Implementation completed successfully'
        )

        # 验证没有调用任何与 review 相关的方法
        # 确保没有意外进入 review 路径
        self.assertFalse(
            any('review' in str(call).lower() for call in daemon.client.update_task_status.call_args_list)
        )

    def test_failed_task_goes_to_failed_column_legacy_config(self):
        """
        失败任务的向后兼容性：默认配置下失败任务仍进入 'failed'
        """
        daemon = self._create_daemon_with_legacy_config()

        mock_task = MockTask(
            status='failed',
            error='Execution timeout after 3600s'
        )
        board_task_id = 'task_failed_67890'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # 失败任务必须进入 'failed'，不受任何新配置影响
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id,
            'failed',
            'Execution timeout after 3600s'
        )

    def test_completion_comment_behavior_preserved(self):
        """
        验证旧有的完成评论行为不被破坏

        确保现有的 "✅ 任务完成" 评论逻辑继续工作
        """
        daemon = self._create_daemon_with_legacy_config()

        mock_task = MockTask(
            status='completed',
            result='Feature implementation done'
        )
        mock_task.progress = [{'tool': 'Edit', 'timestamp': 1234567890}]  # 模拟有进度
        board_task_id = 'task_comment_test'

        with patch.object(daemon, '_heartbeat_worker'):
            with patch('time.time', return_value=1234567950):  # 模拟60秒执行时间
                daemon._execute_task_in_background(board_task_id, mock_task)

        # 验证完成评论被添加
        daemon.client.add_comment.assert_called()

        # 检查评论内容包含预期的成功标识
        comment_calls = daemon.client.add_comment.call_args_list
        success_comment_found = False
        for call_args in comment_calls:
            comment_text = str(call_args)
            if '✅' in comment_text and '任务完成' in comment_text:
                success_comment_found = True
                break

        self.assertTrue(
            success_comment_found,
            f"Expected success comment with '✅ 任务完成' not found in calls: {comment_calls}"
        )

    def test_no_review_metadata_in_legacy_path(self):
        """
        确保在默认配置下不会意外添加 review 相关的元数据

        验证没有 implementedBy, reviewState 等字段被设置
        """
        daemon = self._create_daemon_with_legacy_config()

        # 模拟可能有 update_task_metadata 方法的情况
        if hasattr(daemon.client, 'update_task_metadata'):
            daemon.client.update_task_metadata = MagicMock()
        else:
            # 如果还没有这个方法，添加一个 mock 来确保它不被调用
            daemon.client.update_task_metadata = MagicMock()

        mock_task = MockTask(status='completed', result='Clean implementation')
        board_task_id = 'task_no_metadata'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # 在默认/传统配置下，不应调用任何元数据更新方法
        daemon.client.update_task_metadata.assert_not_called()

    def test_config_without_route_completed_to_key(self):
        """
        验证配置文件中完全没有 route_completed_to 键时的行为

        这测试真正的"零配置"场景 - 现有用户升级后不修改配置的情况
        """
        daemon = self._create_daemon_with_legacy_config()

        # 验证配置中确实没有 route_completed_to
        self.assertNotIn('route_completed_to', daemon.config.get('agent_board', {}))

        # 验证没有 role 字段
        self.assertNotIn('role', daemon.config.get('agent_board', {}))

        mock_task = MockTask(status='completed', result='Zero config test')
        board_task_id = 'task_zero_config'

        with patch.object(daemon, '_heartbeat_worker'):
            daemon._execute_task_in_background(board_task_id, mock_task)

        # 即使没有明确配置，仍应默认到 'done'
        daemon.client.update_task_status.assert_called_once_with(
            board_task_id,
            'done',
            'Zero config test'
        )

    def test_multiple_task_executions_all_go_to_done(self):
        """
        批量测试：多个任务执行都应该进入 'done'

        确保没有间歇性的意外路由到其他列
        """
        daemon = self._create_daemon_with_legacy_config()

        test_cases = [
            ('task_batch_1', 'First completed task'),
            ('task_batch_2', 'Second completed task'),
            ('task_batch_3', 'Third completed task'),
        ]

        for board_task_id, result in test_cases:
            with self.subTest(task_id=board_task_id):
                # Reset mock to get clean call tracking
                daemon.client.update_task_status.reset_mock()

                mock_task = MockTask(status='completed', result=result)

                with patch.object(daemon, '_heartbeat_worker'):
                    daemon._execute_task_in_background(board_task_id, mock_task)

                # 每个任务都应该进入 'done'
                daemon.client.update_task_status.assert_called_once_with(
                    board_task_id, 'done', result
                )


if __name__ == '__main__':
    # 设置更详细的输出以便调试
    unittest.main(verbosity=2)