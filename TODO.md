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

#### [TODO-003] Git Worktree 并行开发模式

> **背景**：Claude Code 原生支持 `claude -w <name>` 在隔离的 git worktree 中工作，每个 worktree 是一个独立 branch。多个 Rapper 可以各自在不同 worktree/branch 上并行开发，最后统一 merge 回主干。Rapper 目前完全未封装此能力。

**期望用法：**

```bash
# 在 worktree 模式启动后台任务（自动创建 branch + worktree）
rapper --background feature-auth --worktree -p "实现 JWT 认证模块" -w /app/myproject

# 多个 Rapper 并行，各自在独立 branch
rapper --background feat-auth   --worktree -p "实现认证" -w /app/myproject
rapper --background feat-search --worktree -p "实现搜索" -w /app/myproject
rapper --background feat-cache  --worktree -p "实现缓存" -w /app/myproject

# 查看各任务的 branch/worktree 信息
rapper --status <task-id>   # 输出中显示 branch 和 worktree 路径

# 任务完成后统一 merge（由 Hermes 或用户手动触发）
rapper --merge <task-id>    # 将该任务的 branch merge 回主干并清理 worktree
```

**需要修改的地方：**

- `rapper`（主脚本）：`do_background()` 加 `--worktree` flag，启动前调用 `git worktree add .claude/worktrees/<name> -b rapper/<name>` 创建隔离环境，把 worktree 路径作为 workdir 传给 claude。
- `lib/task_runner.py`：`Task` dataclass 加 `worktree_path: str | None` 和 `branch_name: str | None` 字段，save/load 同步。
- `rapper`（主脚本）：新增 `--merge <task-id>` 子命令，读取任务的 branch_name，执行 `git merge`，成功后 `git worktree remove` 清理。
- `lib/task_runner.py`：status 输出中展示 worktree 路径和 branch 名。
- **与 Claude Code 原生 `-w` 的关系**：Claude Code 的 `claude -w <name>` 会自动在 `.claude/worktrees/<name>` 创建 worktree；Rapper 自己管理 worktree 生命周期，这样 `rapper --merge` 才能有完整元数据。

**估计工作量**：中等

- [ ] 任务完成后自动回报结构化结果（JSON）给 Hermes
  - 当前：Rapper 返回纯文本，Hermes 自己解析
  - 目标：标准化 `{ status, output_path, pr_url, errors }` 格式

- [ ] 多 Rapper 并发时的资源限制
  - 防止同时跑 5 个 Rapper 把 GPU/内存打满
  - 在 Hermes 侧做信号量控制，或在 Rapper 侧做锁

---

### 🟢 低优先级

#### [TODO-004] Claude Code 版本管理与自动更新

> **发现时间**：2026-04-29
> **背景**：Rapper 包装的是 Claude Code，但对其版本完全不感知。Claude Code 目前安装为 ELF 二进制（非 npm 包），路径 `~/.local/share/claude/versions/<version>/`，`~/.local/bin/claude` 是 symlink。更新方式是 `claude update`（Claude Code 自带自更新机制），而不是 `npm update`。

**问题：**

1. Rapper 无法感知当前 Claude Code 版本，无法判断是否需要更新
2. 没有 `rapper --update-claude` 命令，无法从 Rapper 层面触发更新
3. 无法在启动时检查版本并给出警告（"当前 Claude Code 已落后 N 个版本"）
4. 无法在任务 status JSON 中记录执行时使用的 Claude Code 版本，不利于问题排查

**期望功能：**

```bash
# 查看当前 Claude Code 版本
rapper --claude-version

# 检查是否有新版本可用（不更新）
rapper --check-update

# 触发 Claude Code 自更新
rapper --update-claude
# 内部执行：claude update
# 更新成功后输出：Claude Code updated: 2.1.114 → 2.x.xxx
```

**需要修改的地方：**

- `rapper`（主脚本）：新增 `--claude-version` / `--check-update` / `--update-claude` 三个子命令
  - `--claude-version`：读取 `~/.local/bin/claude` symlink target，从路径提取版本号；或直接调用 `claude --version`
  - `--check-update`：调用 `claude update --dry-run`（若支持），或查询 Claude Code 的 release API
  - `--update-claude`：调用 `claude update`，捕获输出，记录更新前后版本到 `~/.rapper/update_log.txt`
- `lib/task_runner.py`：status JSON 中补充 `"claude_version": "2.1.114"` 字段（每次任务启动时采集）
- **可选**：启动时自动检查版本（如落后超过 N 个 minor，打印警告）

**估计工作量**：小，主要是加 3 个 CLI 参数和一个版本采集函数

---

- [ ] Rapper 执行日志结构化（写入 Agent Board audit trail）
- [ ] 支持 Rapper 在任务执行中途向 Hermes 发送中间状态更新
