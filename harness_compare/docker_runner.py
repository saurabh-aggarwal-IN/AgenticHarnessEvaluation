from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from harness_compare.config import Config
from harness_compare.process import (
    CommandError,
    CommandResult,
    CommandTimeout,
    SubprocessRunner,
    write_log,
)


class DockerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        log_path: Path | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.log_path = log_path


class DockerClient:
    """Thin, auditable Docker CLI wrapper.

    The local environment uses a Podman-provided `docker` shim, so the
    orchestrator intentionally avoids Compose and calls the CLI directly.
    """

    def __init__(self, cfg: Config, runner: SubprocessRunner | None = None) -> None:
        self.cfg = cfg
        self.runner = runner or SubprocessRunner()

    async def _run(
        self,
        args: list[str],
        *,
        timeout_s: float = 120.0,
        allow_failure: bool = False,
    ) -> CommandResult:
        command = [self.cfg.docker_bin, *args]
        try:
            result = await self.runner.run(command, timeout_s=timeout_s)
        except CommandTimeout as exc:
            raise DockerError(
                "DOCKER_TIMEOUT",
                f"docker {args[0]} timed out after {timeout_s:.0f}s",
                details={"stderr": (exc.result.tail(2_000) if exc.result else "")},
            ) from exc
        except CommandError as exc:
            raise DockerError(
                "DOCKER_UNAVAILABLE",
                "docker CLI is not available on the orchestrator host",
                details={"stderr": exc.result.tail(2_000)},
            ) from exc
        if not result.ok and not allow_failure:
            raise DockerError(
                "DOCKER_COMMAND_FAILED",
                f"docker {args[0]} failed with exit code {result.returncode}",
                details={"stderr": result.tail(3_000)},
            )
        return result

    async def version(self) -> str:
        try:
            result = await self._run(
                ["version", "--format", "{{.Server.Version}}"],
                timeout_s=10,
            )
            return f"ok: {result.stdout.strip() or 'unknown'}"
        except DockerError as exc:
            return f"error: {exc.code}"

    async def ensure_network(self, name: str, comparison_id: str) -> None:
        inspect = await self._run(
            ["network", "inspect", name], timeout_s=30, allow_failure=True
        )
        if inspect.ok:
            return
        await self._run(
            [
                "network",
                "create",
                "--label",
                f"cc.comparison={comparison_id}",
                name,
            ],
            timeout_s=60,
        )

    async def remove_network(self, name: str) -> None:
        await self._run(
            ["network", "rm", name], timeout_s=60, allow_failure=True
        )

    async def connect_container(self, network: str, container: str) -> None:
        result = await self._run(
            ["network", "connect", network, container],
            timeout_s=30,
            allow_failure=True,
        )
        if not result.ok:
            text = result.tail(1_000).lower()
            if "already exists" in text or "already connected" in text:
                return
            raise DockerError(
                "DOCKER_NETWORK_CONNECT",
                f"could not attach {container} to {network}",
                details={"stderr": result.tail(2_000)},
            )

    async def disconnect_container(self, network: str, container: str) -> None:
        await self._run(
            ["network", "disconnect", "-f", network, container],
            timeout_s=30,
            allow_failure=True,
        )

    async def build_image(
        self,
        *,
        context: Path,
        tag: str,
        harness_id: str,
        commit: str,
        comparison_id: str,
        version: str,
        log_path: Path,
    ) -> str:
        args = [
            "build",
            "-t",
            tag,
            "--label",
            f"cc.comparison={comparison_id}",
            "--label",
            f"cc.harness={harness_id}",
            "--build-arg",
            f"HARNESS_ID={harness_id}",
            "--build-arg",
            f"GIT_COMMIT={commit}",
            "--build-arg",
            f"HARNESS_VERSION={version}",
            str(context),
        ]
        try:
            result = await self._run(args, timeout_s=self.cfg.build_timeout_s)
        except DockerError as exc:
            text = _command_text(exc)
            write_log(log_path, text)
            raise DockerError(
                exc.code,
                exc.message,
                details={**exc.details, "log": str(log_path)},
                log_path=log_path,
            ) from exc
        write_log(
            log_path,
            f"image={tag}\ncommit={commit}\nharness={harness_id}\n\n"
            + result.stdout
            + "\n"
            + result.stderr,
        )
        return tag

    async def start_worker(
        self,
        *,
        name: str,
        image: str,
        network: str,
        comparison_id: str,
        harness_id: str,
        env: dict[str, str],
        env_file: Path,
        command: list[str] | None = None,
    ) -> str:
        env_file.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        for key, value in env.items():
            if "\n" in value or "\r" in value:
                raise DockerError(
                    "DOCKER_ENV_INVALID", f"environment value for {key} has a newline"
                )
            lines.append(f"{key}={value}")
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(env_file, 0o600)
        except OSError:
            pass

        args = [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--label",
            f"cc.comparison={comparison_id}",
            "--label",
            f"cc.harness={harness_id}",
            "--init",
            "--security-opt",
            "no-new-privileges",
            "--cap-drop",
            "ALL",
            "--stop-timeout",
            "15",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={self.cfg.worker_tmpfs_mb}m",
            "--tmpfs",
            "/run:rw,nosuid,nodev,size=16m",
        ]
        # Resource ceilings are enabled by default. Hosts running rootless
        # Podman without the cgroup memory/cpu/pids controllers cannot create
        # such containers, so each ceiling can be disabled explicitly with an
        # empty/zero setting. Security options above are never optional.
        if self.cfg.worker_memory:
            args.extend(["--memory", self.cfg.worker_memory])
        if self.cfg.worker_cpus:
            args.extend(["--cpus", self.cfg.worker_cpus])
        if self.cfg.worker_pids > 0:
            args.extend(["--pids-limit", str(self.cfg.worker_pids)])
        if self.cfg.worker_read_only:
            args.extend(["--read-only"])
        args.extend(["--env-file", str(env_file)])
        args.append(image)
        args.extend(command or ["/opt/harness-venv/bin/python", "-m", "clinical_coder.worker"])
        try:
            result = await self._run(args, timeout_s=120)
            return result.stdout.strip()
        except DockerError as exc:
            # Clean up any partial container created by the failed attempt
            stderr = str(exc.details.get("stderr") or "").lower()
            if "already in use" in stderr or "conflict" in stderr or "crun" in stderr or "memory.max" in stderr:
                await self._run(["rm", "-f", name], timeout_s=30, allow_failure=True)
            # Some host/VM combinations cannot read a Windows env-file path,
            # or rootless Podman lacks cgroup v2 controller delegation.
            strip_cgroups = "memory.max" in stderr or "cgroup" in stderr or "controller" in stderr or "125" in str(exc)
            cleaned: list[str] = []
            index = 0
            while index < len(args):
                if strip_cgroups and args[index] in ("--memory", "--cpus", "--pids-limit"):
                    index += 2
                    continue
                if args[index] == "--env-file":
                    index += 2
                    continue
                cleaned.append(args[index])
                index += 1
            insert_at = cleaned.index(image)
            explicit: list[str] = []
            for key, value in env.items():
                explicit.extend(["-e", f"{key}={value}"])
            cleaned[insert_at:insert_at] = explicit
            result = await self._run(cleaned, timeout_s=120)
            return result.stdout.strip()

    async def worker_status(self, name: str) -> dict[str, Any] | None:
        result = await self._run(
            ["inspect", "--format", "{{json .State}}", name],
            timeout_s=30,
            allow_failure=True,
        )
        if not result.ok:
            return None
        try:
            value = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    async def worker_logs(self, name: str, tail: int = 120) -> str:
        result = await self._run(
            ["logs", "--tail", str(tail), name],
            timeout_s=30,
            allow_failure=True,
        )
        return (result.stdout + "\n" + result.stderr).strip()[-20_000:]

    async def stop_worker(self, name: str) -> None:
        await self._run(
            ["rm", "-f", name], timeout_s=60, allow_failure=True
        )

    async def remove_container(self, name: str) -> None:
        await self.stop_worker(name)

    async def image_exists(self, tag: str) -> bool:
        result = await self._run(
            ["image", "inspect", tag], timeout_s=30, allow_failure=True
        )
        return result.ok

    async def remove_image(self, tag: str) -> None:
        await self._run(
            ["image", "rm", "-f", tag], timeout_s=120, allow_failure=True
        )

    async def list_comparison_containers(
        self, comparison_id: str
    ) -> list[str]:
        result = await self._run(
            [
                "ps",
                "-a",
                "--filter",
                f"label=cc.comparison={comparison_id}",
                "--format",
                "{{.Names}}",
            ],
            timeout_s=30,
            allow_failure=True,
        )
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    async def kill_all_workers(self, comparison_id: str | None = None) -> list[str]:
        """Forcefully stop and remove all worker containers."""
        stopped: list[str] = []
        if comparison_id:
            containers = await self.list_comparison_containers(comparison_id)
            for c in containers:
                await self.stop_worker(c)
                stopped.append(c)
        for filter_expr in ("name=worker", "name=cc-cmp", "name=cc-"):
            result = await self._run(
                ["ps", "-a", "--filter", filter_expr, "--format", "{{.Names}}"],
                timeout_s=30,
                allow_failure=True,
            )
            if result.ok and result.stdout.strip():
                for name in result.stdout.splitlines():
                    name = name.strip()
                    if name in (self.cfg.postgres_container, self.cfg.redis_container, "cc-postgres", "cc-redis"):
                        continue
                    if name and name not in stopped:
                        await self.stop_worker(name)
                        stopped.append(name)
        return stopped


def _command_text(exc: DockerError) -> str:
    details = exc.details or {}
    return (
        f"{exc.code}: {exc.message}\n"
        + str(details.get("stderr") or details.get("log") or "")
    )
