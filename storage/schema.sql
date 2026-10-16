-- ============================================================================
-- Auto-SE Pipeline Database Schema v1.1
-- ============================================================================

-- 1. 目标仓库与合规政策表
CREATE TABLE IF NOT EXISTS repos (
    repo_name TEXT PRIMARY KEY,        -- e.g. "psf/requests"
    is_blacklisted BOOLEAN DEFAULT 0,
    blacklist_reason TEXT,
    ai_policy_status TEXT NOT NULL,    -- ALLOWED, FORBIDDEN, CONDITIONAL, UNKNOWN
    contributing_summary TEXT,
    has_cla BOOLEAN DEFAULT 0,
    last_pr_submitted_at TIMESTAMP,
    active_pr_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 任务生命周期主表
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,               -- UUID string
    repo_name TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    issue_title TEXT NOT NULL,
    issue_url TEXT NOT NULL,
    status TEXT NOT NULL,              -- PENDING, EVALUATING, REJECTED_UNSUITABLE, CONTEXT_INDEXED,
                                       -- BASELINE_TESTING, TEST_REPRODUCING, ABORTED_CANNOT_REPRODUCE,
                                       -- CODE_REPAIRING, SANDBOX_VALIDATING, FAILED_BUDGET_EXCEEDED,
                                       -- AWAITING_HUMAN_APPROVAL, REJECTED_BY_HUMAN, PR_SUBMITTED,
                                       -- MERGED_SUCCESS, FEEDBACK_CAPTURED
    branch_name TEXT,
    pr_number INTEGER,
    pr_url TEXT,
    retry_count INTEGER DEFAULT 0,
    total_tokens_consumed INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(repo_name) REFERENCES repos(repo_name)
);

-- 3. 状态流转历史表
CREATE TABLE IF NOT EXISTS task_state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

-- 4. 演进记忆与被拒规则库 (带人工审核机制)
CREATE TABLE IF NOT EXISTS rejection_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_name TEXT NOT NULL,           -- '*' 或特定 'owner/repo'
    trigger_type TEXT NOT NULL,        -- CI_FAIL, CODE_STYLE, ARCHITECTURE, HUMAN_REVIEW
    original_comment TEXT,
    rule_statement TEXT NOT NULL,
    confidence_score INTEGER DEFAULT 3,-- 1-5 分
    status TEXT DEFAULT 'PENDING_REVIEW', -- PENDING_REVIEW, ACTIVE, ARCHIVED, REJECTED
    source_pr_url TEXT,
    reviewed_by TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. 沙箱与 Agent 执行明细及成本表
CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    stage TEXT NOT NULL,               -- EVALUATE, CONTEXT, DEBUGGER, CODER, SANDBOX_BASELINE, SANDBOX_REPRODUCE, SANDBOX_REGRESSION
    attempt INTEGER NOT NULL,
    input_payload TEXT,
    output_payload TEXT,
    exit_code INTEGER,
    error_trace TEXT,
    tokens_used INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    duration_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

-- 索引加速
CREATE INDEX IF NOT EXISTS idx_tasks_repo_status ON tasks(repo_name, status);
CREATE INDEX IF NOT EXISTS idx_history_task_id ON task_state_history(task_id);
CREATE INDEX IF NOT EXISTS idx_rules_repo_status ON rejection_rules(repo_name, status);
