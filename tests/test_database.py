import tempfile
from pathlib import Path
import pytest
from storage.database import Database, TaskStatus, AIPolicyStatus


def test_database_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_se.db"
        db = Database(db_file)

        # 1. 插入 Repo
        db.upsert_repo("psf/requests", AIPolicyStatus.ALLOWED, "Please submit clean code", False)
        repo = db.get_repo("psf/requests")
        assert repo is not None
        assert repo["ai_policy_status"] == "ALLOWED"

        # 2. 创建 Task
        task_id = "task-test-uuid-101"
        db.create_task(
            task_id=task_id,
            repo_name="psf/requests",
            issue_number=42,
            issue_title="Fix session connection leak",
            issue_url="https://github.com/psf/requests/issues/42",
        )
        task = db.get_task(task_id)
        assert task["status"] == TaskStatus.PENDING.value

        # 3. 状态流转
        db.transition_task_status(task_id, TaskStatus.EVALUATING, "Starting evaluation")
        db.transition_task_status(task_id, TaskStatus.CONTEXT_INDEXED, "Context built")
        task_updated = db.get_task(task_id)
        assert task_updated["status"] == TaskStatus.CONTEXT_INDEXED.value

        # 4. 检查状态流转历史
        with db.get_connection() as conn:
            cur = conn.execute("SELECT * FROM task_state_history WHERE task_id = ? ORDER BY id ASC", (task_id,))
            history = cur.fetchall()
            assert len(history) == 3
            assert history[0]["to_status"] == TaskStatus.PENDING.value
            assert history[1]["to_status"] == TaskStatus.EVALUATING.value
            assert history[2]["to_status"] == TaskStatus.CONTEXT_INDEXED.value

        # 5. 测试规则库演化
        rule_id = db.add_rejection_rule(
            repo_name="psf/requests",
            trigger_type="CODE_STYLE",
            rule_statement="Always use session context manager",
            auto_activate=True,
        )
        rules = db.get_active_rules_for_repo("psf/requests")
        assert len(rules) == 1
        assert rules[0]["rule_statement"] == "Always use session context manager"
