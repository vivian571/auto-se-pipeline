import tempfile
from pathlib import Path
import pytest
from core.context_engine import ASTPruner, ContextEngine, TracebackExtractor


def test_ast_pruner_python():
    pruner = ASTPruner()
    sample_py = b"""
import os
import sys
from typing import Optional

class UserManager(BaseManager):
    \"\"\"Manages users and their credentials.
    Additional detail that should be pruned.
    \"\"\"
    
    @property
    def admin_role(self) -> str:
        \"\"\"Return admin role string.\"\"\"
        return "ADMIN"
        
    def find_user(self, user_id: int) -> Optional[dict]:
        \"\"\"Find user by ID.\"\"\"
        user = self.db.query(user_id)
        if not user:
            raise ValueError("not found")
        return user.to_dict()

def standalone_helper(x: int, y: int) -> int:
    return x + y
"""
    pruned = pruner.prune("user_mgr.py", sample_py)
    assert "class UserManager(BaseManager):" in pruned
    assert '"""Manages users and their credentials.' in pruned
    assert "Additional detail that should be pruned." not in pruned
    assert "def find_user(self, user_id: int) -> Optional[dict]:" in pruned
    assert "def standalone_helper(x: int, y: int) -> int:" in pruned
    assert "user = self.db.query(user_id)" not in pruned
    assert "..." in pruned


def test_ast_pruner_typescript():
    pruner = ASTPruner()
    sample_ts = b"""
import { Request, Response } from 'express';

export interface UserPayload {
    id: string;
    email: string;
}

export class AuthService {
    verifyToken(token: string): boolean {
        const decoded = jwt.decode(token);
        return decoded != null;
    }
}

function processRequest(req: Request): UserPayload {
    const user = req.body.user;
    return { id: user.id, email: user.email };
}
"""
    pruned = pruner.prune("auth.ts", sample_ts)
    assert "interface UserPayload" in pruned
    assert "class AuthService" in pruned
    assert "verifyToken(token: string)" in pruned
    assert "function processRequest(req: Request): UserPayload" in pruned
    assert "jwt.decode(token)" not in pruned


def test_traceback_extractor():
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        src_dir = repo_root / "src" / "api"
        src_dir.mkdir(parents=True)
        target_file = src_dir / "handler.py"
        target_file.write_text("def handle(): pass\n")

        issue_text = """
        When calling `execute()`, we hit an unhandled exception:
        Traceback (most recent call last):
          File "src/api/handler.py", line 42, in handle
            raise KeyError("missing token")
        KeyError: 'missing token'
        """

        traces = TracebackExtractor.extract_traces(issue_text, repo_root)
        assert len(traces) == 1
        assert traces[0][0] == "src/api/handler.py"
        assert traces[0][1] == 42

        exceptions = TracebackExtractor.extract_exceptions(issue_text)
        assert any("KeyError" in e for e in exceptions)


def test_context_engine_budget_limit():
    engine = ContextEngine()
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        (repo_root / "src").mkdir()
        (repo_root / "tests").mkdir()

        # 创建样本文件
        for i in range(10):
            p = repo_root / "src" / f"module_{i}.py"
            p.write_text(f"""
class Service{i}:
    def run_action_{i}(self):
        \"\"\"Runs action {i}\"\"\"
        x = {i} * 10
        return x
""")

        # 创建测试用例
        test_p = repo_root / "tests" / "test_sample.py"
        test_p.write_text("""
import pytest
from unittest.mock import MagicMock

def test_service():
    assert True
""")

        issue_title = "Crash in Service2 when running action"
        issue_body = "Traceback:\n  File 'src/module_2.py', line 12, in run_action_2\nValueError: invalid"

        slice_result = engine.analyze_repository(
            repo_root=repo_root,
            issue_title=issue_title,
            issue_body=issue_body,
            max_tokens_budget=2000,
        )

        assert slice_result.total_tokens <= 2000
        assert slice_result.test_patterns.framework == "pytest"
        # 命中堆栈的 module_2 应该有最高优先级且标记 is_traceback_source
        matched_module2 = [f for f in slice_result.primary_files if "module_2.py" in f.path]
        assert len(matched_module2) == 1
        assert matched_module2[0].is_traceback_source is True
