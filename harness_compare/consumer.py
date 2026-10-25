from __future__ import annotations

import asyncio
import logging
from typing import Any

from harness_compare.context import AppContext
from harness_compare.runner import ComparisonRunner

logger = logging.getLogger("harness_compare.consumer")

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


class ComparisonConsumer:
    """Redis Streams consumer that executes queued comparisons."""

    def __init__(
        self,
        ctx: AppContext,
        runner: ComparisonRunner | None = None,
        *,
        consumer: str | None = None,
    ) -> None:
        self.ctx = ctx
        self.runner = runner or ctx.runner
        self.consumer = consumer or f"{ctx.cfg.run_consumer}-cmp"

    async def run(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.run_once(stop)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - keep the consumer alive
                logger.exception("comparison consumer iteration failed")
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=2.0
                    )
                except asyncio.TimeoutError:
                    pass

    async def run_once(self, stop: asyncio.Event | None = None) -> bool:
        stop = stop or asyncio.Event()
        pending = await self.ctx.bus.read_pending_comparisons(self.consumer)
        if pending:
            message_id, comparison_id = pending[0]
        else:
            messages = await self.ctx.bus.read_comparisons(self.consumer)

            if not messages:
                return False
            message_id, comparison_id = messages[0]
        if stop.is_set():
            return False
        try:
            row = await self.ctx.store.get_comparison(comparison_id)
            if row is None:
                logger.warning("queued comparison %s does not exist", comparison_id)
            elif row["status"] in TERMINAL:
                logger.info(
                    "skipping comparison %s in status %s",
                    comparison_id,
                    row["status"],
                )
            else:
                logger.info("running comparison %s", comparison_id)
                await self.runner.run(comparison_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a single comparison must not kill the loop
            logger.exception("comparison %s crashed", comparison_id)
        finally:
            await self.ctx.bus.ack_comparison(message_id)
        return True

    async def drain(self, *, limit: int = 20) -> int:
        count = 0
        while count < limit:
            did_work = await self.run_once()
            if not did_work:
                break
            count += 1
        return count


async def serve(ctx: AppContext, stop: asyncio.Event | None = None) -> None:
    consumer = ComparisonConsumer(ctx)
    await consumer.run(stop)
