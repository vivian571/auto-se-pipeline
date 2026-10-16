"""
Swarm Orchestrator for Auto-SE Pipeline (Sprint 2 Milestone)
Connects Debugger Agent, Coder Agent, and Two-Stage Sandbox into a closed-loop
verification engine with 3-attempt retry limit and baseline diffing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from core.coder_agent import CoderAgent
from core.context_engine import ContextSlice
from core.debugger_agent import DebuggerAgent
from core.llm_provider import BaseLLMProvider, BudgetExceededError
from core.sandbox import BaseSandbox, BaselineResult, ReproduceResult, RegressionResult
from storage.database import Database, TaskStatus

logger = logging.getLogger(__name__)


class SwarmRepairExhaustedError(Exception):
    """Raised when maximum repair attempts are reached without passing regression tests."""
    pass


class ReproductionFailedError(Exception):
    """Raised when the debugger cannot produce a valid failing reproduction test."""
    pass


@dataclass
class SwarmResult:
    success: bool
    reproduced: bool
    reproduction_code: str
    patch_diff: str
    final_test_file: str
    attempts_used: int
    baseline_failed_count: int
    tokens_consumed: int
    cost_usd: float
    error_message: Optional[str] = None


class SwarmOrchestrator:
    def __init__(
        self,
        sandbox: BaseSandbox,
        llm_provider: BaseLLMProvider,
        database: Optional[Database] = None,
        max_attempts: int = 3,
    ):
        self.sandbox = sandbox
        self.llm = llm_provider
        self.db = database
        self.max_attempts = max_attempts
        self.debugger = DebuggerAgent(llm_provider)
        self.coder = CoderAgent(llm_provider)

    def run_repair_swarm(
        self,
        task_id: str,
        issue_number: int,
        issue_title: str,
        issue_body: str,
        context_slice: ContextSlice,
        expected_exception: Optional[str] = None,
        full_test_cmd: str = "pytest -q",
    ) -> SwarmResult:
        """
        Executes the full closed-loop repair swarm.
        """
        # 1. 跑原始基线测试
        if self.db:
            self.db.transition_task_status(task_id, TaskStatus.BASELINE_TESTING, "Running baseline test suite")

        baseline = self.sandbox.run_baseline_test(full_test_cmd)
        logger.info(f"Baseline completed: {len(baseline.failed_tests)} existing failures out of {baseline.total_tests} tests")

        # 2. Debugger Agent 编写复现测试
        if self.db:
            self.db.transition_task_status(task_id, TaskStatus.TEST_REPRODUCING, "Debugger writing reproduction test")

        repro_test_rel_path = "tests/reproduce_issue.py"
        repro_test_full = self.sandbox.workspace_path / repro_test_rel_path
        repro_code = self.debugger.generate_reproduction_test(
            issue_title=issue_title,
            issue_body=issue_body,
            context_slice=context_slice,
        )

        repro_test_full.parent.mkdir(parents=True, exist_ok=True)
        repro_test_full.write_text(repro_code, encoding="utf-8")

        # 验证复现测试是否在未修补代码上有效失败 3 次
        repro_result = self.sandbox.run_reproduce_test(
            test_file_relative_path=repro_test_rel_path,
            expected_exception=expected_exception,
            repeats=3,
        )

        if not repro_result.reproduced:
            msg = f"Debugger failed to produce a valid failing test. Failures={repro_result.consistent_failures}/3, MatchedException={repro_result.matched_exception}"
            if self.db:
                self.db.transition_task_status(task_id, TaskStatus.ABORTED_CANNOT_REPRODUCE, msg)
            self.sandbox.rollback()
            return SwarmResult(
                success=False,
                reproduced=False,
                reproduction_code=repro_code,
                patch_diff="",
                final_test_file="",
                attempts_used=0,
                baseline_failed_count=len(baseline.failed_tests),
                tokens_consumed=self.llm.total_tokens_consumed,
                cost_usd=self.llm.total_cost_usd,
                error_message=msg,
            )

        logger.info("Reproduction test successfully failed 3 consecutive times with matching exception!")

        # 3. Coder Agent 对抗闭环修复 (最多重试 3 次)
        active_rules = []
        if self.db:
            active_rules = self.db.get_active_rules_for_repo(context_slice.target_repo)
            self.db.transition_task_status(task_id, TaskStatus.CODE_REPAIRING, "Coder attempting fix")

        last_feedback = None
        applied_diff = ""
        success = False

        failure_trace_sample = repro_result.raw_traces[0] if repro_result.raw_traces else "Failed with exit code"

        for attempt in range(1, self.max_attempts + 1):
            logger.info(f"--- Starting Repair Attempt {attempt}/{self.max_attempts} ---")

            if self.db:
                self.db.update_task_metrics(
                    task_id=task_id,
                    add_tokens=0,
                    add_cost_usd=0,
                    retry_count=attempt,
                )

            # 生成补丁
            try:
                diff_patch = self.coder.generate_patch(
                    issue_title=issue_title,
                    issue_body=issue_body,
                    context_slice=context_slice,
                    reproduction_test_code=repro_code,
                    reproduction_failure_trace=failure_trace_sample,
                    active_rules=active_rules,
                    previous_attempt_feedback=last_feedback,
                )
            except PermissionError as e:
                last_feedback = f"Security error: {str(e)}"
                continue

            # 应用补丁
            ok, patch_msg = self.sandbox.apply_patch(diff_patch)
            if not ok:
                last_feedback = f"Patch application failed: {patch_msg}"
                self.sandbox.rollback()
                # 重新写入复现测试
                repro_test_full.write_text(repro_code, encoding="utf-8")
                continue

            # 4. 沙箱回归验证
            if self.db:
                self.db.transition_task_status(task_id, TaskStatus.SANDBOX_VALIDATING, f"Validating attempt {attempt}")

            # 检查复现测试是否变绿
            re_code, re_out, re_err = self.sandbox.run_command(f"pytest -v {repro_test_rel_path}")
            if re_code != 0:
                last_feedback = f"Reproduction test still failed after patch:\n{re_out}\n{re_err}"
                self.sandbox.rollback()
                repro_test_full.write_text(repro_code, encoding="utf-8")
                continue

            # 跑全量回归测试对比基线
            reg_result = self.sandbox.run_regression_test(full_test_cmd, baseline=baseline)
            if not reg_result.passed:
                last_feedback = (
                    f"Regression introduced {len(reg_result.new_failures)} new test failures:\n"
                    f"{list(reg_result.new_failures)[:5]}"
                )
                self.sandbox.rollback()
                repro_test_full.write_text(repro_code, encoding="utf-8")
                continue

            # 验证完全通过！
            success = True
            applied_diff = diff_patch
            break

        if not success:
            msg = f"Repair failed after {self.max_attempts} attempts. Last feedback: {last_feedback}"
            if self.db:
                self.db.transition_task_status(task_id, TaskStatus.FAILED_BUDGET_EXCEEDED, msg)
            self.sandbox.rollback()
            return SwarmResult(
                success=False,
                reproduced=True,
                reproduction_code=repro_code,
                patch_diff="",
                final_test_file="",
                attempts_used=self.max_attempts,
                baseline_failed_count=len(baseline.failed_tests),
                tokens_consumed=self.llm.total_tokens_consumed,
                cost_usd=self.llm.total_cost_usd,
                error_message=msg,
            )

        # 5. 测试转正：重构为正式回归测试文件
        final_test_rel = f"tests/test_issue_{issue_number}.py"
        final_test_path = self.sandbox.workspace_path / final_test_rel
        final_test_path.write_text(repro_code, encoding="utf-8")
        if repro_test_full.exists():
            repro_test_full.unlink()

        if self.db:
            self.db.transition_task_status(
                task_id,
                TaskStatus.AWAITING_HUMAN_APPROVAL,
                "Reproduction confirmed and regression passed. Ready for human review.",
            )

        return SwarmResult(
            success=True,
            reproduced=True,
            reproduction_code=repro_code,
            patch_diff=applied_diff,
            final_test_file=final_test_rel,
            attempts_used=attempt,
            baseline_failed_count=len(baseline.failed_tests),
            tokens_consumed=self.llm.total_tokens_consumed,
            cost_usd=self.llm.total_cost_usd,
        )
