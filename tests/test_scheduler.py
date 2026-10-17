import tempfile
from pathlib import Path
import pytest
from core.scheduler import RateLimitCircuitBreaker, TelemetryReporter
from storage.database import Database, TaskStatus


def test_rate_limit_circuit_breaker_active_pr():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        breaker = RateLimitCircuitBreaker(db, max_active_prs_per_repo=1)

        # 初始化仓库且 active_pr_count = 1
        db.upsert_repo("pallets/flask")
        with db.get_connection() as conn:
            conn.execute("UPDATE repos SET active_pr_count = 1 WHERE repo_name = 'pallets/flask'")

        # 检查是否允许再次提交 PR
        res = breaker.check_pr_submission_allowed("pallets/flask")
        assert res.allowed is False
        assert "already has 1 active open PR" in res.reason


def test_rate_limit_circuit_breaker_blacklisting():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        breaker = RateLimitCircuitBreaker(db, rejection_blacklist_threshold=2)

        repo_name = "test/target"
        db.upsert_repo(repo_name)

        # 模拟产生两个被拒的任务
        for i in range(2):
            task_id = f"task-rej-{i}"
            db.create_task(task_id, repo_name, 10 + i, "title", "url")
            db.transition_task_status(task_id, TaskStatus.FEEDBACK_CAPTURED, "PR was closed by maintainer")

        # 触发连续被拒熔断器
        breaker.record_pr_rejection(repo_name, "Maintainer closed PR without merging")

        # 验证仓库已被自动拉黑
        repo = db.get_repo(repo_name)
        assert repo["is_blacklisted"] == 1
        assert "Auto-blacklisted after 2 consecutive rejected PRs" in repo["blacklist_reason"]

        # 后续提交检查被彻底阻断
        res = breaker.check_pr_submission_allowed(repo_name)
        assert res.allowed is False
        assert "blacklisted" in res.reason


def test_telemetry_reporter():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        reporter = TelemetryReporter(db)

        # 创建一个任务并记录指标
        task_id = "task-telemetry-1"
        db.create_task(task_id, "sample/repo", 1, "test title", "http://url")
        db.transition_task_status(task_id, TaskStatus.PR_SUBMITTED, "Submitted")
        db.update_task_metrics(task_id, add_tokens=2500, add_cost_usd=0.035)

        summary = reporter.generate_summary()
        assert summary["total_tasks"] == 1
        assert summary["total_tokens_consumed"] == 2500
        assert summary["total_cost_usd"] == pytest.approx(0.035)
        assert summary["status_distribution"].get(TaskStatus.PR_SUBMITTED.value) == 1
