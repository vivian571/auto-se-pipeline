"""
Context Engine (v1.1) for Auto-SE Pipeline
Performs multi-language AST pruning (Python & TypeScript), traceback extraction,
dependency closure expansion, test pattern extraction, and budget-constrained token slicing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import tiktoken
import tree_sitter
import tree_sitter_python
import tree_sitter_typescript
from pydantic import BaseModel, Field


# ============================================================================
# Data Models
# ============================================================================

class FileContext(BaseModel):
    path: str
    relevance_score: float
    is_traceback_source: bool = False
    skeleton: str
    token_count: int


class TestPatterns(BaseModel):
    framework: str = "unknown"  # pytest, unittest, jest, vitest
    imports: List[str] = Field(default_factory=list)
    fixtures_or_mocks: List[str] = Field(default_factory=list)
    sample_assertion: Optional[str] = None


class ContextSlice(BaseModel):
    target_repo: str
    primary_files: List[FileContext]
    test_patterns: TestPatterns
    total_tokens: int
    rendered_markdown: str


# ============================================================================
# Multi-Language AST Pruner
# ============================================================================

class ASTPruner:
    """Prunes source code into lightweight structural skeletons with signatures & docstrings."""

    def __init__(self):
        self.py_language = tree_sitter.Language(tree_sitter_python.language())
        self.py_parser = tree_sitter.Parser(self.py_language)

        self.ts_language = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        self.ts_parser = tree_sitter.Parser(self.ts_language)

    def prune_python(self, code_bytes: bytes) -> str:
        tree = self.py_parser.parse(code_bytes)
        root = tree.root_node
        lines = code_bytes.splitlines()

        pruned_lines: List[str] = []

        def extract_first_docstring_line(body_node: tree_sitter.Node) -> Optional[str]:
            for child in body_node.children:
                if child.type == "expression_statement":
                    for sub in child.children:
                        if sub.type == "string":
                            text = sub.text.decode("utf-8", errors="replace").strip('"""\'\'\' \n\r\t')
                            first_line = text.splitlines()[0] if text else ""
                            return first_line.strip()
            return None

        def visit(node: tree_sitter.Node, indent_level: int = 0):
            indent = "    " * indent_level
            for child in node.children:
                if child.type == "comment":
                    continue
                if child.type in ("import_statement", "import_from_statement"):
                    stmt = child.text.decode("utf-8", errors="replace").strip()
                    pruned_lines.append(f"{indent}{stmt}")
                elif child.type == "class_definition":
                    name_node = child.child_by_field_name("name")
                    super_node = child.child_by_field_name("superclasses")
                    name = name_node.text.decode("utf-8", errors="replace") if name_node else "UnknownClass"
                    super_text = super_node.text.decode("utf-8", errors="replace") if super_node else ""
                    # super_text 本身如果已经带括号则直接拼，否则加括号
                    if super_text:
                        supers = super_text if super_text.startswith("(") else f"({super_text})"
                    else:
                        supers = ""
                    pruned_lines.append(f"\n{indent}class {name}{supers}:")

                    body_node = child.child_by_field_name("body")
                    if body_node:
                        doc = extract_first_docstring_line(body_node)
                        if doc:
                            pruned_lines.append(f'{indent}    """{doc}"""')
                        visit(body_node, indent_level + 1)
                        if not any(c.type in ("function_definition", "class_definition") for c in body_node.children):
                            pruned_lines.append(f"{indent}    ...")
                elif child.type == "function_definition":
                    name_node = child.child_by_field_name("name")
                    params_node = child.child_by_field_name("parameters")
                    return_node = child.child_by_field_name("return_type")

                    name = name_node.text.decode("utf-8", errors="replace") if name_node else "func"
                    params = params_node.text.decode("utf-8", errors="replace") if params_node else "()"
                    ret = f" -> {return_node.text.decode('utf-8', errors='replace')}" if return_node else ""

                    pruned_lines.append(f"{indent}def {name}{params}{ret}:")
                    body_node = child.child_by_field_name("body")
                    if body_node:
                        doc = extract_first_docstring_line(body_node)
                        if doc:
                            pruned_lines.append(f'{indent}    """{doc}"""')
                    pruned_lines.append(f"{indent}    ...")
                elif child.type in ("decorated_definition",):
                    # 包含 @property, @staticmethod 等
                    decorator_texts = []
                    actual_def = None
                    for sub in child.children:
                        if sub.type == "decorator":
                            decorator_texts.append(sub.text.decode("utf-8", errors="replace").strip())
                        elif sub.type in ("function_definition", "class_definition"):
                            actual_def = sub
                    for dec in decorator_texts:
                        pruned_lines.append(f"{indent}{dec}")
                    if actual_def:
                        # 暂时复用内部处理
                        sub_node = actual_def
                        if sub_node.type == "function_definition":
                            name_node = sub_node.child_by_field_name("name")
                            params_node = sub_node.child_by_field_name("parameters")
                            return_node = sub_node.child_by_field_name("return_type")
                            name = name_node.text.decode("utf-8", errors="replace") if name_node else "func"
                            params = params_node.text.decode("utf-8", errors="replace") if params_node else "()"
                            ret = f" -> {return_node.text.decode('utf-8', errors='replace')}" if return_node else ""
                            pruned_lines.append(f"{indent}def {name}{params}{ret}:")
                            body_node = sub_node.child_by_field_name("body")
                            if body_node:
                                doc = extract_first_docstring_line(body_node)
                                if doc:
                                    pruned_lines.append(f'{indent}    """{doc}"""')
                            pruned_lines.append(f"{indent}    ...")

        visit(root)
        return "\n".join(pruned_lines)

    def prune_typescript(self, code_bytes: bytes) -> str:
        tree = self.ts_parser.parse(code_bytes)
        root = tree.root_node
        pruned_lines: List[str] = []

        def visit(node: tree_sitter.Node, indent_level: int = 0):
            indent = "  " * indent_level
            for child in node.children:
                if child.type in ("import_statement", "type_alias_declaration", "interface_declaration"):
                    # 保留完整 interface 和 import 签名
                    stmt = child.text.decode("utf-8", errors="replace").strip()
                    pruned_lines.append(f"{indent}{stmt}")
                elif child.type == "export_statement":
                    # 递归解构 export 里的内容
                    for sub in child.children:
                        if sub.type in ("function_declaration", "class_declaration", "interface_declaration", "type_alias_declaration"):
                            pruned_lines.append(f"{indent}export ...")
                            visit(child, indent_level)
                            break
                elif child.type == "function_declaration":
                    name_node = child.child_by_field_name("name")
                    params_node = child.child_by_field_name("parameters")
                    return_node = child.child_by_field_name("return_type")
                    name = name_node.text.decode("utf-8", errors="replace") if name_node else "func"
                    params = params_node.text.decode("utf-8", errors="replace") if params_node else "()"
                    ret_text = return_node.text.decode("utf-8", errors="replace") if return_node else ""
                    if ret_text:
                        ret = ret_text if ret_text.startswith(":") else f": {ret_text}"
                    else:
                        ret = ""
                    pruned_lines.append(f"{indent}function {name}{params}{ret} {{ ... }}")
                elif child.type == "class_declaration":
                    name_node = child.child_by_field_name("name")
                    name = name_node.text.decode("utf-8", errors="replace") if name_node else "Class"
                    pruned_lines.append(f"\n{indent}class {name} {{")
                    body_node = child.child_by_field_name("body")
                    if body_node:
                        for member in body_node.children:
                            if member.type == "method_definition":
                                m_name = member.child_by_field_name("name")
                                m_params = member.child_by_field_name("parameters")
                                m_ret = member.child_by_field_name("return_type")
                                mn = m_name.text.decode("utf-8", errors="replace") if m_name else "method"
                                mp = m_params.text.decode("utf-8", errors="replace") if m_params else "()"
                                mr_text = m_ret.text.decode("utf-8", errors="replace") if m_ret else ""
                                if mr_text:
                                    mr = mr_text if mr_text.startswith(":") else f": {mr_text}"
                                else:
                                    mr = ""
                                pruned_lines.append(f"{indent}  {mn}{mp}{mr} {{ ... }}")
                    pruned_lines.append(f"{indent}}}")

        visit(root)
        return "\n".join(pruned_lines)

    def prune(self, file_path: str, code_bytes: bytes) -> str:
        if file_path.endswith((".py", ".pyi")):
            return self.prune_python(code_bytes)
        elif file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
            return self.prune_typescript(code_bytes)
        # 默认不修剪或做简单前100行截断
        text = code_bytes.decode("utf-8", errors="replace")
        lines = text.splitlines()[:80]
        return "\n".join(lines)


# ============================================================================
# Traceback & Path Extractor
# ============================================================================

class TracebackExtractor:
    """Extracts file paths, line numbers and exception signatures from issue text."""

    # Python stack trace regex: File "path/to/file.py", line 123
    PY_STACK_PATTERN = re.compile(r'File\s+["\']([^"\']+\.py)["\']\s*,\s*line\s+(\d+)', re.IGNORECASE)
    # Generic path matching: e.g. src/foo/bar.py or src/foo/bar.ts
    GENERIC_PATH_PATTERN = re.compile(r'(?:[a-zA-Z0-9_\-.]+/)+[a-zA-Z0-9_\-.]+\.(?:py|ts|tsx|js)', re.IGNORECASE)
    # Exception type pattern: e.g. KeyError: 'foo' or TypeError
    EXCEPTION_PATTERN = re.compile(r'([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning|Fault))(?::\s*(.*))?')

    @classmethod
    def extract_traces(cls, text: str, repo_root: Path) -> List[Tuple[str, int]]:
        results: List[Tuple[str, int]] = []
        # 1. 精准抓取 Python 堆栈行
        for match in cls.PY_STACK_PATTERN.finditer(text):
            raw_path = match.group(1)
            line_no = int(match.group(2))
            rel_path = cls._normalize_to_repo(raw_path, repo_root)
            if rel_path:
                results.append((rel_path, line_no))

        # 2. 匹配可能裸露在正文中的路径
        for match in cls.GENERIC_PATH_PATTERN.finditer(text):
            raw_path = match.group(0)
            rel_path = cls._normalize_to_repo(raw_path, repo_root)
            if rel_path and not any(r[0] == rel_path for r in results):
                results.append((rel_path, 1))

        return results

    @classmethod
    def extract_exceptions(cls, text: str) -> List[str]:
        exceptions = []
        for match in cls.EXCEPTION_PATTERN.finditer(text):
            exc = match.group(0).strip()
            if exc not in exceptions:
                exceptions.append(exc)
        return exceptions

    @staticmethod
    def _normalize_to_repo(raw_path: str, repo_root: Path) -> Optional[str]:
        p = Path(raw_path)
        # 尝试直接相对路径
        if (repo_root / p).is_file():
            return p.as_posix()
        # 尝试匹配路径结尾（针对包含 /home/runner/work/... 绝对路径的情况）
        parts = p.parts
        for i in range(len(parts)):
            candidate = Path(*parts[i:])
            if (repo_root / candidate).is_file():
                return candidate.as_posix()
        return None


# ============================================================================
# Keyword & Dependency Scorer
# ============================================================================

class ContextEngine:
    """Orchestrates source discovery, ranking, AST pruning, and context slicing."""

    def __init__(self, token_encoding: str = "cl100k_base"):
        self.pruner = ASTPruner()
        self.tokenizer = None
        try:
            self.tokenizer = tiktoken.get_encoding(token_encoding)
        except Exception:
            try:
                self.tokenizer = tiktoken.encoding_for_model("gpt-4o")
            except Exception:
                self.tokenizer = None

    def count_tokens(self, text: str) -> int:
        if self.tokenizer is not None:
            try:
                return len(self.tokenizer.encode(text))
            except Exception:
                pass
        # 离线启发式分词估算：按 4 字符或单词统计混合
        words = len(re.findall(r'\w+|[^\w\s]', text))
        char_estimate = len(text) // 4
        return max(words, char_estimate)

    def analyze_repository(
        self,
        repo_root: Path | str,
        issue_title: str,
        issue_body: str,
        max_tokens_budget: int = 3000,
        max_files_limit: int = 8,
    ) -> ContextSlice:
        repo_path = Path(repo_root)
        full_issue_text = f"{issue_title}\n{issue_body}"

        # 1. 提取 Traceback 与显式文件路径
        trace_hits = TracebackExtractor.extract_traces(full_issue_text, repo_path)
        trace_files = {path: line for path, line in trace_hits}

        # 2. 提取实体关键字（CamelCase, snake_case, 错误名）
        keywords = set(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{3,}', full_issue_text))
        # 排除普遍停用词
        stopwords = {"this", "that", "with", "from", "have", "issue", "error", "test", "when", "using", "expected", "actual"}
        keywords = {k.lower() for k in keywords if k.lower() not in stopwords}

        # 3. 扫描源码并打分
        scored_files: List[Tuple[str, float, bool]] = []
        for root, dirs, files in os.walk(repo_path):
            # 过滤不需要的目录
            dirs[:] = [d for d in dirs if d not in {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache"}]
            for file in files:
                if not file.endswith((".py", ".ts", ".js")):
                    continue
                file_full = Path(root) / file
                rel_path = file_full.relative_to(repo_path).as_posix()

                # 跳过测试文件（测试文件单独由 TestPatternExtractor 处理）
                if "test" in rel_path.lower():
                    continue

                score = 0.0
                is_trace = False

                # 命中堆栈：最高优先级
                if rel_path in trace_files:
                    score += 5.0
                    is_trace = True

                # 命中文件名中的关键字
                path_lower = rel_path.lower()
                for kw in keywords:
                    if kw in path_lower:
                        score += 1.5

                # 快速抽样文件内容匹配
                try:
                    with open(file_full, "r", encoding="utf-8", errors="ignore") as f:
                        header_sample = f.read(4096).lower()
                        match_count = sum(1 for kw in keywords if kw in header_sample)
                        score += min(match_count * 0.3, 3.0)
                except Exception:
                    pass

                if score > 0:
                    scored_files.append((rel_path, score, is_trace))

        # 按得分从高到低排序，优先保留堆栈文件
        scored_files.sort(key=lambda x: (x[2], x[1]), reverse=True)
        top_candidates = scored_files[:max_files_limit]

        # 4. AST 骨架剪枝与 Token 控制
        primary_files: List[FileContext] = []
        current_tokens = 0

        for rel_path, score, is_trace in top_candidates:
            abs_path = repo_path / rel_path
            try:
                with open(abs_path, "rb") as f:
                    raw_bytes = f.read()
                skeleton = self.pruner.prune(rel_path, raw_bytes)
                file_tokens = self.count_tokens(skeleton)

                # 若单个骨架过大，做二次裁剪
                if file_tokens > 800:
                    lines = skeleton.splitlines()[:60]
                    skeleton = "\n".join(lines) + "\n... [truncated]"
                    file_tokens = self.count_tokens(skeleton)

                if current_tokens + file_tokens > max_tokens_budget:
                    # 预算耗尽，停止纳入更多文件
                    break

                primary_files.append(
                    FileContext(
                        path=rel_path,
                        relevance_score=score,
                        is_traceback_source=is_trace,
                        skeleton=skeleton,
                        token_count=file_tokens,
                    )
                )
                current_tokens += file_tokens
            except Exception:
                continue

        # 5. 提取测试范式
        test_patterns = self._extract_test_patterns(repo_path)

        # 6. 渲染统一的 Markdown 上下文切片
        rendered_md = self._render_slice_markdown(repo_path.name, primary_files, test_patterns, full_issue_text)

        total_tokens = self.count_tokens(rendered_md)

        return ContextSlice(
            target_repo=repo_path.name,
            primary_files=primary_files,
            test_patterns=test_patterns,
            total_tokens=total_tokens,
            rendered_markdown=rendered_md,
        )

    def _extract_test_patterns(self, repo_path: Path) -> TestPatterns:
        patterns = TestPatterns()
        # 寻找 tests/ 下的第一个测试文件
        test_candidates = list(repo_path.glob("**/test_*.py")) or list(repo_path.glob("**/*.test.ts"))
        if not test_candidates:
            return patterns

        sample_file = test_candidates[0]
        if sample_file.suffix == ".py":
            patterns.framework = "pytest"
            try:
                with open(sample_file, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()[:40]
                imports = [line.strip() for line in lines if line.startswith(("import ", "from "))]
                patterns.imports = imports[:8]
                patterns.fixtures_or_mocks = [
                    line.strip() for line in lines if "@pytest.fixture" in line or "mock" in line.lower()
                ][:3]
                patterns.sample_assertion = "assert result == expected"
            except Exception:
                pass
        elif sample_file.suffix in (".ts", ".js"):
            patterns.framework = "vitest/jest"
            patterns.imports = ["import { describe, it, expect } from 'vitest';"]
            patterns.sample_assertion = "expect(result).toBe(expected);"

        return patterns

    def _render_slice_markdown(
        self,
        repo_name: str,
        files: List[FileContext],
        test_patterns: TestPatterns,
        issue_text: str,
    ) -> str:
        md = [
            f"# Context Slice for Repository: `{repo_name}`",
            "",
            "## 1. Issue Information",
            "```text",
            issue_text.strip()[:1000],
            "```",
            "",
            "## 2. Test Paradigm & Framework",
            f"- **Framework**: `{test_patterns.framework}`",
            "- **Sample Imports**:",
            "```python" if test_patterns.framework == "pytest" else "```typescript",
            "\n".join(test_patterns.imports) if test_patterns.imports else "# Standard testing library",
            "```",
            f"- **Assertion Style**: `{test_patterns.sample_assertion}`",
            "",
            "## 3. High-Relevance Code Skeletons (AST Pruned)",
        ]

        for fc in files:
            source_tag = " (Traceback Match)" if fc.is_traceback_source else ""
            lang = "python" if fc.path.endswith(".py") else "typescript"
            md.extend(
                [
                    f"### `{fc.path}` (Relevance: {fc.relevance_score:.1f}){source_tag}",
                    f"```{lang}",
                    fc.skeleton,
                    "```",
                    "",
                ]
            )

        return "\n".join(md)
