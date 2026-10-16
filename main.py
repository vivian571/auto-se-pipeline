"""
Auto-SE Pipeline CLI & Orchestrator Entrypoint (v1.1)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from storage.database import Database, TaskStatus, AIPolicyStatus
from core.context_engine import ContextEngine


def main():
    parser = argparse.ArgumentParser(description="Auto-SE: Autonomous Software Engineering Pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. 运行上下文切片测试
    slice_parser = subparsers.add_parser("analyze-context", help="Run context engine on local repo")
    slice_parser.add_argument("--repo-path", required=True, help="Path to local repository")
    slice_parser.add_argument("--issue-title", required=True, help="Issue title")
    slice_parser.add_argument("--issue-body", default="", help="Issue description")
    slice_parser.add_argument("--budget", type=int, default=3000, help="Max token budget")

    # 3. 双 Agent 对抗修复闭环执行
    repair_parser = subparsers.add_parser("repair", help="Execute closed-loop swarm repair on repository")
    repair_parser.add_argument("--repo-path", required=True, help="Path to local repository")
    repair_parser.add_argument("--issue-number", type=int, default=1, help="Issue number")
    repair_parser.add_argument("--issue-title", required=True, help="Issue title")
    repair_parser.add_argument("--issue-body", default="", help="Issue description")
    repair_parser.add_argument("--expected-exception", default=None, help="Expected exception signature")
    repair_parser.add_argument("--test-cmd", default="pytest -v", help="Test command to run")

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

    elif args.command == "repair":
        import uuid
        from core.llm_provider import MockLLMProvider
        from core.sandbox import ProcessIsolationSandbox
        from core.swarm_orchestrator import SwarmOrchestrator

        db = Database()
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        repo_name = Path(args.repo_path).name

        db.create_task(
            task_id=task_id,
            repo_name=repo_name,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            issue_url=f"https://github.com/local/{repo_name}/issues/{args.issue_number}",
        )

        engine = ContextEngine()
        context_slice = engine.analyze_repository(
            repo_root=args.repo_path,
            issue_title=args.issue_title,
            issue_body=args.issue_body,
        )

        sandbox = ProcessIsolationSandbox(args.repo_path)
        # 默认示例使用 MockLLMProvider 演示，如配置真实模型则由 Provider 读取 settings.yaml
        llm = MockLLMProvider()
        orchestrator = SwarmOrchestrator(sandbox=sandbox, llm_provider=llm, database=db)

        print(f"🚀 Starting closed-loop Swarm Repair for Task {task_id} ({repo_name}#{args.issue_number})...")
        res = orchestrator.run_repair_swarm(
            task_id=task_id,
            issue_number=args.issue_number,
            issue_title=args.issue_title,
            issue_body=args.issue_body,
            context_slice=context_slice,
            expected_exception=args.expected_exception,
            full_test_cmd=args.test_cmd,
        )

        if res.success:
            print("\n" + "=" * 60)
            print("🎉 REPAIR COMPLETED SUCCESSFULLY!")
            print(f"✅ Final Regression Test : {res.final_test_file}")
            print(f"✅ Attempts Used         : {res.attempts_used}")
            print(f"✅ Tokens / Cost         : {res.tokens_consumed} tokens / ${res.cost_usd:.4f}")
            print("=" * 60)
            print("Generated Unified Diff:")
            print(res.patch_diff)
        else:
            print("\n❌ Repair aborted or failed:")
            print(res.error_message)


if __name__ == "__main__":
    main()
