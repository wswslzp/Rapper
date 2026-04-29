# Claude-Code Improvements Implementation Plan

**Goal:** 把 claude-code skill 中三个优先级的改进建议落地到 Rapper 代码和 claude-background-tasks skill。

**Architecture:** 三步串行实施，每步包含：Rapper 代码修改（如需要） + skill 文档更新。

**Tech Stack:** Python (task_runner.py), Bash (rapper 入口), Skill YAML+Markdown

---

## 步骤 1：高优先级 — subtype 持久化 + session_id 保存与续接

### 现状分析
- `_monitor_task()` L354-357 和 `_run_task_sync()` L480-483 已解析 subtype，但结果被丢弃
- `Task` dataclass 没有 `fail_reason` 和 `session_id` 字段
- `status` 命令输出没有展示 fail_reason，cronjob monitor 无法区分失败类型
- `result` event 里有 `session_id` 字段，完全未被保存

### 任务列表

**Task 1.1** — 给 `Task` dataclass 加两个字段
- 文件：`/app/rapper/lib/task_runner.py`
- 修改：在 `error: str | None = None` 后加 `fail_reason: str | None = None` 和 `session_id: str | None = None`
- 同步更新 `save()` 和 `load()` 方法

**Task 1.2** — `_monitor_task()` 中持久化 fail_reason 和 session_id
- 在 `type == result` 分支里，提取 `subtype` 存入 `task.fail_reason`，提取 `session_id` 存入 `task.session_id`
- 在 `_run_task_sync()` 中同步修改（复制相同逻辑）

**Task 1.3** — `status` 命令输出展示 fail_reason 和 session_id
- 在 CLI `status` 子命令里加两行打印

**Task 1.4** — 更新 `claude-background-tasks` skill
- 新增"Session ID 续接"章节，说明如何用 `--status` 拿到 session_id，再用 `claude -p ... --resume <id>` 续接
- 更新 fail_reason 区分逻辑说明（error_max_turns vs error_budget vs 其他）

---

## 步骤 2：中优先级 — --max-budget-usd + --fallback-model 参数穿透

### 现状分析
- `start_task()` 接受 `max_turns` 参数，有扩展性
- `_run_task_sync()` 硬编码 `--max-turns 50`，不接受任何外部参数
- `do_background()` 的 bash 解析只支持 `-p` 和 `-w`，不支持预算/fallback
- `Task` dataclass 没有存储这些参数（重启后无法回溯）

### 任务列表

**Task 2.1** — `Task` dataclass 加 `max_budget_usd` 和 `fallback_model` 字段

**Task 2.2** — `_run_task_sync()` 接受这两个参数并透传给 claude cmd
- 函数签名加 `max_budget_usd: float | None = None` 和 `fallback_model: str | None = None`
- 构建 cmd 时条件追加 `--max-budget-usd` 和 `--fallback-model`

**Task 2.3** — `do_background()` bash 函数解析新参数
- 在 `rapper` 入口脚本里，`do_background()` 解析 `--budget <usd>` 和 `--fallback <model>`
- 透传给 `task_runner.py run` 子命令
- `run` 子命令的 argparse 也加这两个参数

**Task 2.4** — 更新 `claude-background-tasks` skill
- Rapper CLI 用法章节加 `--budget` 和 `--fallback` 参数说明
- 成本控制章节说明 `--budget` 和 `--max-turns` 的组合策略

---

## 步骤 3：低优先级 — --no-session-persistence + --debug-file

### 现状分析
- 这两个纯属 claude CLI flag，不需要改 Rapper 代码
- 仅需更新 skill 文档

### 任务列表

**Task 3.1** — `claude-background-tasks` skill 加 `--no-session-persistence` 说明
- 在 Pitfalls 章节加一条：后台 daemon 任务默认会在 `~/.claude/` 积累 session 文件，长期运行需注意清理，或在 `_run_task_sync` 里加 `--no-session-persistence`
- 在 `_run_task_sync` 的 cmd 列表里可选加入此 flag

**Task 3.2** — `claude-background-tasks` skill 加 `--debug-file` 说明
- 在 Pitfalls 章节加一条：后台 daemon 出问题时可以在 `_run_task_sync` 里加 `--debug-file <path>` 把调试日志写到单独文件
- 给出具体路径建议：`~/.rapper/tasks/<task_id>.debug.log`

---

---

## 实施进度

### ✅ 已完成

**Task 1.1** — `Task` dataclass 加字段（`fail_reason`、`session_id`、`max_budget_usd`、`fallback_model`），save()/load() 同步更新

**Task 1.2** — `_monitor_task()` 和 `_run_task_sync()` 两处都解析 subtype → `fail_reason`，提取 `session_id`

**Task 1.3** — `status` 子命令输出展示 `fail_reason` 和 `session_id`（含 --resume 提示）

**Task 2.1** — `Task` 加 `max_budget_usd`、`fallback_model` 字段（含 save/load）✅（合并进 Task 1.1 一起做了）

**Task 2.2** — `start_task()` 签名加两个新参数，构建 claude cmd 时条件追加 `--max-budget-usd` / `--fallback-model`

**Task 2.3** — `rapper` bash 脚本 `do_background()` 解析 `--budget <usd>` 和 `--fallback <model>`；`task_runner.py run` 子命令 argparse 加这两个参数并透传

**Task 1.4 / 2.4 / 3.1 / 3.2** — `claude-background-tasks` skill 文档更新（session_id 续接、fail_reason 区分、--budget/--fallback 用法、--no-session-persistence、--debug-file）

---

## 验证步骤

每步完成后：
1. `cd /app/rapper && uv run python tests/test_mcp_simple.py` — 确保 MCP 未被破坏
2. `uv run python lib/task_runner.py list` — 确保 CLI 正常
3. 对于步骤1和2，用一个短任务实测：`rapper --background test-plan -p "echo hello world"`，然后 `rapper --status <id>` 查看新字段
