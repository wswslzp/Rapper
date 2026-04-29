# Rapper - Claude Code Wrapper 设计文档

**Date:** 2025-04-23
**Status:** Draft
**Author:** Hermes + Roc.Liao

---

## 1. 概述

Rapper 🎤 是一个 Claude Code 的安全包装器，解决以下问题：
1. Claude Code 原生不支持 auto-run → 通过 Bash MCP 实现自动执行
2. 需要安全防护 → Outbound Guard 阻止发送到非白名单目标
3. 需要与 Hermes 协作 → 作为 `delegate_task` 的执行后端

**核心参考：** NVIDIA Pixel (`/data/pixel/1.28/`)

---

## 2. 使用模式

### 2.1 短任务模式 (Prompt Mode)
类似 `claude -p`，即用即扔：

```bash
# 被 Hermes 调用
delegate_task(
    goal="实现 feature X",
    acp_command="rapper",
    acp_args=["--acp", "--stdio"]
)

# 或命令行直接使用
rapper -p "帮我写一个 Python hello world"
```

### 2.2 长任务模式 (Tmux Session Mode)
通过 Tmux 启动持久会话，适合长时间协作开发：

```bash
# 启动持久会话
rapper --tmux [session-name]

# Hermes 连接到已有会话
rapper --attach <session-name>

# 列出活跃会话
rapper --list
```

**典型工作流：**
1. Hermes 启动一个 Rapper Tmux 会话用于某个项目
2. Hermes 通过 `delegate_task` 发送任务到该会话
3. Rapper 持续工作，保持上下文
4. Hermes 可以随时检查进度、发送新指令

---

## 3. 架构

```
┌─────────────────────────────────────────────────────────────┐
│                      Hermes (协调层)                         │
│               Discord / Telegram / CLI                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ delegate_task / ACP / tmux attach
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      Rapper (执行层)                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   rapper    │  │   Claude    │  │    MCP Servers      │  │
│  │  (入口脚本) │──▶│    Code     │◀─▶│  - bash-runner     │  │
│  └─────────────┘  └─────────────┘  │  - (future: more)   │  │
│                          ▲          └─────────────────────┘  │
│                          │                                   │
│  ┌───────────────────────┴───────────────────────────────┐  │
│  │                    Safety Hooks                        │  │
│  │  - PreToolUse: outbound_guard.py                      │  │
│  │  - Bash Guard: 危险命令拦截 (in bash-runner)          │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 目录结构

```
/app/rapper/
├── rapper                      # 主入口脚本 (Bash)
├── bin/
│   └── rapper                  # $PATH 软链接
├── config/
│   ├── default-settings.json   # Claude Code settings 模板
│   ├── outbound_guard.py       # PreToolUse 安全钩子
│   └── usage_tracker.py        # 使用量追踪 (Phase 2)
├── mcp-servers/
│   └── bash-runner/
│       ├── server.py           # FastMCP 实现
│       └── requirements.txt
├── docs/
│   └── superpowers/
│       ├── specs/              # 设计文档
│       └── plans/              # 实现计划
├── requirements.txt            # Python 依赖
├── CLAUDE.md                   # Rapper 项目说明 (给 Claude Code 看)
└── README.md

~/.rapper/                      # 用户配置目录
├── config.yaml                 # 用户配置
├── logs/                       # 日志目录
│   └── usage-YYYY-MM-DD.jsonl  # 使用量日志
└── sessions/                   # Tmux 会话状态 (可选)
```

---

## 5. 核心组件设计

### 5.1 入口脚本 (`rapper`)

**功能：**
- 首次运行自动设置 (创建 ~/.rapper/, 安装依赖, 注册 MCP)
- 启动 Claude Code (短任务或 Tmux 模式)
- 管理 Tmux 会话

**命令接口：**
```bash
rapper                          # 启动交互式 Claude Code
rapper -p "prompt"              # 短任务模式 (claude -p)
rapper -c                       # 继续上次对话
rapper --acp --stdio            # ACP 模式 (被 Hermes 调用)

rapper --tmux [name]            # 启动 Tmux 持久会话
rapper --attach <name>          # 连接到已有会话
rapper --list                   # 列出活跃会话
rapper --kill <name>            # 终止会话

rapper --setup                  # 强制重新设置
rapper --check                  # 检查状态
```

### 5.2 Bash Runner MCP (`mcp-servers/bash-runner/server.py`)

**参考：** `/data/pixel/1.28/mcp-servers/bash-runner/bash_mcp.py`

**核心功能：**
1. **安全命令执行** - 阻止危险命令 (rm -rf, sudo, fork bomb 等)
2. **后台任务支持** - `background=True`, `auto_background=N`
3. **多步骤工作流** - `steps=[...]` 链式执行

**工具定义：**
```python
@mcp.tool()
async def run_bash(
    command: str,
    timeout: int = 300,
    workdir: str = None,
    background: bool = False,
    auto_background: int = None,  # 秒数，超时自动转后台
    steps: list[str] = None,      # 多步骤链式执行
) -> dict:
    """安全执行 bash 命令"""
```

**阻止模式 (从 Pixel 移植)：**
```python
BLOCKED_PATTERNS = [
    r'\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*-rf\b|.*-fr\b)',  # rm -rf
    r'\bsudo\b',
    r'\bsu\s+root\b',
    r':\(\)\s*\{\s*:\|:\s*&\s*\}\s*;',  # fork bomb
    r'\bshutdown\b',
    r'\breboot\b',
    # ... 更多
]
```

### 5.3 Outbound Guard (`config/outbound_guard.py`)

**参考：** `/data/pixel/1.28/config/outbound_guard.py`

**功能：** PreToolUse 钩子，阻止发送到非白名单目标

**白名单来源：**
```yaml
# ~/.rapper/config.yaml
safety:
  outbound_whitelist:
    discord_channels:
      - "1495837744721563770"  # Hermes Home
    telegram_chats:
      - "-1001234567890"
    emails:
      - "user@example.com"
```

**拦截的工具：**
- `send_message` (Hermes)
- `mcp__bash-runner__run_bash` 中的 `curl POST`, `wget --post-data`
- 未来: Discord/Telegram 直接发送

### 5.4 配置文件 (`~/.rapper/config.yaml`)

```yaml
# Rapper 配置文件

# Claude Code 设置
claude:
  model: "claude-sonnet-4-20250514"  # 默认模型
  # model: "claude-opus-4-20250514"   # 复杂任务

# 安全设置
safety:
  # 是否启用 Outbound Guard
  outbound_guard_enabled: true
  
  # 白名单
  outbound_whitelist:
    discord_channels: []
    telegram_chats: []
    emails: []
  
  # Bash 额外阻止模式 (追加到默认列表)
  bash_extra_blocked: []

# Tmux 设置
tmux:
  default_session_name: "rapper"
  # 会话空闲超时 (小时)，0 = 不超时
  idle_timeout: 0

# 日志
logging:
  enabled: true
  level: "info"  # debug | info | warn | error
```

---

## 6. 实现计划

### Phase 1: 基础框架 (MVP)

| 任务 | 优先级 | 预估 |
|-----|-------|------|
| 1.1 创建项目骨架 | P0 | 10min |
| 1.2 实现 `rapper` 入口脚本 | P0 | 30min |
| 1.3 实现 `bash-runner` MCP | P0 | 1h |
| 1.4 配置加载和 ~/.rapper/ 初始化 | P0 | 20min |
| 1.5 Claude Code settings.json 自动配置 | P0 | 20min |
| 1.6 基础测试 | P0 | 30min |

**MVP 验收标准：**
- [ ] `rapper` 可以启动 Claude Code
- [ ] `rapper -p "ls"` 可以执行短任务
- [ ] `bash-runner` MCP 可以安全执行命令
- [ ] 危险命令被阻止
- [ ] Hermes 可以通过 `delegate_task(acp_command="rapper")` 调用

### Phase 2: 安全增强

| 任务 | 优先级 | 预估 |
|-----|-------|------|
| 2.1 实现 Outbound Guard | P1 | 1h |
| 2.2 使用量追踪 | P2 | 30min |
| 2.3 更完善的错误处理 | P1 | 30min |

### Phase 3: Tmux 长任务模式

| 任务 | 优先级 | 预估 |
|-----|-------|------|
| 3.1 Tmux 会话管理 | P1 | 1h |
| 3.2 会话状态持久化 | P2 | 30min |
| 3.3 Hermes 与 Tmux 会话交互 | P1 | 1h |

---

## 7. 与 Hermes 的集成

### 7.1 短任务模式

```python
# Hermes 调用 Rapper
result = delegate_task(
    goal="在 /app/rapper 项目中添加 README.md",
    acp_command="rapper",
    acp_args=["--acp", "--stdio"],
    context="项目是一个 Claude Code wrapper，参考 Pixel 实现"
)
```

### 7.2 长任务模式 (Tmux)

```python
# 1. Hermes 启动 Tmux 会话
terminal("rapper --tmux project-x --workdir /data/project-x")

# 2. Hermes 发送任务到会话 (通过 tmux send-keys 或专用接口)
terminal("tmux send-keys -t rapper-project-x 'implement feature Y' Enter")

# 3. Hermes 检查进度
terminal("tmux capture-pane -t rapper-project-x -p | tail -50")
```

---

## 8. 安全考量

1. **Bash 命令安全** - 阻止危险命令，参考 Pixel 的 BLOCKED_PATTERNS
2. **Outbound 安全** - 阻止发送到非白名单目标
3. **文件系统安全** - 保护系统目录 (/etc, /usr, /boot 等)
4. **网络安全** - 阻止 netcat listen, nmap 等

---

## 9. 未来扩展

- **更多 MCP Servers** - memory, web, office 等
- **Discord Bot 集成** - 类似 Pixel 的 Teams Bot
- **多 Rapper 实例管理** - 并行任务调度
- **资源限制** - CPU/内存/磁盘配额

---

## 10. 已确认问题

1. ✅ 配置目录: `~/.rapper/`
2. ✅ 两种工作模式: 短任务 + Tmux 长任务
3. ✅ Tmux 会话命名: `rapper-<project>` (带前缀)
4. ✅ Python 环境: 使用 `uv` 管理

---

**下一步：** 确认设计后，开始编写 Phase 1 实现计划。
