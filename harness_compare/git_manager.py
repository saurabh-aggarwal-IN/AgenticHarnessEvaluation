from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


def _rmtree(path: Path) -> None:
    def _onerror(func, p, excinfo):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass

    if path.exists():
        shutil.rmtree(path, onerror=_onerror)


from harness_compare.config import Config
from harness_compare.process import (
    CommandError,
    CommandTimeout,
    SubprocessRunner,
    write_log,
)

SCP_RE = re.compile(r"^git@([^:]+):(.+)$")
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class RepoError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class RepoRef:
    url: str
    is_local: bool
    path: Path | None = None
    host: str | None = None


@dataclass
class PreparedRepo:
    root: Path
    repo_dir: Path
    worktrees: dict[str, Path]
    contexts: dict[str, Path]
    commits: dict[str, str]
    ref: RepoRef


def validate_repo_url(url: str, cfg: Config) -> RepoRef:
    value = url.strip()
    if not value:
        raise RepoError("REPO_URL", "repository URL is empty")
    if value.startswith("-") or any(ord(char) < 32 for char in value):
        raise RepoError("REPO_URL", "repository URL contains unsafe characters")

    if value.startswith("file://"):
        if not cfg.allow_local_repo:
            raise RepoError(
                "REPO_LOCAL_DISABLED",
                "local repositories are disabled (ALLOW_LOCAL_REPO=false)",
            )
        parsed = urlparse(value)
        path = Path(unquote(parsed.path))
        if os.name == "nt" and str(path).startswith("\\") and len(str(path)) > 3:
            path = Path(str(path)[1:])
        return _local(path)

    if (
        DRIVE_RE.match(value)
        or value.startswith(("/", "\\", "./", "../", "~"))
        or Path(value).exists()
    ):
        if not cfg.allow_local_repo:
            raise RepoError(
                "REPO_LOCAL_DISABLED",
                "local repositories are disabled (ALLOW_LOCAL_REPO=false)",
            )
        return _local(Path(value).expanduser())

    scp = SCP_RE.match(value)
    if scp:
        host = scp.group(1).casefold()
        _check_host(host, cfg)
        return RepoRef(url=value, is_local=False, host=host)

    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http", "ssh"}:
        raise RepoError(
            "REPO_SCHEME",
            "repository URL must use https, http (local only), ssh, or a local path",
        )
    local_http = parsed.scheme == "http" and (parsed.hostname or "") in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if parsed.scheme == "http" and not local_http:
        raise RepoError(
            "REPO_SCHEME", "plain http is only allowed for localhost"
        )
    if parsed.password:
        raise RepoError(
            "REPO_CREDENTIALS",
            "do not embed credentials in the repository URL; use GIT_TOKEN",
        )
    host = (parsed.hostname or "").casefold()
    if not host:
        raise RepoError("REPO_URL", "repository URL has no host")
    if not local_http:
        _check_host(host, cfg)
    return RepoRef(url=value, is_local=False, host=host)


def _local(path: Path) -> RepoRef:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise RepoError(
            "REPO_LOCAL_NOT_FOUND", f"local repository does not exist: {resolved}"
        )
    return RepoRef(url=str(resolved), is_local=True, path=resolved)


def _check_host(host: str, cfg: Config) -> None:
    allowed = {item.casefold() for item in cfg.repo_allowed_hosts}
    if host in allowed:
        return
    if host.startswith("www.") and host[4:] in allowed:
        return
    raise RepoError(
        "REPO_HOST",
        f"repository host {host!r} is not allowed; "
        f"allowed hosts: {', '.join(sorted(allowed))}",
    )


class GitManager:
    def __init__(self, cfg: Config, runner: SubprocessRunner | None = None) -> None:
        self.cfg = cfg
        self.runner = runner or SubprocessRunner()

    def _env(self) -> dict[str, str]:
        env = {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "echo",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "LC_ALL": "C",
        }
        # Worktrees are Docker build contexts. Honor repository bytes exactly:
        # Windows hosts commonly set core.autocrlf=true, which would rewrite
        # shell shebangs to CRLF and make Linux containers fail to exec them.
        config: list[tuple[str, str]] = [("core.autocrlf", "false")]
        if self.cfg.git_token:
            config.append(
                (
                    "http.extraHeader",
                    f"Authorization: Bearer {self.cfg.git_token}",
                )
            )
        env["GIT_CONFIG_COUNT"] = str(len(config))
        for index, (key, value) in enumerate(config):
            env[f"GIT_CONFIG_KEY_{index}"] = key
            env[f"GIT_CONFIG_VALUE_{index}"] = value
        return env

    async def _git(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout_s: float | None = None,
    ) -> str:
        command = ["git", *args]
        try:
            result = await self.runner.run(
                command,
                cwd=cwd,
                env=self._env(),
                timeout_s=timeout_s or self.cfg.git_timeout_s,
            )
        except CommandTimeout as exc:
            raise RepoError(
                "GIT_TIMEOUT",
                f"git {args[0]} timed out after {self.cfg.git_timeout_s:.0f}s",
            ) from exc
        except CommandError as exc:
            raise RepoError(
                "GIT_UNAVAILABLE",
                "git is not available on the orchestrator host",
                details={"stderr": exc.result.tail(1_000)},
            ) from exc
        if not result.ok:
            raise RepoError(
                "GIT_COMMAND_FAILED",
                f"git {args[0]} failed with exit code {result.returncode}",
                details={"stderr": result.tail(2_000)},
            )
        return result.stdout.strip()

    async def prepare(
        self,
        *,
        comparison_id: str,
        repo_url: str,
        commits: dict[str, str],
        run_dir: Path,
    ) -> PreparedRepo:
        ref = validate_repo_url(repo_url, self.cfg)
        root = run_dir
        repo_dir = root / "repo"
        logs = root / "logs"
        logs.mkdir(parents=True, exist_ok=True)

        is_valid_git = False
        if repo_dir.exists():
            try:
                await self._git(["rev-parse", "--git-dir"], cwd=repo_dir)
                is_valid_git = True
            except RepoError:
                _rmtree(repo_dir)

        if not is_valid_git or not repo_dir.exists():
            _rmtree(repo_dir)
            if ref.is_local:
                if ref.path is None:
                    raise RepoError("REPO_LOCAL_NOT_FOUND", "local path is missing")
                await self._git(
                    ["clone", "--no-checkout", str(ref.path), str(repo_dir)],
                    timeout_s=self.cfg.git_timeout_s,
                )
            else:
                repo_dir.mkdir(parents=True, exist_ok=True)
                await self._git(["init", "-q"], cwd=repo_dir)
                await self._git(
                    ["remote", "add", "origin", ref.url], cwd=repo_dir
                )

        resolved: dict[str, str] = {}
        missing: list[str] = []
        for harness_id, commit in commits.items():
            try:
                resolved[harness_id] = await self._resolve_commit(repo_dir, commit)
            except RepoError:
                missing.append(commit)

        if missing:
            await self._fetch(repo_dir, missing)
            for harness_id, commit in commits.items():
                resolved[harness_id] = await self._resolve_commit(repo_dir, commit)

        worktrees: dict[str, Path] = {}
        contexts: dict[str, Path] = {}
        for harness_id, commit in resolved.items():
            tree = root / f"tree-{harness_id}"
            if tree.exists():
                try:
                    await self._git(
                        ["worktree", "remove", "--force", str(tree)],
                        cwd=repo_dir,
                    )
                except RepoError:
                    _rmtree(tree)
            try:
                await self._git(["worktree", "prune"], cwd=repo_dir)
            except RepoError:
                pass
            await self._git(
                ["worktree", "add", "--detach", "--force", str(tree), commit],
                cwd=repo_dir,
            )
            context = (tree / self.cfg.harness_subdir).resolve() if self.cfg.harness_subdir else tree
            if tree.resolve() not in context.parents and context != tree.resolve():
                raise RepoError(
                    "REPO_SUBDIR", "HARNESS_SUBDIR escapes the checked-out worktree"
                )
            if not (context / "Dockerfile").is_file():
                raise RepoError(
                    "REPO_DOCKERFILE",
                    f"commit {commit[:12]} has no Dockerfile at {context}",
                )
            worktrees[harness_id] = tree
            contexts[harness_id] = context

        log_path = logs / "git-prepare.log"
        write_log(
            log_path,
            f"comparison={comparison_id}\nrepo={ref.url}\n"
            + "\n".join(f"{key}={value}" for key, value in resolved.items())
            + "\n",
        )
        return PreparedRepo(
            root=root,
            repo_dir=repo_dir,
            worktrees=worktrees,
            contexts=contexts,
            commits=resolved,
            ref=ref,
        )

    async def _resolve_commit(self, repo_dir: Path, commit: str) -> str:
        try:
            output = await self._git(
                ["rev-parse", "--verify", f"{commit}^{{commit}}"],
                cwd=repo_dir,
                timeout_s=30,
            )
        except RepoError as exc:
            raise RepoError(
                "GIT_COMMIT_MISSING", f"commit {commit} is not in the repository"
            ) from exc
        resolved = output.strip().splitlines()[-1]
        if not re.match(r"^[0-9a-f]{40}$", resolved):
            raise RepoError(
                "GIT_COMMIT_MISSING", f"commit {commit} did not resolve to a full SHA"
            )
        return resolved

    async def _fetch(self, repo_dir: Path, commits: list[str]) -> None:
        unique = list(dict.fromkeys(commits))
        try:
            await self._git(
                [
                    "fetch",
                    "--depth",
                    "1",
                    "--no-tags",
                    "origin",
                    *unique,
                ],
                cwd=repo_dir,
            )
            return
        except RepoError:
            pass
        for commit in unique:
            try:
                await self._git(
                    ["fetch", "--depth", "1", "--no-tags", "origin", commit],
                    cwd=repo_dir,
                )
            except RepoError:
                continue
        try:
            await self._git(
                [
                    "fetch",
                    "origin",
                    "+refs/heads/*:refs/remotes/origin/*",
                    "+refs/tags/*:refs/tags/*",
                ],
                cwd=repo_dir,
                timeout_s=max(300.0, self.cfg.git_timeout_s),
            )
        except RepoError as exc:
            raise RepoError(
                "GIT_FETCH_FAILED",
                "could not fetch the requested commits; check the URL, visibility, "
                "and that each commit belongs to the repository",
                details=exc.details,
            ) from exc

    async def cleanup(self, prepared: PreparedRepo, *, keep: bool) -> None:
        if keep:
            return
        for tree in prepared.worktrees.values():
            try:
                await self._git(
                    ["worktree", "remove", "--force", str(tree)],
                    cwd=prepared.repo_dir,
                    timeout_s=60,
                )
            except Exception:  # noqa: BLE001 - cleanup must not mask the result
                _rmtree(tree)
        try:
            await self._git(["worktree", "prune"], cwd=prepared.repo_dir, timeout_s=60)
        except Exception:  # noqa: BLE001
            pass
        _rmtree(prepared.repo_dir)

