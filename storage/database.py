"""
Database module for Auto-SE Pipeline (v1.1)
Manages SQLite with WAL mode, task state machine transitions, repo compliance,
rejection rule evolutions, and token/cost telemetry.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    EVALUATING = "EVALUATING"
    REJECTED_UNSUITABLE = "REJECTED_UNSUITABLE"
    CONTEXT_INDEXED = "CONTEXT_INDEXED"
    BASELINE_TESTING = "BASELINE_TESTING"
    TEST_REPRODUCING = "TEST_REPRODUCING"
    ABORTED_CANNOT_REPRODUCE = "ABORTED_CANNOT_REPRODUCE"
    CODE_REPAIRING = "CODE_REPAIRING"
    SANDBOX_VALIDATING = "SANDBOX_VALIDATING"
    FAILED_BUDGET_EXCEEDED = "FAILED_BUDGET_EXCEEDED"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    REJECTED_BY_HUMAN = "REJECTED_BY_HUMAN"
    PR_SUBMITTED = "PR_SUBMITTED"
    MERGED_SUCCESS = "MERGED_SUCCESS"
    FEEDBACK_CAPTURED = "FEEDBACK_CAPTURED"


class AIPolicyStatus(str, Enum):
    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"
    CONDITIONAL = "CONDITIONAL"
    UNKNOWN = "UNKNOWN"


class Database:
    def __init__(self, db_path: str | Path = "auto_se.db"):
        self.db_path = Path(db_path)
        self.schema_path = Path(__file__).parent / "schema.sql"
        self._init_db()

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        if not self.schema_path.exists():
            raise FileNotFoundError(f"Schema not found at {self.schema_path}")
        with open(self.schema_path, "r", encoding="utf-8") as f:
            ddl = f.read()
        with self.get_connection() as conn:
            conn.executescript(ddl)

    # -------------------------------------------------------------------------
    # Repo Management
    # -------------------------------------------------------------------------
    def upsert_repo(
        self,
        repo_name: str,
        ai_policy_status: AIPolicyStatus = AIPolicyStatus.UNKNOWN,
        contributing_summary: Optional[str] = None,
        has_cla: bool = False,
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO repos (repo_name, ai_policy_status, contributing_summary, has_cla)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_name) DO UPDATE SET
                    ai_policy_status = excluded.ai_policy_status,
                    contributing_summary = COALESCE(excluded.contributing_summary, repos.contributing_summary),
                    has_cla = excluded.has_cla,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (repo_name, ai_policy_status.value, contributing_summary, has_cla),
            )

    def get_repo(self, repo_name: str) -> Optional[dict]:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM repos WHERE repo_name = ?", (repo_name,))
            row = cur.fetchone()
            return dict(row) if row else None

    def blacklist_repo(self, repo_name: str, reason: str) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE repos
                SET is_blacklisted = 1, blacklist_reason = ?, updated_at = CURRENT_TIMESTAMP
                WHERE repo_name = ?;
                """,
                (reason, repo_name),
            )

    # -------------------------------------------------------------------------
    # Task Management & State Transitions
    # -------------------------------------------------------------------------
    def create_task(
        self,
        task_id: str,
        repo_name: str,
        issue_number: int,
        issue_title: str,
        issue_url: str,
    ) -> None:
        with self.get_connection() as conn:
            # 确保外键 repo 存在
            conn.execute(
                """
                INSERT OR IGNORE INTO repos (repo_name, ai_policy_status)
                VALUES (?, ?);
                """,
                (repo_name, AIPolicyStatus.UNKNOWN.value),
            )
            conn.execute(
                """
                INSERT INTO tasks (id, repo_name, issue_number, issue_title, issue_url, status)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (task_id, repo_name, issue_number, issue_title, issue_url, TaskStatus.PENDING.value),
            )
            conn.execute(
                """
                INSERT INTO task_state_history (task_id, from_status, to_status, message)
                VALUES (?, NULL, ?, 'Task initialized');
                """,
                (task_id, TaskStatus.PENDING.value),
            )

    def transition_task_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        message: Optional[str] = None,
    ) -> None:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError(f"Task {task_id} not found")
            current_status = row["status"]

            conn.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (new_status.value, task_id),
            )
            conn.execute(
                """
                INSERT INTO task_state_history (task_id, from_status, to_status, message)
                VALUES (?, ?, ?, ?);
                """,
                (task_id, current_status, new_status.value, message),
            )

    def update_task_metrics(
        self,
        task_id: str,
        add_tokens: int = 0,
        add_cost_usd: float = 0.0,
        retry_count: Optional[int] = None,
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE tasks
                SET total_tokens_consumed = total_tokens_consumed + ?,
                    cost_usd = cost_usd + ?,
                    retry_count = COALESCE(?, retry_count),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?;
                """,
                (add_tokens, add_cost_usd, retry_count, task_id),
            )

    def get_task(self, task_id: str) -> Optional[dict]:
        with self.get_connection() as conn:
            cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Rejection Rules & Evolution Memory
    # -------------------------------------------------------------------------
    def add_rejection_rule(
        self,
        repo_name: str,
        trigger_type: str,
        rule_statement: str,
        original_comment: Optional[str] = None,
        source_pr_url: Optional[str] = None,
        confidence_score: int = 3,
        auto_activate: bool = False,
    ) -> int:
        status = "ACTIVE" if auto_activate else "PENDING_REVIEW"
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO rejection_rules
                (repo_name, trigger_type, original_comment, rule_statement, confidence_score, status, source_pr_url)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    repo_name,
                    trigger_type,
                    original_comment,
                    rule_statement,
                    confidence_score,
                    status,
                    source_pr_url,
                ),
            )
            return cur.lastrowid

    def get_active_rules_for_repo(self, repo_name: str, min_confidence: int = 3) -> List[dict]:
        with self.get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM rejection_rules
                WHERE status = 'ACTIVE'
                  AND confidence_score >= ?
                  AND (repo_name = ? OR repo_name = '*');
                """,
                (min_confidence, repo_name),
            )
            return [dict(r) for r in cur.fetchall()]

    # -------------------------------------------------------------------------
    # Execution Logs
    # -------------------------------------------------------------------------
    def log_execution(
        self,
        task_id: str,
        stage: str,
        attempt: int,
        input_payload: Optional[str] = None,
        output_payload: Optional[str] = None,
        exit_code: Optional[int] = None,
        error_trace: Optional[str] = None,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        duration_ms: Optional[int] = None,
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO execution_logs
                (task_id, stage, attempt, input_payload, output_payload, exit_code, error_trace, tokens_used, cost_usd, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    task_id,
                    stage,
                    attempt,
                    input_payload,
                    output_payload,
                    exit_code,
                    error_trace,
                    tokens_used,
                    cost_usd,
                    duration_ms,
                ),
            )
