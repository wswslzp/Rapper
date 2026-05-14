#!/usr/bin/env python3
"""
[TEST-SUPP-002] 验证 Daemon 不被历史 todo 残留阻塞

关联 Bug: task_120fd217 — 大量历史 todo 残留阻塞 Daemon
Triage: escape · 设计遗漏

RED 验证要求: 此测试在当前代码下必须 FAIL

测试场景:
1. Board 预置 10+ 历史 todo 任务
2. 创建 1 个新 todo 任务 → Daemon 应在 60s 内认领
3. Daemon 不重复拾取历史 todo

代码路径: lib/daemon.py poll loop + picked_tasks 去重
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch
from datetime import datetime, timedelta

# Add lib directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))

from daemon import AgentBoardClient, RapperDaemon


def _make_minimal_config():
    """创建最小 daemon 配置"""
    return {
        'agent_board': {
            'url': 'http://localhost:3456',
            'api_key': 'sk-test',
            'agent_id': 'test-agent',
            'poll_interval': 5,  # 更快轮询以测试及时性
            'webhook_port': 19999,
        },
        'tasks': {'max_concurrent_tasks': 5},
        'logging': {'level': 'warning'},
    }


def _create_historical_todo_tasks(count=15, days_old=7):
    """创建历史 todo 任务列表"""
    base_time = datetime.utcnow() - timedelta(days=days_old)
    historical_tasks = []

    for i in range(count):
        task_time = base_time + timedelta(hours=i)  # 按小时分布历史任务
        task = {
            'id': f'historical_todo_{i:03d}',
            'title': f'历史任务 #{i+1}',
            'description': f'历史 todo 任务创建于 {task_time.strftime("%Y-%m-%d %H:%M")}',
            'column': 'todo',
            'assignee': None,  # 未分配的历史任务
            'created_at': task_time.isoformat() + 'Z',
            'updated_at': task_time.isoformat() + 'Z'
        }
        historical_tasks.append(task)

    return historical_tasks


def _create_new_urgent_task(task_id='new_urgent_task'):
    """创建新的紧急任务"""
    now = datetime.utcnow()
    return {
        'id': task_id,
        'title': '紧急：生产环境 Bug 修复',
        'description': '需要立即处理的生产环境关键问题',
        'column': 'todo',
        'assignee': None,
        'created_at': now.isoformat() + 'Z',
        'updated_at': now.isoformat() + 'Z',
        'priority': 'high'
    }


class TestDaemonHistoricalBlocking(unittest.TestCase):
    """测试 Daemon 被历史 todo 任务阻塞的问题"""

    def test_daemon_blocked_by_historical_todos_RED(self):
        """
        RED 测试: Daemon 被大量历史 todo 任务阻塞，无法及时处理新任务

        此测试必须在当前代码下 FAIL 以证明 bug 存在

        测试场景:
        1. Board 上有 15 个历史 todo 任务（7天前创建）
        2. 新增 1 个紧急 todo 任务
        3. Daemon 应该优先处理新任务，而非被历史任务阻塞
        4. BUG: 当前 Daemon 线性处理，历史任务阻塞新任务
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')

            # 写入配置文件
            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                # 创建测试数据: 15个历史任务 + 1个新紧急任务
                historical_tasks = _create_historical_todo_tasks(15)
                new_urgent_task = _create_new_urgent_task()

                # 合并所有任务 - 历史任务在前（模拟 API 返回顺序问题）
                all_todo_tasks = historical_tasks + [new_urgent_task]

                # 追踪哪个任务被选中
                claimed_task_id = None
                claim_order = []

                def mock_claim_task(task_id, agent_id, retries=3):
                    nonlocal claimed_task_id
                    claimed_task_id = task_id
                    claim_order.append(task_id)
                    return True

                # 模拟 API 响应
                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        return all_todo_tasks
                    elif 'column=ready' in endpoint:
                        return []
                    elif 'column=doing' in endpoint:
                        return []
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)
                daemon.client.claim_task = MagicMock(side_effect=mock_claim_task)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                # 模拟任务执行器
                def mock_run_task_sync(task, timeout=None, max_turns=None):
                    task.status = 'completed'
                    task.result = f'已完成 {task.name}'

                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner') as mock_task_runner:
                        mock_task_runner._run_task_sync = MagicMock(side_effect=mock_run_task_sync)

                        with patch('daemon.generate_task_id', return_value='internal_123'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = '任务完成'
                                mock_task.error = None
                                mock_task_class.return_value = mock_task

                                with patch.object(daemon, '_heartbeat_worker'):
                                    # 记录开始时间
                                    start_time = time.time()

                                    # 执行一轮轮询
                                    daemon._poll_and_execute_tasks()

                                    elapsed_time = time.time() - start_time

                # 断言: 这个测试应该在当前代码下 FAIL
                # 当前行为: 选择列表中第一个任务（历史任务）
                # 期望行为: 应该优先选择新的紧急任务

                self.assertIsNotNone(claimed_task_id, "Daemon 应该选择了一个任务")

                # BUG 断言: 这个应该 FAIL，因为 daemon 选择历史任务而非新任务
                self.assertEqual(claimed_task_id, 'new_urgent_task',
                    f"期望失败: Daemon 应该优先选择新紧急任务，但选择了 {claimed_task_id}。"
                    f"这证明了阻塞 bug - daemon 线性处理任务，不区分新旧/紧急程度。")

                # 额外断言记录问题行为
                if claimed_task_id != 'new_urgent_task':
                    self.assertTrue(claimed_task_id.startswith('historical_todo_'),
                                  f"Bug 确认: 选择了历史任务 {claimed_task_id} 而非紧急任务")

                # 性能断言: 即使有很多历史任务，任务选择也应该很快
                self.assertLess(elapsed_time, 1.0,
                              f"任务选择即使有 {len(historical_tasks)} 个历史任务也应该很快，"
                              f"但用了 {elapsed_time:.2f}s")

    def test_daemon_deduplication_prevents_repeated_pickup(self):
        """
        测试 Daemon 去重机制防止重复拾取历史任务

        场景:
        1. Daemon 启动时已有历史任务被处理过（在 picked_tasks 文件中）
        2. 新任务到来时，不应重复处理已处理的历史任务
        3. 确保 picked_tasks 文件正确维护状态
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')
            picked_file = os.path.join(temp_dir, 'picked.json')

            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            # 预先设置已处理的任务列表
            already_picked = ['historical_todo_001', 'historical_todo_002', 'historical_todo_003']
            with open(picked_file, 'w') as f:
                json.dump(already_picked, f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = picked_file

                # 创建任务: 一些已处理的历史任务 + 新任务
                historical_tasks = _create_historical_todo_tasks(10)
                new_task = _create_new_urgent_task('new_task_456')
                all_tasks = historical_tasks + [new_task]

                claimed_tasks = []

                def mock_claim_task(task_id, agent_id, retries=3):
                    claimed_tasks.append(task_id)
                    return True

                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        return all_tasks
                    elif 'column=ready' in endpoint:
                        return []
                    elif 'column=doing' in endpoint:
                        return []
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)
                daemon.client.claim_task = MagicMock(side_effect=mock_claim_task)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner'):
                        daemon.task_runner._run_task_sync = MagicMock()
                        with patch('daemon.generate_task_id', return_value='internal_456'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = '完成'
                                mock_task_class.return_value = mock_task

                                with patch.object(daemon, '_heartbeat_worker'):
                                    # 执行轮询
                                    daemon._poll_and_execute_tasks()

                # 验证去重机制工作
                self.assertEqual(len(claimed_tasks), 1, "应该只处理一个任务")

                claimed_task = claimed_tasks[0]

                # 不应该处理已经在 picked_tasks 中的任务
                self.assertNotIn(claimed_task, already_picked,
                                f"不应该重复处理已选择的任务 {claimed_task}")

                # 应该处理新任务或未处理的历史任务
                if claimed_task == 'new_task_456':
                    # 理想情况：选择了新任务
                    pass
                else:
                    # 如果选择了历史任务，至少应该是未处理过的
                    self.assertNotIn(claimed_task, already_picked,
                                   f"选择的历史任务 {claimed_task} 应该是未处理过的")

    def test_daemon_performance_with_many_historical_tasks(self):
        """
        测试 Daemon 在大量历史任务存在时的性能表现

        场景: 50个历史任务 + 1个新任务
        期望: 任务选择和过滤应该在合理时间内完成（< 2秒）
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')

            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = os.path.join(temp_dir, 'picked.json')

                # 创建大量历史任务
                historical_tasks = _create_historical_todo_tasks(50)
                new_task = _create_new_urgent_task('perf_test_task')
                all_tasks = historical_tasks + [new_task]

                claimed_tasks = []

                def mock_claim_task(task_id, agent_id, retries=3):
                    claimed_tasks.append(task_id)
                    return True

                def mock_api_call(method, endpoint, data=None):
                    if 'column=todo' in endpoint and 'assignee=' not in endpoint:
                        return all_tasks
                    elif 'column=ready' in endpoint:
                        return []
                    elif 'column=doing' in endpoint:
                        return []
                    else:
                        return []

                daemon.client._make_request = MagicMock(side_effect=mock_api_call)
                daemon.client.claim_task = MagicMock(side_effect=mock_claim_task)
                daemon.client.update_task_status = MagicMock(return_value=True)
                daemon.client.add_comment = MagicMock(return_value=True)

                with patch.object(daemon, '_count_running_tasks', return_value=0):
                    with patch.object(daemon, 'task_runner'):
                        daemon.task_runner._run_task_sync = MagicMock()
                        with patch('daemon.generate_task_id', return_value='internal_perf'):
                            with patch('daemon.Task') as mock_task_class:
                                mock_task = MagicMock()
                                mock_task.status = 'completed'
                                mock_task.result = '完成'
                                mock_task_class.return_value = mock_task

                                with patch.object(daemon, '_heartbeat_worker'):
                                    # 性能测试
                                    start_time = time.time()
                                    daemon._poll_and_execute_tasks()
                                    elapsed_time = time.time() - start_time

                # 性能断言
                self.assertLess(elapsed_time, 2.0,
                              f"处理 {len(all_tasks)} 个任务的轮询应该在 2s 内完成，"
                              f"实际用时 {elapsed_time:.2f}s")

                # 验证确实处理了一个任务
                self.assertEqual(len(claimed_tasks), 1, "应该处理了一个任务")

    def test_picked_tasks_file_corruption_recovery(self):
        """
        测试 picked_tasks 文件损坏时的恢复机制

        场景: picked_tasks.json 文件损坏或为空
        期望: Daemon 能够恢复并继续正常工作，不会崩溃
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, 'config.yaml')
            picked_file = os.path.join(temp_dir, 'picked.json')

            import yaml
            with open(config_path, 'w') as f:
                yaml.dump(_make_minimal_config(), f)

            # 创建损坏的 picked_tasks 文件
            with open(picked_file, 'w') as f:
                f.write('{"invalid": json format')  # 损坏的 JSON

            with patch('daemon.RapperDaemon._load_config') as mock_load_config:
                mock_load_config.return_value = _make_minimal_config()

                daemon = RapperDaemon(config_path, 'test-agent')
                daemon.picked_tasks_file = picked_file

                # 尝试加载损坏的文件
                picked_tasks = daemon._load_picked_tasks()

                # 应该返回空集合，不应该崩溃
                self.assertEqual(picked_tasks, set(),
                               "损坏的 picked_tasks 文件应该返回空集合")

                # 验证可以保存新任务
                daemon._save_picked_task('test_task_001')

                # 重新加载应该包含新保存的任务
                updated_tasks = daemon._load_picked_tasks()
                self.assertIn('test_task_001', updated_tasks,
                            "应该能够保存和加载新的 picked_tasks")


if __name__ == '__main__':
    # 运行测试，显示详细输出
    unittest.main(verbosity=2)