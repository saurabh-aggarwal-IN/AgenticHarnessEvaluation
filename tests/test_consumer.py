from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from conftest import FakeContext
from harness_compare.consumer import ComparisonConsumer
from harness_compare.redis_bus import RedisBus


def test_envelope_parser_drops_malformed_messages() -> None:
    batches = [
        (
            "stream",
            [
                ("1-0", {"result": '{"job_id":"job-1","response":{}}'}),
                ("2-0", {"result": "not json"}),
                ("3-0", {}),
                ("4-0", {"result": '["not","an","object"]'}),
            ],
        )
    ]
    parsed = RedisBus._envelopes(batches)
    assert parsed == [("1-0", {"job_id": "job-1", "response": {}})]


class RecordingRunner:
    def __init__(self, *, fail: bool = False) -> None:
        self.comparisons: list[str] = []
        self.fail = fail

    async def run(self, comparison_id: str):
        self.comparisons.append(comparison_id)
        if self.fail:
            raise RuntimeError("runner exploded")
        return {"comparison_id": comparison_id}


@pytest.mark.asyncio
async def test_consumer_runs_and_acks_queued_comparison(fake_ctx: FakeContext) -> None:
    fake_ctx.store.comparisons["cmp-1"] = {
        "id": "cmp-1",
        "status": "QUEUED",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fake_ctx.bus.comparisons = ["cmp-1"]
    runner = RecordingRunner()
    consumer = ComparisonConsumer(fake_ctx, runner=runner, consumer="test")
    assert await consumer.run_once() is True
    assert runner.comparisons == ["cmp-1"]
    assert fake_ctx.bus.acked == ["1-0"]


@pytest.mark.asyncio
async def test_consumer_skips_terminal_comparison(fake_ctx: FakeContext) -> None:
    fake_ctx.store.comparisons["cmp-1"] = {"id": "cmp-1", "status": "COMPLETED"}
    fake_ctx.bus.comparisons = ["cmp-1"]
    runner = RecordingRunner()
    consumer = ComparisonConsumer(fake_ctx, runner=runner, consumer="test")
    assert await consumer.run_once() is True
    assert runner.comparisons == []
    assert fake_ctx.bus.acked == ["1-0"]


@pytest.mark.asyncio
async def test_consumer_survives_runner_failure(fake_ctx: FakeContext) -> None:
    fake_ctx.store.comparisons["cmp-1"] = {"id": "cmp-1", "status": "QUEUED"}
    fake_ctx.bus.comparisons = ["cmp-1"]
    consumer = ComparisonConsumer(fake_ctx, runner=RecordingRunner(fail=True), consumer="test")
    assert await consumer.run_once() is True
    assert fake_ctx.bus.acked == ["1-0"]


@pytest.mark.asyncio
async def test_consumer_returns_false_with_no_messages(fake_ctx: FakeContext) -> None:
    consumer = ComparisonConsumer(fake_ctx, runner=RecordingRunner(), consumer="test")
    assert await consumer.run_once() is False


@pytest.mark.asyncio
async def test_consumer_drain_processes_a_batch(fake_ctx: FakeContext) -> None:
    for index in range(3):
        comparison_id = f"cmp-{index}"
        fake_ctx.store.comparisons[comparison_id] = {
            "id": comparison_id,
            "status": "QUEUED",
        }
        fake_ctx.bus.comparisons.append(comparison_id)
    consumer = ComparisonConsumer(fake_ctx, runner=RecordingRunner(), consumer="test")
    assert await consumer.drain(limit=2) == 2
    assert len(fake_ctx.bus.comparisons) == 1
