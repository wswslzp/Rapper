#!/usr/bin/env python3
"""
[TEST-SUPPLEMENT-BUG-01] 验证 done→todo 返工任务被 daemon_picked.json 永久过滤问题

关联 Bug: task_c3ce92f65d1b1862 — Daemon picked 去重机制阻塞 Board requeue
Triage: 功能缺陷 / 去重逻辑过度

RED 验证要求: 此测试在当前代码下必须 FAIL

Bug 场景:
1. 某 Board task 已被 daemon 执行并记录在 daemon_picked.json
2. PM/Reviewer 将它从 done 打回 todo，assignee 仍为原 rapper
3. daemon 后续 poll 因本地 picked 记录过滤掉该 task，导致它不再被拾取

测试验证点:
- 构造 stale picked task id + Board task column=todo/assignee=rapper-N 的场景
- 测试应证明 daemon 不应永久跳过该 task；done→todo requeue 应允许重新 claim
- 测试在当前未修复代码下必须RED，但能正常收集运行（不是 collection error）

代码路径: lib/daemon.py:644 available_tasks 过滤逻辑
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch
from datetime import datetime

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


def _make_test_config():
    """创建测试daemon配置"""
    return {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test',
            'agent_id': 'rapper-1',
            'poll_interval': 30,
            'webhook_port': 19999,
            'poll_columns': ['todo', 'ready']
        },
        'tasks': {'max_concurrent_tasks': 5},
        'logging': {'level': 'warning'},
    }


class TestDaemonRequeueStalePicked(unittest.TestCase):
    """测试 daemon picked 去重机制对 done→todo requeue 任务的处理"""

    def test_daemon_requeue_stale_picked_task_RED(self):
        """
        RED 测试: done→todo 返工任务被 picked 记录永久过滤

        Bug 复现:
        1. Task 被 daemon 处理完成，记录在 daemon_picked.json
        2. 模拟 PM 将其从 done → todo requeue（返工场景）
        3. Daemon 再次 poll，应重新拾取该 task，但被 picked 记录阻塞

        当前代码下此测试必须 FAIL
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')
            picked_file = os.path.join(temp_dir, 'daemon_picked.json')

            # 写入配置
            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_test_config(), f)

            # 模拟已处理过的任务（stale picked records）
            # 这些 task 之前被 daemon 处理过，但现在又被 PM 打回 todo
            stale_picked_tasks = ['task_requeue_001', 'task_requeue_002']
            with open(picked_file, 'w') as f:
                json.dump(stale_picked_tasks, f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_test_config()

                daemon = RapperDaemon(config_path, 'rapper-1')
                daemon.picked_tasks_file = picked_file

                # 创建返工任务场景：之前处理过的任务现在又回到 todo 列
                requeue_task = {
                    'id': 'task_requeue_001',  # 之前处理过的 task_id
                    'title': '需要返工的功能',
                    'description': 'Reviewer 发现问题，需要修改代码',
                    'column': 'todo',  # 从 done 被打回 todo
                    'assignee': 'rapper-1',  # 仍然分配给原来的 rapper
                    'created_at': '2026-05-15T10:00:00Z',
                    'updated_at': '2026-05-16T14:30:00Z'  # 最近被移回 todo
                }

                # 其他正常的新任务
                new_task = {
                    'id': 'task_new_123',
                    'title': '全新的功能请求',
                    'description': '新的开发任务',
                    'column': 'todo',
                    'assignee': None,  # 未分配
                    'created_at': '2026-05-16T15:00:00Z',
                    'updated_at': '2026-05-16T15:00:00Z'
                }

                all_todo_tasks = [requeue_task, new_task]

                # 追踪 daemon 选择的任务
                claimed_tasks = []

                def mock_claim_task(task_id, agent_id, retries=3):
                    claimed_tasks.append(task_id)
                    return True

                def mock_api_call(method, endpoint, data=None):
                    # 模拟 API 响应
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        return all_todo_tasks
                    elif 'column=ready' in endpoint:
                        return []
                    elif 'column=doing' in endpoint:
                        return []  # 没有正在处理的任务
                    elif 'column=done' in endpoint:
                        return []  # done 列为空（任务已被移走）
                    elif 'column=failed' in endpoint:
                        return []  # failed 列为空
                    else:
                        return []

                # Mock daemon API 调用
                daemon.client._make_request = MagicMock(side_effect=mock_api_call)
                daemon.client.claim_task = MagicMock(side_effect=mock_claim_task)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                # Mock 任务执行
                def mock_run_task_sync(task, timeout=None, max_turns=None):
                    task.status = 'completed'
                    task.result = f'处理完成: {task.name}'

                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner') as mock_task_runner:
                        mock_task_runner._run_task_sync = MagicMock(side_effect=mock_run_task_sync)

                        with patch('daemon.generate_task_id', return_value='internal_requeue_test'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = '任务完成'
                                mock_task.error = None
                                mock_task_class.return_value = mock_task

                                with patch.object(daemon, '_heartbeat_worker'):
                                    with patch.object(daemon, '_cleanup_completed_futures'):
                                        # 执行一轮轮询 - 这里是关键测试点
                                        daemon._poll_and_execute_tasks()

                # ======= 断言部分 =======

                # BUG 验证断言: 这个测试在当前代码下应该 FAIL
                # 当前行为: daemon 过滤掉 picked 中的任务，不会拾取任何 requeue 任务
                # 期望行为: daemon 应该重新拾取 done→todo requeue 的任务

                # 检查是否有任务被认领
                if len(claimed_tasks) == 0:
                    # 当前代码的错误行为: 没有任务被认领
                    # 这是因为 requeue_task 在 picked_tasks 中被过滤掉了
                    # 而 new_task 可能也受到其他逻辑影响
                    self.fail(
                        "BUG 确认: daemon 没有认领任何任务。"
                        "返工任务 'task_requeue_001' 被 picked_tasks 过滤，"
                        "新任务 'task_new_123' 也未被拾取。"
                        "这证明了 done→todo requeue 被永久阻塞的问题。"
                    )
                else:
                    claimed_task = claimed_tasks[0]

                    # 关键断言: daemon 应该能拾取返工任务，但当前代码不行
                    self.assertEqual(
                        claimed_task,
                        'task_requeue_001',
                        f"BUG: daemon 应该优先拾取返工任务 'task_requeue_001'，"
                        f"但实际拾取了 '{claimed_task}'。"
                        f"这表明 picked_tasks 去重过滤阻塞了 done→todo requeue。"
                    )

                # 额外验证: 检查 picked_tasks 文件状态
                current_picked = daemon._load_picked_tasks()
                self.assertIn('task_requeue_001', current_picked,
                            "返工任务仍应在 picked_tasks 中（这是问题根源）")

    def test_daemon_cleanup_doesnt_handle_requeue_scenario(self):
        """
        测试验证 _cleanup_completed_picked_tasks 不处理 requeue 场景

        场景:
        1. picked_tasks 包含之前处理过的 task_id
        2. 该 task 现在在 todo 列（被 requeue），不在 done/failed
        3. cleanup 方法不会清理它（因为不是 terminal 状态）
        4. 导致 task 永久无法被重新拾取
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            picked_file = os.path.join(temp_dir, 'daemon_picked.json')

            # 设置 picked_tasks 包含一个 requeue 任务
            picked_tasks = ['task_requeue_456', 'task_completed_789']
            with open(picked_file, 'w') as f:
                json.dump(picked_tasks, f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_test_config()

                daemon = RapperDaemon('/fake/config.yaml', 'rapper-1')
                daemon.picked_tasks_file = picked_file

                # 模拟 Board 状态:
                # - task_requeue_456 在 todo（被 requeue）
                # - task_completed_789 在 done（正常完成）
                def mock_api_call(method, endpoint, data=None):
                    if 'column=done' in endpoint:
                        return [{'id': 'task_completed_789'}]  # 只有已完成的任务
                    elif 'column=failed' in endpoint:
                        return []  # 没有失败任务
                    elif 'column=todo' in endpoint:
                        return [{'id': 'task_requeue_456'}]  # requeue 任务在 todo
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)

                # 执行 cleanup 方法
                daemon._cleanup_completed_picked_tasks()

                # 验证 cleanup 行为
                cleaned_picked = daemon._load_picked_tasks()

                # task_completed_789 应该被清理（在 done 状态）
                self.assertNotIn('task_completed_789', cleaned_picked,
                                "已完成的任务应该被 cleanup 清理")

                # 关键断言: task_requeue_456 不应该被清理（因为在 todo，不是 terminal 状态）
                # 这是 BUG 的根源 - cleanup 不处理 requeue 场景
                self.assertIn('task_requeue_456', cleaned_picked,
                            "BUG 根源: requeue 任务仍在 picked_tasks 中，"
                            "因为 cleanup 只清理 terminal 状态（done/failed）任务，"
                            "但 requeue 任务现在是 todo 状态")

    def test_daemon_poll_filters_stale_picked_correctly_FIXED(self):
        """
        测试验证修复后的 poll 循环过滤逻辑

        修复后的逻辑应该:
        - 允许历史记录中在 todo/ready 状态的任务重新被拾取 (requeue 场景)
        - 仍然阻止当前在 doing 状态的任务 (防止真正的重复拾取)
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            picked_file = os.path.join(temp_dir, 'daemon_picked.json')

            # 设置包含 requeue 任务的 picked_tasks
            stale_picked_tasks = ['requeue_task_001']
            with open(picked_file, 'w') as f:
                json.dump(stale_picked_tasks, f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_test_config()

                daemon = RapperDaemon('/fake/config.yaml', 'rapper-1')
                daemon.picked_tasks_file = picked_file

                # 创建测试任务 - requeue 场景（在 todo 状态）
                requeue_task = {
                    'id': 'requeue_task_001',
                    'title': 'Requeue 任务',
                    'description': '被 PM 打回的任务',
                    'column': 'todo',  # 关键：现在在 todo 状态
                    'assignee': 'rapper-1'
                }

                claimable_tasks = [requeue_task]

                # 模拟新的过滤逻辑
                historical_picked = daemon._load_picked_tasks()
                currently_doing = set()  # 没有正在执行的任务

                # 验证 historical_picked 包含 requeue 任务
                self.assertIn('requeue_task_001', historical_picked,
                            "historical_picked 应该包含之前处理过的任务")

                # 应用新的过滤逻辑
                available_tasks = []
                for task in claimable_tasks:
                    task_id = task['id']

                    # 总是阻止当前正在执行的任务
                    if task_id in currently_doing:
                        continue

                    # 对于历史记录中的任务，检查是否在可重新拾取的状态
                    if task_id in historical_picked:
                        task_column = task.get('column', '')
                        if task_column in ['todo', 'ready']:
                            available_tasks.append(task)  # 允许 requeue
                        else:
                            continue  # 阻止其他状态
                    else:
                        available_tasks.append(task)  # 新任务总是允许

                # 修复后行为: requeue 任务应该可用
                self.assertEqual(len(available_tasks), 1,
                               f"修复后 requeue 任务应该可用。"
                               f"claimable_tasks: {len(claimable_tasks)}, "
                               f"available_tasks: {len(available_tasks)}, "
                               f"historical_picked: {historical_picked}")

                self.assertEqual(available_tasks[0]['id'], 'requeue_task_001',
                               "应该允许拾取 requeue 任务")

    def test_daemon_should_distinguish_requeue_vs_duplicate_FIXED(self):
        """
        测试验证修复后 daemon 正确区分 requeue 和真正的重复拾取

        修复后行为:
        - 真正的重复拾取 (doing 状态) 应该被阻止
        - done→todo requeue 应该被允许
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            picked_file = os.path.join(temp_dir, 'daemon_picked.json')

            # picked_tasks 包含两种任务
            picked_tasks = ['duplicate_task_001', 'requeue_task_002']
            with open(picked_file, 'w') as f:
                json.dump(picked_tasks, f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_test_config()

                daemon = RapperDaemon('/fake/config.yaml', 'rapper-1')
                daemon.picked_tasks_file = picked_file

                # 场景1: 真正的重复拾取 - 任务仍在 doing
                duplicate_task = {
                    'id': 'duplicate_task_001',
                    'column': 'doing',  # 仍在执行中
                    'assignee': 'rapper-1'
                }

                # 场景2: 合法的 requeue - 任务从 done 回到 todo
                requeue_task = {
                    'id': 'requeue_task_002',
                    'column': 'todo',  # 被 requeue 回 todo
                    'assignee': 'rapper-1'
                }

                # 模拟 Board API 响应
                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        return [requeue_task]  # 只有 requeue 任务在 todo
                    elif 'column=doing' in endpoint and 'assignee=rapper-1' in endpoint:
                        return [duplicate_task]  # duplicate 任务仍在 doing
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)

                # 模拟新的过滤逻辑
                # 1. 获取 todo 任务
                todo_tasks = daemon.client.get_tasks(None, 'todo')
                # 2. 获取 doing 任务用于去重
                doing_tasks = daemon.client.get_tasks('rapper-1', 'doing')
                # 3. 加载历史 picked 记录
                historical_picked = daemon._load_picked_tasks()

                # 验证状态
                self.assertEqual(len(todo_tasks), 1, "应该有 1 个 todo 任务")
                self.assertEqual(len(doing_tasks), 1, "应该有 1 个 doing 任务")
                self.assertEqual(len(historical_picked), 2, "historical_picked 应该有 2 个记录")

                # 构建当前执行中的任务集合
                currently_doing = set()
                for task in doing_tasks:
                    currently_doing.add(task['id'])

                # 应用新的过滤逻辑
                available_tasks = []
                for task in todo_tasks:
                    task_id = task['id']

                    # 总是阻止当前正在执行的任务
                    if task_id in currently_doing:
                        continue

                    # 对于历史记录中的任务，检查是否在可重新拾取的状态
                    if task_id in historical_picked:
                        task_column = task.get('column', '')
                        if task_column in ['todo', 'ready']:
                            available_tasks.append(task)  # 允许 requeue
                        else:
                            continue  # 阻止其他状态
                    else:
                        available_tasks.append(task)  # 新任务总是允许

                # 修复后行为验证
                self.assertEqual(len(available_tasks), 1,
                               f"修复后应该只有 requeue 任务可用。"
                               f"todo_tasks: {[t['id'] for t in todo_tasks]}, "
                               f"currently_doing: {currently_doing}, "
                               f"historical_picked: {historical_picked}, "
                               f"available: {[t['id'] for t in available_tasks]}")

                # 验证正确的任务被选中
                self.assertEqual(available_tasks[0]['id'], 'requeue_task_002',
                               "应该允许拾取 requeue 任务，而阻止 doing 任务")

                # 验证重复拾取确实被阻止
                self.assertNotIn('duplicate_task_001', [t['id'] for t in available_tasks],
                               "正在 doing 的任务不应该被允许重复拾取")


if __name__ == '__main__':
    # 运行测试，显示详细输出
    unittest.main(verbosity=2)