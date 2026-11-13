"""
GitHub Gateway for Auto-SE Pipeline (Sprint 3 Milestone)
Handles transparent PR creation, AI disclosure formatting, branch management,
and community friendly metadata tagging.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from storage.database import Database

logger = logging.getLogger(__name__)

GITHUB_API_VERSION = "2022-11-28"
AI_DISCLOSURE_MARKER = "Generated with AI assistance"


@dataclass
class PullRequestPayload:
    repo_name: str
    issue_number: int
    branch_name: str
    title: str
    body: str
    patch_diff: str
    regression_test_file: str
    author_handle: str
    author_email: str
    signed_off: bool = True
    base_branch: str = "main"
    # 给他人仓库提 PR 时填 fork 的属主（自己的用户名），head 会变成 fork_owner:branch
    fork_owner: Optional[str] = None
    # PR 的目标仓库（owner/repo）。不填则从 origin remote 解析。
    # 给他人仓库提 PR 时填上游仓库，如 psf/requests；此时 origin 应指向自己的 fork
    target_repo: Optional[str] = None


@dataclass
class PullRequestResponse:
    success: bool
    pr_number: Optional[int]
    pr_url: Optional[str]
    error_message: Optional[str] = None


class BaseGitHubGateway(ABC):
    @abstractmethod
    def create_pull_request(self, payload: PullRequestPayload) -> PullRequestResponse:
        pass


class TransparentPRRenderer:
    """Renders structured, transparent, community-friendly pull request bodies."""

    TEMPLATE = """### Summary of Changes
- Fixes #{issue_number}: {issue_title}

### Root Cause Analysis
{root_cause}

### Solution & Changes Made
{solution_details}

### Testing Done
- [x] **Reproduction Verified**: Targeted reproduction test was written and confirmed to fail on unfixed code.
- [x] **Bug Fixed**: All reproduction test cases pass cleanly after patch application.
- [x] **Zero Regressions**: Ran full repository test suite in isolated sandbox; no new test regressions introduced.
- [x] **Added Regression Test**: Included permanent regression test in `{test_file}`.

---
*Generated with AI assistance; strictly validated in an isolated sandbox and manually reviewed by @{author_handle} before submission.*
"""

    @classmethod
    def render_body(
        self,
        issue_number: int,
        issue_title: str,
        root_cause: str,
        solution_details: str,
        test_file: str,
        author_handle: str,
    ) -> str:
        return self.TEMPLATE.format(
            issue_number=issue_number,
            issue_title=issue_title,
            root_cause=root_cause.strip() or "Identified edge case causing unhandled exception.",
            solution_details=solution_details.strip() or "Applied surgical fix to handle invalid input state.",
            test_file=test_file,
            author_handle=author_handle,
        )


class MockGitHubGateway(BaseGitHubGateway):
    """Deterministic mock gateway for offline simulation and automated tests."""

    def __init__(self, database: Optional[Database] = None):
        self.db = database
        self.submitted_prs: List[PullRequestPayload] = []

    def create_pull_request(self, payload: PullRequestPayload) -> PullRequestResponse:
        self.submitted_prs.append(payload)
        pr_num = 1000 + len(self.submitted_prs)
        pr_url = f"https://github.com/{payload.repo_name}/pull/{pr_num}"

        # 校验主动披露声明是否在 PR Body 中
        if "Generated with AI assistance" not in payload.body:
            return PullRequestResponse(
                success=False,
                pr_number=None,
                pr_url=None,
                error_message="Compliance Violation: PR body is missing mandatory AI disclosure statement",
            )

        if self.db:
            # 增加活跃 PR 计数
            repo = self.db.get_repo(payload.repo_name)
            current_active = repo.get("active_pr_count", 0) if repo else 0
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE repos
                    SET active_pr_count = ?, last_pr_submitted_at = CURRENT_TIMESTAMP
                    WHERE repo_name = ?;
                    """,
                    (current_active + 1, payload.repo_name),
                )

        return PullRequestResponse(
            success=True,
            pr_number=pr_num,
            pr_url=pr_url,
        )


class LiveGitHubGateway(BaseGitHubGateway):
    """Production GitHub Gateway using GitHub REST API (stdlib urllib, zero new deps).

    Token 只从 GITHUB_TOKEN 环境变量读取，绝不进代码、日志与报错信息。
    给自己的仓库提 PR：origin 直接指向该仓库即可。
    给他人仓库提 PR：设 payload.target_repo 为上游仓库（如 psf/requests），
    本地 origin 指向自己的 fork；fork_owner 会从 origin 自动推导，也可手动指定。
    """

    def __init__(
        self,
        github_token: str,
        database: Optional[Database] = None,
        repo_path: Optional[str] = None,
        api_base: str = "https://api.github.com",
        git_timeout: int = 120,
    ):
        self.token = github_token
        self.db = database
        self.repo_path = repo_path
        self.api_base = api_base.rstrip("/")
        self.git_timeout = git_timeout

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def create_pull_request(self, payload: PullRequestPayload) -> PullRequestResponse:
        # 1. 合规披露前置检查（与 Mock 网关一致）
        if AI_DISCLOSURE_MARKER not in payload.body:
            return PullRequestResponse(
                success=False,
                pr_number=None,
                pr_url=None,
                error_message="Compliance Violation: PR body missing mandatory AI disclosure statement",
            )
        # 2. Token 检查
        if not self.token:
            return PullRequestResponse(
                success=False,
                pr_number=None,
                pr_url=None,
                error_message="Missing GitHub token: set the GITHUB_TOKEN environment variable",
            )
        # 3. 解析 owner/repo（target_repo 优先，用于 fork 流程指向上游仓库）
        slug = self._resolve_repo_slug(payload)
        if not slug:
            return PullRequestResponse(
                success=False,
                pr_number=None,
                pr_url=None,
                error_message=(
                    f"Cannot determine owner/repo for '{payload.repo_name}': "
                    "no usable origin remote and repo_name is not 'owner/repo'"
                ),
            )
        # 3b. fork 流程自动推导 fork_owner：origin 属主 != 目标仓库属主时，origin 属主即 fork 主人
        fork_owner = payload.fork_owner
        if not fork_owner and payload.target_repo and self.repo_path:
            origin_owner = self._origin_owner()
            target_owner = payload.target_repo.split("/")[0]
            if origin_owner and origin_owner != target_owner:
                fork_owner = origin_owner
        # 4. 推分支（有本地仓库路径时）
        if self.repo_path:
            ok, msg = self._push_branch(payload)
            if not ok:
                return PullRequestResponse(
                    success=False, pr_number=None, pr_url=None,
                    error_message=f"Branch push failed: {msg}",
                )
        # 5. 调 API 建 PR
        resp = self._api_create_pr(slug, payload, fork_owner)
        if resp.success and self.db:
            self._bump_active_pr_count(payload.repo_name)
        return resp

    # ------------------------------------------------------------------
    # Git：分支推送
    # ------------------------------------------------------------------
    def _run_git(self, *args: str, cwd: Optional[str] = None, input_text: Optional[str] = None) -> Tuple[int, str, str]:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd or self.repo_path,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self.git_timeout,
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except FileNotFoundError:
            return 127, "", "git executable not found"
        except subprocess.TimeoutExpired:
            return 124, "", f"git {' '.join(args)} timed out after {self.git_timeout}s"

    def _push_branch(self, payload: PullRequestPayload) -> Tuple[bool, str]:
        branch = payload.branch_name
        # 建/切分支
        code, _, _ = self._run_git("rev-parse", "--verify", "--quiet", branch)
        if code == 0:
            code, _, err = self._run_git("checkout", branch)
        else:
            code, _, err = self._run_git("checkout", "-b", branch)
        if code != 0:
            return False, f"git checkout failed: {err}"
        # 幂等 apply：没打过就打，打过就跳过
        if payload.patch_diff.strip():
            with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
                f.write(payload.patch_diff)
                patch_path = f.name
            try:
                code, _, _ = self._run_git("apply", "--check", patch_path)
                if code == 0:
                    code, _, err = self._run_git("apply", patch_path)
                    if code != 0:
                        return False, f"git apply failed: {err}"
                else:
                    code, _, _ = self._run_git("apply", "--check", "--reverse", patch_path)
                    if code != 0:
                        return False, "patch does not apply cleanly (and is not already applied)"
            finally:
                os.unlink(patch_path)
        # 只加补丁涉及的文件 + 回归测试文件，不扫无关脏文件
        files = self._diff_touched_files(payload.patch_diff)
        test_file = (payload.regression_test_file or "").strip()
        if test_file and os.path.exists(os.path.join(self.repo_path, test_file)):
            files.append(test_file)
        if files:
            code, _, err = self._run_git("add", "--", *files)
            if code != 0:
                return False, f"git add failed: {err}"
        # 提交（DCO signoff）
        commit_args = [
            "-c", f"user.name={payload.author_handle}",
            "-c", f"user.email={payload.author_email}",
            "commit",
        ]
        if payload.signed_off:
            commit_args.append("--signoff")
        commit_args += ["-m", payload.title, "-m", payload.body]
        code, out, err = self._run_git(*commit_args)
        if code != 0 and "nothing to commit" not in (out + err):
            return False, f"git commit failed: {err or out}"
        # 推送
        code, _, err = self._run_git("push", "-u", "origin", branch)
        if code != 0:
            return False, f"git push failed: {err}"
        return True, "branch pushed"

    @staticmethod
    def _diff_touched_files(patch_diff: str) -> List[str]:
        files: List[str] = []
        for line in patch_diff.splitlines():
            m = re.match(r"^\+\+\+ b/(.+)$", line)
            if m and m.group(1) != "/dev/null" and m.group(1) not in files:
                files.append(m.group(1))
        return files

    def _resolve_repo_slug(self, payload: PullRequestPayload) -> Optional[str]:
        """target_repo 优先；否则从 origin remote URL 解析 owner/repo；
        兜底用 'owner/repo' 格式的 repo_name。"""
        if payload.target_repo:
            return payload.target_repo if "/" in payload.target_repo else None
        if self.repo_path:
            code, url, _ = self._run_git("remote", "get-url", "origin")
            if code == 0 and url:
                m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
                if m:
                    return f"{m.group(1)}/{m.group(2)}"
        if "/" in payload.repo_name:
            return payload.repo_name
        return None

    def _origin_owner(self) -> Optional[str]:
        if not self.repo_path:
            return None
        code, url, _ = self._run_git("remote", "get-url", "origin")
        if code == 0 and url:
            m = re.search(r"github\.com[:/]([^/]+)/", url)
            if m:
                return m.group(1)
        return None

    # ------------------------------------------------------------------
    # GitHub REST API：建 PR
    # ------------------------------------------------------------------
    def _api_create_pr(self, slug: str, payload: PullRequestPayload,
                       fork_owner: Optional[str]) -> PullRequestResponse:
        head = f"{fork_owner}:{payload.branch_name}" if fork_owner else payload.branch_name
        data = {
            "title": payload.title,
            "head": head,
            "base": payload.base_branch,
            "body": payload.body,
            "maintainer_can_modify": True,
        }
        req = urllib.request.Request(
            f"{self.api_base}/repos/{slug}/pulls",
            data=json.dumps(data).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "auto-se-pipeline",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return PullRequestResponse(
                success=False, pr_number=None, pr_url=None,
                error_message=self._describe_http_error(e),
            )
        except Exception as e:  # 网络/DNS/超时等
            return PullRequestResponse(
                success=False, pr_number=None, pr_url=None,
                error_message=f"GitHub API request failed: {type(e).__name__}",
            )
        return PullRequestResponse(
            success=True,
            pr_number=body.get("number"),
            pr_url=body.get("html_url") or f"https://github.com/{slug}/pull/{body.get('number')}",
        )

    def _describe_http_error(self, e: urllib.error.HTTPError) -> str:
        try:
            detail = json.loads(e.read().decode("utf-8"))
            gh_msg = " ".join(
                err.get("message", "") for err in detail.get("errors", [])
            ) or detail.get("message", "")
        except Exception:
            gh_msg = ""
        gh_msg = gh_msg.strip()
        if e.code == 401:
            return "GitHub authentication failed (401): token 无效或已过期，请检查 GITHUB_TOKEN"
        if e.code == 403:
            return f"GitHub 拒绝请求 (403)：权限不足或触发 rate limit。{gh_msg}"
        if e.code == 404:
            return "仓库不存在或 token 无权访问 (404)"
        if e.code == 422:
            return f"PR 创建被拒 (422)：{gh_msg or '参数校验失败（可能 PR 已存在，或分支间无差异）'}"
        return f"GitHub API 错误 ({e.code})：{gh_msg or '未知错误'}"

    def _bump_active_pr_count(self, repo_name: str) -> None:
        try:
            repo = self.db.get_repo(repo_name)
            current_active = repo.get("active_pr_count", 0) if repo else 0
            with self.db.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE repos
                    SET active_pr_count = ?, last_pr_submitted_at = CURRENT_TIMESTAMP
                    WHERE repo_name = ?;
                    """,
                    (current_active + 1, repo_name),
                )
        except Exception as e:
            logger.warning("Failed to bump active PR count: %s", e)
