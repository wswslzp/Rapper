# Hermes Integration Guide

本指南展示如何在Hermes中使用Rapper的结构化结果和并发控制功能。

## 🚀 功能概览

### 1. 并发控制
- **检查资源可用性**: `rapper --concurrency --json`
- **自动限制**: 任务启动前自动检查并发限制
- **配置**: 通过 `~/.rapper/config.yaml` 的 `tasks.max_concurrent_tasks` 配置

### 2. 结构化结果回报
- **标准JSON格式**: `{"status": "completed|failed|partial", "output_path": "...", "pr_url": "...", "errors": [...]}`
- **智能解析**: 5层解析策略，高成功率
- **状态API**: `rapper --status <task_id> --json`

## 📝 Hermes 集成代码示例

### 1. 任务启动前的并发检查

```python
import subprocess
import json
import time

def can_start_new_rapper_task() -> bool:
    """检查是否可以启动新的Rapper任务"""
    try:
        result = subprocess.run(
            ["rapper", "--concurrency", "--json"], 
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return False
            
        concurrency = json.loads(result.stdout)
        return concurrency.get("can_start_new", False)
    except Exception:
        return False

def wait_for_available_slot(max_wait_seconds=300):
    """等待有可用的Rapper槽位"""
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        if can_start_new_rapper_task():
            return True
        time.sleep(30)  # 每30秒检查一次
    return False

# 使用示例
if can_start_new_rapper_task():
    # 立即启动任务
    task_id = start_rapper_task(prompt)
elif wait_for_available_slot():
    # 等待后启动任务
    task_id = start_rapper_task(prompt)
else:
    # 等待超时，处理错误
    raise Exception("Rapper concurrency limit reached, cannot start task")
```

### 2. 任务状态轮询和结果提取

```python
def get_rapper_task_result(task_id: str) -> dict:
    """获取Rapper任务的结构化结果"""
    try:
        result = subprocess.run(
            ["rapper", "--status", task_id, "--json"], 
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {"error": f"Failed to get task status: {result.stderr}"}
            
        task_data = json.loads(result.stdout)
        
        # 检查任务是否完成
        if not task_data.get("is_complete", False):
            return {"status": "running", "task_data": task_data}
        
        # 提取结构化结果（优先）
        structured = task_data.get("structured_result")
        if structured:
            return {
                "status": "completed_with_structured_result",
                "structured_result": structured,
                "success": structured.get("status") == "completed",
                "output_path": structured.get("output_path"),
                "pr_url": structured.get("pr_url"),
                "errors": structured.get("errors", []),
                "task_status": task_data.get("status"),
                "elapsed": task_data.get("elapsed_str")
            }
        
        # 降级到基本状态检查
        return {
            "status": "completed_without_structured_result", 
            "success": task_data.get("is_successful", False),
            "has_errors": task_data.get("has_errors", False),
            "task_status": task_data.get("status"),
            "error": task_data.get("error"),
            "elapsed": task_data.get("elapsed_str")
        }
        
    except Exception as e:
        return {"error": f"Exception getting task result: {str(e)}"}

def poll_rapper_task_completion(task_id: str, timeout_seconds=3600) -> dict:
    """轮询Rapper任务直到完成"""
    start_time = time.time()
    
    while time.time() - start_time < timeout_seconds:
        result = get_rapper_task_result(task_id)
        
        if result.get("status") != "running":
            return result
            
        # 每30秒轮询一次
        time.sleep(30)
    
    return {"error": "Task timeout", "timeout_seconds": timeout_seconds}
```

### 3. 完整的Hermes delegate_task 集成

```python
def delegate_task_with_rapper(prompt: str, workdir: str = None, max_wait=300) -> dict:
    """使用Rapper执行任务的完整流程"""
    
    # 1. 检查并发限制
    if not wait_for_available_slot(max_wait):
        return {
            "success": False,
            "error": "Rapper concurrency limit reached, timeout waiting for available slot"
        }
    
    # 2. 启动任务
    try:
        cmd = ["rapper", "--background", f"hermes-task-{int(time.time())}", "-p", prompt]
        if workdir:
            cmd.extend(["--workdir", workdir])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"success": False, "error": f"Failed to start rapper: {result.stderr}"}
        
        # 从输出中提取task_id（格式："Started task: 20260430-210845-gcao"）
        import re
        match = re.search(r'Started task: ([a-z0-9-]+)', result.stdout)
        if not match:
            return {"success": False, "error": "Could not extract task ID from rapper output"}
        
        task_id = match.group(1)
        
    except Exception as e:
        return {"success": False, "error": f"Exception starting rapper: {str(e)}"}
    
    # 3. 轮询等待完成
    task_result = poll_rapper_task_completion(task_id)
    
    # 4. 处理结果
    if "error" in task_result:
        return {"success": False, **task_result}
    
    # 成功完成
    if task_result.get("status") == "completed_with_structured_result":
        structured = task_result["structured_result"]
        return {
            "success": structured.get("status") == "completed",
            "task_id": task_id,
            "output_path": structured.get("output_path"),
            "pr_url": structured.get("pr_url"),
            "errors": structured.get("errors", []),
            "elapsed": task_result.get("elapsed"),
            "result_type": "structured"
        }
    else:
        # 基本完成状态
        return {
            "success": task_result.get("success", False),
            "task_id": task_id,
            "has_errors": task_result.get("has_errors", False),
            "error": task_result.get("error"),
            "elapsed": task_result.get("elapsed"),
            "result_type": "basic"
        }

# 使用示例
result = delegate_task_with_rapper(
    prompt="Implement a binary search function in Python",
    workdir="/path/to/project"
)

if result["success"]:
    print(f"Task completed! Output: {result.get('output_path')}")
    if result.get('pr_url'):
        print(f"PR created: {result['pr_url']}")
else:
    print(f"Task failed: {result.get('error')}")
    if result.get('errors'):
        print(f"Structured errors: {result['errors']}")
```

## 🧪 测试验证

### 测试成功场景

```bash
# 启动测试任务
rapper --background test-success -p "创建hello.py文件，打印hello world"

# 检查结果
rapper --status <task_id> --json
```

期望结果：
```json
{
  "structured_result": {
    "status": "completed",
    "output_path": "hello.py",
    "pr_url": null,
    "errors": []
  }
}
```

### 测试失败场景

```bash
# 启动会失败的任务
rapper --background test-fail -p "读取不存在的文件/nonexistent.txt"

# 检查结果
rapper --status <task_id> --json
```

期望结果：
```json
{
  "structured_result": {
    "status": "completed", 
    "output_path": null,
    "pr_url": null,
    "errors": ["File does not exist: /nonexistent.txt"]
  }
}
```

## ⚙️ 配置

### ~/.rapper/config.yaml

```yaml
tasks:
  max_concurrent_tasks: 5  # 最大并发任务数

# 其他配置...
agent_board:
  # ...
safety:
  # ...
```

### 监控并发状态

```bash
# 实时查看并发状态
watch -n 5 "rapper --concurrency --json | jq"
```

这个集成方案为Hermes提供了强大的任务调度和结果处理能力！ 🚀