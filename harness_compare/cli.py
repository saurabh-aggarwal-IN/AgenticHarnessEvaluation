from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from harness_compare.config import Config
from harness_compare.consumer import ComparisonConsumer
from harness_compare.context import AppContext
from harness_compare.infra import InfraManager
from harness_compare.models import CommitSet, ComparisonCreate, JobBudget
from harness_compare.service import ServiceError


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harness-compare",
        description=(
            "Build three clinical-coding harness commits and compare them on a "
            "synthetic dataset. Screening only; not clinical advice."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    infra = sub.add_parser("infra", help="manage local Postgres and Redis")
    infra.add_argument("action", choices=["up", "down", "status"])
    infra.add_argument("--volumes", action="store_true", help="also delete data volumes")

    dataset = sub.add_parser("dataset", help="register a dataset directory")
    dataset.add_argument("path")
    dataset.add_argument("--name")

    compare = sub.add_parser("compare", help="create and run a comparison")
    compare.add_argument("--repo", required=True, help="GitHub URL or local path")
    compare.add_argument("--h1", required=True, help="commit SHA for the H1 harness")
    compare.add_argument("--h2", required=True, help="commit SHA for the H2 harness")
    compare.add_argument("--h3", required=True, help="commit SHA for the H3 harness")
    compare.add_argument("--dataset-id", help="registered dataset id")
    compare.add_argument("--dataset-path", help="register this directory first")
    compare.add_argument(
        "--model",
        action="append",
        required=True,
        help="router model slug; repeat for a model matrix",
    )
    compare.add_argument("--trials", type=int, default=1)
    compare.add_argument("--name")
    compare.add_argument("--notes")
    compare.add_argument("--max-llm-calls", type=int)
    compare.add_argument("--max-wall-seconds", type=float)
    compare.add_argument("--max-cost-usd", type=float)
    compare.add_argument(
        "--enqueue",
        action="store_true",
        help="only enqueue; let an orchestrator worker execute it",
    )
    compare.add_argument("--out", help="write the full summary JSON here")

    which = sub.add_parser("status", help="show a comparison")
    which.add_argument("comparison_id")

    listing = sub.add_parser("list", help="list comparisons")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--status")

    radar = sub.add_parser("radar", help="print radar/summary data")
    radar.add_argument("comparison_id")
    radar.add_argument("--out")

    cancel = sub.add_parser("cancel", help="request cancellation")
    cancel.add_argument("comparison_id")

    worker = sub.add_parser("worker", help="consume queued comparisons")
    worker.add_argument("--drain", type=int, default=0, help="process at most N then exit")

    return parser


async def _connect(cfg: Config) -> AppContext:
    ctx = AppContext.from_config(cfg)
    await ctx.connect()
    return ctx


async def _run(args: argparse.Namespace, cfg: Config) -> int:
    if args.command == "infra":
        manager = InfraManager(cfg)
        if args.action == "up":
            _print(await manager.up())
        elif args.action == "down":
            _print(await manager.down(volumes=args.volumes))
        else:
            _print(await manager.status())
        return 0

    ctx = await _connect(cfg)
    try:
        if args.command == "dataset":
            row = await ctx.service.register_dataset_path(
                Path(args.path), name=args.name
            )
            _print(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "case_count": row["case_count"],
                    "digest": row["digest"],
                    "path": row["path"],
                }
            )
            return 0

        if args.command == "worker":
            consumer = ComparisonConsumer(ctx)
            if args.drain:
                processed = await consumer.drain(limit=args.drain)
                _print({"processed": processed})
                return 0
            try:
                await consumer.run()
            except KeyboardInterrupt:
                pass
            return 0

        if args.command == "list":
            rows = await ctx.store.list_comparisons(
                limit=args.limit, status=args.status
            )
            _print(
                [
                    {
                        "id": row["id"],
                        "name": row["name"],
                        "status": row["status"],
                        "stage": row.get("stage"),
                        "progress": row.get("progress"),
                        "created_at": str(row.get("created_at")),
                    }
                    for row in rows
                ]
            )
            return 0

        if args.command == "status":
            row = await ctx.store.get_comparison(args.comparison_id)
            if row is None:
                print(f"comparison not found: {args.comparison_id}", file=sys.stderr)
                return 2
            counts = await ctx.store.count_jobs(args.comparison_id)
            runs = await ctx.store.list_harness_runs(args.comparison_id)
            _print(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "status": row["status"],
                    "stage": row.get("stage"),
                    "progress": row.get("progress"),
                    "jobs": counts,
                    "harness_runs": [
                        {
                            "harness_id": run["harness_id"],
                            "commit": run["commit"],
                            "status": run["status"],
                            "error": run.get("error"),
                        }
                        for run in runs
                    ],
                    "error": row.get("error"),
                }
            )
            return 0

        if args.command == "radar":
            summary = await ctx.service.radar(args.comparison_id)
            if summary is None:
                print(f"comparison not found: {args.comparison_id}", file=sys.stderr)
                return 2
            if args.out:
                Path(args.out).write_text(
                    json.dumps(summary, indent=2, default=str), encoding="utf-8"
                )
                print(f"wrote {args.out}")
            _print(_compact_summary(summary))
            return 0

        if args.command == "cancel":
            row = await ctx.service.cancel_comparison(args.comparison_id)
            if row is None:
                print(f"comparison not found: {args.comparison_id}", file=sys.stderr)
                return 2
            _print(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "cancel_requested": row.get("cancel_requested"),
                }
            )
            return 0

        if args.command == "compare":
            dataset_id = args.dataset_id
            if args.dataset_path:
                dataset = await ctx.service.register_dataset_path(
                    Path(args.dataset_path)
                )
                dataset_id = str(dataset["id"])
            if not dataset_id:
                print("--dataset-id or --dataset-path is required", file=sys.stderr)
                return 2
            budget = JobBudget(
                max_llm_calls=args.max_llm_calls,
                max_wall_seconds=args.max_wall_seconds,
                max_cost_usd=args.max_cost_usd,
            )
            body = ComparisonCreate(
                name=args.name,
                repo_url=args.repo,
                commits=CommitSet(h1=args.h1, h2=args.h2, h3=args.h3),
                dataset_id=dataset_id,
                models=args.model,
                trials=args.trials,
                budget=budget,
                notes=args.notes,
            )
            row = await ctx.service.create_comparison(body)
            _print(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "jobs": (row.get("progress") or {}).get("total_jobs"),
                }
            )
            if args.enqueue:
                return 0
            summary = await ctx.runner.run(str(row["id"]))
            final = await ctx.store.get_comparison(str(row["id"]))
            if args.out and summary:
                Path(args.out).write_text(
                    json.dumps(summary, indent=2, default=str), encoding="utf-8"
                )
                print(f"wrote {args.out}")
            _print(
                {
                    "id": row["id"],
                    "status": (final or row)["status"],
                    "error": (final or row).get("error"),
                    "summary": _compact_summary(summary)
                    if isinstance(summary, dict)
                    else None,
                }
            )
            return 0 if (final or row)["status"] == "COMPLETED" else 1

        print(f"unknown command {args.command}", file=sys.stderr)
        return 2
    finally:
        await ctx.close()


def _compact_summary(summary: dict[str, Any] | None) -> dict[str, Any] | None:
    if not summary:
        return summary
    return {
        "comparison_id": summary.get("comparison_id"),
        "status": summary.get("status"),
        "series": [
            {
                "label": series.get("label"),
                "n": series.get("n"),
                "values": series.get("values"),
            }
            for series in summary.get("series") or []
        ],
        "aggregate": [
            {
                "harness_id": series.get("harness_id"),
                "n": series.get("n"),
                "values": series.get("values"),
            }
            for series in summary.get("aggregate") or []
        ],
        "paired": summary.get("paired"),
        "warnings": summary.get("warnings"),
        "disclaimer": summary.get("disclaimer"),
    }


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    cfg = Config.from_env()
    try:
        code = asyncio.run(_run(args, cfg))
    except ServiceError as exc:
        print(
            json.dumps(
                {"error": {"code": exc.code, "message": exc.message}}, indent=2
            ),
            file=sys.stderr,
        )
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
