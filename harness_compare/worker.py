from __future__ import annotations

import asyncio
import logging
import os
import signal

from harness_compare.config import Config
from harness_compare.consumer import ComparisonConsumer
from harness_compare.context import AppContext

logger = logging.getLogger("harness_compare.worker")


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    cfg = Config.from_env()
    ctx = AppContext.from_config(cfg)
    await ctx.connect()
    try:
        await ctx.store.fail_orphaned_runs()
    except Exception:  # noqa: BLE001
        logger.exception("could not mark orphaned comparisons")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    consumer = ComparisonConsumer(ctx)
    try:
        await consumer.run(stop)
    finally:
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
