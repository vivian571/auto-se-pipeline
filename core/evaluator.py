"""
Compliance & Issue Evaluator for Auto-SE Pipeline (Sprint 3 Milestone)
Sniffs AI contribution policies in target repositories, screens issues for qualification,
and detects community claim conflicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple
from storage.database import AIPolicyStatus, Database


@dataclass
class EvaluationResult:
    qualified: bool
    rejection_reason: Optional[str] = None
    ai_policy: AIPolicyStatus = AIPolicyStatus.UNKNOWN
    has_traceback: bool = False
    has_reproduction_steps: bool = False
    is_claimed: bool = False
    active_pr_detected: bool = False


class IssueEvaluator:
    # 命中禁止 AI 政策的关键词正则（忽略大小写）
    FORBIDDEN_AI_PATTERNS = [
        re.compile(r'\bno\s+(?:ai|llm)\b', re.IGNORECASE),
        re.compile(r'\bdisallow\s+(?:ai|llm)\b', re.IGNORECASE),
        re.compile(r'\bprohibit\s+(?:ai|llm)\b', re.IGNORECASE),
        re.compile(r'\bforbid(?:s)?\s+(?:ai|llm)\b', re.IGNORECASE),
        re.compile(r'\bstrictly\s+forbids\s+(?:ai|llm)\b', re.IGNORECASE),
        re.compile(r'\bdo\s+not\s+use\s+(?:ai|llm|copilot|chatgpt)\b', re.IGNORECASE),
        re.compile(r'\bno\s+ai[\s-]generated\s+(?:pr|pull\s+request|code)\b', re.IGNORECASE),
        re.compile(r'\bhuman[\s-]only\s+contributions?\b', re.IGNORECASE),
    ]

    # 社区有人认领的常见表述
    CLAIMED_PATTERNS = [
        re.compile(r'\bi(?:(?:\'d|\s+would)\s+like\s+to|(?:\s+can|\s+will))\s+work\s+on\s+this\b', re.IGNORECASE),
        re.compile(r'\bi\s+am\s+working\s+on\s+this\b', re.IGNORECASE),
        re.compile(r'\bassign\s+(?:this\s+to\s+)?me\b', re.IGNORECASE),
        re.compile(r'\btaking\s+this\b', re.IGNORECASE),
    ]

    TRACEBACK_PATTERN = re.compile(r'Traceback\s*\(most\s+recent\s+call\s+last\):', re.IGNORECASE)
    CODE_BLOCK_PATTERN = re.compile(r'```(?:python|ts|js|bash)?\s*\n(.*?)\n```', re.DOTALL)

    def __init__(self, database: Optional[Database] = None):
        self.db = database

    def sniff_ai_policy(self, repo_root: Path | str) -> Tuple[AIPolicyStatus, Optional[str]]:
        """
        Inspects CONTRIBUTING.md, AI_POLICY.md, README.md, and .github/ files
        to determine whether AI contributions are allowed, conditional, or strictly forbidden.
        """
        root = Path(repo_root)
        candidate_files = [
            root / "CONTRIBUTING.md",
            root / "contributing.md",
            root / "AI_POLICY.md",
            root / "ai_policy.md",
            root / "README.md",
            root / "readme.md",
            root / ".github" / "CONTRIBUTING.md",
            root / ".github" / "AI_POLICY.md",
            root / ".github" / "PULL_REQUEST_TEMPLATE.md",
        ]

        for file_path in candidate_files:
            if not file_path.is_file():
                continue
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for pat in self.FORBIDDEN_AI_PATTERNS:
                    m = pat.search(content)
                    if m:
                        snippet = content[max(0, m.start() - 40): min(len(content), m.end() + 40)]
                        reason = f"Forbidden AI policy detected in {file_path.name}: '...{snippet.strip()}...'"
                        return AIPolicyStatus.FORBIDDEN, reason
            except Exception:
                continue

        return AIPolicyStatus.ALLOWED, None

    def evaluate_issue(
        self,
        repo_name: str,
        repo_root: Path | str,
        issue_title: str,
        issue_body: str,
        assignees: Optional[List[str]] = None,
        comments: Optional[List[str]] = None,
        linked_prs: Optional[List[int]] = None,
    ) -> EvaluationResult:
        """
        Performs holistic qualification check on an Issue:
        1. Repo blacklist & AI Policy checks;
        2. Assignee & Claim conflict checks;
        3. Traceback & Technical reproducibility check;
        4. Active open PR limits.
        """
        # 1. 检查仓库黑名单状态
        if self.db:
            repo_meta = self.db.get_repo(repo_name)
            if repo_meta and repo_meta.get("is_blacklisted"):
                return EvaluationResult(
                    qualified=False,
                    rejection_reason=f"Repository {repo_name} is blacklisted: {repo_meta.get('blacklist_reason')}",
                )
            if repo_meta and repo_meta.get("active_pr_count", 0) >= 1:
                return EvaluationResult(
                    qualified=False,
                    rejection_reason=f"Repository {repo_name} already has an active open PR by Auto-SE (rate limited)",
                )

        # 2. 嗅探 AI Policy
        ai_policy, policy_reason = self.sniff_ai_policy(repo_root)
        if ai_policy == AIPolicyStatus.FORBIDDEN:
            if self.db:
                self.db.upsert_repo(repo_name, ai_policy_status=AIPolicyStatus.FORBIDDEN, contributing_summary=policy_reason)
            return EvaluationResult(
                qualified=False,
                rejection_reason=policy_reason,
                ai_policy=AIPolicyStatus.FORBIDDEN,
            )

        # 3. 检查认领状态与关联 PR
        if assignees and len(assignees) > 0:
            return EvaluationResult(
                qualified=False,
                rejection_reason=f"Issue is already assigned to {', '.join(assignees)}",
                is_claimed=True,
            )

        if linked_prs and len(linked_prs) > 0:
            return EvaluationResult(
                qualified=False,
                rejection_reason=f"Issue already has associated PRs: {linked_prs}",
                active_pr_detected=True,
            )

        if comments:
            for c in comments:
                for pat in self.CLAIMED_PATTERNS:
                    if pat.search(c):
                        return EvaluationResult(
                            qualified=False,
                            rejection_reason="Community contributor has already claimed this issue in comments",
                            is_claimed=True,
                        )

        # 4. 检查信息清晰度与复现线索 (Traceback 或 复现步骤)
        combined_text = f"{issue_title}\n{issue_body}"
        has_tb = bool(self.TRACEBACK_PATTERN.search(combined_text))
        has_code_snippet = bool(self.CODE_BLOCK_PATTERN.search(combined_text))
        has_error_keyword = any(err in combined_text for err in ["Error", "Exception", "failed with", "crash"])

        if not (has_tb or (has_code_snippet and has_error_keyword)):
            return EvaluationResult(
                qualified=False,
                rejection_reason="Issue lacks specific traceback or actionable reproduction code snippet",
                has_traceback=has_tb,
                has_reproduction_steps=has_code_snippet,
            )

        return EvaluationResult(
            qualified=True,
            ai_policy=AIPolicyStatus.ALLOWED,
            has_traceback=has_tb,
            has_reproduction_steps=has_code_snippet,
            is_claimed=False,
            active_pr_detected=False,
        )
