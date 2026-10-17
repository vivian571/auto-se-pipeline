import tempfile
from pathlib import Path
import pytest
from core.approval_ui import HumanApprovalConsole
from core.github_gateway import MockGitHubGateway, PullRequestPayload, TransparentPRRenderer
from core.swarm_orchestrator import SwarmResult
from storage.database import Database, TaskStatus


def test_transparent_pr_renderer():
    body = TransparentPRRenderer.render_body(
        issue_number=42,
        issue_title="Fix memory leak in pool",
        root_cause="Connection socket was not closed on timeout.",
        solution_details="Added try/finally to ensure socket.close() is called.",
        test_file="tests/test_issue_42.py",
        author_handle="developer-bot",
    )
    assert "Fixes #42: Fix memory leak in pool" in body
    assert "Generated with AI assistance" in body
    assert "@developer-bot" in body


def test_mock_github_gateway_compliance_enforcement():
    gateway = MockGitHubGateway()

    # 缺少披露声明的非法 PR
    illegal_payload = PullRequestPayload(
        repo_name="sample/repo",
        issue_number=1,
        branch_name="fix/1",
        title="fix bug",
        body="Normal body without any disclosure",
        patch_diff="...",
        regression_test_file="...",
        author_handle="dev",
        author_email="dev@example.com",
    )
    res = gateway.create_pull_request(illegal_payload)
    assert res.success is False
    assert "Compliance Violation" in res.error_message

    # 合规 PR
    legal_payload = PullRequestPayload(
        repo_name="sample/repo",
        issue_number=1,
        branch_name="fix/1",
        title="fix bug",
        body="Fixes #1\n*Generated with AI assistance; strictly validated in an isolated sandbox.*",
        patch_diff="...",
        regression_test_file="...",
        author_handle="dev",
        author_email="dev@example.com",
    )
    legal_res = gateway.create_pull_request(legal_payload)
    assert legal_res.success is True
    assert legal_res.pr_number == 1002


def test_human_approval_console_rejection_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        task_id = "task-reject-1"
        db.create_task(
            task_id=task_id,
            repo_name="sample/repo",
            issue_number=10,
            issue_title="Some issue",
            issue_url="https://github.com/sample/repo/issues/10",
        )

        gateway = MockGitHubGateway(db)
        console = HumanApprovalConsole(gateway, db)

        swarm_res = SwarmResult(
            success=True,
            reproduced=True,
            reproduction_code="...",
            patch_diff="--- a/file.py\n+++ b/file.py",
            final_test_file="tests/test_issue_10.py",
            attempts_used=1,
            baseline_failed_count=0,
            tokens_consumed=1000,
            cost_usd=0.01,
        )

        # 模拟人工拒绝
        decision = console.present_and_request_approval(
            task_id=task_id,
            repo_name="sample/repo",
            issue_number=10,
            issue_title="Some issue",
            swarm_result=swarm_res,
            interactive=False,
            auto_approve=False,
        )

        assert decision.approved is False
        assert len(gateway.submitted_prs) == 0

        # 校验数据库已流转至 REJECTED_BY_HUMAN
        task = db.get_task(task_id)
        assert task["status"] == TaskStatus.REJECTED_BY_HUMAN.value


def test_human_approval_console_approve_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        task_id = "task-approve-1"
        db.create_task(
            task_id=task_id,
            repo_name="sample/repo",
            issue_number=20,
            issue_title="Fix null pointer",
            issue_url="https://github.com/sample/repo/issues/20",
        )

        gateway = MockGitHubGateway(db)
        console = HumanApprovalConsole(gateway, db, author_handle="my-handle")

        swarm_res = SwarmResult(
            success=True,
            reproduced=True,
            reproduction_code="...",
            patch_diff="--- a/file.py\n+++ b/file.py",
            final_test_file="tests/test_issue_20.py",
            attempts_used=1,
            baseline_failed_count=0,
            tokens_consumed=1200,
            cost_usd=0.015,
        )

        # 模拟人工批准
        decision = console.present_and_request_approval(
            task_id=task_id,
            repo_name="sample/repo",
            issue_number=20,
            issue_title="Fix null pointer",
            swarm_result=swarm_res,
            interactive=False,
            auto_approve=True,
        )

        assert decision.approved is True
        assert len(gateway.submitted_prs) == 1
        assert decision.pr_response.success is True

        # 校验数据库已流转至 PR_SUBMITTED 并关联了 pr_number / pr_url
        task = db.get_task(task_id)
        assert task["status"] == TaskStatus.PR_SUBMITTED.value
        assert task["pr_number"] == 1001
        assert "pull/1001" in task["pr_url"]
