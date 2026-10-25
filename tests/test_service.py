from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from conftest import FakeContext, register_dataset
from harness_compare.models import CommitSet, ComparisonCreate, JobBudget
from harness_compare.service import ComparisonService, ServiceError


def _body(dataset_id: str, **overrides):
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": CommitSet(h1="938bb8b", h2="6c94476", h3="517120c"),
        "dataset_id": dataset_id,
        "models": ["model/a"],
        "trials": 1,
    }
    payload.update(overrides)
    return ComparisonCreate(**payload)


@pytest.mark.asyncio
async def test_register_dataset_snapshots_files(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    row = await fake_ctx.service.register_dataset_path(dataset_dir, name="test")
    assert row["case_count"] == 2
    snapshot = Path(row["path"])
    assert (snapshot / "cases" / "C01-t2dm-chf-ekg.json").is_file()
    assert (snapshot / "expected" / "C01.expected.json").is_file()
    assert row["id"].startswith("ds-")
    assert row["manifest"]["cases"] == ["C01", "C02"]


@pytest.mark.asyncio
async def test_register_same_dataset_is_idempotent(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    first = await fake_ctx.service.register_dataset_path(dataset_dir)
    second = await fake_ctx.service.register_dataset_path(dataset_dir)
    assert first["id"] == second["id"]
    assert len(fake_ctx.store.datasets) == 1


@pytest.mark.asyncio
async def test_register_upload_extracts_and_snapshots(
    fake_ctx: FakeContext, uploaded_zip: Path
) -> None:
    row = await fake_ctx.service.register_dataset_upload(
        uploaded_zip, name="uploaded", original_name="dataset.zip"
    )
    assert row["name"] == "uploaded"
    assert row["case_count"] == 2
    assert Path(row["path"]).is_dir()


@pytest.mark.asyncio
async def test_create_comparison_queues_jobs(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    assert row["status"] == "QUEUED"
    assert row["progress"]["total_jobs"] == 6
    assert row["progress"]["by_harness"]["h1"]["total"] == 2
    assert row["config"]["budget"]["max_llm_calls"] == fake_ctx.cfg.server_max_llm_calls
    assert row["commits"] if "commits" in row else True
    assert row["commit_h1"] == "938bb8b"
    assert fake_ctx.bus.comparisons == [row["id"]]
    assert fake_ctx.store.events[row["id"]][-1]["type"] == "comparison_created"


@pytest.mark.asyncio
async def test_budget_override_is_clamped(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(
        _body(
            dataset["id"],
            budget=JobBudget(max_llm_calls=32, max_wall_seconds=3_000),
        )
    )
    budget = row["config"]["budget"]
    assert budget["max_llm_calls"] == fake_ctx.cfg.server_max_llm_calls
    assert budget["max_wall_seconds"] == min(
        fake_ctx.cfg.server_max_wall_seconds, fake_ctx.cfg.job_budget_wall_seconds
    )


@pytest.mark.asyncio
async def test_create_rejects_unregistered_dataset(fake_ctx: FakeContext) -> None:
    with pytest.raises(ServiceError) as exc:
        await fake_ctx.service.create_comparison(_body("ds-missing"))
    assert exc.value.code == "DATASET_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_rejects_untrusted_repo_host(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    with pytest.raises(ServiceError) as exc:
        await fake_ctx.service.create_comparison(
            _body(dataset["id"], repo_url="https://gitlab.com/example/repo")
        )
    assert exc.value.code == "REPO_HOST"


@pytest.mark.asyncio
async def test_create_requires_llm_configuration(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    service = ComparisonService(
        replace(fake_ctx.cfg, llm_base_url="", worker_llm_base_url=""),
        fake_ctx.store,
        fake_ctx.bus,
    )
    with pytest.raises(ServiceError) as exc:
        await service.create_comparison(_body(dataset["id"]))
    assert exc.value.code == "LLM_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_create_enforces_job_limit(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    ctx = FakeContext(replace(fake_ctx.cfg, max_jobs=5))
    dataset_id = register_dataset(ctx.store, Path(dataset["path"]))
    with pytest.raises(ServiceError) as exc:
        await ctx.service.create_comparison(_body(dataset_id))
    assert exc.value.code == "TOO_MANY_JOBS"


@pytest.mark.asyncio
async def test_active_comparison_limit(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    ctx = FakeContext(replace(fake_ctx.cfg, max_active_comparisons=1))
    dataset_id = register_dataset(ctx.store, Path(dataset["path"]))
    ctx.store.comparisons["other"] = {
        "id": "other",
        "status": "RUNNING",
    }
    with pytest.raises(ServiceError) as exc:
        await ctx.service.create_comparison(_body(dataset_id))
    assert exc.value.code == "TOO_MANY_ACTIVE_COMPARISONS"


@pytest.mark.asyncio
async def test_cancel_queued_comparison_is_immediate(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    cancelled = await fake_ctx.service.cancel_comparison(row["id"])
    assert cancelled is not None
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cancel_requested"] is True
    assert cancelled["error"]["code"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_running_comparison_signals_runner(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    fake_ctx.store.comparisons[row["id"]]["status"] = "RUNNING"
    cancelled = await fake_ctx.service.cancel_comparison(row["id"])
    assert cancelled is not None
    assert cancelled["status"] == "CANCELLING"
    assert fake_ctx.bus.cancels == [row["id"]]


@pytest.mark.asyncio
async def test_cancel_is_idempotent_for_terminal_rows(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    fake_ctx.store.comparisons[row["id"]]["status"] = "COMPLETED"
    result = await fake_ctx.service.cancel_comparison(row["id"])
    assert result is not None
    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_radar_returns_stored_summary(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    fake_ctx.store.comparisons[row["id"]]["summary"] = {
        "comparison_id": row["id"],
        "series": [],
        "axes": [],
            "warnings": ["stored"],
    }
    summary = await fake_ctx.service.radar(row["id"])
    assert summary is not None
    assert summary["warnings"] == ["stored"]


@pytest.mark.asyncio
async def test_radar_generates_live_summary(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    summary = await fake_ctx.service.radar(row["id"])
    assert summary is not None
    assert len(summary["series"]) == 3
    assert summary["overall"]["n"] == 0
    assert summary["disclaimer"]


@pytest.mark.asyncio
async def test_queue_failure_marks_comparison_failed(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)

    async def explode(_comparison_id: str) -> str:
        raise ConnectionError("redis down")

    fake_ctx.bus.enqueue_comparison = explode  # type: ignore[method-assign]
    with pytest.raises(ServiceError) as exc:
        await fake_ctx.service.create_comparison(_body(dataset["id"]))
    assert exc.value.code == "QUEUE_UNAVAILABLE"
    row = next(iter(fake_ctx.store.comparisons.values()))
    assert row["status"] == "FAILED"
    assert row["error"]["retryable"] is True


@pytest.mark.asyncio
async def test_retry_failed_jobs_resets_jobs_and_reenqueues(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset = await fake_ctx.service.register_dataset_path(dataset_dir)
    row = await fake_ctx.service.create_comparison(_body(dataset["id"]))
    cmp_id = row["id"]

    # Seed one completed job and two failed jobs
    await fake_ctx.store.create_jobs([
        {
            "id": "job-1",
            "comparison_id": cmp_id,
            "case_id": "C01",
            "harness_id": "h1",
            "model": "model/a",
            "status": "COMPLETED",
            "trial": 1,
            "evaluation": {"f1": 1.0},
        },
        {
            "id": "job-2",
            "comparison_id": cmp_id,
            "case_id": "C02",
            "harness_id": "h1",
            "model": "model/a",
            "status": "FAILED",
            "trial": 1,
            "error": {"code": "LLM_429", "message": "quota exceeded"},
        },
        {
            "id": "job-3",
            "comparison_id": cmp_id,
            "case_id": "C01",
            "harness_id": "h2",
            "model": "model/a",
            "status": "TIMEOUT",
            "trial": 1,
        },
    ])

    # Mark comparison as failed
    await fake_ctx.store.update_comparison(cmp_id, status="FAILED", error={"code": "FAIL", "message": "Failed"})

    # Test retrying all failed jobs
    res = await fake_ctx.service.retry_failed_jobs(
        cmp_id,
        gateway={"base_url": "http://127.0.0.1:3001/v1", "api_key": "new-key"},
    )
    assert res["status"] == "QUEUED"
    assert res["config"]["gateway"]["base_url"] == "http://127.0.0.1:3001/v1"
    assert res["config"]["gateway"]["api_key"] == "new-key"

    # Verify comparison was reset to QUEUED with updated gateway
    updated_cmp = await fake_ctx.store.get_comparison(cmp_id)
    assert updated_cmp["status"] == "QUEUED"
    assert updated_cmp["config"]["gateway"]["base_url"] == "http://127.0.0.1:3001/v1"
    assert updated_cmp["config"]["gateway"]["api_key"] == "new-key"

    # Verify job-2 status reset
    job2_updated = await fake_ctx.store.get_job("job-2")
    assert job2_updated["status"] == "QUEUED"
    assert job2_updated.get("error") is None

    # Verify completed job was preserved
    job1_check = await fake_ctx.store.get_job("job-1")
    assert job1_check["status"] == "COMPLETED"
    assert job1_check["evaluation"] == {"f1": 1.0}
