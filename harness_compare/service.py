from __future__ import annotations

import logging
import os
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_compare.config import Config
from harness_compare.constants import HARNESS_IDS, HARNESS_NAMES
from harness_compare.dataset import (
    DatasetBundle,
    DatasetError,
    find_dataset_root,
    load_dataset,
    safe_extract_zip,
    snapshot_dataset,
)
from harness_compare.db import PostgresStore
from harness_compare.evaluator import summarize
from harness_compare.git_manager import RepoError, validate_repo_url
from harness_compare.models import ComparisonCreate, DatasetRegister
from harness_compare.redis_bus import RedisBus

logger = logging.getLogger("harness_compare.service")


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_comparison_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"cmp-{stamp}-{secrets.token_hex(3)}"


def initial_progress(
    case_count: int,
    model_count: int,
    trials: int,
    harness_models: dict[str, str] | None = None,
) -> dict[str, Any]:
    if harness_models:
        per_harness = case_count * trials
        total = per_harness * len(HARNESS_IDS)
        return {
            "total_jobs": total,
            "done_jobs": 0,
            "remaining_jobs": total,
            "percent": 0.0,
            "completed": 0,
            "failed": 0,
            "timed_out": 0,
            "cancelled": 0,
            "by_harness": {
                harness_id: {
                    "total": per_harness,
                    "done": 0,
                    "completed": 0,
                    "failed": 0,
                }
                for harness_id in HARNESS_IDS
            },
            "updated_at": _now(),
        }
    per_harness = case_count * model_count * trials
    total = per_harness * len(HARNESS_IDS)
    return {
        "total_jobs": total,
        "done_jobs": 0,
        "remaining_jobs": total,
        "percent": 0.0,
        "completed": 0,
        "failed": 0,
        "timed_out": 0,
        "cancelled": 0,
        "by_harness": {
            harness_id: {
                "total": per_harness,
                "done": 0,
                "completed": 0,
                "failed": 0,
            }
            for harness_id in HARNESS_IDS
        },
        "updated_at": _now(),
    }


class ComparisonService:
    """Dataset registration and comparison creation shared by API and CLI."""

    def __init__(self, cfg: Config, store: PostgresStore, bus: RedisBus) -> None:
        self.cfg = cfg
        self.store = store
        self.bus = bus

    # --------------------------------------------------------------- datasets

    async def register_dataset_path(
        self, path: Path, *, name: str | None = None
    ) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        try:
            bundle = load_dataset(
                resolved,
                name=name,
                max_cases=self.cfg.max_cases,
                max_note_chars=50_000,
            )
        except DatasetError as exc:
            raise ServiceError(exc.code, exc.message) from exc
        return await self._persist_dataset(bundle)

    async def register_dataset_upload(
        self,
        archive: Path,
        *,
        name: str | None = None,
        original_name: str | None = None,
    ) -> dict[str, Any]:
        self.cfg.ensure_dirs()
        upload_dir = self.cfg.data_dir / "uploads" / uuid.uuid4().hex
        upload_dir.mkdir(parents=True, exist_ok=True)
        try:
            try:
                safe_extract_zip(
                    archive,
                    upload_dir,
                    max_bytes=self.cfg.max_upload_mb * 1024 * 1024,
                    max_files=2_000,
                )
                root = find_dataset_root(upload_dir)
                bundle = load_dataset(
                    root,
                    name=name,
                    max_cases=self.cfg.max_cases,
                    max_note_chars=50_000,
                )
            except DatasetError as exc:
                raise ServiceError(exc.code, exc.message) from exc
            bundle.name = name or original_name or bundle.name
            return await self._persist_dataset(bundle, source="upload")
        finally:
            shutil.rmtree(upload_dir, ignore_errors=True)

    async def _persist_dataset(
        self, bundle: DatasetBundle, *, source: str = "register"
    ) -> dict[str, Any]:
        self.cfg.ensure_dirs()
        datasets_root = self.cfg.data_dir / "datasets"
        target = datasets_root / bundle.id
        if not (target / "cases").is_dir():
            tmp = datasets_root / f".tmp-{uuid.uuid4().hex}"
            datasets_root.mkdir(parents=True, exist_ok=True)
            try:
                snapshot_dataset(bundle, tmp)
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(tmp, target)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        record = {
            "id": bundle.id,
            "name": bundle.name,
            "digest": bundle.digest,
            "case_count": bundle.case_count,
            "path": str(target),
            "source": source,
            "manifest": {
                **(bundle.manifest or {}),
                "cases": [case.case_id for case in bundle.cases],
            },
        }
        logger.info(
            "dataset registered id=%s cases=%s source=%s",
            bundle.id,
            bundle.case_count,
            source,
        )
        return await self.store.create_dataset(record)

    async def register_dataset_record(self, body: DatasetRegister) -> dict[str, Any]:
        return await self.register_dataset_path(Path(body.path), name=body.name)

    # ------------------------------------------------------------ comparisons

    async def create_comparison(
        self, body: ComparisonCreate
    ) -> dict[str, Any]:
        gateway = body.gateway or {}
        has_gateway = bool(gateway.get("base_url") or gateway.get("api_key"))
        if not has_gateway and not self.cfg.effective_worker_llm_url():
            raise ServiceError(
                "LLM_NOT_CONFIGURED",
                "LLM_BASE_URL (or WORKER_LLM_BASE_URL) must be configured or a Gateway Provider selected",
                status_code=503,
            )
        try:
            ref = validate_repo_url(body.repo_url, self.cfg)
        except RepoError as exc:
            raise ServiceError(exc.code, str(exc)) from exc

        dataset = await self.store.get_dataset(body.dataset_id)
        if dataset is None:
            raise ServiceError(
                "DATASET_NOT_FOUND",
                f"dataset {body.dataset_id} is not registered",
                status_code=404,
            )
        if len(body.models) > self.cfg.max_models:
            raise ServiceError(
                "TOO_MANY_MODELS",
                f"at most {self.cfg.max_models} models are allowed",
            )
        if body.trials > self.cfg.max_trials:
            raise ServiceError(
                "TOO_MANY_TRIALS",
                f"at most {self.cfg.max_trials} trials are allowed",
            )
        case_count = int(dataset["case_count"])
        total_jobs = (
            case_count * body.trials * len(HARNESS_IDS)
            if body.harness_models
            else case_count * len(body.models) * body.trials * len(HARNESS_IDS)
        )
        if total_jobs > self.cfg.max_jobs:
            raise ServiceError(
                "TOO_MANY_JOBS",
                f"this comparison would create {total_jobs} jobs; "
                f"the limit is {self.cfg.max_jobs}",
                status_code=413,
            )
        active = await self.store.count_active_comparisons()
        if active >= self.cfg.max_active_comparisons:
            raise ServiceError(
                "TOO_MANY_ACTIVE_COMPARISONS",
                f"{active} comparison(s) are already queued or running; "
                f"the limit is {self.cfg.max_active_comparisons}",
                status_code=429,
            )

        budget = self._budget(body)
        comparison_id = _new_comparison_id()
        commits = body.commits
        name = (body.name or "").strip() or (
            f"{Path(ref.url.rstrip('/')).name or ref.url} "
            f"{commits.h1[:7]}/{commits.h2[:7]}/{commits.h3[:7]}"
        )
        record = {
            "id": comparison_id,
            "name": name[:120],
            "status": "QUEUED",
            "stage": "queued",
            "repo_url": ref.url,
            "commit_h1": commits.h1,
            "commit_h2": commits.h2,
            "commit_h3": commits.h3,
            "dataset_id": dataset["id"],
            "dataset_name": dataset.get("name"),
            "dataset_digest": dataset.get("digest"),
            "models": body.models,
            "trials": body.trials,
            "config": {
                "budget": budget,
                "harness_models": body.harness_models or {},
                "gateway": gateway,
                "concurrency": body.concurrency,
                "repo_local": ref.is_local,
                "harness_names": HARNESS_NAMES,
                "guardrails": {
                    "allow_phi": False,
                    "allow_prompt_injection": False,
                    "strict": False,
                },
            },
            "progress": initial_progress(
                case_count,
                len(body.models),
                body.trials,
                harness_models=body.harness_models,
            ),
            "notes": body.notes,
            "created_at": _now(),
        }
        row = await self.store.create_comparison(record)
        try:
            await self.bus.enqueue_comparison(comparison_id)
        except Exception as exc:  # noqa: BLE001 - surface a retryable failure
            await self.store.update_comparison(
                comparison_id,
                status="FAILED",
                stage="queue_failed",
                error={
                    "code": "QUEUE_UNAVAILABLE",
                    "message": f"could not enqueue comparison: {type(exc).__name__}",
                    "retryable": True,
                },
                finished_at=_now(),
            )
            raise ServiceError(
                "QUEUE_UNAVAILABLE",
                "comparison was created but could not be queued",
                status_code=503,
            ) from exc
        await self.store.add_event(
            comparison_id,
            "comparison_created",
            {
                "comparison_id": comparison_id,
                "models": body.models,
                "trials": body.trials,
                "dataset_id": dataset["id"],
                "jobs": total_jobs,
            },
        )
        return row

    def _budget(self, body: ComparisonCreate) -> dict[str, Any]:
        from harness_compare.runner import request_budget

        override = body.budget.as_request() if body.budget else {}
        return request_budget(self.cfg, override)

    async def cancel_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        row = await self.store.get_comparison(comparison_id)
        if row is None:
            return None
        if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return row
        if row["status"] == "QUEUED":
            updated = await self.store.update_comparison(
                comparison_id,
                status="CANCELLED",
                stage="cancelled",
                cancel_requested=True,
                finished_at=_now(),
                error={
                    "code": "CANCELLED",
                    "message": "comparison was cancelled before it started",
                    "retryable": True,
                },
            )
            await self.store.add_event(
                comparison_id, "comparison_cancelled", {"before_start": True}
            )
            return updated
        await self.store.update_comparison(
            comparison_id, status="CANCELLING", cancel_requested=True
        )
        await self.store.add_event(
            comparison_id, "cancellation_requested", {"status": row["status"]}
        )
        try:
            await self.bus.publish_cancel(comparison_id)
        except Exception:  # noqa: BLE001 - DB flag is the source of truth
            logger.warning("could not publish cancellation for %s", comparison_id)
        return await self.store.get_comparison(comparison_id)

    async def retry_failed_jobs(
        self,
        comparison_id: str,
        job_ids: list[str] | None = None,
        gateway: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        comparison = await self.store.get_comparison(comparison_id)
        if comparison is None:
            raise ServiceError(
                "COMPARISON_NOT_FOUND",
                f"Comparison {comparison_id} not found",
                status_code=404,
            )

        all_jobs = await self.store.list_jobs(comparison_id, limit=self.cfg.max_jobs + 1)
        if job_ids:
            target_set = set(job_ids)
            failed_jobs = [j for j in all_jobs if j["id"] in target_set]
        else:
            failed_jobs = [
                j for j in all_jobs if j.get("status") in ("FAILED", "TIMEOUT", "CANCELLED")
            ]

        if not failed_jobs:
            raise ServiceError(
                "NO_FAILED_JOBS",
                "There are no failed or cancelled jobs to retry in this comparison.",
                status_code=400,
            )

        # Reset failed jobs to QUEUED
        for job in failed_jobs:
            await self.store.update_job(
                job["id"],
                status="QUEUED",
                error=None,
                dispatched_at=None,
                completed_at=None,
                only_unfinished=False,
            )

        # Update gateway config if new credentials / endpoint provided
        config = dict(comparison.get("config") or {})
        if gateway:
            config["gateway"] = gateway

        # Reset comparison status to QUEUED and clear finished_at / error
        updated = await self.store.update_comparison(
            comparison_id,
            status="QUEUED",
            stage="queued",
            finished_at=None,
            error=None,
            cancel_requested=False,
            config=config,
        )

        await self.store.add_event(
            comparison_id,
            "jobs_retried",
            {
                "retried_job_count": len(failed_jobs),
                "retried_job_ids": [j["id"] for j in failed_jobs],
                "gateway_updated": bool(gateway),
            },
        )

        # Enqueue comparison for worker execution
        await self.bus.enqueue_comparison(comparison_id)
        return updated or comparison

    async def delete_comparison(self, comparison_id: str) -> bool:
        row = await self.store.get_comparison(comparison_id)
        if row is None:
            return False
        if row["status"] in {"QUEUED", "RUNNING", "CANCELLING"}:
            try:
                await self.bus.publish_cancel(comparison_id)
            except Exception:
                pass
        run_dir = self.cfg.runs_dir / comparison_id
        if run_dir.exists():
            shutil.rmtree(run_dir, ignore_errors=True)
        return await self.store.delete_comparison(comparison_id)

    async def radar(self, comparison_id: str) -> dict[str, Any] | None:
        row = await self.store.get_comparison(comparison_id)
        if row is None:
            return None
        models = [str(item) for item in row.get("models") or []]
        stored = row.get("summary")
        if isinstance(stored, dict) and stored.get("series") is not None:
            first_pair = (stored.get("paired") or [{}])[0] if stored.get("paired") else {}
            if "ci_95_low" not in first_pair:
                jobs = await self.store.list_jobs(comparison_id, limit=self.cfg.max_jobs + 1)
                fresh = summarize(
                    comparison_id=comparison_id,
                    status=str(row.get("status")),
                    harness_names=HARNESS_NAMES,
                    models=list(dict.fromkeys(models)),
                    jobs=jobs,
                )
                stored["paired"] = fresh["paired"]
                await self.store.update_comparison(comparison_id, summary=stored)
            return stored
        jobs = await self.store.list_jobs(comparison_id, limit=self.cfg.max_jobs + 1)
        summary = summarize(
            comparison_id=comparison_id,
            status=str(row.get("status")),
            harness_names=HARNESS_NAMES,
            models=list(dict.fromkeys(models)),
            jobs=jobs,
        )
        summary["harness_runs"] = await self.store.list_harness_runs(comparison_id)
        summary["disclaimer"] = self.cfg.disclaimer
        return summary
