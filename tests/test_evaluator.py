import tempfile
from pathlib import Path
import pytest
from core.evaluator import IssueEvaluator
from storage.database import AIPolicyStatus, Database


def test_ai_policy_sniffing_forbidden():
    evaluator = IssueEvaluator()
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "CONTRIBUTING.md").write_text("""
# Contributing Guidelines
Please read carefully: This project strictly forbids AI generated PRs or LLM assistance.
All contributions must be human authored.
""")
        status, reason = evaluator.sniff_ai_policy(repo)
        assert status == AIPolicyStatus.FORBIDDEN
        assert "strictly forbids AI" in reason


def test_ai_policy_sniffing_allowed():
    evaluator = IssueEvaluator()
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        (repo / "CONTRIBUTING.md").write_text("""
# Contributing Guidelines
We welcome contributions from everyone! Please follow PEP8 and add unit tests.
""")
        status, reason = evaluator.sniff_ai_policy(repo)
        assert status == AIPolicyStatus.ALLOWED
        assert reason is None


def test_issue_qualification_claimed_rejection():
    evaluator = IssueEvaluator()
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)

        # 场景 A: 已经被 Assign
        res1 = evaluator.evaluate_issue(
            repo_name="sample/repo",
            repo_root=repo,
            issue_title="Bug in parser",
            issue_body="Traceback (most recent call last):\n  File 'a.py', line 1\nValueError",
            assignees=["alice"],
        )
        assert res1.qualified is False
        assert "already assigned" in res1.rejection_reason

        # 场景 B: 评论区有人认领
        res2 = evaluator.evaluate_issue(
            repo_name="sample/repo",
            repo_root=repo,
            issue_title="Bug in parser",
            issue_body="Traceback (most recent call last):\n  File 'a.py', line 1\nValueError",
            comments=["Hey, I would like to work on this issue!"],
        )
        assert res2.qualified is False
        assert "already claimed" in res2.rejection_reason


def test_issue_qualification_vague_rejection():
    evaluator = IssueEvaluator()
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        # 纯文字讨论，没有报错堆栈也没有代码
        res = evaluator.evaluate_issue(
            repo_name="sample/repo",
            repo_root=repo,
            issue_title="Feature suggestion or vague thought",
            issue_body="I think we should rethink the way users authenticate. Maybe OAuth?",
        )
        assert res.qualified is False
        assert "lacks specific traceback" in res.rejection_reason


def test_issue_qualification_success():
    evaluator = IssueEvaluator()
    with tempfile.TemporaryDirectory() as tmpdir:
        repo = Path(tmpdir)
        res = evaluator.evaluate_issue(
            repo_name="sample/repo",
            repo_root=repo,
            issue_title="Crash in serializer with KeyError",
            issue_body="""
Calling dump() raises:
Traceback (most recent call last):
  File "src/serialize.py", line 42, in dump
    return data['id']
KeyError: 'id'
""",
        )
        assert res.qualified is True
        assert res.has_traceback is True
