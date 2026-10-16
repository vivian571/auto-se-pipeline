"""
Benchmark Runner for Auto-SE Context Engine (Sprint 1 Milestone)
Evaluates context recall speed, token budget compliance, and AST pruning on realistic test cases.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context_engine import ContextEngine


def run_benchmark():
    print("=" * 60)
    print("🚀 Auto-SE Pipeline: Sprint 1 Context Engine Benchmark")
    print("=" * 60)

    engine = ContextEngine()

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        src = repo_root / "requests_mock_repo"
        src.mkdir(parents=True)
        (repo_root / "tests").mkdir()

        # 生成 30 个包含典型 Python 逻辑的代码文件模拟真实项目
        for i in range(30):
            p = src / f"service_{i}.py"
            p.write_text(f'''"""Service {i} module description."""
from typing import Dict, Any, List

class Handler{i}:
    """Handles logic for component {i}."""
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def execute_task_{i}(self, payload: List[str]) -> bool:
        """Executes task with payload."""
        for item in payload:
            print("Processing", item)
        return True

def helper_{i}(val: int) -> int:
    """Helper calculation {i}."""
    return val * {i}
''')

        # 写入测试范式
        (repo_root / "tests" / "test_service.py").write_text("""
import pytest
from unittest.mock import MagicMock

def test_handler():
    assert True
""")

        # 构造一个模拟真实 Bug 的 Issue
        issue_title = "Unhandled exception in Handler17 execute_task_17"
        issue_body = """
When passing an empty list to execute_task_17, an unhandled error is raised:
Traceback (most recent call last):
  File "requests_mock_repo/service_17.py", line 12, in execute_task_17
    raise IndexError("list index out of range")
IndexError: list index out of range
"""

        print(f"📦 Simulated Repository: 30 Python modules (~600 lines)")
        print(f"🎯 Target File: requests_mock_repo/service_17.py")

        start_time = time.perf_counter()
        slice_result = engine.analyze_repository(
            repo_root=repo_root,
            issue_title=issue_title,
            issue_body=issue_body,
            max_tokens_budget=3000,
            max_files_limit=8,
        )
        duration_ms = (time.perf_counter() - start_time) * 1000

        print("\n📊 Benchmark Results:")
        print(f"  ⏱️  Analysis Latency : {duration_ms:.2f} ms (Target: < 2000 ms)")
        print(f"  🏷️  Tokens Consumed  : {slice_result.total_tokens} tokens (Target: < 3000 tokens)")
        print(f"  📂 Files Recalled   : {len(slice_result.primary_files)} files")

        target_recalled = any("service_17.py" in f.path for f in slice_result.primary_files)
        target_top1 = slice_result.primary_files[0].path if slice_result.primary_files else None
        print(f"  🎯 Target Recalled  : {'✅ YES' if target_recalled else '❌ NO'}")
        print(f"  🥇 Top-1 File Hit   : {target_top1}")
        print(f"  🧪 Test Framework   : {slice_result.test_patterns.framework}")

        assert duration_ms < 2000, "Performance failure: Latency exceeded 2000 ms"
        assert slice_result.total_tokens <= 3000, "Budget failure: Tokens exceeded 3000"
        assert target_recalled, "Recall failure: Target file was not recalled"
        print("\n🎉 Sprint 1 Milestone Benchmark PASSED perfectly!\n")


if __name__ == "__main__":
    run_benchmark()
