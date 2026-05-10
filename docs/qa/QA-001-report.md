# QA-001 回归测试报告（最终版）

**执行时间**: 2026-05-11  
**执行者**: hermes-pm  
**Rapper commit**: cc7018f（含今日全部修复）  
**Claude Code 版本**: 2.1.123  
**Agent Board**: systemd --user service，port 3456  

---

## 前置修复（本次 QA 基于此版本）

| Bug | Commit |
|---|---|
| Agent Board MCP 并发写 | HTTP single-writer 架构 |
| rapper-daemons.service Type=forking | c9216c6 |
| daemon 防重入 | 807070c |
| daemon poll 卡死 | fcd58c4 |
| rapper --background --workdir 失效 | 1988bb9 |
| worktree 未自动 commit | 279581e |
| GET /tasks?column= filter 失效 | routes.ts fix |
| daemon 客户端未过滤非 todo 任务 | daemon.py fix |
| 无中途进度 comment（BUG-P13） | 6274ec3 + cc7018f |
| 无终态 comment（BUG-P14） | 6274ec3 |

---

## P0 — 基础设施 ✅ 4/4

| ID | 测试项 | 结果 | 备注 |
|---|---|---|---|
| P0-1 | Agent Board API 可用 | ✅ PASS | HTTP 200 |
| P0-2 | rapper-daemons.service 持久运行 | ✅ PASS | active (running) + supervisor loop |
| P0-3 | rapper-1/2/3 进程存活 | ✅ PASS | 3 进程在 systemd CGroup 下 |
| P0-4 | rapper-1/2/3 心跳注册到 Board | ✅ PASS | 4 agents online |

---

## P1 — 核心工作流 ✅ 7/7

| ID | 测试项 | 结果 | 备注 |
|---|---|---|---|
| P1-1 | Daemon 自动认领任务 | ✅ PASS | 10s 内 todo → doing |
| P1-2 | 任务执行完成 | ✅ PASS | column=done，产出文件存在 |
| P1-3 | Board 进度 comment 上报 | ✅ PASS | "+45s: ⏳ 执行中：已完成 6 步" |
| P1-4 | 任务完成 comment | ✅ PASS | "+55s: ✅ 任务完成 耗时：43s 步数：9" |
| P1-5 | rapper --status 返回正确结果 | ✅ PASS | status=completed, board_task_id 绑定 |
| P1-6 | Worktree 并行隔离 | ✅ PASS | 两个 branch 各自独立 |
| P1-7 | --merge 正确合并 | ✅ PASS | 文件出现在主 repo，git log 有新 commit |

---

## P2 — 辅助功能 ✅ 5/6（1 跳过）

| ID | 测试项 | 结果 | 备注 |
|---|---|---|---|
| P2-1 | --task-count-json 正确 | ✅ PASS | at_capacity=false, available_slots=4 |
| P2-2 | 并发限制生效 | ⏭ SKIP | 需 5 个 running 任务，跳过避免副作用 |
| P2-3 | --task-count 正确 | ✅ PASS | 返回整数 |
| P2-4 | Claude Code 版本感知 | ✅ PASS | 2.1.123 |
| P2-5 | 版本更新检查 | ✅ PASS | exit 0 |
| P2-6 | Board task_id 绑定 | ✅ PASS | board_task_id 正确绑定 |
| P2-7 | outbound_guard 白名单 | ✅ PASS | localhost:3456 在白名单 |

---

## 总结

| 级别 | 通过 | 失败 | 跳过 |
|---|---|---|---|
| P0 基础设施 | **4/4** | 0 | 0 |
| P1 核心工作流 | **7/7** | 0 | 0 |
| P2 辅助功能 | **5/6** | 0 | 1 |
| **合计** | **16/17** | **0** | **1** |

**结论：所有核心功能验证通过。基础设施健康，可继续推进新 feature。**
