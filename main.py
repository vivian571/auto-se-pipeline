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

    # 2. 数据库状态查看
    status_parser = subparsers.add_parser("status", help="Check task status in database")
    status_parser.add_argument("--task-id", required=True, help="Task UUID")

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


if __name__ == "__main__":
    main()
