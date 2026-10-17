import tempfile
from pathlib import Path
import pytest
from core.evolution import EvolutionEngine
from core.llm_provider import MockLLMProvider
from storage.database import Database


def test_rule_distillation_and_human_review_gate():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(Path(tmpdir) / "test.db")
        mock_llm = MockLLMProvider(canned_responses=[
            "TRIGGER: CODE_STYLE\nRULE: Always use pathlib.Path instead of os.path for filesystem operations."
        ])
        engine = EvolutionEngine(database=db, llm_provider=mock_llm)

        # 1. 模拟 PR 收到 Maintainer 评论
        comment = "Please do not use os.path here. In this project we strictly use pathlib.Path everywhere."
        rule_id = engine.extract_rule_from_comment(
            repo_name="psf/requests",
            comment_text=comment,
            source_pr_url="https://github.com/psf/requests/pull/123",
        )
        assert rule_id is not None

        # 2. 验证初始状态为 PENDING_REVIEW (防止污染)
        pending = engine.list_pending_rules()
        assert len(pending) == 1
        assert pending[0]["id"] == rule_id
        assert pending[0]["status"] == "PENDING_REVIEW"
        assert "pathlib" in pending[0]["rule_statement"]

        # 此时该规则尚未激活，不应该被注入 Agent Prompt
        active_rules = db.get_active_rules_for_repo("psf/requests")
        assert len(active_rules) == 0

        # 3. 人工终审确认激活规则
        ok = engine.review_and_activate_rule(
            rule_id=rule_id,
            approved=True,
            adjusted_statement="Must use pathlib.Path instead of os.path",
            confidence_score=5,
            reviewer_name="senior-reviewer",
        )
        assert ok is True

        # 4. 激活后可以被正常召回
        active_rules_after = db.get_active_rules_for_repo("psf/requests")
        assert len(active_rules_after) == 1
        assert active_rules_after[0]["rule_statement"] == "Must use pathlib.Path instead of os.path"
        assert active_rules_after[0]["confidence_score"] == 5
        assert active_rules_after[0]["reviewed_by"] == "senior-reviewer"
