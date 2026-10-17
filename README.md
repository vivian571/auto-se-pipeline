# 📖 Auto-SE Pipeline (v1.1)

> **Autonomous Software Engineering Pipeline**: 从 GitHub Issue 智能合规筛选、沙箱精准复现、测试驱动补丁生成，到全量回归验证、闭环演进记忆库与透明合规人工终审的端到端自动化开源工程流水线。

---

## 🏗️ 系统全景架构图

```text
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                      SaaS & Webhook / CLI 调度层                             │
 │         GitHub Webhook / 频率与熔断管控器 (RateLimitCircuitBreaker)          │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │ 1. 抓取候选 Issue
 ┌──────────────────────────────────────▼──────────────────────────────────────┐
 │             合规准入与 Issue 评估引擎 (Compliance & Issue Evaluator)         │
 │   - CONTRIBUTING / AI_POLICY 政策嗅探   - 仓库黑白名单与已存在 PR 冲突检测   │
 │   - Traceback / 复现指引完整性校验      - 无人认领 / 无正在进行的 PR 校验   │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │ 2. 准入通过 (EVALUATED_QUALIFIED)
 ┌──────────────────────────────────────▼──────────────────────────────────────┐
 │                      上下文全景引擎 (Context Engine)                         │
 │   - 多语言 Tree-sitter (Python/TS) AST 骨架修剪 (仅签名+首行 docstring)    │
 │   - Traceback 路径提取 + 关键词加权打分 + Import 依赖闭包分析              │
 │   - 原生测试用例与 Mock 范式抽取                                           │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │ 3. 注入结构化上下文 (ContextSlice)
 ┌──────────────────────────────────────▼──────────────────────────────────────┐
 │                    双 Agent 对抗协作体 (Dual-Agent Swarm)                   │
 │   ┌───────────────────────────┐             ┌───────────────────────────┐   │
 │   │   Debugger Agent (红方)   │ ◄─────────► │    Coder Agent (蓝方)     │   │
 │   │ - 编写最小复现用例         │   反思反馈流 │ - 生成 Unified Diff 补丁  │   │
 │   │ - 校验堆栈/异常匹配度      │             │ - 严格遵守路径与安全白名单│   │
 │   └─────────────┬─────────────┘             └─────────────▲─────────────┘   │
 └─────────────────┼─────────────────────────────────────────┼─────────────────┘
                   │ 4. 交付复现测试                          │ 5. 修复重试 (≤3次)
 ┌─────────────────▼─────────────────────────────────────────┴─────────────────┐
 │               两阶段隔离沙箱运行时 (Two-Stage Sandbox Runtime)               │
 │   Stage 1: 受限联网阶段 (包管理器官方镜像源白名单)，预构建依赖与工具链缓存  │
 │   Stage 2: 严格断网阶段 (network_mode=none)，跑基线、复现与全量回归测试     │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │ 6. 验证全绿 (PASS)
 ┌──────────────────────────────────────▼──────────────────────────────────────┐
 │                人工终审与合规透明提交网关 (HITL & Gateway)                   │
 │   - 生成 Human-Review 仪表盘/交互提示 (包含 Diff、复现测试、回归报告、成本) │
 │   - 人工确认 (Approve) ➔ 携带主动 AI 披露声明提交透明 PR                    │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │ 7. 社区反馈/Review 驱动沉淀
 ┌──────────────────────────────────────▼──────────────────────────────────────┐
 │                置信度自我演进引擎 (Evolution Engine)                         │
 │     Review 评论提取 ── 规则归因模型 ── 人工审核确认 ── 置信度动态注入 Prompt│
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 模块清单与功能特性

| 模块目录 | 核心职责与关键技术 |
| :--- | :--- |
| `storage/` | SQLite WAL 模式，统一 `TaskStatus` 状态机历史 (`task_state_history`)，仓库政策 (`repos`)，规则记忆 (`rejection_rules`) 与成本开销流水 (`execution_logs`) |
| `core/context_engine.py` | `tree-sitter` 多语言 (Python / TS) 骨架剪枝，堆栈 Traceback 路径提取，测试范式抽取，严格 3000 Token 上下文切片 |
| `core/sandbox.py` | 深度脱敏（剔除所有云凭据/Token），全量回归基线比对（$C_{fail} \subseteq B_{fail}$），Flaky 3 连挂校验，自适应补丁容错与 `git reset --hard` 硬回滚 |
| `core/debugger_agent.py` | 红方 Agent：编写最小复现测试 `reproduce_issue.py`，校验堆栈异常对齐度，防注入防线 |
| `core/coder_agent.py` | 蓝方 Agent：生成 Unified Diff 补丁，白名单路径防线（禁止篡改 CI 与依赖清单），注入被拒记忆规则 |
| `core/swarm_orchestrator.py` | 对抗协同闭环：最多 3 次重试修复，回归全绿后将复现测试自动重构转正为 `test_issue_{number}.py` |
| `core/evaluator.py` | 扫描 `CONTRIBUTING.md` / `AI_POLICY.md` 是否禁止 AI；检测 Issue 是否已被认领；过滤模糊 Issue |
| `core/approval_ui.py` | 人工终审看板 (Human-in-the-Loop)：展示 Diff、测试结果与开销账单，人工确认后才准许提交 |
| `core/github_gateway.py` | 透明 PR 提交网关，内置强制 AI 披露规范检查与 DCO 签名 |
| `core/evolution.py` | 闭环演进记忆库：从 Maintainer 评论中提炼规则，通过人工审核门禁后赋予置信度注入 Prompt |
| `core/scheduler.py` | 频控熔断守护：单仓同时最多 1 个 PR、日限额控制、连续被拒 2 次自动拉黑 |

---

## 🚀 完整使用指南与 CLI 命令

### 1. 运行测试套件与基准测试
```bash
source .venv/bin/activate

# 运行全部 23 项单元与集成测试 (100% 绿灯)
pytest -v

# 运行离线 AST 骨架提取与上下文切片基准测试 (<30ms, <3000 tokens)
python eval/benchmark_runner.py
```

### 2. 端到端自主运行流水线 (`pipeline run`)
```bash
# 启动完整流水线（自动经历合规嗅探 ➔ AST 切片 ➔ 双 Agent 沙箱对抗 ➔ 人工终审看板 ➔ 透明 PR 提交）
python main.py run \
  --repo-path /path/to/target_repo \
  --issue-number 100 \
  --issue-title "safe_divide crashes on zero divisor" \
  --issue-body "Traceback (most recent call last): ..." \
  --expected-exception "ZeroDivisionError"
```

### 3. 查看系统度量与开销报表 (`metrics`)
```bash
python main.py metrics
```

### 4. 演进规则人工审核与激活 (`rules`)
```bash
# 查看待审核的规则候选
python main.py rules list-pending

# 人工审核并激活规则（设置置信度为 5 分）
python main.py rules approve --rule-id 1 --confidence 5
```

---

## 🗺️ 架构演进全量达成 (Sprint 1 ~ 4)

- [x] **Sprint 1**：上下文全景引擎 (Context Engine)、状态机持久化与评测基座。
- [x] **Sprint 2**：两阶段隔离沙箱、双 Agent (Debugger vs Coder) 对抗闭环与基线比对。
- [x] **Sprint 3**：合规政策嗅探 (AI Policy)、人工终审 (HITL) 与透明 GitHub 提交网关。
- [x] **Sprint 4**：置信度演进记忆库、频控熔断调度器、度量报表与全链路收官。
