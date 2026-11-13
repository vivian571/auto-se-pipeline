"""Tests for LiveGitHubGateway real PR submission (all offline).

- GitHub REST API is faked by monkeypatching urllib.request.urlopen.
- git push flow is tested against a local bare remote (no network).
"""
import io
import json
import os
import subprocess
import urllib.error

import pytest

from core.github_gateway import LiveGitHubGateway, PullRequestPayload

TOKEN = "ghp_testtoken1234567890"
BODY = "### Summary\n\n*Generated with AI assistance; validated in sandbox.*"


def make_payload(**kw):
    base = dict(
        repo_name="owner/repo",
        issue_number=7,
        branch_name="fix/issue-7",
        title="fix: resolve #7 - sample bug",
        body=BODY,
        patch_diff="",
        regression_test_file="",
        author_handle="tester",
        author_email="tester@example.com",
    )
    base.update(kw)
    return PullRequestPayload(**base)


def make_http_error(code, payload_dict):
    fp = io.BytesIO(json.dumps(payload_dict).encode())
    return urllib.error.HTTPError(
        url="https://api.github.com/repos/owner/repo/pulls",
        code=code, msg="error", hdrs={}, fp=fp,
    )


class FakeResponse:
    def __init__(self, data):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def gw():
    return LiveGitHubGateway(github_token=TOKEN)


def test_api_success_parses_pr_number_and_url(gw, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=30):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode())
        return FakeResponse({"number": 123, "html_url": "https://github.com/owner/repo/pull/123"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = gw.create_pull_request(make_payload())

    assert resp.success and resp.pr_number == 123
    assert resp.pr_url == "https://github.com/owner/repo/pull/123"
    assert seen["url"] == "https://api.github.com/repos/owner/repo/pulls"
    assert seen["method"] == "POST"
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["body"]["head"] == "fix/issue-7"
    assert seen["body"]["base"] == "main"
    assert seen["body"]["maintainer_can_modify"] is True


def test_fork_owner_changes_head_ref(gw, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=30):
        seen["body"] = json.loads(req.data.decode())
        return FakeResponse({"number": 9, "html_url": "https://github.com/upstream/repo/pull/9"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    gw.create_pull_request(make_payload(repo_name="upstream/repo", fork_owner="tester"))
    assert seen["body"]["head"] == "tester:fix/issue-7"


def test_401_maps_to_token_error_without_leaking_token(gw, monkeypatch):
    def fake_urlopen(req, timeout=30):
        raise make_http_error(401, {"message": "Bad credentials"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = gw.create_pull_request(make_payload())
    assert not resp.success
    assert "401" in resp.error_message
    assert TOKEN not in resp.error_message


def test_422_reports_github_message(gw, monkeypatch):
    def fake_urlopen(req, timeout=30):
        raise make_http_error(422, {"message": "Validation Failed",
                                    "errors": [{"message": "A pull request already exists"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    resp = gw.create_pull_request(make_payload())
    assert not resp.success and "already exists" in resp.error_message


def test_disclosure_check_enforced_and_token_never_in_errors(monkeypatch):
    gw = LiveGitHubGateway(github_token=TOKEN)
    resp = gw.create_pull_request(make_payload(body="no disclosure here"))
    assert not resp.success and "AI disclosure" in resp.error_message

    gw2 = LiveGitHubGateway(github_token="")
    resp2 = gw2.create_pull_request(make_payload())
    assert not resp2.success and "GITHUB_TOKEN" in resp2.error_message
    assert TOKEN not in (resp.error_message + resp2.error_message)


def test_slug_parsed_from_https_and_ssh_remotes(monkeypatch, tmp_path):
    gw = LiveGitHubGateway(github_token=TOKEN, repo_path=str(tmp_path))
    monkeypatch.setattr(gw, "_run_git",
                        lambda *a, **k: (0, "https://github.com/acme/widgets.git", ""))
    assert gw._resolve_repo_slug(make_payload(repo_name="widgets")) == "acme/widgets"
    monkeypatch.setattr(gw, "_run_git",
                        lambda *a, **k: (0, "git@github.com:acme/widgets.git", ""))
    assert gw._resolve_repo_slug(make_payload(repo_name="widgets")) == "acme/widgets"
    # fallback: repo_name already owner/repo, no git needed
    gw2 = LiveGitHubGateway(github_token=TOKEN)
    assert gw2._resolve_repo_slug(make_payload(repo_name="acme/widgets")) == "acme/widgets"
    assert gw2._resolve_repo_slug(make_payload(repo_name="widgets")) is None


def test_target_repo_wins_and_fork_owner_auto_derived(monkeypatch, tmp_path):
    seen = {}

    def fake_urlopen(req, timeout=30):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return FakeResponse({"number": 5, "html_url": "https://github.com/psf/requests/pull/5"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    # origin 指向自己的 fork，target 指向上游
    gw = LiveGitHubGateway(github_token=TOKEN, repo_path=str(tmp_path))
    monkeypatch.setattr(gw, "_run_git",
                        lambda *a, **k: (0, "https://github.com/tester/requests.git", ""))
    resp = gw.create_pull_request(make_payload(repo_name="requests",
                                               target_repo="psf/requests"))
    assert resp.success
    assert seen["url"] == "https://api.github.com/repos/psf/requests/pulls"
    assert seen["body"]["head"] == "tester:fix/issue-7"  # fork_owner 自动推导
    # 手动指定的 fork_owner 优先
    gw.create_pull_request(make_payload(repo_name="requests", target_repo="psf/requests",
                                        fork_owner="other"))
    assert seen["body"]["head"] == "other:fix/issue-7"


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def test_push_branch_end_to_end_offline(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git("init", "--bare", str(origin), cwd=str(tmp_path))
    _git("init", "-b", "main", str(work), cwd=str(tmp_path))
    _git("config", "user.email", "t@e.com", cwd=str(work))
    _git("config", "user.name", "t", cwd=str(work))
    (work / "app.py").write_text("x = 1\n")
    _git("add", ".", cwd=str(work))
    _git("commit", "-m", "init", cwd=str(work))
    _git("remote", "add", "origin", str(origin), cwd=str(work))
    _git("push", "-u", "origin", "main", cwd=str(work))

    diff = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
    )
    gw = LiveGitHubGateway(github_token=TOKEN, repo_path=str(work))
    payload = make_payload(repo_name="acme/widgets", patch_diff=diff,
                           regression_test_file="")

    ok, msg = gw._push_branch(payload)
    assert ok, msg

    # 远端确有分支且内容正确
    out = subprocess.run(["git", "--git-dir", str(origin), "show", "fix/issue-7:app.py"],
                         capture_output=True, text=True)
    assert out.stdout.strip() == "x = 2"
    log = subprocess.run(["git", "--git-dir", str(origin), "log", "--format=%B", "-1", "fix/issue-7"],
                         capture_output=True, text=True)
    assert "Signed-off-by" in log.stdout  # DCO

    # 幂等：再推一次也成功（补丁已打过，跳过 apply）
    ok2, msg2 = gw._push_branch(payload)
    assert ok2, msg2
