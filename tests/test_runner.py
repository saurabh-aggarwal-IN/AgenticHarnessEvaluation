from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import (
    CASE_ONE_NOTE,
    CASE_TWO_NOTE,
    FakeBus,
    FakeContext,
    FakeDocker,
)
from harness_compare.docker_runner import DockerError
from harness_compare.models import CommitSet, ComparisonCreate
from harness_compare.runner import plan_jobs, request_budget
from harness_compare.dataset import load_dataset
from harness_compare.service import ComparisonService
from harness_compare.runner import ComparisonRunner


def test_plan_jobs_covers_every_harness_model_case_trial(
    cfg, dataset_dir: Path
) -> None:
    bundle = load_dataset(dataset_dir)
    plan = plan_jobs(
        comparison_id="cmp-1",
        bundle=bundle,
        models=["model/a", "model/b"],
        trials=2,
    )
    assert set(plan) == {"h1", "h2", "h3"}
    for harness_id, jobs in plan.items():
        assert len(jobs) == 2 * 2 * 2
        ids = [job.id for job in jobs]
        assert len(ids) == len(set(ids))
        assert all(job.id.startswith("job-") for job in jobs)
    all_ids = [job.id for jobs in plan.values() for job in jobs]
    assert len(all_ids) == len(set(all_ids))


def test_job_payload_has_no_grading_material(cfg, dataset_dir: Path) -> None:
    bundle = load_dataset(dataset_dir)
    plan = plan_jobs(
        comparison_id="cmp-1", bundle=bundle, models=["model/a"], trials=1
    )
    payload = plan["h2"][0].payload(
        comparison_id="cmp-1",
        budget=request_budget(cfg),
    )
    serialized = json.dumps(payload)
    for forbidden in ("expectedCodes", "forbiddenCodes", "historySnippet"):
        assert forbidden not in serialized
    assert payload["comparison_id"] == "cmp-1"
    assert payload["slot"] == "h2"
    assert payload["request"]["case_id"] == plan["h2"][0].case_id


def test_request_budget_clamps_overrides(cfg) -> None:
    budget = request_budget(
        replace(cfg, server_max_llm_calls=4, server_max_wall_seconds=60.0),
        {
            "max_llm_calls": 32,
            "max_wall_seconds": 900.0,
            "max_cost_usd": 12.0,
        },
    )
    assert budget["max_llm_calls"] == 4
    assert budget["max_wall_seconds"] == 60.0
    assert budget["max_cost_usd"] == cfg.server_max_cost_usd


def test_worker_env_is_complete_and_scoped(cfg, dataset_dir: Path) -> None:
    ctx = FakeContext(cfg)
    runner = ComparisonRunner(ctx.cfg, ctx.store, ctx.bus, ctx.docker, ctx.git)
    env = runner._worker_env("cmp-1", "h1")
    assert env["HARNESS_ID"] == "h1"
    assert env["HARNESS_SLOT"] == "h1"
    assert env["COMPARISON_ID"] == "cmp-1"
    assert env["STREAM_JOBS"] == "cc:jobs:cmp-1:h1"
    assert env["STREAM_RESULTS"] == "cc:results:cmp-1"
    assert env["REDIS_URL"] == cfg.worker_redis_url
    assert env["LLM_BASE_URL"] == cfg.worker_llm_base_url
    assert env["ALLOW_PHI"] == "false"
    assert env["GUARDRAIL_STRICT"] == "false"
    serialized = json.dumps(env)
    assert "expectedCodes" not in serialized
    assert "forbiddenCodes" not in serialized
    assert "goldCodes" not in serialized


class EchoBus(FakeBus):
    """Simulate a worker: every dispatched job produces a perfect result."""

    async def enqueue_job(self, stream: str, job: dict) -> str:
        await super().enqueue_job(stream, job)
        self.results.append(
            {
                "job_id": job["job_id"],
                "comparison_id": job["comparison_id"],
                "slot": job["slot"],
                "case_id": job["case_id"],
                "trial": job["trial"],
                "model": job["model"],
                "response": perfect_response(job["case_id"]),
            }
        )
        return "1-0"

    async def worker_heartbeats(self, comparison_id: str):
        return {harness_id: [{"slot": harness_id}] for harness_id in ("h1", "h2", "h3")}


def perfect_response(case_id: str) -> dict:
    if case_id == "C01":
        codes = [
            {
                "code": "E11.9",
                "system": "ICD-10-CM",
                "snippet": "stable chronic T2DM without complications",
                "rationale": "documented",
            },
            {
                "code": "I50.22",
                "system": "ICD-10-CM",
                "snippet": "stable chronic systolic heart failure",
                "rationale": "documented",
            },
            {
                "code": "93000",
                "system": "CPT",
                "snippet": "A 12-lead EKG was performed in the office today",
                "rationale": "documented",
            },
        ]
        note = CASE_ONE_NOTE
    else:
        codes = [
            {
                "code": "N39.0",
                "system": "ICD-10-CM",
                "snippet": "uncomplicated urinary tract infection",
                "rationale": "documented",
            }
        ]
        note = CASE_TWO_NOTE
    assert note
    return {
        "request_id": "req",
        "case_id": case_id,
        "codes": codes,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "total_tokens": 150,
            "calls": 1,
        },
        "latency_ms": 250,
        "trace": [{"type": "history_read", "payload": {}}],
        "budget": {"used": {"cost_usd": 0.001}, "exceeded": False},
        "guardrails": {"input_flags": [], "output_flags": [], "blocked": False},
        "error": None,
    }


async def _make_ready_ctx(
    cfg, dataset_dir: Path, *, docker: FakeDocker | None = None
) -> tuple[FakeContext, str]:
    ctx = FakeContext(
        replace(
            cfg,
            worker_ready_timeout_s=10.0,
            job_timeout_s=30.0,
            comparison_timeout_s=60.0,
            poll_interval_s=0.1,
            progress_interval_s=0.2,
            max_inflight_per_harness=1,
            worker_restarts=1,
        )
    )
    ctx.bus = EchoBus()
    ctx.service = ComparisonService(ctx.cfg, ctx.store, ctx.bus)
    if docker is not None:
        ctx.docker = docker
    ctx.runner = ComparisonRunner(ctx.cfg, ctx.store, ctx.bus, ctx.docker, ctx.git)
    dataset = await ctx.service.register_dataset_path(dataset_dir)
    row = await ctx.service.create_comparison(
        ComparisonCreate(
            name="test run",
            repo_url="https://github.com/example/repo",
            commits=CommitSet(h1="938bb8b", h2="6c94476", h3="517120c"),
            dataset_id=dataset["id"],
            models=["model/a"],
            trials=1,
        )
    )
    return ctx, row["id"]


@pytest.mark.asyncio
async def test_runner_end_to_end_with_fake_workers(cfg, dataset_dir: Path) -> None:
    ctx, comparison_id = await _make_ready_ctx(cfg, dataset_dir)
    summary = await ctx.runner.run(comparison_id)
    assert summary is not None
    assert summary["status"] == "COMPLETED"
    assert len(ctx.store.jobs) == 6
    assert all(row["status"] == "COMPLETED" for row in ctx.store.jobs.values())
    assert all(row["evaluation"]["f1"] == 1.0 for row in ctx.store.jobs.values())
    for row in ctx.store.jobs.values():
        expected_history = row["case_id"] == "C01" and row["harness_id"] != "h1"
        assert row["evaluation"]["history_present"] is expected_history
    assert len(summary["series"]) == 3
    assert all(series["n"] == 2 for series in summary["series"])
    assert all(series["values"]["accuracy"] == 1.0 for series in summary["series"])
    assert summary["paired"]
    assert len(ctx.docker.built) == 3
    assert len(ctx.docker.started) == 3
    assert ctx.bus.expired == 1
    row = await ctx.store.get_comparison(comparison_id)
    assert row is not None
    assert row["status"] == "COMPLETED"
    assert row["progress"]["done_jobs"] == 6
    assert row["progress"]["percent"] == 100.0


class BrokenH2Docker(FakeDocker):
    async def build_image(self, **kwargs):
        if kwargs["harness_id"] == "h2":
            raise DockerError("DOCKER_COMMAND_FAILED", "h2 build failed")
        return await super().build_image(**kwargs)


@pytest.mark.asyncio
async def test_runner_isolates_a_broken_harness_build(
    cfg, dataset_dir: Path
) -> None:
    ctx, comparison_id = await _make_ready_ctx(
        cfg, dataset_dir, docker=BrokenH2Docker()
    )
    summary = await ctx.runner.run(comparison_id)
    assert summary is not None
    assert summary["status"] == "COMPLETED"
    h2_jobs = [
        row for row in ctx.store.jobs.values() if row["harness_id"] == "h2"
    ]
    assert len(h2_jobs) == 2
    assert all(row["status"] == "FAILED" for row in h2_jobs)
    assert all(
        row["evaluation"]["error_code"] == "BUILD_FAILED" for row in h2_jobs
    )
    runs = {run["harness_id"]: run for run in await ctx.store.list_harness_runs(comparison_id)}
    assert runs["h2"]["status"] == "BUILD_FAILED"
    h2_series = next(
        series for series in summary["series"] if series["harness_id"] == "h2"
    )
    assert h2_series["metrics"]["success_rate"] == 0.0


@pytest.mark.asyncio
async def test_runner_cancellation_marks_remaining_jobs(
    cfg, dataset_dir: Path
) -> None:
    ctx, comparison_id = await _make_ready_ctx(cfg, dataset_dir)
    await ctx.store.update_comparison(comparison_id, cancel_requested=True)
    summary = await ctx.runner.run(comparison_id)
    assert summary is not None
    assert summary["status"] == "CANCELLED"
    assert len(ctx.store.jobs) == 6
    assert all(row["status"] == "CANCELLED" for row in ctx.store.jobs.values())
    row = await ctx.store.get_comparison(comparison_id)
    assert row is not None
    assert row["status"] == "CANCELLED"
    assert row["error"]["code"] == "CANCELLED"


@pytest.mark.asyncio
async def test_runner_rejects_unknown_comparison(cfg) -> None:
    ctx = FakeContext(cfg)
    assert await ctx.runner.run("cmp-missing") is None
