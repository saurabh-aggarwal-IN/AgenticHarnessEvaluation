from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = logging.getLogger("harness_compare.redis")


class BusError(RuntimeError):
    pass


class RedisBus:
    """Redis Streams transport for comparisons, jobs, and results."""

    def __init__(
        self,
        url: str,
        *,
        comparison_stream: str = "cc:comparisons",
        comparison_group: str = "orchestrators",
        results_group: str = "orchestrator",
    ) -> None:
        self.url = url
        self.comparison_stream = comparison_stream
        self.comparison_group = comparison_group
        self.results_group = results_group
        self.redis: Redis | None = None

    async def connect(self) -> None:
        if self.redis is not None:
            return
        self.redis = Redis.from_url(
            self.url,
            decode_responses=True,
            health_check_interval=15,
        )
        await self.redis.ping()

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None

    def _redis(self) -> Redis:
        if self.redis is None:
            raise BusError("redis is not connected")
        return self.redis

    async def ping(self) -> str:
        try:
            await self._redis().ping()
            return "ok"
        except Exception as exc:  # noqa: BLE001 - health must not raise
            return f"error: {type(exc).__name__}"

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis().xgroup_create(
                stream, group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def init_comparison_queue(self) -> None:
        await self._ensure_group(self.comparison_stream, self.comparison_group)

    async def enqueue_comparison(self, comparison_id: str) -> str:
        return str(
            await self._redis().xadd(
                self.comparison_stream,
                {"comparison_id": comparison_id},
                maxlen=10_000,
                approximate=True,
            )
        )

    async def read_comparisons(
        self, consumer: str, *, block_ms: int = 5_000, count: int = 1
    ) -> list[tuple[str, str]]:
        batches = await self._redis().xreadgroup(
            self.comparison_group,
            consumer,
            {self.comparison_stream: ">"},
            count=count,
            block=block_ms,
        )
        result: list[tuple[str, str]] = []
        for _stream, messages in batches or []:
            for message_id, fields in messages:
                comparison_id = fields.get("comparison_id")
                if comparison_id:
                    result.append((str(message_id), str(comparison_id)))
        return result

    async def read_pending_comparisons(
        self, consumer: str, *, count: int = 10
    ) -> list[tuple[str, str]]:
        batches = await self._redis().xreadgroup(
            self.comparison_group,
            consumer,
            {self.comparison_stream: "0"},
            count=count,
        )
        result: list[tuple[str, str]] = []
        for _stream, messages in batches or []:
            for message_id, fields in messages:
                comparison_id = fields.get("comparison_id")
                if comparison_id:
                    result.append((str(message_id), str(comparison_id)))
        return result

    async def ack_comparison(self, message_id: str) -> None:
        await self._redis().xack(
            self.comparison_stream, self.comparison_group, message_id
        )

    async def init_results(self, stream: str) -> None:
        await self._ensure_group(stream, self.results_group)

    async def enqueue_job(self, stream: str, job: dict[str, Any]) -> str:
        return str(
            await self._redis().xadd(
                stream,
                {"job": json.dumps(job, separators=(",", ":"))},
                maxlen=200_000,
                approximate=True,
            )
        )

    async def pending_results(
        self, stream: str, consumer: str, *, count: int = 100
    ) -> list[tuple[str, dict[str, Any]]]:
        batches = await self._redis().xreadgroup(
            self.results_group,
            consumer,
            {stream: "0"},
            count=count,
        )
        return self._envelopes(batches)

    async def read_results(
        self,
        stream: str,
        consumer: str,
        *,
        block_ms: int = 1_000,
        count: int = 50,
    ) -> list[tuple[str, dict[str, Any]]]:
        batches = await self._redis().xreadgroup(
            self.results_group,
            consumer,
            {stream: ">"},
            count=count,
            block=block_ms,
        )
        return self._envelopes(batches)

    @staticmethod
    def _envelopes(
        batches: Any,
    ) -> list[tuple[str, dict[str, Any]]]:
        result: list[tuple[str, dict[str, Any]]] = []
        for _stream, messages in batches or []:
            for message_id, fields in messages:
                raw = fields.get("result")
                if not raw:
                    continue
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("dropping malformed result message %s", message_id)
                    continue
                if isinstance(envelope, dict):
                    result.append((str(message_id), envelope))
        return result

    async def ack_result(self, stream: str, message_id: str) -> None:
        await self._redis().xack(stream, self.results_group, message_id)

    async def worker_ready(
        self, comparison_id: str, harness_id: str
    ) -> bool:
        heartbeats = await self.worker_heartbeats(comparison_id)
        return bool(heartbeats.get(harness_id))

    async def worker_heartbeats(
        self, comparison_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        async for key in self._redis().scan_iter(
            match="cc:worker:*", count=100
        ):
            if not isinstance(key, str):
                continue
            parts = key.split(":")
            if len(parts) < 4:
                continue
            harness_id = parts[2]
            payload = await self._redis().hgetall(key)
            if not payload or payload.get("comparison_id") != comparison_id:
                continue
            result.setdefault(harness_id, []).append(
                {"key": key, **payload}
            )
        return result

    async def expire_streams(
        self, comparison_id: str, harness_ids: list[str], seconds: int = 604_800
    ) -> None:
        """Keep streams briefly for debugging without retaining them forever."""
        for harness_id in harness_ids:
            await self._redis().expire(
                f"cc:jobs:{comparison_id}:{harness_id}", seconds
            )
        await self._redis().expire(
            f"cc:results:{comparison_id}", seconds
        )

    async def publish_cancel(self, comparison_id: str) -> None:
        """A best-effort wake-up so a blocked runner notices cancellation."""
        await self._redis().xadd(
            f"cc:cancel:{comparison_id}",
            {"comparison_id": comparison_id},
            maxlen=10,
            approximate=True,
        )
        await self._redis().expire(f"cc:cancel:{comparison_id}", 3_600)
