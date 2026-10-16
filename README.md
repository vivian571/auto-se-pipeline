# 📖 Auto-SE Pipeline (v1.1)

> **Autonomous Software Engineering Pipeline**: 从 GitHub Issue 智能合规筛选、沙箱精准复现、测试驱动补丁生成，到全量回归验证与透明合规人工终审的端到端自动化开源工程流水线。

---

## 🛠️ 当前已实现模块 (Sprint 1 Milestone)

1. **统一数据模型与状态机持久化 (`storage/`)**
   - SQLite 3 (WAL 模式) + 外键约束。
   - 全局一致的状态机枚举 `TaskStatus` 与全流程流转历史跟踪 `task_state_history`。
   - 包含人工审核机制的规则演进记忆库 `rejection_rules`。
   - 任务级 Token 消耗与成本计量 `execution_logs`。
2. **多语言上下文全景分析引擎 (`core/context_engine.py`)**
   - 基于 `tree-sitter` 的跨语言 AST 骨架剪枝（支持 Python 与 TypeScript/JavaScript）。
   - 保留类型签名与首行 Docstring，剥离函数体实现与噪声。
   - Traceback 堆栈精准提取（5.0 权重召回）与异常类型提取。
   - 依赖拓扑图单层展开与实体词频加权排序。
   - 测试用例与 Mock 范式自动抽取（pytest / vitest / jest）。
   - 严格受限的 Token 预算裁切（控制在 3000 Token 内）。
3. **离线基准评测基座 (`eval/benchmark_runner.py`)**
   - 验证目标：多模块代码库分析延迟 $< 2$ 秒，上下文切片 Token $< 3000$，目标缺陷文件 Top-1 召回率 $100\%$。
   - 压测实测指标：**28.12 ms** 延迟，**1035 tokens** 消耗。

---

## 🚀 快速上手

```bash
# 1. 激活虚拟环境
source .venv/bin/activate

# 2. 运行完整单元测试套件
pytest -v

# 3. 运行 Sprint 1 离线基准测试
python eval/benchmark_runner.py

# 4. 执行上下文分析 CLI
python main.py analyze-context \
  --repo-path /path/to/repo \
  --issue-title "Issue Title" \
  --issue-body "Traceback info..."
```

---

## 🗺️ 架构路线图

- [x] **Sprint 1 (第 1-3 天)**：上下文全景引擎 (Context Engine)、状态机持久化与评测基座。
- [ ] **Sprint 2 (第 4-7 天)**：两阶段 Docker 沙箱 (构建受限联网+断网执行) 与双 Agent (Debugger vs Coder) 闭环。
- [ ] **Sprint 3 (第 8-10 天)**：合规政策嗅探 (AI Policy)、人工终审 (HITL) 与私有测试仓演练。
- [ ] **Sprint 4 (第 11-14 天)**：置信度演进记忆库、频控熔断与任务调度器。
