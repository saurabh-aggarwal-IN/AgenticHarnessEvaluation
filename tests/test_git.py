from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from harness_compare.config import Config
from harness_compare.git_manager import (
    GitManager,
    RepoError,
    validate_repo_url,
)
from harness_compare.process import CommandResult


def test_github_https_url_is_allowed(cfg: Config) -> None:
    ref = validate_repo_url("https://github.com/example/repo.git", cfg)
    assert ref.host == "github.com"
    assert ref.is_local is False


def test_www_github_url_is_allowed(cfg: Config) -> None:
    ref = validate_repo_url("https://www.github.com/example/repo", cfg)
    assert ref.host == "www.github.com"


def test_scp_style_url_is_allowed(cfg: Config) -> None:
    ref = validate_repo_url("git@github.com:example/repo.git", cfg)
    assert ref.host == "github.com"
    assert ref.is_local is False


def test_ssh_url_is_allowed(cfg: Config) -> None:
    ref = validate_repo_url("ssh://git@github.com/example/repo.git", cfg)
    assert ref.host == "github.com"


def test_other_host_is_rejected(cfg: Config) -> None:
    with pytest.raises(RepoError) as exc:
        validate_repo_url("https://gitlab.com/example/repo", cfg)
    assert exc.value.code == "REPO_HOST"


def test_plain_http_outside_localhost_is_rejected(cfg: Config) -> None:
    with pytest.raises(RepoError) as exc:
        validate_repo_url("http://github.com/example/repo", cfg)
    assert exc.value.code == "REPO_SCHEME"


def test_localhost_http_is_allowed(cfg: Config) -> None:
    ref = validate_repo_url("http://127.0.0.1:3000/repo.git", cfg)
    assert ref.host == "127.0.0.1"


def test_embedded_credentials_are_rejected(cfg: Config) -> None:
    with pytest.raises(RepoError) as exc:
        validate_repo_url("https://user:secret@github.com/example/repo", cfg)
    assert exc.value.code == "REPO_CREDENTIALS"


def test_unsupported_scheme_is_rejected(cfg: Config) -> None:
    with pytest.raises(RepoError) as exc:
        validate_repo_url("ftp://github.com/example/repo", cfg)
    assert exc.value.code == "REPO_SCHEME"


def test_option_like_url_is_rejected(cfg: Config) -> None:
    with pytest.raises(RepoError) as exc:
        validate_repo_url("--upload-pack=evil", cfg)
    assert exc.value.code == "REPO_URL"


def test_local_path_requires_the_explicit_flag(cfg: Config, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(RepoError) as exc:
        validate_repo_url(str(repo), replace(cfg, allow_local_repo=False))
    assert exc.value.code == "REPO_LOCAL_DISABLED"
    ref = validate_repo_url(str(repo), cfg)
    assert ref.is_local is True
    assert ref.path == repo.resolve()


def test_missing_local_path_is_reported(cfg: Config, tmp_path: Path) -> None:
    with pytest.raises(RepoError) as exc:
        validate_repo_url(str(tmp_path / "absent"), cfg)
    assert exc.value.code == "REPO_LOCAL_NOT_FOUND"


def test_git_env_disables_autocrlf_and_adds_token(cfg: Config) -> None:
    manager = GitManager(replace(cfg, git_token="secret-token"), FakeGitRunner())
    env = manager._env()
    assert env["GIT_CONFIG_COUNT"] == "2"
    assert env["GIT_CONFIG_KEY_0"] == "core.autocrlf"
    assert env["GIT_CONFIG_VALUE_0"] == "false"
    assert env["GIT_CONFIG_KEY_1"] == "http.extraHeader"
    assert "secret-token" in env["GIT_CONFIG_VALUE_1"]


def test_git_env_without_token_has_only_lf_policy(cfg: Config) -> None:
    manager = GitManager(replace(cfg, git_token=""), FakeGitRunner())
    env = manager._env()
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "core.autocrlf"


class FakeGitRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
        self.calls.append(list(args))
        joined = " ".join(args)
        if "rev-parse --git-dir" in joined:
            return CommandResult(args, 0, "true", "", 1)
        if "rev-parse --verify" in joined:
            return CommandResult(args, 0, "a" * 40, "", 1)
        if args[0] == "git" and "worktree" in args and "add" in args:
            tree = Path(args[-2])
            (tree / "docker").mkdir(parents=True, exist_ok=True)
            (tree / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            return CommandResult(args, 0, "", "", 1)
        if args[0] == "git" and "clone" in args:
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
            return CommandResult(args, 0, "", "", 1)
        if "worktree" in args and "remove" in args:
            return CommandResult(args, 0, "", "", 1)
        return CommandResult(args, 0, "", "", 1)


@pytest.mark.asyncio
async def test_prepare_creates_one_worktree_per_commit(
    cfg: Config, tmp_path: Path
) -> None:
    runner = FakeGitRunner()
    manager = GitManager(cfg, runner)
    run_dir = tmp_path / "run"
    prepared = await manager.prepare(
        comparison_id="cmp-test",
        repo_url=str(tmp_path),
        commits={"h1": "a" * 40, "h2": "b" * 40, "h3": "c" * 40},
        run_dir=run_dir,
    )
    assert set(prepared.commits) == {"h1", "h2", "h3"}
    assert prepared.contexts["h1"] == run_dir / "tree-h1"
    assert (prepared.contexts["h1"] / "Dockerfile").is_file()
    assert any("clone" in call for call in runner.calls)
    assert sum(1 for call in runner.calls if "worktree" in call and "add" in call) == 3


@pytest.mark.asyncio
async def test_prepare_rejects_commit_without_dockerfile(
    cfg: Config, tmp_path: Path
) -> None:
    class NoDockerfile(FakeGitRunner):
        async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
            if args[0] == "git" and "worktree" in args and "add" in args:
                tree = Path(args[-2])
                tree.mkdir(parents=True, exist_ok=True)
                return CommandResult(args, 0, "", "", 1)
            return await super().run(args, cwd=cwd, env=env, timeout_s=timeout_s)

    manager = GitManager(cfg, NoDockerfile())
    with pytest.raises(RepoError) as exc:
        await manager.prepare(
            comparison_id="cmp-test",
            repo_url=str(tmp_path),
            commits={"h1": "a" * 40, "h2": "b" * 40, "h3": "c" * 40},
            run_dir=tmp_path / "run",
        )
    assert exc.value.code == "REPO_DOCKERFILE"


@pytest.mark.asyncio
async def test_prepare_rejects_subdir_escape(cfg: Config, tmp_path: Path) -> None:
    manager = GitManager(cfg, FakeGitRunner())
    unsafe = replace(cfg, harness_subdir="../outside")
    manager.cfg = unsafe
    with pytest.raises(RepoError) as exc:
        await manager.prepare(
            comparison_id="cmp-test",
            repo_url=str(tmp_path),
            commits={"h1": "a" * 40, "h2": "b" * 40, "h3": "c" * 40},
            run_dir=tmp_path / "run",
        )
    assert exc.value.code == "REPO_SUBDIR"
