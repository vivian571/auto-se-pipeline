"""
Scheduler, Rate Limiting Circuit Breaker, and Telemetry Engine (Sprint 4 Milestone)
Enforces multi-tier anti-spam rate limits, auto-blacklists repeatedly rejected repositories,
and aggregates pipeline telemetry and cost metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from storage.database import Database, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class RateLimitCheckResult:
    allowed: bool
    reason: Optional[str] = None


class RateLimitCircuitBreaker:
    """Enforces strict community-friendly rate limits and circuit-breaking blacklists."""

    def __init__(
        self,
        database: Database,
        max_active_prs_per_repo: int = 1,
        max_daily_prs_per_repo: int = 1,
        max_daily_prs_global: int = 3,
        rejection_blacklist_threshold: int = 2,
    ):
        self.db = database
        self.max_active_prs_per_repo = max_active_prs_per_repo
        self.max_daily_prs_per_repo = max_daily_prs_daily = max_daily_prs_per_repo
        self.max_daily_prs_global = max_daily_prs_global
        self.rejection_blacklist_threshold = rejection_blacklist_threshold

    def check_pr_submission_allowed(self, repo_name: str) -> RateLimitCheckResult:
        """
        Validates whether creating a new PR for `repo_name` violates any safety or spam threshold.
        """
        # 1. 检查黑名单
        repo = self.db.get_repo(repo_name)
        if repo and repo.get("is_blacklisted"):
            return RateLimitCheckResult(
                allowed=False,
                reason=f"Repository {repo_name} is blacklisted: {repo.get('blacklist_reason')}",
            )

        # 2. 检查当前处于 Open 状态的活跃 PR 数量
        active_count = repo.get("active_pr_count", 0) if repo else 0
        if active_count >= self.max_active_prs_per_repo:
            return RateLimitCheckResult(
                allowed=False,
                reason=f"Repository {repo_name} already has {active_count} active open PR(s) (Limit: {self.max_active_prs_per_repo})",
            )

        # 3. 检查单仓库 24 小时提交频次
        now = datetime.utcnow()
        twenty_four_hours_ago = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

        with self.db.get_connection() as conn:
            # 统计单仓库最近 24 小时已提交的 PR
            cur = conn.execute(
                """
                SELECT COUNT(*) as count FROM tasks
                WHERE repo_name = ?
                  AND status IN ('PR_SUBMITTED', 'MERGED_SUCCESS')
                  AND created_at >= ?;
                """,
                (repo_name, twenty_four_hours_ago),
            )
            repo_daily_count = cur.fetchone()["count"]
            if repo_daily_count >= self.max_daily_prs_per_repo:
                return RateLimitCheckResult(
                    allowed=False,
                    reason=f"Repository {repo_name} reached daily limit of {self.max_daily_prs_per_repo} PR(s)",
                )

            # 4. 检查全局 24 小时提交频次
            cur_global = conn.execute(
                """
                SELECT COUNT(*) as count FROM tasks
                WHERE status IN ('PR_SUBMITTED', 'MERGED_SUCCESS')
                  AND created_at >= ?;
                """,
                (twenty_four_hours_ago,),
            )
            global_daily_count = cur_global.fetchone()["count"]
            if global_daily_count >= self.max_daily_prs_global:
                return RateLimitCheckResult(
                    allowed=False,
                    reason=f"Global daily PR submission limit reached ({global_daily_count}/{self.max_daily_prs_global})",
                )

        return RateLimitCheckResult(allowed=True)

    def record_pr_rejection(self, repo_name: str, rejection_reason: str) -> None:
        """
        Records a PR rejection by a maintainer. If rejected threshold is reached, auto-blacklists repo.
        """
        with self.db.get_connection() as conn:
            # 统计连续被拒次数 (最近两个处于 FEEDBACK_CAPTURED 或 REJECTED 状态且未成功被合入的任务)
            cur = conn.execute(
                """
                SELECT status FROM tasks
                WHERE repo_name = ?
                  AND status IN ('FEEDBACK_CAPTURED', 'MERGED_SUCCESS', 'REJECTED_UNSUITABLE', 'REJECTED_BY_HUMAN')
                ORDER BY created_at DESC
                LIMIT ?;
                """,
                (repo_name, self.rejection_blacklist_threshold),
            )
            recent_statuses = [row["status"] for row in cur.fetchall()]

            # 检查连续被拒次数
            consecutive_rejections = sum(1 for s in recent_statuses if s in ("FEEDBACK_CAPTURED", "REJECTED_BY_HUMAN"))
            if consecutive_rejections >= self.rejection_blacklist_threshold:
                reason = f"Auto-blacklisted after {consecutive_rejections} consecutive rejected PRs: {rejection_reason}"
                self.db.blacklist_repo(repo_name, reason)
                logger.warning(f"🚨 Circuit Breaker Triggered: {reason}")


class TelemetryReporter:
    """Aggregates cost, token, latency, and success metrics across all tasks."""

    def __init__(self, database: Database):
        self.db = database

    def generate_summary(self) -> Dict[str, any]:
        with self.db.get_connection() as conn:
            cur = conn.execute("SELECT COUNT(*) as total FROM tasks")
            total_tasks = cur.fetchone()["total"]

            cur_status = conn.execute(
                "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
            )
            status_dist = {r["status"]: r["count"] for r in cur_status.fetchall()}

            cur_metrics = conn.execute(
                "SELECT SUM(total_tokens_consumed) as tokens, SUM(cost_usd) as cost FROM tasks"
            )
            m_row = cur_metrics.fetchone()
            total_tokens = m_row["tokens"] or 0
            total_cost = m_row["cost"] or 0.0

            cur_rules = conn.execute(
                "SELECT status, COUNT(*) as count FROM rejection_rules GROUP BY status"
            )
            rules_dist = {r["status"]: r["count"] for r in cur_rules.fetchall()}

        return {
            "total_tasks": total_tasks,
            "status_distribution": status_dist,
            "total_tokens_consumed": total_tokens,
            "total_cost_usd": total_cost,
            "rejection_rules": rules_dist,
        }
