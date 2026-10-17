"""
Human-in-the-Loop Approval Console for Auto-SE Pipeline (Sprint 3 Milestone)
Presents comprehensive verification review dashboard and gates GitHub submission
behind explicit human approval.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional
from core.github_gateway import BaseGitHubGateway, PullRequestPayload, PullRequestResponse, TransparentPRRenderer
from core.swarm_orchestrator import SwarmResult
from storage.database import Database, TaskStatus


@dataclass
class ApprovalDecision:
    approved: bool
    rejection_feedback: Optional[str] = None
    pr_response: Optional[PullRequestResponse] = None


class HumanApprovalConsole:
    def __init__(
        self,
        github_gateway: BaseGitHubGateway,
        database: Optional[Database] = None,
        author_handle: str = "antigravity-bot",
        author_email: str = "bot@example.com",
    ):
        self.gateway = github_gateway
        self.db = database
        self.author_handle = author_handle
        self.author_email = author_email

    def present_and_request_approval(
        self,
        task_id: str,
        repo_name: str,
        issue_number: int,
        issue_title: str,
        swarm_result: SwarmResult,
        interactive: bool = True,
        auto_approve: bool = False,
    ) -> ApprovalDecision:
        """
        Renders the review dashboard and waits for human input.
        """
        # 1. 渲染 PR 正文
        pr_body = TransparentPRRenderer.render_body(
            issue_number=issue_number,
            issue_title=issue_title,
            root_cause="Handled potential nil/boundary edge case revealed in reproduction test.",
            solution_details="Added boundary validation logic and added regression test case.",
            test_file=swarm_result.final_test_file,
            author_handle=self.author_handle,
        )

        # 2. 输出终端看板
        print("\n" + "=" * 70)
        print("🧑‍💻 [HUMAN-IN-THE-LOOP] PULL REQUEST APPROVAL CONSOLE")
        print("=" * 70)
        print(f"📦 Target Repository   : {repo_name}")
        print(f"🎯 Target Issue        : #{issue_number} - {issue_title}")
        print(f"🆔 Task ID             : {task_id}")
        print(f"🔄 Swarm Attempts Used : {swarm_result.attempts_used} attempt(s)")
        print(f"💰 Telemetry / Cost    : {swarm_result.tokens_consumed} tokens (${swarm_result.cost_usd:.4f})")
        print(f"🧪 Final Regression Test: {swarm_result.final_test_file}")
        print("-" * 70)
        print("📝 PROPOSED UNIFIED DIFF:")
        print(swarm_result.patch_diff if swarm_result.patch_diff else "(No diff)")
        print("-" * 70)
        print("📄 PROPOSED PULL REQUEST BODY (WITH MANDATORY AI DISCLOSURE):")
        print(pr_body)
        print("=" * 70)

        decision_approved = False
        feedback = None

        if auto_approve:
            print("⚡ Auto-approve flag set. Approving automatically for pipeline testing...")
            decision_approved = True
        elif interactive:
            try:
                choice = input("\n👉 Do you approve submitting this Pull Request to GitHub? [y/N/feedback]: ").strip()
                if choice.lower() in ("y", "yes", "approve"):
                    decision_approved = True
                else:
                    decision_approved = False
                    feedback = choice if choice.lower() not in ("n", "no") else "Rejected by human reviewer"
            except (KeyboardInterrupt, EOFError):
                decision_approved = False
                feedback = "Interrupted by user"
        else:
            decision_approved = False
            feedback = "Non-interactive environment without auto_approve"

        # 3. 处理流转
        if not decision_approved:
            print(f"\n❌ Pull Request submission was REJECTED: {feedback}")
            if self.db:
                self.db.transition_task_status(
                    task_id=task_id,
                    new_status=TaskStatus.REJECTED_BY_HUMAN,
                    message=feedback,
                )
            return ApprovalDecision(approved=False, rejection_feedback=feedback)

        # 4. 人工确认批准，网关提交 PR
        print("\n🚀 Submitting Pull Request via GitHub Gateway...")
        payload = PullRequestPayload(
            repo_name=repo_name,
            issue_number=issue_number,
            branch_name=f"fix/issue-{issue_number}",
            title=f"fix: resolve #{issue_number} - {issue_title}",
            body=pr_body,
            patch_diff=swarm_result.patch_diff,
            regression_test_file=swarm_result.final_test_file,
            author_handle=self.author_handle,
            author_email=self.author_email,
            signed_off=True,
        )

        pr_resp = self.gateway.create_pull_request(payload)

        if pr_resp.success:
            print(f"🎉 PULL REQUEST CREATED SUCCESSFULLY: {pr_resp.pr_url}")
            if self.db:
                self.db.transition_task_status(
                    task_id=task_id,
                    new_status=TaskStatus.PR_SUBMITTED,
                    message=f"PR submitted: {pr_resp.pr_url}",
                )
                with self.db.get_connection() as conn:
                    conn.execute(
                        "UPDATE tasks SET pr_number = ?, pr_url = ? WHERE id = ?",
                        (pr_resp.pr_number, pr_resp.pr_url, task_id),
                    )
        else:
            print(f"❌ Failed to create PR: {pr_resp.error_message}")

        return ApprovalDecision(
            approved=True,
            pr_response=pr_resp,
        )
