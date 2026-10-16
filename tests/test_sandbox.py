import os
import subprocess
import tempfile
from pathlib import Path
import pytest
from core.sandbox import BaselineResult, ProcessIsolationSandbox


@pytest.fixture
def temp_git_repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True)

        # 创建基础代码和测试
        src = repo_path / "src"
        src.mkdir()
        (src / "math_ops.py").write_text("def divide(a, b):\n    return a / b\n")

        tests = repo_path / "tests"
        tests.mkdir()
        (tests / "test_math.py").write_text("""
from src.math_ops import divide

def test_divide():
    assert divide(10, 2) == 5
""")

        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True)

        yield repo_path


def test_sandbox_env_sanitization(temp_git_repo):
    os.environ["GITHUB_TOKEN"] = "secret_token_123"
    sandbox = ProcessIsolationSandbox(temp_git_repo)
    code, out, err = sandbox.run_command("python3 -c 'import os; print(os.environ.get(\"GITHUB_TOKEN\", \"NONE\"))'")
    assert out.strip() == "NONE"


def test_sandbox_patch_and_rollback(temp_git_repo):
    sandbox = ProcessIsolationSandbox(temp_git_repo)

    patch = """--- a/src/math_ops.py
+++ b/src/math_ops.py
@@ -1,2 +1,4 @@
 def divide(a, b):
+    if b == 0:
+        raise ValueError("Division by zero")
     return a / b
"""
    ok, msg = sandbox.apply_patch(patch)
    assert ok is True

    # 验证内容已被修改
    content = (temp_git_repo / "src" / "math_ops.py").read_text()
    assert "Division by zero" in content

    # 回滚
    sandbox.rollback()
    restored_content = (temp_git_repo / "src" / "math_ops.py").read_text()
    assert "Division by zero" not in restored_content


def test_sandbox_baseline_and_regression_diffing(temp_git_repo):
    sandbox = ProcessIsolationSandbox(temp_git_repo)

    # 模拟 Baseline 结果：已存在一个已知的 Flaky 测试
    baseline = BaselineResult(
        exit_code=1,
        failed_tests={"tests/test_existing.py::test_flaky"},
        total_tests=10,
        duration_seconds=1.2,
        raw_output="FAILED tests/test_existing.py::test_flaky - assert False",
    )

    # 场景 A: 跑完后没有新增失败，只有原本的 flaky 失败
    fake_output_no_regression = "FAILED tests/test_existing.py::test_flaky - assert False\n1 failed, 9 passed in 0.5s"
    current_failures = sandbox._parse_pytest_failures(fake_output_no_regression)
    new_failures = current_failures - baseline.failed_tests
    assert len(new_failures) == 0

    # 场景 B: 引入了新回归
    fake_output_with_regression = """FAILED tests/test_existing.py::test_flaky - assert False
FAILED tests/test_math.py::test_divide - AssertionError
2 failed, 8 passed in 0.5s"""
    current_failures_reg = sandbox._parse_pytest_failures(fake_output_with_regression)
    new_failures_reg = current_failures_reg - baseline.failed_tests
    assert "tests/test_math.py::test_divide" in new_failures_reg
