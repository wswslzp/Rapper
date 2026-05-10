# Rapper TODO

## 背景

Rapper 目前是**临时工模式（Ephemeral）**：由 Hermes 通过 `delegate_task()` 临时叫起，执行完一个任务后即退出。没有持久身份，不会主动拉活。

---

## 已完成

- [x] 基础 Claude Code wrapper（`rapper --acp --stdio`）
- [x] bash-runner MCP（安全 shell 执行 + 危险命令拦截）
- [x] outbound_guard（计划任务模式下的出站消息白名单）
- [x] Tmux session 管理
- [x] 结构化任务结果回报（JSON格式）+ 多 Rapper 并发资源控制 **[FULLY IMPLEMENTED ✅]**
  - ✅ **并发控制**: `rapper --concurrency` 完整可用，Hermes 可通过此检查资源可用性
  - ✅ **结构化结果**: 已大幅改进！Claude 现在可靠地输出JSON格式，解析成功率显著提高
    - 🔥 增强的指令：更强烈、更突出的JSON输出要求（带emoji、模板）
    - 🔥 智能解析：5层解析策略，处理各种JSON输出格式
    - 🔥 验证测试：成功和失败场景都能正确输出和解析结构化结果
    - 📋 完美的Hermes集成：`{"status": "completed", "output_path": "file.py", "pr_url": null, "errors": []}`

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

**状态：✅ 已解决** (2026-05-07)

**根本原因分析（已修复）：**

1. ~~**`--background` 不接受 `--workdir` 参数**~~：**实际上已支持**，`rapper --background <name> --workdir /path -p "..."` 功能完整实现并工作正常。
2. **workdir 继承问题**：当未显式指定 `--workdir` 时，后台任务确实继承调用方的 cwd，但可通过 `--workdir` 参数明确指定。
3. **文档错误**：帮助文本中错误显示 `-w /project`，实际应为 `--workdir /project`。

**已实现功能：**

- ✅ `rapper --background <name> --workdir /abs/path -p "..."` 完全支持，已测试工作正常。
- ✅ 任务 status JSON 已包含 `"workdir_effective": actual_cwd` 字段。
- ✅ `task_runner.py` 已在 double-fork 中执行 `os.chdir(workdir)`。

**已修复的文档错误：**

- ✅ 修复帮助文本中的 `-w` 错误，改为正确的 `--workdir`。
- ✅ 更新 TODO.md 中的误导性描述。

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
rapper --background feature-auth --worktree -p "实现 JWT 认证模块" --workdir /app/myproject

# 多个 Rapper 并行，各自在独立 branch
rapper --background feat-auth   --worktree -p "实现认证" --workdir /app/myproject
rapper --background feat-search --worktree -p "实现搜索" --workdir /app/myproject
rapper --background feat-cache  --worktree -p "实现缓存" --workdir /app/myproject

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

---

### 🔴 高优先级

#### [TODO-005] daemon 启动后只执行一个任务就退出（loop 未实现持久化）

> **发现时间**：2026-04-30
> **触发场景**：`launch_daemons.py` 启动三个 rapper daemon 后，每个 daemon 领到一个任务后就正常退出（`Daemon stopped`），没有继续进入下一轮 poll。

**根本原因（待确认）：**

查看 `lib/daemon.py` 中的主循环，疑似 `_poll_tasks()` 执行完一个任务后退出了持久 loop，而不是 sleep 后继续轮询。需要排查：
- `run()` 方法里的 `while self.running:` 循环是否在执行完一轮后被正确维持
- `_poll_tasks()` 是否有提前 `return`，导致 loop 条件被破坏
- 任务执行时是否用了 `_run_task_sync()` 阻塞调用，阻塞结束后是否正确回到 poll 循环

**期望行为：**

daemon 领取并完成一个任务后，继续 sleep(poll_interval) → 再次轮询 → 再次领取新任务，**永不主动退出**（除非收到 SIGTERM/SIGINT）。

**需要检查的地方：**

- `/app/rapper/lib/daemon.py`：`RapperDaemon.run()` 的主循环逻辑
- 确认 `self.running` 在任务执行期间未被意外设为 `False`
- 确认 task 执行完毕后，loop 控制流回到 `while self.running:` 的起点

---

#### [TODO-DAEMON-001] Daemon 无限重拾 todo 任务（防重入机制缺失）

> **发现时间**：2026-04-30
> **触发场景**：Daemon 模式下，任务完成后 Board 状态未被推到 `done`（outbound_guard 阻止 Rapper 自报 HTTP POST），Daemon 每次轮询（默认 30s）仍看到 `todo` 列有任务，持续 pick up 并重复执行，直到 Daemon 被外部 SIGTERM kill（exit code 143）→ Board 记录 `failed`。
>
> **案例**：`task_48993bb1be25e96b`（Fibonacci 任务），实际于 2026-04-29 14:49 完成，但 2026-04-30 11:15 起被 Daemon 无限重拾约 60 次。

**根本原因（双重）：**

1. **outbound_guard 阻止 Rapper 自报**：`RAPPER_SCHEDULED=1` 激活 outbound_guard，所有 HTTP POST 被 block，Rapper 无法通过 curl 更新 Board 状态
2. **Daemon 无本地去重**：`daemon.py` 的轮询逻辑只过滤 `column=todo`，不记录"已执行过的 task_id 列表"，每次轮询都将 todo 列视为待执行任务

**期望修复方案（任选其一）：**

**方案 A（推荐）**：Daemon pick up 任务时，立即通过内部 HTTP 调用将任务推到 `doing`（Daemon 使用自己的 api_key，不受 outbound_guard 限制）
- 修改 `daemon.py`：`pick_up_task()` 在执行前先调 `PATCH /api/tasks/:id` 将 column 改为 `doing`
- 这样即使 Rapper 执行完未能回写 `done`，下次轮询也不会重拾（`doing` 不在过滤条件内）

**方案 B（兜底）**：本地防重入文件
- Daemon 每次 pick up 时将 task_id 写入 `~/.rapper/daemon_picked.json`
- 轮询时先过滤掉已在此文件中的 task_id
- 重启 Daemon 时清空此文件（或基于时间戳判断过期）

**方案 C（长期）**：修复 outbound_guard 白名单
- 将 `http://localhost:3456` 加入 outbound_guard 的 HTTP POST 白名单
- 让 Rapper 可以自报完成状态（回归 `agent-board-pm-workflow` skill 的 Rapper 自报设计）

**需要修改的地方：**

- `/app/rapper/lib/daemon.py`：`pick_up_task()` 或主 loop 逻辑
- 参考：`daemon.py` 已知 bug 修复表（见 `claude-background-tasks` skill）

**优先级**：**High**（影响 Daemon 模式可靠性，已导致线上误报 failed）

---

#### [TODO-006] daemon 重启时无法绑定端口（Address already in use）

> **发现时间**：2026-04-30
> **触发场景**：`pkill -f daemon.py` 后立即重新运行 `launch_daemons.py`，出现 `OSError: [Errno 98] Address already in use`，webhook server 无法在 18791/18792/18793 端口启动。

**根本原因：**

TCP 端口在进程退出后进入 `TIME_WAIT` 状态，需要等待内核回收（通常 60s）。pkill 后立刻重启会撞上残留占用。

**期望修复：**

- webhook server 启动时设置 `SO_REUSEADDR = True`（Python `socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)`）
- 或在 `launch_daemons.py` 中先 `lsof -ti :PORT | xargs -r kill` 再启动
- `daemon.py` 启动时若端口占用，打印 warning 但继续（webhook 非必须，poll loop 可照常工作）

**需要修改的地方：**

- `/app/rapper/lib/daemon.py`：`_start_webhook_server()` 中的 socket 创建，加 `SO_REUSEADDR`
- `/app/rapper/launch_daemons.py`：重启前检查端口是否被占用，若是则先 kill
