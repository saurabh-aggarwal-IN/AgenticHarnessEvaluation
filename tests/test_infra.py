from __future__ import annotations

from pathlib import Path

import pytest

from harness_compare.infra import InfraManager
from harness_compare.process import CommandResult


class InfraRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
        self.calls.append(list(args))
        joined = " ".join(args)
        if "redis-cli ping" in joined:
            return CommandResult(args, 0, "PONG", "", 1)
        if "network inspect" in joined:
            return CommandResult(args, 1, "", "not found", 1)
        if "inspect --format" in joined:
            if "cc-postgres" in joined:
                value = (
                    '{"State":{"Status":"running","Running":true,'
                    '"Health":{"Status":"healthy"}},'
                    '"HostConfig":{"PortBindings":{"5432/tcp":[{"HostPort":"55432"}]}}}'
                )
            elif "cc-redis" in joined:
                value = (
                    '{"State":{"Status":"running","Running":true,'
                    '"Health":{"Status":"healthy"}},'
                    '"HostConfig":{"PortBindings":{"6379/tcp":[{"HostPort":"56379"}]}}}'
                )
            else:
                return CommandResult(args, 1, "", "not found", 1)
            return CommandResult(args, 0, value, "", 1)
        if "inspect" in joined:
            return CommandResult(args, 1, "", "not found", 1)
        return CommandResult(args, 0, "", "", 1)


@pytest.mark.asyncio
async def test_infra_up_creates_postgres_redis_and_network(cfg, tmp_path: Path) -> None:
    runner = InfraRunner()
    manager = InfraManager(cfg, runner)
    status = await manager.up()
    joined = [" ".join(call) for call in runner.calls]
    assert any("network create" in call and "cc-infra" in call for call in joined)
    pg = next(call for call in joined if "run -d --name cc-postgres" in call)
    assert "127.0.0.1:55432:5432" in pg
    assert "cc-pgdata:/var/lib/postgresql/data" in pg
    assert "pg_isready" in pg
    redis = next(call for call in joined if "run -d --name cc-redis" in call)
    assert "127.0.0.1:56379:6379" in redis
    assert "redis-server --appendonly yes" in redis
    assert "cc-redisdata:/data" in redis
    assert status["containers"]["postgres"]["running"] is True
    assert status["containers"]["redis"]["healthy"] == "healthy"


@pytest.mark.asyncio
async def test_infra_up_starts_existing_containers(cfg, tmp_path: Path) -> None:
    class Existing(InfraRunner):
        async def run(self, args, *, cwd=None, env=None, timeout_s=300.0):
            self.calls.append(list(args))
            joined = " ".join(args)
            if "redis-cli ping" in joined:
                return CommandResult(args, 0, "PONG", "", 1)
            if "inspect" in joined and "run" not in joined:
                return CommandResult(
                    args,
                    0,
                    '{"Status":"exited","Running":false} {}',
                    "",
                    1,
                )
            return CommandResult(args, 0, "", "", 1)

    runner = Existing()
    manager = InfraManager(cfg, runner)
    await manager.up()
    joined = [" ".join(call) for call in runner.calls]
    assert any("start cc-postgres" in call for call in joined)
    assert any("start cc-redis" in call for call in joined)
    assert not any("run -d --name" in call for call in joined)


@pytest.mark.asyncio
async def test_infra_down_can_remove_volumes(cfg) -> None:
    runner = InfraRunner()
    manager = InfraManager(cfg, runner)
    await manager.down(volumes=True)
    joined = [" ".join(call) for call in runner.calls]
    assert any("rm -f cc-postgres" in call for call in joined)
    assert any("rm -f cc-redis" in call for call in joined)
    assert any("volume rm -f cc-pgdata" in call for call in joined)
    assert any("volume rm -f cc-redisdata" in call for call in joined)
