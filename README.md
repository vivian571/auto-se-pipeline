# 📖 Auto-SE Pipeline (v1.1)

> **Autonomous Software Engineering Pipeline**: 从 GitHub Issue 智能合规筛选、沙箱精准复现、测试驱动补丁生成，到全量回归验证与透明合规人工终审的端到端自动化开源工程流水线。

---

## 🛠️ 当前已实现模块 (Sprint 1 ~ 3 Milestones)

1. **统一数据模型与状态机持久化 (`storage/`)**
   - SQLite 3 (WAL 模式) + 外键约束。
   - 包含人工审核机制的规则演进记忆库 `rejection_rules`。
   - 任务级 Token 消耗与成本计量 `execution_logs`。
2. **多语言上下文全景分析引擎 (`core/context_engine.py`)**
   - 基于 `tree-sitter` 的跨语言 AST 骨架剪枝（支持 Python 与 TypeScript/JavaScript）。
   - 保留类型签名与首行 Docstring，剥离函数体实现与细节噪声。
   - Traceback 堆栈精准提取（5.0 权重召回）与异常签名提取。
   - 严格受限的 Token 预算裁切（控制在 3000 Token 内，实测延迟 < 30ms）。
3. **两阶段隔离沙箱运行时 (`core/sandbox.py`)**
   - 环境变量与凭据深度脱敏（阻断所有 Token 与云凭证访问）。
   - 全量回归基线比对（Baseline Diffing）：允许已知已有失败，严格杜绝任何新增回归失败（$C_{fail} \subseteq B_{fail}$）。
   - 复现用例 3 连挂校验（Flaky 检测）。
   - 自适应 Unified Diff 补丁容错器与 `git reset --hard HEAD` 瞬时硬回滚。
4. **双 Agent 对抗协作体 (`core/debugger_agent.py`, `core/coder_agent.py`, `core/swarm_orchestrator.py`)**
   - Debugger 红方编写最小复现测试，校验堆栈与异常匹配度。
   - Coder 蓝方基于白名单路径生成 Unified Diff 补丁（严禁触碰 CI 与清单配置）。
   - 最多 3 次反馈对抗修复闭环，超时与预算超限立即熔断。
5. **开源合规准入与政策嗅探器 (`core/evaluator.py`)**
   - 深度扫描 `CONTRIBUTING.md`, `AI_POLICY.md` 是否禁止 AI 提交。
   - 冲突检测：避免抢占已被 Assign 或社区贡献者已认领的 Issue。
   - 质量门槛：过滤缺少明确 Traceback 或复现指引的模糊 Issue。
6. **人工终审终端控制台与透明 GitHub 网关 (`core/approval_ui.py`, `core/github_gateway.py`)**
   - 终审看板：展示 Diff、复现日志、回归对比与费用账单。
   - 强制合规披露声明：`Generated with AI assistance; strictly validated in an isolated sandbox and manually reviewed before submission.`
   - 严格阻断未经验证的自动推送，必须经人工 `Approve` 确认后方可向 GitHub 创建 PR。

---

## 🚀 快速上手与 CLI 命令

```bash
# 1. 激活虚拟环境
source .venv/bin/activate

# 2. 运行完整单元测试套件 (19 项全绿)
pytest -v

# 3. 运行 Sprint 1 离线基准性能测试
python eval/benchmark_runner.py

# 4. 上下文切片提取 CLI
python main.py analyze-context \
  --repo-path /path/to/repo \
  --issue-title "Issue Title" \
  --issue-body "Traceback info..."

# 5. 端到端自动化流水线 (包含合规评估、双 Agent 修复与人工终审)
python main.py run \
  --repo-path /path/to/repo \
  --issue-number 100 \
  --issue-title "Unhandled KeyError in serializer" \
  --issue-body "Traceback (most recent call last): ..." \
  --expected-exception "KeyError"
```

---

## 🗺️ 架构路线图

- [x] **Sprint 1 (第 1-3 天)**：上下文全景引擎 (Context Engine)、状态机持久化与评测基座。
- [x] **Sprint 2 (第 4-7 天)**：两阶段隔离沙箱、双 Agent (Debugger vs Coder) 对抗闭环与基线比对。
- [x] **Sprint 3 (第 8-10 天)**：合规政策嗅探 (AI Policy)、人工终审 (HITL) 与透明 GitHub 提交网关。
- [ ] **Sprint 4 (第 11-14 天)**：置信度演进记忆库、频控熔断与任务调度器。
