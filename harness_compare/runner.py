from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness_compare.config import Config
from harness_compare.constants import (
    HARNESS_IDS,
    HARNESS_NAMES,
    HARNESS_RECEIVES_HISTORY,
    TERMINAL_COMPARISON_STATUSES,
    TERMINAL_JOB_STATUSES,
)
from harness_compare.dataset import (
    CaseRecord,
    DatasetBundle,
    build_harness_request,
    load_dataset,
)
from harness_compare.db import PostgresStore
from harness_compare.docker_runner import DockerClient, DockerError
from harness_compare.evaluator import empty_evaluation, evaluate_response, summarize
from harness_compare.git_manager import GitManager, PreparedRepo, RepoError
from harness_compare.redis_bus import RedisBus

logger = logging.getLogger("harness_compare.runner")

class ComparisonTimeout(RuntimeError):
    pass


class RunnerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PlannedJob:
    id: str
    harness_id: str
    model: str
    case_id: str
    trial: int
    case: CaseRecord
    expected: dict[str, Any]

    def payload(
        self, *, comparison_id: str, budget: dict[str, Any]
    ) -> dict[str, Any]:
        request = build_harness_request(
            self.case,
            model=self.model,
            budget=budget,
            request_id=self.id,
        )
        return {
            "job_id": self.id,
            "comparison_id": comparison_id,
            "slot": self.harness_id,
            "case_id": self.case_id,
            "trial": self.trial,
            "model": self.model,
            "request": request,
        }


@dataclass
class WorkerState:
    harness_id: str
    commit: str
    image: str
    container: str
    context: Path
    queue: deque[PlannedJob] = field(default_factory=deque)
    active: dict[str, float] = field(default_factory=dict)
    ready: bool = False
    usable: bool = True
    finished: bool = False
    restarts_used: int = 0
    exited_at: float | None = None
    total_jobs: int = 0
    done_jobs: int = 0
    failed_jobs: int = 0


def job_id_for(
    comparison_id: str, harness_id: str, model: str, case_id: str, trial: int
) -> str:
    key = f"{comparison_id}\0{harness_id}\0{model}\0{case_id}\0{trial}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"job-{digest}"


def plan_jobs(
    *,
    comparison_id: str,
    bundle: DatasetBundle,
    models: list[str],
    trials: int,
    harness_models: dict[str, str] | None = None,
) -> dict[str, list[PlannedJob]]:
    plan: dict[str, list[PlannedJob]] = {harness_id: [] for harness_id in HARNESS_IDS}
    for harness_id in HARNESS_IDS:
        target_models = (
            [harness_models[harness_id]]
            if (harness_models and harness_id in harness_models and harness_models[harness_id])
            else models
        )
        for model in target_models:
            for case in bundle.cases:
                for trial in range(1, max(1, trials) + 1):
                    plan[harness_id].append(
                        PlannedJob(
                            id=job_id_for(
                                comparison_id, harness_id, model, case.case_id, trial
                            ),
                            harness_id=harness_id,
                            model=model,
                            case_id=case.case_id,
                            trial=trial,
                            case=case,
                            expected=bundle.expected[case.case_id],
                        )
                    )
    return plan


def request_budget(
    cfg: Config, override: dict[str, Any] | None = None
) -> dict[str, Any]:
    budget: dict[str, Any] = {
        "max_llm_calls": cfg.server_max_llm_calls,
        "max_input_tokens": cfg.server_max_input_tokens,
        "max_output_tokens": cfg.server_max_output_tokens,
        "max_total_tokens": cfg.server_max_total_tokens,
        "max_wall_seconds": min(
            cfg.job_budget_wall_seconds, cfg.server_max_wall_seconds
        ),
    }
    if cfg.server_max_cost_usd is not None:
        budget["max_cost_usd"] = cfg.server_max_cost_usd
    for key, value in (override or {}).items():
        if value is None:
            continue
        if key == "max_llm_calls":
            value = min(int(value), cfg.server_max_llm_calls)
        elif key == "max_input_tokens":
            value = min(int(value), cfg.server_max_input_tokens)
        elif key == "max_output_tokens":
            value = min(int(value), cfg.server_max_output_tokens)
        elif key == "max_total_tokens":
            value = min(int(value), cfg.server_max_total_tokens)
        elif key == "max_wall_seconds":
            value = min(
                float(value),
                cfg.server_max_wall_seconds,
                cfg.job_budget_wall_seconds,
            )
        elif key == "max_cost_usd" and cfg.server_max_cost_usd is not None:
            value = min(float(value), cfg.server_max_cost_usd)
        budget[key] = value
    return budget


class ComparisonRunner:
    """Run one comparison end to end.

    Responsibilities: prepare commits, build one image per commit, start one
    isolated worker per harness on a private Docker network, dispatch jobs over
    Redis Streams one at a time per harness, persist every result and
    deterministic evaluation to Postgres, and produce the radar summary.

    Harness failures are isolated. A broken build or worker makes that
    harness's jobs fail; the other harnesses keep running so the radar still
    shows partial evidence and the failure is visible.
    """

    def __init__(
        self,
        cfg: Config,
        store: PostgresStore,
        bus: RedisBus,
        docker: DockerClient,
        git: GitManager,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.bus = bus
        self.docker = docker
        self.git = git

    # ------------------------------------------------------------------ run

    async def run(self, comparison_id: str) -> dict[str, Any] | None:
        comparison = await self.store.get_comparison(comparison_id)
        if comparison is None:
            logger.warning("comparison %s not found", comparison_id)
            return None
        if comparison["status"] in TERMINAL_COMPARISON_STATUSES:
            logger.info(
                "comparison %s already %s; skipping",
                comparison_id,
                comparison["status"],
            )
            return comparison.get("summary")
        if comparison["status"] == "QUEUED":
            claimed = await self.store.claim_comparison(comparison_id)
            if claimed is None:
                logger.info("comparison %s could not be claimed", comparison_id)
                return None
            comparison = claimed
        elif comparison["status"] == "CANCELLING":
            await self._set_cancelled(comparison_id)
            row = await self.store.get_comparison(comparison_id)
            return row.get("summary") if row else None

        return await self._run_claimed(comparison)

    async def _run_claimed(self, comparison: dict[str, Any]) -> dict[str, Any] | None:
        comparison_id = str(comparison["id"])
        run_dir = (self.cfg.runs_dir / comparison_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        deadline = started + self.cfg.comparison_timeout_s

        prepared: PreparedRepo | None = None
        workers: dict[str, WorkerState] = {}
        attachments: list[str] = []
        network = self.cfg.network(comparison_id)
        network_created = False
        status_counts: Counter[str] = Counter()
        harness_counts: dict[str, Counter[str]] = {
            harness_id: Counter() for harness_id in HARNESS_IDS
        }
        terminal_jobs: set[str] = set()
        planned: dict[str, list[PlannedJob]] = {}
        last_progress = 0.0
        last_health = 0.0

        try:
            await self._event(comparison_id, "comparison_started", {"run_dir": str(run_dir)})
            stage = "loading_dataset"
            await self._stage(comparison_id, stage)

            bundle, comparison = await self._load_bundle(comparison)
            models = [str(item) for item in comparison["models"]]
            trials = int(comparison["trials"])
            config = comparison.get("config") or {}
            comparison_concurrency = config.get("concurrency")
            budget_override = config.get("budget") or {}
            budget = request_budget(self.cfg, budget_override)
            harness_models = config.get("harness_models") or {}

            planned = plan_jobs(
                comparison_id=comparison_id,
                bundle=bundle,
                models=models,
                trials=trials,
                harness_models=harness_models,
            )
            total_jobs = sum(len(items) for items in planned.values())
            if total_jobs == 0:
                raise RunnerError("NO_JOBS", "the comparison planned no jobs")
            existing_jobs = await self.store.list_jobs(
                comparison_id, limit=self.cfg.max_jobs + 1
            )
            existing_by_id = {j["id"]: j for j in existing_jobs}
            completed_job_ids = {
                j["id"] for j in existing_jobs if j.get("status") == "COMPLETED"
            }

            if not existing_jobs:
                await self.store.delete_jobs(comparison_id)
                await self.store.create_jobs(
                    [
                        {
                            "id": job.id,
                            "comparison_id": comparison_id,
                            "harness_id": job.harness_id,
                            "model": job.model,
                            "case_id": job.case_id,
                            "trial": job.trial,
                            "request_id": job.id,
                        }
                        for items in planned.values()
                        for job in items
                    ]
                )
            else:
                # Pre-populate counts for existing completed jobs on retry
                for j in existing_jobs:
                    if j.get("status") == "COMPLETED":
                        terminal_jobs.add(j["id"])
                        status_counts["COMPLETED"] += 1
                        harness_counts[j["harness_id"]]["COMPLETED"] += 1

            await self._progress(
                comparison_id, planned, harness_counts, status_counts, total_jobs
            )

            stage = "preparing_repository"
            await self._stage(comparison_id, stage)
            try:
                prepared = await self.git.prepare(
                    comparison_id=comparison_id,
                    repo_url=str(comparison["repo_url"]),
                    commits={
                        "h1": str(comparison["commit_h1"]),
                        "h2": str(comparison["commit_h2"]),
                        "h3": str(comparison["commit_h3"]),
                    },
                    run_dir=run_dir,
                )
            except RepoError as exc:
                raise RunnerError(exc.code, str(exc)) from exc
            comparison["commits"] = prepared.commits
            await self._event(
                comparison_id,
                "repository_prepared",
                {
                    "commits": prepared.commits,
                    "git_dir": str(prepared.repo_dir),
                },
            )

            stage = "building_images"
            await self._stage(comparison_id, stage)
            logs = run_dir / "logs"
            for harness_id in HARNESS_IDS:
                commit = prepared.commits[harness_id]
                state = WorkerState(
                    harness_id=harness_id,
                    commit=commit,
                    image=self.cfg.image_tag(comparison_id, harness_id, commit),
                    container=f"cc-{comparison_id}-{harness_id}",
                    context=prepared.contexts[harness_id],
                    total_jobs=len(planned[harness_id]),
                )
                workers[harness_id] = state
                await self.store.upsert_harness_run(
                    comparison_id,
                    harness_id,
                    commit=commit,
                    image=state.image,
                    status="BUILDING",
                )
                await self._event(
                    comparison_id,
                    "harness_build_started",
                    {"harness_id": harness_id, "commit": commit},
                )
                try:
                    await self.docker.build_image(
                        context=state.context,
                        tag=state.image,
                        harness_id=harness_id,
                        commit=commit,
                        comparison_id=comparison_id,
                        version="1.0.0",
                        log_path=logs / f"{harness_id}-build.log",
                    )
                except DockerError as exc:
                    state.usable = False
                    state.finished = True
                    log_path = str(exc.log_path or (logs / f"{harness_id}-build.log"))
                    await self.store.upsert_harness_run(
                        comparison_id,
                        harness_id,
                        commit=commit,
                        image=state.image,
                        status="BUILD_FAILED",
                        error={
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        },
                        log_tail=_tail_file(Path(log_path)),
                    )
                    await self._event(
                        comparison_id,
                        "harness_build_failed",
                        {
                            "harness_id": harness_id,
                            "code": exc.code,
                            "message": exc.message,
                        },
                    )
                    await self._fail_jobs(
                        planned[harness_id],
                        code="BUILD_FAILED",
                        message=f"image build failed for {harness_id}",
                        harness_counts=harness_counts,
                        status_counts=status_counts,
                        terminal_jobs=terminal_jobs,
                    )
                    continue
                await self.store.upsert_harness_run(
                    comparison_id,
                    harness_id,
                    commit=commit,
                    image=state.image,
                    status="BUILT",
                )
                await self._event(
                    comparison_id,
                    "harness_build_completed",
                    {"harness_id": harness_id, "image": state.image},
                )

            stage = "starting_workers"
            await self._stage(comparison_id, stage)
            await self.docker.ensure_network(network, comparison_id)
            network_created = True
            for container, role in (
                (self.cfg.redis_container, "redis"),
                (self.cfg.router_container, "router"),
            ):
                if not container:
                    continue
                try:
                    await self.docker.connect_container(network, container)
                    attachments.append(container)
                except DockerError as exc:
                    # The worker readiness check is the source of truth. An
                    # external Redis/router does not need to be attached.
                    await self._event(
                        comparison_id,
                        "infra_attach_failed",
                        {
                            "container": container,
                            "role": role,
                            "code": exc.code,
                            "message": exc.message,
                        },
                    )

            for harness_id, state in workers.items():
                if not state.usable:
                    continue
                try:
                    await self._start_worker(
                        comparison_id,
                        state,
                        network,
                        gateway=config.get("gateway"),
                        concurrency=comparison_concurrency,
                    )
                except DockerError as exc:
                    state.usable = False
                    state.finished = True
                    await self.store.upsert_harness_run(
                        comparison_id,
                        harness_id,
                        commit=state.commit,
                        image=state.image,
                        container_name=state.container,
                        status="STARTUP_FAILED",
                        error={
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        },
                    )
                    await self._event(
                        comparison_id,
                        "harness_start_failed",
                        {
                            "harness_id": harness_id,
                            "code": exc.code,
                            "message": exc.message,
                        },
                    )
                    await self._fail_jobs(
                        planned[harness_id],
                        code="WORKER_UNAVAILABLE",
                        message=f"worker {harness_id} could not start",
                        harness_counts=harness_counts,
                        status_counts=status_counts,
                        terminal_jobs=terminal_jobs,
                    )

            await self._await_ready(
                comparison_id,
                workers,
                planned,
                harness_counts,
                status_counts,
                terminal_jobs,
            )

            for harness_id, state in workers.items():
                if not state.usable:
                    continue
                await self.store.upsert_harness_run(
                    comparison_id,
                    harness_id,
                    commit=state.commit,
                    image=state.image,
                    container_name=state.container,
                    status="RUNNING",
                )
                await self._event(
                    comparison_id,
                    "harness_ready",
                    {"harness_id": harness_id, "container": state.container},
                )

            if not any(state.usable for state in workers.values()):
                raise RunnerError(
                    "HARNESS_UNAVAILABLE",
                    "no harness worker could be started; see per-harness build/start logs",
                )

            stage = "running"
            await self._stage(comparison_id, stage)
            await self.bus.init_results(self.cfg.results_stream(comparison_id))
            results_stream = self.cfg.results_stream(comparison_id)

            for harness_id, state in workers.items():
                if state.usable:
                    jobs_to_run = [
                        job for job in planned[harness_id]
                        if job.id not in completed_job_ids
                    ]
                    state.queue = deque(jobs_to_run)

            while True:
                now = time.monotonic()
                if now > deadline:
                    raise ComparisonTimeout(
                        f"comparison exceeded {self.cfg.comparison_timeout_s:.0f}s"
                    )

                current = await self.store.get_comparison(comparison_id)
                if current is None:
                    raise RunnerError("COMPARISON_LOST", "comparison row disappeared")
                if current.get("cancel_requested"):
                    await self._cancel(
                        comparison_id,
                        workers,
                        planned,
                        models,
                        harness_counts,
                        status_counts,
                        terminal_jobs,
                        total_jobs,
                    )
                    return (await self.store.get_comparison(comparison_id) or {}).get(
                        "summary"
                    )

                results = await self.bus.read_results(
                    results_stream,
                    self.cfg.run_consumer,
                    block_ms=max(200, int(self.cfg.poll_interval_s * 1_000)),
                    count=25,
                )
                for message_id, envelope in results:
                    try:
                        await self._handle_result(
                            comparison_id,
                            envelope,
                            workers,
                            planned,
                            harness_counts,
                            status_counts,
                            terminal_jobs,
                        )
                    finally:
                        await self.bus.ack_result(results_stream, message_id)

                now = time.monotonic()
                for harness_id, state in workers.items():
                    if not state.usable or state.finished:
                        continue
                    while (
                        state.queue
                        and len(state.active) < self.cfg.max_inflight_for_harness(harness_id, comparison_concurrency)
                    ):
                        job = state.queue.popleft()
                        payload = job.payload(
                            comparison_id=comparison_id, budget=budget
                        )
                        try:
                            await self.bus.enqueue_job(
                                self.cfg.job_stream(comparison_id, harness_id),
                                payload,
                            )
                            await self.store.mark_job_dispatched(job.id)
                        except Exception as exc:  # noqa: BLE001 - transport retry
                            state.queue.appendleft(job)
                            logger.warning(
                                "dispatch failed harness=%s: %s",
                                harness_id,
                                type(exc).__name__,
                            )
                            break
                        state.active[job.id] = now

                    if not state.queue and not state.active and not state.finished:
                        state.finished = True
                        await self.store.upsert_harness_run(
                            comparison_id,
                            harness_id,
                            commit=state.commit,
                            image=state.image,
                            container_name=state.container,
                            status="COMPLETED",
                            log_tail=(
                                await self.docker.worker_logs(state.container, tail=80)
                                if not self.cfg.keep_workers
                                else None
                            ),
                        )
                        await self._event(
                            comparison_id,
                            "harness_completed",
                            {
                                "harness_id": harness_id,
                                "jobs": state.total_jobs,
                                "failed_jobs": state.failed_jobs,
                            },
                        )

                now = time.monotonic()
                if now - last_health >= 3.0:
                    last_health = now
                    await self._health_check(
                        comparison_id,
                        workers,
                        planned,
                        harness_counts,
                        status_counts,
                        terminal_jobs,
                        network,
                    )

                now = time.monotonic()
                await self._timeouts(
                    comparison_id,
                    workers,
                    planned,
                    harness_counts,
                    status_counts,
                    terminal_jobs,
                    network,
                )

                if now - last_progress >= self.cfg.progress_interval_s:
                    last_progress = now
                    await self._progress(
                        comparison_id,
                        planned,
                        harness_counts,
                        status_counts,
                        total_jobs,
                    )

                if all(
                    state.finished or not state.usable for state in workers.values()
                ):
                    break

            await self._progress(
                comparison_id,
                planned,
                harness_counts,
                status_counts,
                total_jobs,
            )
            summary = await self._finish_summary(
                comparison_id, status="COMPLETED", models=models
            )
            await self._stage(comparison_id, "completed")
            await self._event(
                comparison_id,
                "comparison_completed",
                {
                    "jobs": total_jobs,
                    "failed": status_counts.get("FAILED", 0)
                    + status_counts.get("TIMEOUT", 0),
                    "duration_s": round(time.monotonic() - started, 3),
                },
            )
            return summary

        except asyncio.CancelledError:
            await self._close_workers(workers)
            await self._cleanup_network(network, attachments, network_created)
            if prepared is not None:
                await self.git.cleanup(
                    prepared, keep=self.cfg.keep_worktrees
                )
            raise
        except ComparisonTimeout as exc:
            await self._fail_remaining(
                planned,
                workers,
                code="TIMEOUT",
                message=str(exc),
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )
            summary = await self._finish_summary(
                comparison_id,
                status="FAILED",
                models=[str(item) for item in comparison.get("models") or []],
                error={
                    "code": "COMPARISON_TIMEOUT",
                    "message": str(exc),
                    "retryable": True,
                },
            )
            return summary
        except Exception as exc:  # noqa: BLE001 - persist a structured failure
            logger.exception("comparison %s failed", comparison_id)
            code = exc.code if isinstance(exc, RunnerError) else "RUNNER_ERROR"
            message = (
                exc.message
                if isinstance(exc, RunnerError)
                else f"{type(exc).__name__}: {exc}"
            )[:2_000]
            await self._fail_remaining(
                planned,
                workers,
                code=code,
                message=message,
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )
            summary = await self._finish_summary(
                comparison_id,
                status="FAILED",
                models=[str(item) for item in comparison.get("models") or []],
                error={"code": code, "message": message, "retryable": False},
            )
            return summary
        finally:
            await self._close_workers(workers)
            await self._cleanup_network(network, attachments, network_created)
            if prepared is not None:
                await self.git.cleanup(prepared, keep=self.cfg.keep_worktrees)
            if not self.cfg.keep_images:
                for state in workers.values():
                    try:
                        await self.docker.remove_image(state.image)
                    except Exception:  # noqa: BLE001 - cleanup only
                        pass
            try:
                await self.bus.expire_streams(comparison_id, list(HARNESS_IDS))
            except Exception:  # noqa: BLE001 - cleanup only
                pass

    # ------------------------------------------------------------- lifecycle

    async def _load_bundle(
        self, comparison: dict[str, Any]
    ) -> tuple[DatasetBundle, dict[str, Any]]:
        dataset = await self.store.get_dataset(str(comparison["dataset_id"]))
        if dataset is None:
            raise RunnerError(
                "DATASET_NOT_FOUND",
                f"dataset {comparison['dataset_id']} is not registered",
            )
        path = Path(str(dataset["path"]))
        if not path.is_dir():
            raise RunnerError(
                "DATASET_NOT_FOUND", f"dataset snapshot is missing: {path}"
            )
        try:
            bundle = load_dataset(
                path,
                name=str(dataset.get("name") or path.name),
                max_cases=self.cfg.max_cases,
            )
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise RunnerError(
                "DATASET_INVALID", f"{type(exc).__name__}: {exc}"
            ) from exc
        return bundle, comparison

    async def _start_worker(
        self,
        comparison_id: str,
        state: WorkerState,
        network: str,
        gateway: dict[str, Any] | None = None,
        concurrency: int | None = None,
    ) -> None:
        env = self._worker_env(comparison_id, state.harness_id, gateway=gateway, concurrency=concurrency)
        await self.docker.start_worker(
            name=state.container,
            image=state.image,
            network=network,
            comparison_id=comparison_id,
            harness_id=state.harness_id,
            env=env,
            env_file=(self.cfg.runs_dir / comparison_id / "env" / f"{state.harness_id}.env"),
            command=[
                "/opt/harness-venv/bin/python",
                "-m",
                "clinical_coder.worker",
            ],
        )
        state.ready = False
        state.exited_at = None

    def _worker_env(
        self,
        comparison_id: str,
        harness_id: str,
        gateway: dict[str, Any] | None = None,
        concurrency: int | None = None,
    ) -> dict[str, str]:
        llm_base_url = (gateway or {}).get("base_url") or self.cfg.effective_worker_llm_url()
        llm_api_key = (gateway or {}).get("api_key") or self.cfg.llm_api_key
        if not llm_base_url:
            raise RunnerError(
                "LLM_NOT_CONFIGURED",
                "LLM_BASE_URL (or WORKER_LLM_BASE_URL) must be set before a comparison",
            )
        inflight_concurrency = self.cfg.max_inflight_for_harness(harness_id, concurrency)
        env = {
            "HARNESS_ID": harness_id,
            "HARNESS_SLOT": harness_id,
            "HARNESS_IMPLEMENTATION": "python",
            "LOG_LEVEL": self.cfg.log_level,
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "REDIS_URL": self.cfg.effective_worker_redis_url(),
            "COMPARISON_ID": comparison_id,
            "STREAM_JOBS": self.cfg.job_stream(comparison_id, harness_id),
            "STREAM_RESULTS": self.cfg.results_stream(comparison_id),
            "CONSUMER_GROUP": self.cfg.worker_consumer_group,
            "WORKER_CONCURRENCY": str(inflight_concurrency),
            "LLM_BASE_URL": llm_base_url,
            "LLM_API_KEY": llm_api_key or "",
            "LLM_JSON_MODE": _bool_text(self.cfg.llm_json_mode),
            "LLM_MAX_RETRIES": str(self.cfg.llm_max_retries),
            "LLM_TIMEOUT_S": str(self.cfg.llm_timeout_s),
            "LLM_RPM": str(self.cfg.llm_rpm),
            "LLM_MAX_CONCURRENCY": str(inflight_concurrency),
            "SERVER_MAX_LLM_CALLS": str(self.cfg.server_max_llm_calls),
            "SERVER_MAX_INPUT_TOKENS": str(self.cfg.server_max_input_tokens),
            "SERVER_MAX_OUTPUT_TOKENS": str(self.cfg.server_max_output_tokens),
            "SERVER_MAX_TOTAL_TOKENS": str(self.cfg.server_max_total_tokens),
            "SERVER_MAX_WALL_SECONDS": str(self.cfg.server_max_wall_seconds),
            "SERVER_MAX_COST_USD": (
                "none"
                if self.cfg.server_max_cost_usd is None
                else str(self.cfg.server_max_cost_usd)
            ),
            "ALLOW_PHI": "false",
            "ALLOW_PROMPT_INJECTION": "false",
            "GUARDRAIL_STRICT": "false",
            "HERMES_HOME_ROOT": "/tmp/clinical-coder-hermes",
            "API_HOST": "0.0.0.0",
            "API_PORT": "8080",
        }
        return env

    async def _await_ready(
        self,
        comparison_id: str,
        workers: dict[str, WorkerState],
        planned: dict[str, list[PlannedJob]],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
    ) -> None:
        pending = {
            harness_id
            for harness_id, state in workers.items()
            if state.usable and not state.ready
        }
        if not pending:
            return
        deadline = time.monotonic() + self.cfg.worker_ready_timeout_s
        while pending and time.monotonic() < deadline:
            heartbeats = await self.bus.worker_heartbeats(comparison_id)
            for harness_id in list(pending):
                state = workers[harness_id]
                if heartbeats.get(harness_id):
                    state.ready = True
                    pending.discard(harness_id)
                    continue
                status = await self.docker.worker_status(state.container)
                if status is not None and not status.get("Running", False):
                    pending.discard(harness_id)
                    state.usable = False
                    state.finished = True
                    logs = await self.docker.worker_logs(state.container)
                    await self._write_worker_log(comparison_id, harness_id, logs)
                    await self.store.upsert_harness_run(
                        comparison_id,
                        harness_id,
                        commit=state.commit,
                        image=state.image,
                        container_name=state.container,
                        status="STARTUP_FAILED",
                        error={
                            "code": "WORKER_EXITED",
                            "message": "worker exited before reporting ready",
                        },
                        log_tail=logs[-4_000:],
                    )
                    await self._event(
                        comparison_id,
                        "harness_start_failed",
                        {"harness_id": harness_id, "code": "WORKER_EXITED"},
                    )
                    await self._fail_jobs(
                        planned[harness_id],
                        code="WORKER_UNAVAILABLE",
                        message=f"worker {harness_id} exited before ready",
                        harness_counts=harness_counts,
                        status_counts=status_counts,
                        terminal_jobs=terminal_jobs,
                    )
            if pending:
                await asyncio.sleep(0.5)

        for harness_id in pending:
            state = workers[harness_id]
            state.usable = False
            state.finished = True
            logs = await self.docker.worker_logs(state.container)
            await self._write_worker_log(comparison_id, harness_id, logs)
            await self.store.upsert_harness_run(
                comparison_id,
                harness_id,
                commit=state.commit,
                image=state.image,
                container_name=state.container,
                status="STARTUP_FAILED",
                error={
                    "code": "WORKER_READY_TIMEOUT",
                    "message": (
                        f"worker did not heartbeat within "
                        f"{self.cfg.worker_ready_timeout_s:.0f}s"
                    ),
                },
                log_tail=logs[-4_000:],
            )
            await self._event(
                comparison_id,
                "harness_start_failed",
                {"harness_id": harness_id, "code": "WORKER_READY_TIMEOUT"},
            )
            await self._fail_jobs(
                planned[harness_id],
                code="WORKER_UNAVAILABLE",
                message=f"worker {harness_id} did not become ready",
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )

    # ---------------------------------------------------------------- results

    async def _handle_result(
        self,
        comparison_id: str,
        envelope: dict[str, Any],
        workers: dict[str, WorkerState],
        planned: dict[str, list[PlannedJob]],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
    ) -> None:
        job_id = str(envelope.get("job_id") or "")
        planned_job = next(
            (job for items in planned.values() for job in items if job.id == job_id),
            None,
        )
        if planned_job is None:
            logger.warning("result for unknown job %s", job_id[:80])
            return
        if job_id in terminal_jobs:
            return
        state = workers[planned_job.harness_id]
        started_at = state.active.pop(job_id, None)
        elapsed_ms = (
            int((time.monotonic() - started_at) * 1000)
            if started_at is not None
            else None
        )

        response = envelope.get("response")
        worker_error = envelope.get("error")
        error: dict[str, Any] | None = None
        if isinstance(response, dict):
            evaluation = evaluate_response(
                planned_job.case,
                planned_job.expected,
                response,
                fallback_latency_ms=elapsed_ms,
                history_available=(
                    HARNESS_RECEIVES_HISTORY.get(planned_job.harness_id, False)
                    and planned_job.case.history is not None
                ),
            )
            status = (
                "COMPLETED"
                if evaluation.get("job_status") == "COMPLETED"
                else "FAILED"
            )
            response_error = response.get("error")
            if isinstance(response_error, dict):
                error = response_error
            usage = response.get("usage") or {}
            guardrails = response.get("guardrails") or {}
            budget = response.get("budget") or {}
            latency_ms = evaluation.get("latency_ms")
        else:
            code = "WORKER_ERROR"
            message = "worker returned no response"
            if isinstance(worker_error, dict):
                code = str(worker_error.get("code") or code)
                message = str(worker_error.get("message") or message)
            evaluation = empty_evaluation(
                planned_job.case,
                planned_job.expected,
                error_code=code,
                message=message,
                history_available=(
                    HARNESS_RECEIVES_HISTORY.get(planned_job.harness_id, False)
                    and planned_job.case.history is not None
                ),
            )
            status = "FAILED"
            error = {"code": code, "message": message, "retryable": False}
            usage = {}
            guardrails = {}
            budget = {}
            latency_ms = elapsed_ms

        await self.store.update_job(
            job_id,
            only_unfinished=False,
            status=status,
            response=response if isinstance(response, dict) else None,
            evaluation=evaluation,
            error=error,
            usage=usage,
            guardrails=guardrails,
            budget=budget,
            latency_ms=latency_ms if isinstance(latency_ms, int) else None,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        terminal_jobs.add(job_id)
        status_counts[status] += 1
        harness_counts[planned_job.harness_id][status] += 1
        if status != "COMPLETED":
            state.failed_jobs += 1
            # If a job fails, check if the concerned worker is running or not. If not, restart the worker.
            worker_alive = True
            try:
                docker_status = await self.docker.worker_status(state.container)
                docker_running = bool(docker_status and docker_status.get("Running"))
                if not docker_running:
                    worker_alive = False
                else:
                    heartbeats = await self.bus.worker_heartbeats(comparison_id)
                    if not heartbeats.get(planned_job.harness_id):
                        if (docker_status or {}).get("Status") in ("exited", "dead", "restarting"):
                            worker_alive = False
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "failed to inspect worker status for %s on job failure: %s",
                    state.container,
                    exc,
                )
                worker_alive = False

            if not worker_alive:
                logger.warning(
                    "job %s failed and concerned worker %s (harness %s) is not running; restarting worker",
                    job_id,
                    state.container,
                    planned_job.harness_id,
                )
                for active_id in list(state.active):
                    active_job = next(
                        (item for item in planned[planned_job.harness_id] if item.id == active_id),
                        None,
                    )
                    state.active.pop(active_id, None)
                    if active_job:
                        await self._mark_job_failure(
                            active_job,
                            planned_job.harness_id,
                            status="FAILED",
                            code="WORKER_EXITED",
                            message="worker exited while job was in flight",
                            harness_counts=harness_counts,
                            status_counts=status_counts,
                            terminal_jobs=terminal_jobs,
                        )
                        state.failed_jobs += 1

                await self._restart_worker(
                    comparison_id,
                    state,
                    planned,
                    harness_counts,
                    status_counts,
                    terminal_jobs,
                    network=self.cfg.network(comparison_id),
                    reason=f"concerned worker was not running after failed job {planned_job.case.case_id}",
                )

    # ----------------------------------------------------------- failure modes

    async def _timeouts(
        self,
        comparison_id: str,
        workers: dict[str, WorkerState],
        planned: dict[str, list[PlannedJob]],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
        network: str,
    ) -> None:
        now = time.monotonic()
        for harness_id, state in workers.items():
            if not state.usable:
                continue
            expired = [
                job_id
                for job_id, started_at in state.active.items()
                if now - started_at > self.cfg.job_timeout_s
            ]
            for job_id in expired:
                job = next(
                    (item for item in planned[harness_id] if item.id == job_id), None
                )
                if job is None:
                    continue
                state.active.pop(job_id, None)
                await self._mark_job_failure(
                    job,
                    harness_id,
                    status="TIMEOUT",
                    code="JOB_TIMEOUT",
                    message=f"job exceeded {self.cfg.job_timeout_s:.0f}s",
                    harness_counts=harness_counts,
                    status_counts=status_counts,
                    terminal_jobs=terminal_jobs,
                )
                state.failed_jobs += 1
                await self._restart_worker(
                    comparison_id,
                    state,
                    planned,
                    harness_counts,
                    status_counts,
                    terminal_jobs,
                    network,
                    reason="job timeout",
                )

    async def _health_check(
        self,
        comparison_id: str,
        workers: dict[str, WorkerState],
        planned: dict[str, list[PlannedJob]],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
        network: str,
    ) -> None:
        now = time.monotonic()
        for harness_id, state in workers.items():
            if not state.usable or state.finished:
                continue
            status = await self.docker.worker_status(state.container)
            running = bool(status and status.get("Running"))
            if running:
                state.exited_at = None
                continue
            if state.exited_at is None:
                state.exited_at = now
                continue
            if now - state.exited_at < 3.0:
                continue
            logs = await self.docker.worker_logs(state.container)
            await self._write_worker_log(comparison_id, harness_id, logs)
            for job_id in list(state.active):
                job = next(
                    (item for item in planned[harness_id] if item.id == job_id), None
                )
                if job is None:
                    state.active.pop(job_id, None)
                    continue
                state.active.pop(job_id, None)
                await self._mark_job_failure(
                    job,
                    harness_id,
                    status="FAILED",
                    code="WORKER_EXITED",
                    message="worker exited while the job was in flight",
                    harness_counts=harness_counts,
                    status_counts=status_counts,
                    terminal_jobs=terminal_jobs,
                )
                state.failed_jobs += 1
            await self._restart_worker(
                comparison_id,
                state,
                planned,
                harness_counts,
                status_counts,
                terminal_jobs,
                network,
                reason="worker exited",
            )

    async def _restart_worker(
        self,
        comparison_id: str,
        state: WorkerState,
        planned: dict[str, list[PlannedJob]],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
        network: str,
        *,
        reason: str,
    ) -> None:
        if not state.queue:
            await self._mark_harness_finished(comparison_id, state)
            return
        max_restarts = max(self.cfg.worker_restarts, 2)
        if state.restarts_used >= max_restarts:
            await self._event(
                comparison_id,
                "harness_worker_lost",
                {
                    "harness_id": state.harness_id,
                    "reason": reason,
                    "restarts_used": state.restarts_used,
                },
            )
            await self.store.upsert_harness_run(
                comparison_id,
                state.harness_id,
                commit=state.commit,
                image=state.image,
                container_name=state.container,
                status="FAILED",
                error={
                    "code": "WORKER_LOST",
                    "message": f"worker lost ({reason}) and restarts are exhausted",
                },
                log_tail=(
                    await self.docker.worker_logs(state.container, tail=80)
                )[-4_000:],
            )
            await self._fail_jobs(
                list(state.queue),
                code="WORKER_UNAVAILABLE",
                message=f"worker {state.harness_id} was lost: {reason}",
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )
            state.queue.clear()
            state.usable = False
            state.finished = True
            return

        state.restarts_used += 1
        await self._event(
            comparison_id,
            "harness_worker_restart",
            {
                "harness_id": state.harness_id,
                "reason": reason,
                "attempt": state.restarts_used,
            },
        )
        await self.docker.stop_worker(state.container)
        try:
            await self._start_worker(comparison_id, state, network)
        except DockerError as exc:
            await self._fail_jobs(
                list(state.queue),
                code="WORKER_UNAVAILABLE",
                message=f"worker restart failed: {exc.message}",
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )
            state.queue.clear()
            state.usable = False
            state.finished = True
            return
        deadline = time.monotonic() + min(60.0, self.cfg.worker_ready_timeout_s)
        while time.monotonic() < deadline:
            heartbeats = await self.bus.worker_heartbeats(comparison_id)
            if heartbeats.get(state.harness_id):
                state.ready = True
                state.exited_at = None
                return
            await asyncio.sleep(0.5)
        await self._fail_jobs(
            list(state.queue),
            code="WORKER_UNAVAILABLE",
            message="worker did not become ready after restart",
            harness_counts=harness_counts,
            status_counts=status_counts,
            terminal_jobs=terminal_jobs,
        )
        state.queue.clear()
        state.usable = False
        state.finished = True

    async def _mark_harness_finished(
        self, comparison_id: str, state: WorkerState
    ) -> None:
        if state.finished:
            return
        state.finished = True
        await self.store.upsert_harness_run(
            comparison_id,
            state.harness_id,
            commit=state.commit,
            image=state.image,
            container_name=state.container,
            status="COMPLETED" if state.usable else "FAILED",
        )
        await self._event(
            comparison_id,
            "harness_completed",
            {
                "harness_id": state.harness_id,
                "jobs": state.total_jobs,
                "failed_jobs": state.failed_jobs,
            },
        )

    async def _mark_job_failure(
        self,
        job: PlannedJob,
        harness_id: str,
        *,
        status: str,
        code: str,
        message: str,
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
    ) -> None:
        if job.id in terminal_jobs:
            return
        evaluation = empty_evaluation(
            job.case,
            job.expected,
            error_code=code,
            message=message,
            history_available=(
                HARNESS_RECEIVES_HISTORY.get(harness_id, False)
                and job.case.history is not None
            ),
        )
        await self.store.update_job(
            job.id,
            only_unfinished=False,
            status=status,
            evaluation=evaluation,
            error={"code": code, "message": message, "retryable": False},
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        terminal_jobs.add(job.id)
        status_counts[status] += 1
        harness_counts[harness_id][status] += 1

    async def _fail_jobs(
        self,
        jobs: list[PlannedJob],
        *,
        code: str,
        message: str,
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
    ) -> None:
        for job in jobs:
            await self._mark_job_failure(
                job,
                job.harness_id,
                status="FAILED",
                code=code,
                message=message,
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )

    async def _fail_remaining(
        self,
        planned: dict[str, list[PlannedJob]],
        workers: dict[str, WorkerState],
        *,
        code: str,
        message: str,
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
    ) -> None:
        for harness_id, items in planned.items():
            await self._fail_jobs(
                [job for job in items if job.id not in terminal_jobs],
                code=code,
                message=message,
                harness_counts=harness_counts,
                status_counts=status_counts,
                terminal_jobs=terminal_jobs,
            )
            state = workers.get(harness_id)
            if state is not None:
                state.queue.clear()
                state.active.clear()
                state.finished = True
                state.usable = False

    async def _cancel(
        self,
        comparison_id: str,
        workers: dict[str, WorkerState],
        planned: dict[str, list[PlannedJob]],
        models: list[str],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        terminal_jobs: set[str],
        total_jobs: int,
    ) -> None:
        await self._stage(comparison_id, "cancelling")
        for harness_id, items in planned.items():
            for job in items:
                if job.id in terminal_jobs:
                    continue
                await self._mark_job_failure(
                    job,
                    harness_id,
                    status="CANCELLED",
                    code="CANCELLED",
                    message="comparison was cancelled",
                    harness_counts=harness_counts,
                    status_counts=status_counts,
                    terminal_jobs=terminal_jobs,
                )
        for state in workers.values():
            state.queue.clear()
            state.active.clear()
            state.finished = True
            state.usable = False
            if state.ready:
                await self.store.upsert_harness_run(
                    comparison_id,
                    state.harness_id,
                    commit=state.commit,
                    image=state.image,
                    container_name=state.container,
                    status="CANCELLED",
                )
        await self._progress(
            comparison_id, planned, harness_counts, status_counts, total_jobs
        )
        await self._finish_summary(
            comparison_id,
            status="CANCELLED",
            models=models,
            error={
                "code": "CANCELLED",
                "message": "comparison was cancelled",
                "retryable": True,
            },
        )
        await self._event(
            comparison_id,
            "comparison_cancelled",
            {"done": len(terminal_jobs), "total": total_jobs},
        )

    async def _set_cancelled(self, comparison_id: str) -> None:
        comparison = await self.store.get_comparison(comparison_id)
        if comparison is None:
            return
        await self.store.update_comparison(
            comparison_id,
            status="CANCELLED",
            stage="cancelled",
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        await self._event(comparison_id, "comparison_cancelled", {})

    # -------------------------------------------------------------- reporting

    async def _finish_summary(
        self,
        comparison_id: str,
        *,
        status: str,
        models: list[str],
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        jobs = await self.store.list_jobs(comparison_id, limit=self.cfg.max_jobs + 1)
        summary = summarize(
            comparison_id=comparison_id,
            status=status,
            harness_names=HARNESS_NAMES,
            models=list(dict.fromkeys(models)),
            jobs=jobs,
        )
        summary["harness_runs"] = await self.store.list_harness_runs(comparison_id)
        summary["disclaimer"] = self.cfg.disclaimer
        await self.store.update_comparison(
            comparison_id,
            status=status,
            stage=status.lower(),
            summary=summary,
            error=error,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        return summary

    async def _progress(
        self,
        comparison_id: str,
        planned: dict[str, list[PlannedJob]],
        harness_counts: dict[str, Counter[str]],
        status_counts: Counter[str],
        total_jobs: int,
    ) -> None:
        by_harness: dict[str, Any] = {}
        for harness_id in HARNESS_IDS:
            counts = harness_counts[harness_id]
            done = sum(counts.values())
            by_harness[harness_id] = {
                "total": len(planned.get(harness_id) or []),
                "done": done,
                "completed": counts.get("COMPLETED", 0),
                "failed": counts.get("FAILED", 0)
                + counts.get("TIMEOUT", 0)
                + counts.get("CANCELLED", 0),
            }
        done_total = sum(by_harness[harness_id]["done"] for harness_id in HARNESS_IDS)
        progress = {
            "total_jobs": total_jobs,
            "done_jobs": done_total,
            "remaining_jobs": max(0, total_jobs - done_total),
            "percent": round(done_total / total_jobs * 100, 2) if total_jobs else 0.0,
            "completed": status_counts.get("COMPLETED", 0),
            "failed": status_counts.get("FAILED", 0),
            "timed_out": status_counts.get("TIMEOUT", 0),
            "cancelled": status_counts.get("CANCELLED", 0),
            "by_harness": by_harness,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.store.update_comparison(comparison_id, progress=progress)

    async def _stage(self, comparison_id: str, stage: str) -> None:
        await self.store.update_comparison(comparison_id, stage=stage)
        await self._event(comparison_id, "stage", {"stage": stage})

    async def _event(
        self, comparison_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        try:
            await self.store.add_event(comparison_id, event_type, payload)
        except Exception:  # noqa: BLE001 - audit must not abort a run
            logger.warning("could not record event %s", event_type)

    # --------------------------------------------------------------- cleanup

    async def _close_workers(self, workers: dict[str, WorkerState]) -> None:
        for state in workers.values():
            try:
                await self.docker.stop_worker(state.container)
            except Exception:  # noqa: BLE001
                pass
        try:
            await self.docker.kill_all_workers()
        except Exception:
            pass

    async def _cleanup_network(
        self, network: str, attachments: list[str], created: bool
    ) -> None:
        if not created or self.cfg.keep_network:
            return
        for container in attachments:
            try:
                await self.docker.disconnect_container(network, container)
            except Exception:  # noqa: BLE001
                pass
        try:
            await self.docker.remove_network(network)
        except Exception:  # noqa: BLE001
            pass

    async def _write_worker_log(
        self, comparison_id: str, harness_id: str, text: str
    ) -> None:
        path = self.cfg.runs_dir / comparison_id / "logs" / f"{harness_id}-worker.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _tail_file(path: Path, limit: int = 4_000) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError:
        return None
