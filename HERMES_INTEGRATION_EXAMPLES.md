# Hermes Integration Examples

这些示例演示了如何使用Rapper的改进功能进行结构化结果回报和并发控制。

## 子任务 1 — 结构化任务结果回报

### 改进内容

1. **增强的结构化结果解析**：现在可以解析多种JSON格式，包括推断结果
2. **更清晰的Claude指导**：使用强调的提示确保Claude输出正确格式
3. **改进的状态输出**：`--status` 命令现在显示详细的结构化结果
4. **Hermes集成JSON**：为程序化访问提供机器可读格式

### 使用示例

#### 基本任务状态查询
```bash
# 查看任务状态（人类可读）
rapper --status 20260507-223624-cnzj

# 输出示例：
# ID:      20260507-223624-cnzj
# Name:    test-structured-result
# Status:  completed
# Workdir: /app/rapper
# Elapsed: 19s
# 
# Structured Result:
#   Status:      completed
#   Output Path: hello.txt
#   PR URL:      (none)
#   Errors:      []
# 
# # HERMES_INTEGRATION_JSON: {"task_id": "...", "status": "completed", "structured_result": {...}}
```

#### Python集成（Hermes使用）
```python
from lib.hermes_integration import RapperTaskManager

manager = RapperTaskManager()

# 获取任务状态
status = manager.get_task_status("20260507-223624-cnzj")
print(f"Task status: {status['status']}")
print(f"Output: {status['structured_result']['output_path']}")
```

## 子任务 2 — 多 Rapper 并发资源限制

### 改进内容

1. **详细的任务计数**：新的 `--task-count-json` 命令提供完整的并发信息
2. **Python工具库**：`lib/hermes_integration.py` 提供了易用的并发控制API
3. **容量检查**：能够检查是否达到最大并发限制
4. **等待机制**：支持等待任务槽位可用

### 使用示例

#### 基本并发信息查询
```bash
# 简单计数（向后兼容）
rapper --task-count
# 输出: 4

# 详细JSON信息（新功能）
rapper --task-count-json
```

输出示例：
```json
{
  "timestamp": 1778161033,
  "concurrency": {
    "running": 4,
    "max_concurrent": 5,
    "at_capacity": false,
    "available_slots": 1
  },
  "task_counts": {
    "pending": 0,
    "running": 4,
    "completed": 189,
    "failed": 48,
    "cancelled": 6,
    "total": 247
  }
}
```

#### Python集成 — 方案A（推荐）：Hermes侧信号量控制

```python
from lib.hermes_integration import RapperTaskManager
import time

manager = RapperTaskManager()

def start_task_with_concurrency_control(name: str, prompt: str, **kwargs):
    """启动任务，如果达到并发限制则等待"""
    
    # 检查是否可以立即启动
    if manager.can_start_task():
        return manager.start_task(name, prompt, **kwargs)
    
    # 等待有可用槽位
    print("At capacity, waiting for available slot...")
    if manager.wait_for_slot(max_wait=300):  # 等待最多5分钟
        return manager.start_task(name, prompt, **kwargs)
    else:
        print("Timeout waiting for available slot")
        return None

# 使用示例
task_id = start_task_with_concurrency_control(
    "implement-feature",
    "Implement user authentication",
    workdir="/app/myproject",
    worktree=True
)

if task_id:
    print(f"Started task: {task_id}")
    
    # 等待完成
    result = manager.wait_for_completion(task_id, timeout=3600)
    if result:
        structured_result = result.get("structured_result", {})
        if structured_result.get("status") == "completed":
            print(f"Task completed! Output: {structured_result.get('output_path')}")
        else:
            print(f"Task failed: {structured_result.get('errors')}")
else:
    print("Could not start task")
```

#### 便捷函数使用
```python
from lib.hermes_integration import (
    get_available_task_slots,
    can_start_rapper_task,
    start_rapper_task_with_concurrency_check
)

# 检查可用槽位
available = get_available_task_slots()
print(f"Available slots: {available}")

# 检查是否可以启动任务
if can_start_rapper_task():
    # 启动任务（自动处理并发控制）
    task_id = start_rapper_task_with_concurrency_check(
        "test-task",
        "Create a test file",
        workdir="/app/test"
    )
```

#### 批量任务管理
```python
def process_task_queue(tasks, max_concurrent=3):
    """处理任务队列，维持最大并发数"""
    
    manager = RapperTaskManager()
    active_tasks = {}
    completed_tasks = []
    
    task_queue = tasks.copy()
    
    while task_queue or active_tasks:
        # 启动新任务（如果有空间）
        while len(active_tasks) < max_concurrent and task_queue:
            if manager.can_start_task():
                task_spec = task_queue.pop(0)
                task_id = manager.start_task(**task_spec)
                if task_id:
                    active_tasks[task_id] = task_spec
                    print(f"Started task: {task_id}")
                else:
                    task_queue.insert(0, task_spec)  # 放回队列
                    break
            else:
                break
        
        # 检查完成的任务
        for task_id in list(active_tasks.keys()):
            status = manager.get_task_status(task_id)
            if status and status.get("status") in ["completed", "failed", "cancelled"]:
                completed_tasks.append((task_id, status))
                del active_tasks[task_id]
                print(f"Task {task_id} completed: {status.get('status')}")
        
        # 短暂等待
        time.sleep(5)
    
    return completed_tasks

# 使用示例
tasks = [
    {"name": "task1", "prompt": "Task 1 description", "workdir": "/app/project1"},
    {"name": "task2", "prompt": "Task 2 description", "workdir": "/app/project2"},
    {"name": "task3", "prompt": "Task 3 description", "workdir": "/app/project3"},
]

results = process_task_queue(tasks)
```

## 配置

### 最大并发任务数配置

在 `~/.rapper/config.yaml` 中设置：

```yaml
tasks:
  max_concurrent_tasks: 5  # 调整为适合你的资源限制
```

### 结构化结果格式

Claude会被自动指导输出以下格式：

```json
{
  "status": "completed|failed|partial",
  "output_path": "relative/path/to/main/file", 
  "pr_url": "https://github.com/user/repo/pull/123",
  "errors": ["error message 1", "error message 2"]
}
```

如果Claude没有按格式输出，系统会尝试从文本内容中推断结构化结果。