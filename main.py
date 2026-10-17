"""
Auto-SE Pipeline CLI & Full Orchestration Entrypoint (v1.1)
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from core.approval_ui import HumanApprovalConsole
from core.context_engine import ContextEngine
from core.evaluator import IssueEvaluator
from core.github_gateway import MockGitHubGateway, LiveGitHubGateway
from core.llm_provider import MockLLMProvider
from core.sandbox import ProcessIsolationSandbox
from core.swarm_orchestrator import SwarmOrchestrator
from storage.database import Database, TaskStatus, AIPolicyStatus


def main():
    parser = argparse.ArgumentParser(description="Auto-SE: Autonomous Software Engineering Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. 运行上下文切片测试
    slice_parser = subparsers.add_parser("analyze-context", help="Run context engine on local repo")
    slice_parser.add_argument("--repo-path", required=True, help="Path to local repository")
    slice_parser.add_argument("--issue-title", required=True, help="Issue title")
    slice_parser.add_argument("--issue-body", default="", help="Issue description")
    slice_parser.add_argument("--budget", type=int, default=3000, help="Max token budget")

    # 2. 状态查询
    status_parser = subparsers.add_parser("status", help="Check task status in database")
    status_parser.add_argument("--task-id", required=True, help="Task UUID")

    # 3. 完整端到端流水线 (Sprint 3 核心)
    run_parser = subparsers.add_parser("run", help="Run full autonomous pipeline on an issue with human approval")
    run_parser.add_argument("--repo-path", required=True, help="Path to local repository")
    run_parser.add_argument("--issue-number", type=int, default=1, help="Issue number")
    run_parser.add_argument("--issue-title", required=True, help="Issue title")
    run_parser.add_argument("--issue-body", default="", help="Issue description")
    run_parser.add_argument("--expected-exception", default=None, help="Expected exception signature")
    run_parser.add_argument("--test-cmd", default="pytest -v", help="Test command to run")
    run_parser.add_argument("--auto-approve", action="store_true", help="Bypass manual interactive approval for CI/test")

    args = parser.parse_args()

    if args.command == "analyze-context":
        engine = ContextEngine()
        res = engine.analyze_repository(
            repo_root=args.repo_path,
            issue_title=args.issue_title,
            issue_body=args.issue_body,
            max_tokens_budget=args.budget,
        )
        print(f"✅ Context extraction finished in {res.target_repo}")
        print(f"📊 Total tokens: {res.total_tokens} (Files recalled: {len(res.primary_files)})")
        print("\n" + "=" * 50)
        print(res.rendered_markdown[:1500] + "\n... [truncated for CLI output]")
        print("=" * 50)

    elif args.command == "status":
        db = Database()
        task = db.get_task(args.task_id)
        if not task:
            print(f"❌ Task {args.task_id} not found.")
            sys.exit(1)
        print(f"Task ID: {task['id']}")
        print(f"Repo:    {task['repo_name']}")
        print(f"Status:  {task['status']}")
        print(f"Cost:    ${task['cost_usd']:.4f} ({task['total_tokens_consumed']} tokens)")

    elif args.command == "run":
        db = Database()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        repo_name = Path(args.repo_path).name

        print("=" * 70)
        print(f"🚀 Launching Auto-SE Autonomous Pipeline for Task {task_id}")
        print(f"📦 Repository: {repo_name} | Issue #{args.issue_number}")
        print("=" * 70)

        # ---------------------------------------------------------------------
        # Stage 1: 合规准入与 Issue 评估 (Evaluator)
        # ---------------------------------------------------------------------
        print("\n🔍 Stage 1: Evaluating AI Policy & Issue Qualification...")
        evaluator = IssueEvaluator(db)
        eval_result = evaluator.evaluate_issue(
            repo_name=repo_name,
            repo_root=args.repo_path,
            issue_title=args.issue_title,
            issue_body=args.issue_body,
        )

        if not eval_result.qualified:
            print(f"❌ Issue rejected: {eval_result.rejection_reason}")
            sys.exit(1)

        print(f"✅ Qualification PASSED! (AI Policy: {eval_result.ai_policy.value})")

        # 初始化 Task 记录
        db.create_task(
            task_id=task_id,
            repo_name=repo_name,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            issue_url=f"https://github.com/local/{repo_name}/issues/{args.issue_number}",
        )

        # ---------------------------------------------------------------------
        # Stage 2: 上下文全景切片提取 (Context Engine)
        # ---------------------------------------------------------------------
        print("\n🧩 Stage 2: Indexing Repository & Pruning AST Skeletons...")
        db.transition_task_status(task_id, TaskStatus.CONTEXT_INDEXED, "Context indexed")
        engine = ContextEngine()
        context_slice = engine.analyze_repository(
            repo_root=args.repo_path,
            issue_title=args.issue_title,
            issue_body=args.issue_body,
        )
        print(f"✅ Sliced {len(context_slice.primary_files)} relevant source skeletons ({context_slice.total_tokens} tokens)")

        # ---------------------------------------------------------------------
        # Stage 3: 对抗双 Agent 闭环修复与沙箱验证 (Swarm Orchestrator)
        # ---------------------------------------------------------------------
        print("\n🥊 Stage 3: Running Dual-Agent Swarm (Debugger vs Coder in Sandbox)...")
        sandbox = ProcessIsolationSandbox(args.repo_path)
        llm = MockLLMProvider()
        orchestrator = SwarmOrchestrator(sandbox=sandbox, llm_provider=llm, database=db)

        swarm_res = orchestrator.run_repair_swarm(
            task_id=task_id,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            issue_body=args.issue_body,
            context_slice=context_slice,
            expected_exception=args.expected_exception,
            full_test_cmd=args.test_cmd,
        )

        if not swarm_res.success:
            print(f"\n❌ Swarm repair failed or aborted: {swarm_res.error_message}")
            sys.exit(1)

        print(f"✅ Repair verified! All regressions passed in {swarm_res.attempts_used} attempt(s)")

        # ---------------------------------------------------------------------
        # Stage 4: 人工终审审查与透明 GitHub 提交 (Approval UI & Gateway)
        # ---------------------------------------------------------------------
        print("\n🧑‍⚖️ Stage 4: Presenting Verification Dashboard for Human Approval...")
        gateway = MockGitHubGateway(db)
        approval_console = HumanApprovalConsole(gateway, db)

        decision = approval_console.present_and_request_approval(
            task_id=task_id,
            repo_name=repo_name,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            swarm_result=swarm_res,
            interactive=not args.auto_approve,
            auto_approve=args.auto_approve,
        )

        if decision.approved and decision.pr_response and decision.pr_response.success:
            print("\n" + "=" * 70)
            print("🏁 AUTONOMOUS ENGINEERING PIPELINE COMPLETED SUCCESSFULLY!")
            print(f"🔗 Pull Request: {decision.pr_response.pr_url}")
            print(f"💰 Total Cost  : ${swarm_res.cost_usd:.4f} ({swarm_res.tokens_consumed} tokens)")
            print("=" * 70)
        else:
            print("\n🛑 Pipeline halted after review.")


if __name__ == "__main__":
    main()
