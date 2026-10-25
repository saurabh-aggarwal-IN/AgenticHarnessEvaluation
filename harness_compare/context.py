from __future__ import annotations

import logging
from dataclasses import dataclass, field

from harness_compare.config import Config
from harness_compare.docker_runner import DockerClient
from harness_compare.git_manager import GitManager
from harness_compare.db import PostgresStore
from harness_compare.redis_bus import RedisBus
from harness_compare.runner import ComparisonRunner
from harness_compare.service import ComparisonService

logger = logging.getLogger("harness_compare.context")


@dataclass
class AppContext:
    cfg: Config
    store: PostgresStore
    bus: RedisBus
    docker: DockerClient
    git: GitManager
    service: ComparisonService
    runner: ComparisonRunner
    connected: bool = field(default=False)

    @classmethod
    def from_config(cls, cfg: Config) -> "AppContext":
        store = PostgresStore(cfg.database_url)
        bus = RedisBus(
            cfg.redis_url,
            comparison_stream=cfg.comparison_stream,
            comparison_group=cfg.comparison_group,
            results_group=cfg.results_group,
        )
        docker = DockerClient(cfg)
        git = GitManager(cfg)
        service = ComparisonService(cfg, store, bus)
        runner = ComparisonRunner(cfg, store, bus, docker, git)
        return cls(
            cfg=cfg,
            store=store,
            bus=bus,
            docker=docker,
            git=git,
            service=service,
            runner=runner,
        )

    async def connect(self) -> None:
        if self.connected:
            return
        self.cfg.ensure_dirs()
        await self.store.connect()
        await self.store.migrate()
        await self.bus.connect()
        await self.bus.init_comparison_queue()
        self.connected = True
        logger.info(
            "orchestrator connected database=%s redis=%s",
            _safe_url(self.cfg.database_url),
            _safe_url(self.cfg.redis_url),
        )

    async def close(self) -> None:
        try:
            await self.bus.close()
        finally:
            await self.store.close()
            self.connected = False


def _safe_url(url: str) -> str:
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    return f"{scheme}://***@{rest.split('@', 1)[1]}"
