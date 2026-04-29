# Rapper TODO

## 背景

Rapper 目前是**临时工模式（Ephemeral）**：由 Hermes 通过 `delegate_task()` 临时叫起，执行完一个任务后即退出。没有持久身份，不会主动拉活。

---

## 已完成

- [x] 基础 Claude Code wrapper（`rapper --acp --stdio`）
- [x] bash-runner MCP（安全 shell 执行 + 危险命令拦截）
- [x] outbound_guard（计划任务模式下的出站消息白名单）
- [x] Tmux session 管理

---

## 待办

### 🔴 高优先级

#### [DAEMON] 持久化运行模式（Persistent Rapper）

> **背景**：为了接入 Agent Board 等任务看板，需要 Rapper 支持"正式员工"模式——持久运行、主动轮询任务、心跳保活。
>
> **两条路线的选择**（详见 `/app/agent-board/DEPLOY-PLAN.md`）：
>
> - **路线 A（当前采用）**：临时工模式。Hermes 每次 `delegate_task()` 叫起 Rapper，通过 prompt 传入 Agent Board 的 API Key 和 task_id，Rapper 完成后 curl 更新状态，退出。**无需改 Rapper，现在就能用。**
>
> - **路线 B（本 TODO）**：正式员工模式。Rapper 持久运行，定期 heartbeat 轮询 Agent Board，有任务就执行，完成后更新状态，再等下一个。需改造 Rapper。

**改造内容：**

- [ ] 新增 `rapper --daemon` 启动模式
  - 启动时向 Agent Board 注册身份（POST `/api/agents`）
  - 进入持久事件循环（不退出）

- [ ] 心跳轮询逻辑（Heartbeat）
  ```
  loop:
      tasks = GET /api/tasks?assignee={my_agent_id}&column=todo
      if tasks:
          取第一个 task
          PATCH task → column=doing
          执行任务（调用 Claude Code / Hermes delegate）
          PATCH task → column=done 或 failed
      else:
          sleep(30s)
  ```

- [ ] Webhook 唤醒支持
  - 监听本地端口（如 `18789`），接收 Agent Board 的 `task.assign` / `comment.add` 事件
  - 收到 webhook 立刻跳过 sleep，进入执行循环

- [ ] 优雅退出
  - 捕获 SIGTERM/SIGINT
  - 正在执行中的任务标记为 `failed`（带注释说明是 shutdown 导致）
  - 从 Agent Board 注销身份

- [ ] 配置扩展（`~/.rapper/config.yaml`）
  ```yaml
  agent_board:
    url: http://localhost:3456
    api_key: sk-rapper1
    agent_id: rapper-1
    poll_interval: 30  # 秒
    webhook_port: 18789
  ```

- [ ] systemd 服务文件
  - 路径：`/etc/systemd/system/rapper@.service`（模板，支持多实例 rapper@1, rapper@2...）

**估计工作量**：中等，主要是加一个 `--daemon` 入口 + 轮询循环 + webhook 监听器

---

---

### 🔴 高优先级

#### [TODO-002] `--background` 任务缺少上下文隔离，导致 workdir 污染

> **发现时间**：2026-04-29
> **触发场景**：用 `rapper --background` 启动多轮 wiki 构建任务时，monitor cron 自动续启的 r2 任务 workdir 全部错误（落在 `/app/rapper`、`/app/agent-board` 等），而不是目标 vault 目录 `/data/zhihu/articles/dabinge`，导致大多数轮次找不到文件、白跑无效。

**根本原因：**

1. **`--background` 不接受 `--workdir` 参数**：`rapper --background <name> -p "..."` 目前只接受 `-p` 和 `-w`（`-w` 是 git worktree，不是 workdir），无法在命令行层面绑定工作目录。
2. **workdir 继承自调用方**：后台任务的 workdir 取决于谁调用了 rapper，而 cron job 的调用方是 Hermes scheduler，其 cwd 与目标项目无关。
3. **Claude Code 内部的 `cd` 与 prompt 上下文割裂**：即使 prompt 里写了路径，Claude Code 启动时仍从 scheduler cwd 出发，若 prompt 没有足够明确地反复强调绝对路径，子任务很容易漂移。

**期望行为：**

- `rapper --background <name> --workdir /abs/path -p "..."` 能绑定 workdir，启动的 Claude Code 进程以该目录为 cwd。
- 任务 status JSON（`~/.rapper/tasks/<id>.json`）中记录实际使用的 workdir，方便调试。

**需要修改的地方：**

- `rapper`（主脚本）：`do_background()` 函数解析新增的 `--workdir` 参数，传给 `_run_task_sync()`。
- `lib/task_runner.py`：`_run_task_sync()` / `start_background_task()` 接受 `workdir` 参数，`os.chdir(workdir)` 在 double-fork 的 grandchild 中执行（目前只 hardcode 了 `os.chdir(workdir)` 但 workdir 来源不明确，需确认）。
- `lib/task_runner.py`：status JSON 写入时补充 `"workdir_effective": actual_cwd` 字段。
- **文档**：在 SKILL.md（`claude-background-tasks`）和 README 中补充 `--workdir` 用法示例。

**临时规避方案（已验证可用）：**

在 prompt 内开头加一行强制跳转：
```
首先执行：cd /data/zhihu/articles/dabinge
然后再开始任何文件操作。
```
但这依赖 Claude Code 忠实执行，不如 workdir 绑定可靠。

---

### 🟡 中优先级

- [ ] 任务完成后自动回报结构化结果（JSON）给 Hermes
  - 当前：Rapper 返回纯文本，Hermes 自己解析
  - 目标：标准化 `{ status, output_path, pr_url, errors }` 格式

- [ ] 多 Rapper 并发时的资源限制
  - 防止同时跑 5 个 Rapper 把 GPU/内存打满
  - 在 Hermes 侧做信号量控制，或在 Rapper 侧做锁

---

### 🟢 低优先级

- [ ] Rapper 执行日志结构化（写入 Agent Board audit trail）
- [ ] 支持 Rapper 在任务执行中途向 Hermes 发送中间状态更新
