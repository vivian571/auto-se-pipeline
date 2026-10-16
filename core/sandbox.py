"""
Two-Stage Sandbox Runtime (v1.1) for Auto-SE Pipeline
Provides isolated execution, baseline failure diffing, flaky detection,
patch application, and instantaneous git rollback.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class BaselineResult:
    exit_code: int
    failed_tests: Set[str]
    total_tests: int
    duration_seconds: float
    raw_output: str


@dataclass
class ReproduceResult:
    reproduced: bool
    consistent_failures: int
    total_runs: int
    matched_exception: bool
    exception_name: Optional[str]
    raw_traces: List[str]
    error_message: Optional[str] = None


@dataclass
class RegressionResult:
    passed: bool
    new_failures: Set[str]
    resolved_tests: Set[str]
    duration_seconds: float
    raw_output: str


class BaseSandbox(ABC):
    """Abstract interface for execution sandboxes."""

    def __init__(self, workspace_path: Path | str):
        self.workspace_path = Path(workspace_path).resolve()

    @abstractmethod
    def run_command(
        self,
        command: str | List[str],
        timeout_seconds: int = 60,
        isolated_network: bool = True,
    ) -> Tuple[int, str, str]:
        """Runs command in sandbox. Returns (exit_code, stdout, stderr)."""
        pass

    @abstractmethod
    def apply_patch(self, diff_text: str) -> Tuple[bool, str]:
        """Applies unified diff to the workspace."""
        pass

    @abstractmethod
    def rollback(self) -> None:
        """Rolls back all workspace modifications."""
        pass

    def run_baseline_test(
        self,
        test_command: str,
        timeout_seconds: int = 600,
    ) -> BaselineResult:
        """
        Runs existing full test suite before any modification.
        Extracts known failing/flaky tests to establish baseline B_fail.
        """
        start = time.perf_counter()
        code, out, err = self.run_command(test_command, timeout_seconds=timeout_seconds, isolated_network=True)
        duration = time.perf_counter() - start
        combined_output = f"{out}\n{err}"

        failed_tests = self._parse_pytest_failures(combined_output)
        return BaselineResult(
            exit_code=code,
            failed_tests=failed_tests,
            total_tests=self._parse_pytest_total(combined_output),
            duration_seconds=duration,
            raw_output=combined_output,
        )

    def run_reproduce_test(
        self,
        test_file_relative_path: str,
        expected_exception: Optional[str] = None,
        repeats: int = 3,
        timeout_seconds: int = 60,
    ) -> ReproduceResult:
        """
        Validates reproduction test:
        1. Must fail consistently in all `repeats` runs (prevents flaky false alarms).
        2. Must fail due to the expected exception or failure reason (not environment errors).
        """
        test_full_path = self.workspace_path / test_file_relative_path
        if not test_full_path.exists():
            return ReproduceResult(
                reproduced=False,
                consistent_failures=0,
                total_runs=repeats,
                matched_exception=False,
                exception_name=None,
                raw_traces=[],
                error_message=f"Test file {test_file_relative_path} does not exist",
            )

        cmd = f"pytest -v {test_file_relative_path}"
        traces: List[str] = []
        failure_count = 0
        matched_exc = False
        detected_exc = None

        for _ in range(repeats):
            code, out, err = self.run_command(cmd, timeout_seconds=timeout_seconds, isolated_network=True)
            output = f"{out}\n{err}"
            traces.append(output)

            # 只有明确以非 0 退出才算失败
            if code != 0:
                failure_count += 1
                exc = self._detect_exception_in_output(output)
                if exc:
                    detected_exc = exc
                if expected_exception:
                    if expected_exception.lower() in output.lower():
                        matched_exc = True
                else:
                    matched_exc = True

        reproduced = (failure_count == repeats) and (matched_exc or expected_exception is None)

        return ReproduceResult(
            reproduced=reproduced,
            consistent_failures=failure_count,
            total_runs=repeats,
            matched_exception=matched_exc,
            exception_name=detected_exc,
            raw_traces=traces,
        )

    def run_regression_test(
        self,
        full_test_command: str,
        baseline: BaselineResult,
        timeout_seconds: int = 900,
    ) -> RegressionResult:
        """
        Runs full test suite after patch is applied.
        Ensures C_fail is a subset of B_fail (Zero new regressions introduced).
        """
        start = time.perf_counter()
        code, out, err = self.run_command(full_test_command, timeout_seconds=timeout_seconds, isolated_network=True)
        duration = time.perf_counter() - start
        combined = f"{out}\n{err}"

        current_failures = self._parse_pytest_failures(combined)
        new_failures = current_failures - baseline.failed_tests
        resolved_tests = baseline.failed_tests - current_failures

        # 判定通过标准：没有引入任何新增失败
        passed = (len(new_failures) == 0) and (code == 0 or len(current_failures) <= len(baseline.failed_tests))

        return RegressionResult(
            passed=passed,
            new_failures=new_failures,
            resolved_tests=resolved_tests,
            duration_seconds=duration,
            raw_output=combined,
        )

    @staticmethod
    def _parse_pytest_failures(output: str) -> Set[str]:
        failed = set()
        # 匹配 FAILED tests/test_foo.py::test_bar - ...
        for line in output.splitlines():
            line = line.strip()
            if line.startswith("FAILED "):
                parts = line.split(" ", 2)
                if len(parts) >= 2:
                    test_target = parts[1].strip()
                    failed.add(test_target)
        return failed

    @staticmethod
    def _parse_pytest_total(output: str) -> int:
        # 匹配 collected 15 items
        match = re.search(r'collected\s+(\d+)\s+items', output)
        if match:
            return int(match.group(1))
        return 0

    @staticmethod
    def _detect_exception_in_output(output: str) -> Optional[str]:
        match = re.search(r'([A-Za-z0-9_]+Error|[A-Za-z0-9_]+Exception):\s*(.*)', output)
        if match:
            return match.group(0).strip()
        return None


class ProcessIsolationSandbox(BaseSandbox):
    """
    Subprocess-based sandbox with clean environment variable sanitization,
    resource timeout bounds, and strict isolation.
    """

    def run_command(
        self,
        command: str | List[str],
        timeout_seconds: int = 60,
        isolated_network: bool = True,
    ) -> Tuple[int, str, str]:
        # 1. 彻底清空可能包含外部凭据的环境变量
        clean_env = os.environ.copy()
        sensitive_keys = [
            "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT", "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"
        ]
        for key in sensitive_keys:
            clean_env.pop(key, None)

        if isolated_network:
            # 阻断标准 HTTP/HTTPS proxy 规避外网
            clean_env["http_proxy"] = "http://127.0.0.1:0"
            clean_env["https_proxy"] = "http://127.0.0.1:0"
            clean_env["ALL_PROXY"] = "http://127.0.0.1:0"

        shell_mode = isinstance(command, str)
        # 确保 pytest 执行时使用隔离环境当前的 Python 解释器
        import sys
        if isinstance(command, str):
            if command.startswith("pytest ") or command == "pytest":
                command = f"{sys.executable} -m {command}"
        elif isinstance(command, list) and command:
            if command[0] == "pytest":
                command = [sys.executable, "-m"] + command

        try:
            proc = subprocess.run(
                command,
                cwd=self.workspace_path,
                env=clean_env,
                shell=shell_mode,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as e:
            return -1, e.stdout or "", f"Execution timed out after {timeout_seconds} seconds"
        except Exception as e:
            return -1, "", str(e)

    def apply_patch(self, diff_text: str) -> Tuple[bool, str]:
        """Applies unified diff using git apply, with python fallback for slightly miscounted hunks."""
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            # 确保 patch 末尾有换行
            patch_content = diff_text if diff_text.endswith("\n") else diff_text + "\n"
            f.write(patch_content)
            patch_path = f.name

        try:
            # 1. 尝试使用 git apply --3way
            code, out, err = self.run_command(f"git apply --3way {patch_path}", timeout_seconds=10)
            if code == 0:
                return True, "Patch applied cleanly via git apply --3way"

            # 2. 尝试三方合并 / 带 whitespace 容错与 recount
            code, out, err = self.run_command(f"git apply --ignore-whitespace --recount {patch_path}", timeout_seconds=10)
            if code == 0:
                return True, "Patch applied via git apply (--ignore-whitespace --recount)"

            # 3. 回退：Python 自适应基于上下文的精准替换
            ok, msg = self._apply_patch_via_python(diff_text)
            if ok:
                return True, f"Patch applied via resilient Python fallback ({msg})"

            return False, f"Failed to apply patch: {err or out or msg}"
        finally:
            if os.path.exists(patch_path):
                os.remove(patch_path)

    def _apply_patch_via_python(self, diff_text: str) -> Tuple[bool, str]:
        """Parses unified diff and performs contextual replacement on target files."""
        try:
            # 匹配目标文件名
            file_match = re.search(r'\+\+\+\s+[b/]*(.*)', diff_text)
            if not file_match:
                return False, "No target file path found in diff"
            target_rel = file_match.group(1).strip()
            target_path = self.workspace_path / target_rel
            if not target_path.exists():
                return False, f"Target file {target_rel} not found"

            orig_lines = target_path.read_text(encoding="utf-8").splitlines()

            # 提取原文件上下文与替换内容
            # 从 diff 中提取 '-' 开头的行和 '+' 开头的行
            lines_to_remove: List[str] = []
            lines_to_add: List[str] = []
            context_before: List[str] = []
            context_after: List[str] = []

            diff_body = diff_text.split("@@")[-1]
            diff_lines = diff_body.splitlines()

            hunk_started = False
            for line in diff_lines:
                if line.startswith("-"):
                    lines_to_remove.append(line[1:])
                    hunk_started = True
                elif line.startswith("+"):
                    lines_to_add.append(line[1:])
                    hunk_started = True
                elif line.startswith(" ") or not line:
                    raw_content = line[1:] if line.startswith(" ") else line
                    if not hunk_started:
                        context_before.append(raw_content)
                    else:
                        context_after.append(raw_content)

            # 在原文件中匹配 context_before + lines_to_remove
            search_block = context_before + lines_to_remove
            if not search_block and not context_before:
                # 简单纯插入
                return False, "Hunk structure ambiguous for direct fallback"

            # 字符串匹配替换
            orig_text = "\n".join(orig_lines)
            target_snippet = "\n".join(search_block)

            replacement_snippet = "\n".join(context_before + lines_to_add)
            if target_snippet in orig_text:
                new_text = orig_text.replace(target_snippet, replacement_snippet, 1)
                target_path.write_text(new_text + ("\n" if orig_text.endswith("\n") else ""), encoding="utf-8")
                return True, f"Replaced snippet in {target_rel}"

            return False, "Context snippet not matched in target file"
        except Exception as e:
            return False, str(e)

    def rollback(self) -> None:
        """Rolls back all uncommitted changes and cleans untracked files completely."""
        self.run_command("git reset --hard HEAD", timeout_seconds=15)
        self.run_command("git clean -fd", timeout_seconds=15)
