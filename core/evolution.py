"""
Evolution Memory & Rejection Rule Engine for Auto-SE Pipeline (Sprint 4 Milestone)
Distills structured rules from maintainer review comments and CI failures,
manages rule confidence, and enforces human-review gates to prevent rule contamination.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from core.llm_provider import BaseLLMProvider
from storage.database import Database

logger = logging.getLogger(__name__)


@dataclass
class ExtractedRule:
    repo_name: str
    trigger_type: str  # HUMAN_REVIEW, CI_FAIL, CODE_STYLE, ARCHITECTURE
    rule_statement: str
    original_comment: str
    confidence_score: int = 3
    source_pr_url: Optional[str] = None


RULE_DISTILLER_SYSTEM_PROMPT = """You are the Knowledge Distillation Engine in an Autonomous Software Engineering system.
Your task is to convert reviewer feedback, rejection comments, or CI failure reasons into concise, actionable coding guidelines.

RULES:
1. The rule MUST be clear, imperative, and actionable (e.g., "Always use pathlib.Path instead of os.path").
2. DO NOT include polite greetings, project-specific insults, or ambiguous comments.
3. Determine the trigger_type: HUMAN_REVIEW, CI_FAIL, CODE_STYLE, or ARCHITECTURE.
4. Output in exact format:
TRIGGER: <type>
RULE: <imperative rule statement>
"""


class EvolutionEngine:
    def __init__(self, database: Database, llm_provider: Optional[BaseLLMProvider] = None):
        self.db = database
        self.llm = llm_provider

    def extract_rule_from_comment(
        self,
        repo_name: str,
        comment_text: str,
        source_pr_url: Optional[str] = None,
        auto_activate: bool = False,
    ) -> Optional[int]:
        """
        Parses maintainer comment, distills into a clean rule, and saves to database.
        Default status is PENDING_REVIEW unless auto_activate is True.
        """
        clean_text = comment_text.strip()
        if len(clean_text) < 10:
            return None

        # 如果没有配置真实 LLM，使用正则/启发式规则提取器
        trigger_type = "HUMAN_REVIEW"
        rule_statement = ""

        if self.llm:
            try:
                resp = self.llm.generate(
                    system_prompt=RULE_DISTILLER_SYSTEM_PROMPT,
                    user_prompt=f"Reviewer Feedback:\n{clean_text}",
                    temperature=0.1,
                )
                trigger_m = re.search(r'TRIGGER:\s*(.*)', resp.content)
                rule_m = re.search(r'RULE:\s*(.*)', resp.content)
                if trigger_m:
                    trigger_type = trigger_m.group(1).strip()
                if rule_m:
                    rule_statement = rule_m.group(1).strip()
            except Exception as e:
                logger.warning(f"LLM rule extraction failed, using heuristic: {e}")

        if not rule_statement:
            # 启发式提取：提取第一句明确建议
            first_sentence = clean_text.splitlines()[0]
            # 移除请字等前缀
            rule_statement = re.sub(r'^(?:please|could you|you should)\s+', 'Must ', first_sentence, flags=re.IGNORECASE).strip()
            if not rule_statement.endswith('.'):
                rule_statement += '.'

        # 检查是否包含有效指导性内容
        if len(rule_statement) < 8:
            return None

        rule_id = self.db.add_rejection_rule(
            repo_name=repo_name,
            trigger_type=trigger_type,
            rule_statement=rule_statement,
            original_comment=clean_text,
            source_pr_url=source_pr_url,
            confidence_score=3,
            auto_activate=auto_activate,
        )
        return rule_id

    def review_and_activate_rule(
        self,
        rule_id: int,
        approved: bool,
        adjusted_statement: Optional[str] = None,
        confidence_score: int = 4,
        reviewer_name: str = "human-operator",
    ) -> bool:
        """
        Human gatekeeper review for distilled rules to prevent prompt/rule contamination.
        """
        status = "ACTIVE" if approved else "REJECTED"
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE rejection_rules
                SET status = ?,
                    rule_statement = COALESCE(?, rule_statement),
                    confidence_score = ?,
                    reviewed_by = ?
                WHERE id = ?;
                """,
                (status, adjusted_statement, confidence_score, reviewer_name, rule_id),
            )
            return cur.rowcount > 0

    def list_pending_rules(self) -> List[dict]:
        """Lists all distilled rules currently awaiting human activation."""
        with self.db.get_connection() as conn:
            cur = conn.execute("SELECT * FROM rejection_rules WHERE status = 'PENDING_REVIEW' ORDER BY id ASC")
            return [dict(r) for r in cur.fetchall()]
