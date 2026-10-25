from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any

from harness_compare.config import Config
from harness_compare.docker_runner import DockerClient, DockerError
from harness_compare.process import CommandResult, CommandTimeout, SubprocessRunner


@dataclass
class InfraManager:
    """Start the local Postgres and Redis used by the orchestrator.

    Docker Compose is intentionally not required: the local Docker endpoint is
    a Podman shim without a Compose provider, and direct CLI calls are easier
    to audit.
    """

    cfg: Config
    runner: SubprocessRunner | None = None

    def __post_init__(self) -> None:
        self.runner = self.runner or SubprocessRunner()
        self.docker = DockerClient(self.cfg, self.runner)

    async def _run(
        self, args: list[str], *, timeout_s: float = 120.0, allow_failure: bool = False
    ) -> CommandResult:
        command = [self.cfg.docker_bin, *args]
        try:
            result = await self.runner.run(command, timeout_s=timeout_s)
        except CommandTimeout as exc:
            raise DockerError(
                "DOCKER_TIMEOUT", f"docker {args[0]} timed out"
            ) from exc
        if not result.ok and not allow_failure:
            raise DockerError(
                "DOCKER_COMMAND_FAILED",
                f"docker {args[0]} failed: {result.tail(2_000)}",
            )
        return result

    async def up(self, *, wait: bool = True) -> dict[str, Any]:
        await self.docker.ensure_network(self.cfg.infra_network, "infra")
        await self._ensure_postgres()
        await self._ensure_redis()
        if wait:
            await self._wait_postgres()
            await self._wait_redis()
        return await self.status()

    async def _ensure_postgres(self) -> None:
        port = os.getenv("POSTGRES_PORT", "55432")
        user = os.getenv("POSTGRES_USER", "cc")
        password = os.getenv("POSTGRES_PASSWORD", "cc")
        database = os.getenv("POSTGRES_DB", "harness_compare")
        image = os.getenv("POSTGRES_IMAGE", "postgres:16-alpine")
        if await self._exists(self.cfg.postgres_container):
            await self._run(["start", self.cfg.postgres_container], allow_failure=True)
            return
        await self._run(
            [
                "run",
                "-d",
                "--name",
                self.cfg.postgres_container,
                "--network",
                self.cfg.infra_network,
                "--label",
                "cc.infra=postgres",
                "-e",
                f"POSTGRES_USER={user}",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                f"POSTGRES_DB={database}",
                "-p",
                f"127.0.0.1:{port}:5432",
                "-v",
                "cc-pgdata:/var/lib/postgresql/data",
                "--health-cmd",
                f"pg_isready -U {user} -d {database}",
                "--health-interval",
                "5s",
                "--health-timeout",
                "3s",
                "--health-retries",
                "20",
                image,
            ],
            timeout_s=180,
        )

    async def _ensure_redis(self) -> None:
        port = os.getenv("REDIS_PORT", "56379")
        image = os.getenv("REDIS_IMAGE", "redis:7-alpine")
        if await self._exists(self.cfg.redis_container):
            await self._run(["start", self.cfg.redis_container], allow_failure=True)
            return
        await self._run(
            [
                "run",
                "-d",
                "--name",
                self.cfg.redis_container,
                "--network",
                self.cfg.infra_network,
                "--label",
                "cc.infra=redis",
                "-p",
                f"127.0.0.1:{port}:6379",
                "-v",
                "cc-redisdata:/data",
                "--health-cmd",
                "redis-cli ping",
                "--health-interval",
                "5s",
                "--health-timeout",
                "3s",
                "--health-retries",
                "20",
                image,
                "redis-server",
                "--appendonly",
                "yes",
            ],
            timeout_s=180,
        )

    async def _exists(self, container: str) -> bool:
        result = await self._run(
            ["inspect", container], timeout_s=30, allow_failure=True
        )
        return result.ok

    async def _wait_postgres(self) -> None:
        user = os.getenv("POSTGRES_USER", "cc")
        database = os.getenv("POSTGRES_DB", "harness_compare")
        for _ in range(60):
            result = await self._run(
                [
                    "exec",
                    self.cfg.postgres_container,
                    "pg_isready",
                    "-U",
                    user,
                    "-d",
                    database,
                ],
                timeout_s=10,
                allow_failure=True,
            )
            if result.ok:
                return
            await asyncio.sleep(1)
        raise DockerError(
            "POSTGRES_NOT_READY",
            "Postgres did not become ready within 60s",
        )

    async def _wait_redis(self) -> None:
        for _ in range(60):
            result = await self._run(
                ["exec", self.cfg.redis_container, "redis-cli", "ping"],
                timeout_s=10,
                allow_failure=True,
            )
            if result.ok and "PONG" in result.stdout:
                return
            await asyncio.sleep(1)
        raise DockerError("REDIS_NOT_READY", "Redis did not become ready within 60s")

    async def down(self, *, volumes: bool = False) -> dict[str, Any]:
        for container in (self.cfg.postgres_container, self.cfg.redis_container):
            await self._run(["rm", "-f", container], allow_failure=True)
        await self._run(
            ["network", "rm", self.cfg.infra_network], allow_failure=True
        )
        if volumes:
            for volume in ("cc-pgdata", "cc-redisdata"):
                await self._run(["volume", "rm", "-f", volume], allow_failure=True)
        return await self.status()

    async def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "network": self.cfg.infra_network,
            "containers": {},
        }
        for role, container in (
            ("postgres", self.cfg.postgres_container),
            ("redis", self.cfg.redis_container),
        ):
            command = await self._run(
                ["inspect", "--format", "{{json .}}", container],
                timeout_s=30,
                allow_failure=True,
            )
            if not command.ok:
                result["containers"][role] = {"name": container, "status": "missing"}
                continue
            # Parse one JSON document rather than splitting on a space: health
            # log messages can contain spaces, newlines, and arbitrary text.
            try:
                inspected = json.loads(command.stdout)
            except json.JSONDecodeError:
                inspected = {}
            if not isinstance(inspected, dict):
                inspected = {}
            state = inspected.get("State") or {}
            host = inspected.get("HostConfig") or {}
            ports = host.get("PortBindings") or {}
            if not isinstance(state, dict):
                state = {}
            if not isinstance(ports, dict):
                ports = {}
            result["containers"][role] = {
                "name": container,
                "status": state.get("Status", "unknown"),
                "running": bool(state.get("Running")),
                "healthy": state.get("Health", {}).get("Status")
                if isinstance(state.get("Health"), dict)
                else None,
                "ports": ports,
            }
        return result
