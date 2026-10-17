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
from core.evolution import EvolutionEngine
from core.github_gateway import MockGitHubGateway, LiveGitHubGateway
from core.llm_provider import MockLLMProvider
from core.sandbox import ProcessIsolationSandbox
from core.scheduler import RateLimitCircuitBreaker, TelemetryReporter
from core.swarm_orchestrator import SwarmOrchestrator
from storage.database import Database, TaskStatus, AIPolicyStatus


def main():
    parser = argparse.ArgumentParser(description="Auto-SE: Autonomous Software Engineering Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. 上下文切片分析
    slice_parser = subparsers.add_parser("analyze-context", help="Run context engine on local repo")
    slice_parser.add_argument("--repo-path", required=True, help="Path to local repository")
    slice_parser.add_argument("--issue-title", required=True, help="Issue title")
    slice_parser.add_argument("--issue-body", default="", help="Issue description")
    slice_parser.add_argument("--budget", type=int, default=3000, help="Max token budget")

    # 2. 任务状态查询
    status_parser = subparsers.add_parser("status", help="Check task status in database")
    status_parser.add_argument("--task-id", required=True, help="Task UUID")

    # 3. 完整端到端流水线 (Sprint 3)
    run_parser = subparsers.add_parser("run", help="Run full autonomous pipeline on an issue with human approval")
    run_parser.add_argument("--repo-path", required=True, help="Path to local repository")
    run_parser.add_argument("--issue-number", type=int, default=1, help="Issue number")
    run_parser.add_argument("--issue-title", required=True, help="Issue title")
    run_parser.add_argument("--issue-body", default="", help="Issue description")
    run_parser.add_argument("--expected-exception", default=None, help="Expected exception signature")
    run_parser.add_argument("--test-cmd", default="pytest -v", help="Test command to run")
    run_parser.add_argument("--auto-approve", action="store_true", help="Bypass manual interactive approval for CI/test")

    # 4. 系统度量与成本报表 (Sprint 4)
    subparsers.add_parser("metrics", help="Display global pipeline telemetry and cost metrics")

    # 5. 演进记忆与负样本规则管理 (Sprint 4)
    rules_parser = subparsers.add_parser("rules", help="Manage learned rejection rules")
    rules_sub = rules_parser.add_subparsers(dest="rule_action", required=True)
    rules_sub.add_parser("list-pending", help="List rules awaiting human approval")
    approve_rule_p = rules_sub.add_parser("approve", help="Approve and activate a rule")
    approve_rule_p.add_argument("--rule-id", type=int, required=True, help="Rule ID")
    approve_rule_p.add_argument("--confidence", type=int, default=5, help="Confidence score (1-5)")

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

    elif args.command == "metrics":
        db = Database()
        reporter = TelemetryReporter(db)
        summary = reporter.generate_summary()
        print("\n" + "=" * 60)
        print("📊 AUTO-SE PIPELINE TELEMETRY & COST REPORT")
        print("=" * 60)
        print(f"Total Tasks Managed   : {summary['total_tasks']}")
        print(f"Total Tokens Consumed : {summary['total_tokens_consumed']}")
        print(f"Total Expense USD     : ${summary['total_cost_usd']:.4f}")
        print("\nTask Status Distribution:")
        for st, cnt in summary["status_distribution"].items():
            print(f"  - {st:<25}: {cnt}")
        print("\nLearned Rules Distribution:")
        for st, cnt in summary["rejection_rules"].items():
            print(f"  - {st:<25}: {cnt}")
        print("=" * 60)

    elif args.command == "rules":
        db = Database()
        evolution = EvolutionEngine(db)
        if args.rule_action == "list-pending":
            pending = evolution.list_pending_rules()
            print(f"\n📋 Pending Rules Awaiting Human Review ({len(pending)} total):")
            for r in pending:
                print(f"  [{r['id']}] [{r['repo_name']}] ({r['trigger_type']}): {r['rule_statement']}")
                print(f"       Original: {r['original_comment'][:80]}...")
        elif args.rule_action == "approve":
            ok = evolution.review_and_activate_rule(
                rule_id=args.rule_id,
                approved=True,
                confidence_score=args.confidence,
            )
            if ok:
                print(f"✅ Rule #{args.rule_id} activated with confidence {args.confidence}!")
            else:
                print(f"❌ Rule #{args.rule_id} not found.")

    elif args.command == "run":
        db = Database()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        repo_name = Path(args.repo_path).name

        print("=" * 70)
        print(f"🚀 Launching Auto-SE Autonomous Pipeline for Task {task_id}")
        print(f"📦 Repository: {repo_name} | Issue #{args.issue_number}")
        print("=" * 70)

        # ---------------------------------------------------------------------
        # Stage 1: 频控熔断与合规准入评估 (Circuit Breaker & Evaluator)
        # ---------------------------------------------------------------------
        print("\n🔍 Stage 1: Checking Circuit Breakers & Evaluating AI Policy...")
        breaker = RateLimitCircuitBreaker(db)
        limit_check = breaker.check_pr_submission_allowed(repo_name)
        if not limit_check.allowed:
            print(f"🚨 Circuit Breaker Blocked: {limit_check.reason}")
            sys.exit(1)

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
