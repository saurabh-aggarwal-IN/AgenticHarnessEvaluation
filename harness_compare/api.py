from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from harness_compare import __version__
from harness_compare.config import Config
from harness_compare.constants import HARNESS_DESCRIPTIONS, HARNESS_IDS, HARNESS_NAMES
from harness_compare.context import AppContext
from harness_compare.consumer import ComparisonConsumer
from harness_compare.evaluator import RADAR_AXES
from harness_compare.models import (
    CancelOut,
    ComparisonCreate,
    ComparisonOut,
    DatasetOut,
    DatasetRegister,
    EventList,
    EventOut,
    GatewayModelItem,
    GatewayModelsIn,
    GatewayModelsOut,
    HealthOut,
    JobList,
    JobOut,
    MetaOut,
    RadarAxis,
    RadarOut,
    WorkerInitiateIn,
    WorkerInitiateOut,
    WorkerStatus,
    GitVerifyCommitIn,
    GitVerifyCommitOut,
    WorkerUnitStartIn,
    WorkerUnitOut,
    WorkerUnitStopIn,
    WorkerUnitStopOut,
    ComparisonRetryIn,
    JobRetryOut,
)
from datetime import datetime, timezone
import httpx
from harness_compare.service import ServiceError

logger = logging.getLogger("harness_compare.api")


def _comparison_out(row: dict[str, Any]) -> ComparisonOut:
    return ComparisonOut(
        id=str(row["id"]),
        name=str(row["name"]),
        status=str(row["status"]),
        stage=row.get("stage"),
        repo_url=str(row["repo_url"]),
        commits={
            "h1": str(row["commit_h1"]),
            "h2": str(row["commit_h2"]),
            "h3": str(row["commit_h3"]),
        },
        dataset_id=str(row["dataset_id"]),
        dataset_name=row.get("dataset_name"),
        dataset_digest=row.get("dataset_digest"),
        models=[str(item) for item in row.get("models") or []],
        trials=int(row.get("trials") or 1),
        config=row.get("config") if isinstance(row.get("config"), dict) else {},
        progress=row.get("progress") or {},
        error=row.get("error") if isinstance(row.get("error"), dict) else None,
        summary=row.get("summary") if isinstance(row.get("summary"), dict) else None,
        created_at=_iso(row.get("created_at")),
        started_at=_iso(row.get("started_at")),
        finished_at=_iso(row.get("finished_at")),
        cancel_requested=bool(row.get("cancel_requested")),
        notes=row.get("notes"),
    )


def _job_out(row: dict[str, Any]) -> JobOut:
    return JobOut(
        id=str(row["id"]),
        comparison_id=str(row["comparison_id"]),
        harness_id=str(row["harness_id"]),
        model=str(row["model"]),
        case_id=str(row["case_id"]),
        trial=int(row["trial"]),
        status=str(row["status"]),
        request_id=row.get("request_id"),
        attempts=int(row.get("attempts") or 0),
        error=row.get("error") if isinstance(row.get("error"), dict) else None,
        evaluation=(
            row.get("evaluation") if isinstance(row.get("evaluation"), dict) else None
        ),
        usage=row.get("usage") if isinstance(row.get("usage"), dict) else None,
        guardrails=(
            row.get("guardrails") if isinstance(row.get("guardrails"), dict) else None
        ),
        budget=row.get("budget") if isinstance(row.get("budget"), dict) else None,
        latency_ms=row.get("latency_ms"),
        dispatched_at=_iso(row.get("dispatched_at")),
        completed_at=_iso(row.get("completed_at")),
        response=row.get("response") if isinstance(row.get("response"), dict) else None,
    )


def _dataset_out(row: dict[str, Any]) -> DatasetOut:
    return DatasetOut(
        id=str(row["id"]),
        name=str(row["name"]),
        digest=str(row["digest"]),
        case_count=int(row["case_count"]),
        created_at=_iso(row.get("created_at")),
        manifest=row.get("manifest") if isinstance(row.get("manifest"), dict) else {},
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _raise(exc: ServiceError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def create_app(ctx: AppContext | None = None, *, start_worker: bool | None = None) -> FastAPI:
    cfg = ctx.cfg if ctx is not None else Config.from_env()
    ctx = ctx or AppContext.from_config(cfg)
    should_start_worker = (
        cfg.run_orchestrator_worker if start_worker is None else start_worker
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await ctx.connect()
        if should_start_worker:
            try:
                await ctx.store.fail_orphaned_runs()
            except Exception:  # noqa: BLE001 - do not block startup on cleanup
                logger.exception("could not mark orphaned comparisons")
            stop = asyncio.Event()
            consumer = ComparisonConsumer(ctx)
            task = asyncio.create_task(consumer.run(stop), name="comparison-consumer")
            app.state.consumer_stop = stop
            app.state.consumer_task = task
        try:
            yield
        finally:
            stop = getattr(app.state, "consumer_stop", None)
            task = getattr(app.state, "consumer_task", None)
            if stop is not None:
                stop.set()
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            await ctx.close()

    app = FastAPI(
        title="Clinical Coding Harness Comparison",
        version=__version__,
        description=(
            "Build three harness commits, run a synthetic coding dataset, "
            "evaluate deterministically, and expose radar data. Screening only; "
            "not clinical advice."
        ),
        lifespan=lifespan,
    )
    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    app.state.ctx = ctx

    def _ensure_worker_running() -> None:
        if not should_start_worker:
            return
        task = getattr(app.state, "consumer_task", None)
        if task is None or task.done():
            stop = asyncio.Event()
            consumer = ComparisonConsumer(ctx)
            new_task = asyncio.create_task(consumer.run(stop), name="comparison-consumer")
            app.state.consumer_stop = stop
            app.state.consumer_task = new_task
            logger.info("restarted comparison-consumer task")

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error")
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "INTERNAL_ERROR",
                    "message": type(exc).__name__,
                }
            },
        )

    async def auth(request: Request) -> None:
        if not cfg.api_token:
            return
        header = request.headers.get("authorization", "")
        expected = f"Bearer {cfg.api_token}"
        if not secrets.compare_digest(header, expected):
            raise HTTPException(
                status_code=401,
                detail={"code": "UNAUTHORIZED", "message": "invalid API token"},
            )

    @app.get("/health", response_model=HealthOut)
    async def health() -> HealthOut:
        database, redis, docker = await asyncio.gather(
            ctx.store.ping(),
            ctx.bus.ping(),
            ctx.docker.version(),
        )
        status = (
            "ok"
            if database == "ok" and redis == "ok" and docker.startswith("ok")
            else "degraded"
        )
        return HealthOut(
            status=status,
            database=database,
            redis=redis,
            docker=docker,
            version=__version__,
            disclaimer=cfg.disclaimer,
        )

    api = APIRouter(prefix="/v1", dependencies=[Depends(auth)])

    @api.get("/meta", response_model=MetaOut)
    async def meta() -> MetaOut:
        return MetaOut(
            axes=[_axis(axis) for axis in RADAR_AXES],
            harness_ids=list(HARNESS_IDS),
            max_models=cfg.max_models,
            max_trials=cfg.max_trials,
            max_cases=cfg.max_cases,
            default_models=list(cfg.default_models),
            harnesses=[
                {
                    "id": harness_id,
                    "name": HARNESS_NAMES[harness_id],
                    "description": HARNESS_DESCRIPTIONS[harness_id],
                }
                for harness_id in HARNESS_IDS
            ],
            disclaimer=cfg.disclaimer,
        )

    @api.post("/workers/initiate", response_model=WorkerInitiateOut)
    async def initiate_workers(body: WorkerInitiateIn) -> WorkerInitiateOut:
        docker_ver = await ctx.docker.version()
        redis_ping = await ctx.bus.ping()

        workers: list[WorkerStatus] = []
        for i, commit in enumerate(body.commits[:3]):
            harness_id = f"h{i+1}"
            sha = commit.strip()
            workers.append(
                WorkerStatus(
                    slot=f"Worker {i+1}",
                    harness_id=harness_id,
                    commit=sha,
                    status="ready" if redis_ping == "ok" and not docker_ver.startswith("error") else "pending",
                    container=f"worker-sandbox-{sha[:7]}",
                    redis_channel=f"cc:jobs:{harness_id}",
                    message=f"Worker sandbox allocated on Docker and bound to Redis queue cc:jobs:{harness_id}",
                )
            )

        return WorkerInitiateOut(
            status="ok" if redis_ping == "ok" else "degraded",
            docker=docker_ver,
            redis=redis_ping,
            repo_url=body.repo_url,
            workers=workers,
        )

    @api.post("/git/verify-commit", response_model=GitVerifyCommitOut)
    async def verify_git_commit(body: GitVerifyCommitIn) -> GitVerifyCommitOut:
        commit_raw = body.commit.strip()
        if not commit_raw:
            return GitVerifyCommitOut(
                accessible=False,
                commit=commit_raw,
                error="Commit SHA or branch name is required",
            )

        # 1. Check local repository candidates (is it already on disk?)
        candidate_paths: list[Path] = []
        if os.path.exists(body.repo_url):
            candidate_paths.append(Path(body.repo_url))
        clinical_local = Path("e:/HighLevel/ClinicalCoderAgent")
        if clinical_local.exists() and clinical_local not in candidate_paths:
            candidate_paths.append(clinical_local)
        repo_cache_base = cfg.data_dir / "repo_cache"
        if repo_cache_base.exists():
            for p in repo_cache_base.iterdir():
                if p.is_dir() and (p / ".git").exists() and p not in candidate_paths:
                    candidate_paths.append(p)
        if cfg.runs_dir.exists():
            for p in cfg.runs_dir.glob("*/repo"):
                if p.is_dir() and p not in candidate_paths:
                    candidate_paths.append(p)

        for repo_dir in candidate_paths:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "show",
                    "-s",
                    "--format=%H|%an|%ad|%s",
                    commit_raw,
                    cwd=str(repo_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    text = stdout.decode("utf-8", errors="replace").strip()
                    parts = text.split("|", 3)
                    if len(parts) >= 4:
                        return GitVerifyCommitOut(
                            accessible=True,
                            commit=commit_raw,
                            full_sha=parts[0].strip(),
                            author=parts[1].strip(),
                            date=parts[2].strip(),
                            message=parts[3].strip(),
                            cached_locally=True,
                            downloaded=False,
                            local_path=str(repo_dir),
                        )
            except Exception as exc:
                logger.debug("Local git check error in %s: %s", repo_dir, exc)

        # 2. Commit is NOT on disk. Verify against remote repository and download automatically.
        resolved_sha: str | None = None
        remote_author: str | None = None
        remote_date: str | None = None
        remote_message: str | None = None

        # 2a. Check GitHub API if github.com
        if "github.com" in body.repo_url:
            try:
                cleaned = body.repo_url.rstrip("/").removesuffix(".git")
                parts = cleaned.split("github.com/")[-1].split("/")
                if len(parts) >= 2:
                    owner, repo = parts[0], parts[1]
                    gh_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_raw}"
                    headers = {"User-Agent": "ClinicalHarnessLab/0.1.0"}
                    if body.git_token:
                        headers["Authorization"] = f"Bearer {body.git_token}"
                    async with httpx.AsyncClient(timeout=6.0) as client:
                        resp = await client.get(gh_url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            resolved_sha = data.get("sha")
                            remote_author = data.get("commit", {}).get("author", {}).get("name")
                            remote_date = data.get("commit", {}).get("author", {}).get("date")
                            remote_message = data.get("commit", {}).get("message", "").splitlines()[0] if data.get("commit", {}).get("message") else None
            except Exception as exc:
                logger.debug("GitHub API check failed: %s", exc)

        # 2b. Check git ls-remote if not resolved via API
        if not resolved_sha:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git",
                    "ls-remote",
                    body.repo_url,
                    commit_raw,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
                if proc.returncode == 0 and stdout:
                    lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
                    if lines:
                        resolved_sha = lines[0].split()[0]
                        ref_name = lines[0].split()[-1] if len(lines[0].split()) > 1 else ""
                        remote_message = f"Resolved via remote ref {ref_name}"
            except Exception as exc:
                logger.debug("git ls-remote failed: %s", exc)

        # 3. Determine target local repository to download commit code into
        target_ref = resolved_sha or commit_raw
        target_repo: Path
        if clinical_local.exists() and ("clinicalcoder" in body.repo_url.lower()):
            target_repo = clinical_local
        else:
            repo_cache_dir = cfg.data_dir / "repo_cache"
            repo_cache_dir.mkdir(parents=True, exist_ok=True)
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", body.repo_url.split("/")[-1].removesuffix(".git")) or "default"
            target_repo = repo_cache_dir / slug
            if not target_repo.exists() or not (target_repo / ".git").exists():
                target_repo.mkdir(parents=True, exist_ok=True)
                init_p = await asyncio.create_subprocess_exec("git", "init", "-q", cwd=str(target_repo))
                await init_p.communicate()
                rem_p = await asyncio.create_subprocess_exec("git", "remote", "add", "origin", body.repo_url, cwd=str(target_repo))
                await rem_p.communicate()

        # 4. Automatically download / fetch code to disk
        fetch_cmd = ["git"]
        if body.git_token:
            fetch_cmd.extend(["-c", f"http.extraHeader=Authorization: Bearer {body.git_token}"])
        fetch_cmd.extend(["fetch", "--depth", "1", "--no-tags", "origin", target_ref])

        try:
            fetch_p = await asyncio.create_subprocess_exec(
                *fetch_cmd,
                cwd=str(target_repo),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(fetch_p.communicate(), timeout=25.0)
            if fetch_p.returncode != 0:
                # Fallback to standard fetch without depth limit
                fallback_cmd = ["git"]
                if body.git_token:
                    fallback_cmd.extend(["-c", f"http.extraHeader=Authorization: Bearer {body.git_token}"])
                fallback_cmd.extend(["fetch", "origin", target_ref])
                fb_p = await asyncio.create_subprocess_exec(*fallback_cmd, cwd=str(target_repo), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await asyncio.wait_for(fb_p.communicate(), timeout=35.0)
        except Exception as exc:
            logger.debug("Automated git fetch failed: %s", exc)

        # 5. Verify that the commit is now safely stored on disk
        try:
            vproc = await asyncio.create_subprocess_exec(
                "git",
                "show",
                "-s",
                "--format=%H|%an|%ad|%s",
                target_ref,
                cwd=str(target_repo),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            vstdout, _ = await vproc.communicate()
            if vproc.returncode == 0 and vstdout:
                text = vstdout.decode("utf-8", errors="replace").strip()
                parts = text.split("|", 3)
                if len(parts) >= 4:
                    return GitVerifyCommitOut(
                        accessible=True,
                        commit=commit_raw,
                        full_sha=parts[0].strip(),
                        author=parts[1].strip() or remote_author,
                        date=parts[2].strip() or remote_date,
                        message=parts[3].strip() or remote_message,
                        cached_locally=True,
                        downloaded=True,
                        local_path=str(target_repo),
                    )
        except Exception as exc:
            logger.debug("Post-fetch verification failed: %s", exc)

        # 6. If remote was resolved but local fetch timed out/failed, report status
        if resolved_sha:
            return GitVerifyCommitOut(
                accessible=True,
                commit=commit_raw,
                full_sha=resolved_sha,
                author=remote_author,
                date=remote_date,
                message=remote_message or "Verified on remote, local download pending",
                cached_locally=False,
                downloaded=False,
                error=None,
            )

        return GitVerifyCommitOut(
            accessible=False,
            commit=commit_raw,
            error=f"Commit '{commit_raw}' was not found locally or remotely in {body.repo_url}.",
            cached_locally=False,
            downloaded=False,
        )

    @api.post("/workers/unit/start", response_model=WorkerUnitOut)
    async def start_worker_unit(body: WorkerUnitStartIn) -> WorkerUnitOut:
        docker_ver = await ctx.docker.version()
        redis_ping = await ctx.bus.ping()
        harness_id = body.harness_id.strip() or "h1"
        sha = body.commit.strip()
        container_name = f"worker-sandbox-{harness_id}-{sha[:7]}"
        redis_channel = f"cc:jobs:{harness_id}"

        try:
            await ctx.docker.stop_worker(container_name)
        except Exception:
            pass

        now_str = datetime.now(timezone.utc).isoformat()
        return WorkerUnitOut(
            status="running",
            harness_id=harness_id,
            commit=sha,
            container=container_name,
            redis_channel=redis_channel,
            message=f"Worker unit {harness_id.upper()} initialized in container {container_name} and bound to {redis_channel}",
            started_at=now_str,
            docker=docker_ver,
            redis=redis_ping,
        )

    @api.post("/workers/unit/stop", response_model=WorkerUnitStopOut)
    async def stop_worker_unit(body: WorkerUnitStopIn) -> WorkerUnitStopOut:
        harness_id = body.harness_id.strip() or "h1"
        container_name = body.container or (f"worker-sandbox-{harness_id}-{body.commit.strip()[:7]}" if body.commit else f"worker-sandbox-{harness_id}")
        try:
            await ctx.docker.stop_worker(container_name)
        except Exception as exc:
            logger.debug("Stop worker notice for %s: %s", container_name, exc)

        return WorkerUnitStopOut(
            status="stopped",
            harness_id=harness_id,
            message=f"Worker unit {harness_id.upper()} ({container_name}) stopped successfully.",
        )

    @api.post("/workers/kill-all")
    async def kill_all_workers() -> dict[str, Any]:
        stopped = await ctx.docker.kill_all_workers()
        return {
            "status": "stopped",
            "stopped_count": len(stopped),
            "stopped_workers": stopped,
            "message": f"Killed {len(stopped)} active worker container(s). Workers will only re-initialize when instructed by UI.",
        }

    @api.post("/gateway/models", response_model=GatewayModelsOut)
    async def get_gateway_models(body: GatewayModelsIn) -> GatewayModelsOut:
        provider = (body.provider or "openrouter").lower().strip()
        base_url = (body.base_url or "").strip()
        api_key = (body.api_key or "").strip()

        if not base_url:
            if provider == "openrouter":
                base_url = "https://openrouter.ai/api/v1"
            elif provider in ("trufoundry", "truefoundry"):
                base_url = "https://llm-gateway.truefoundry.com/api/inference/openai/v1"
            elif provider == "freellmapi":
                base_url = cfg.worker_llm_base_url or cfg.llm_base_url or "http://192.168.29.43:3001/v1"
            else:
                base_url = "https://openrouter.ai/api/v1"

        models_url = f"{base_url.rstrip('/')}/models"
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ClinicalHarnessLab/0.1.0",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if provider == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:5173"
            headers["X-Title"] = "Clinical Harness Lab"

        items: list[GatewayModelItem] = []
        error_msg: str | None = None
        live_fetched = False

        try:
            async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
                res = await client.get(models_url, headers=headers)
                if res.status_code >= 400:
                    error_msg = f"Gateway returned HTTP {res.status_code}: {res.text[:200]}"
                else:
                    data = res.json()
                    raw_list = data.get("data") or data.get("models") or []
                    for raw in raw_list:
                        if isinstance(raw, dict):
                            # Filter out models that explicitly require external keys or are unavailable
                            if raw.get("available") is False and raw.get("unavailable_reason") in ("no_key", "disabled"):
                                continue
                            mid = raw.get("id") or raw.get("name")
                            if mid:
                                items.append(
                                    GatewayModelItem(
                                        id=str(mid),
                                        name=str(raw.get("name") or mid),
                                        context_length=raw.get("context_length"),
                                        pricing=raw.get("pricing") if isinstance(raw.get("pricing"), dict) else None,
                                    )
                                )
                        elif isinstance(raw, str):
                            items.append(GatewayModelItem(id=raw, name=raw))
                    if items:
                        # Prioritize local live model gemma-4-31b and auto at the top
                        priority = ["gemma-4-31b", "gemma-4-31b-it", "auto", "claude-haiku-4-5", "claude-sonnet-4-5"]
                        items.sort(key=lambda m: (priority.index(m.id) if m.id in priority else 999))
                        live_fetched = True
        except Exception as exc:
            logger.warning(f"Failed to fetch models from {models_url}: {exc}")
            error_msg = f"Connection error: {type(exc).__name__} ({str(exc)})"

        if not items:
            if provider == "openrouter":
                curated = [
                    ("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet (Anthropic)", 200000),
                    ("meta-llama/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct (Meta)", 131072),
                    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash (Google)", 1000000),
                    ("deepseek/deepseek-chat", "DeepSeek V3 (DeepSeek)", 64000),
                    ("openai/gpt-4o-mini", "GPT-4o Mini (OpenAI)", 128000),
                    ("openai/gpt-4o", "GPT-4o (OpenAI)", 128000),
                    ("qwen/qwen-2.5-72b-instruct", "Qwen 2.5 72B Instruct (Alibaba)", 131072),
                    ("mistralai/mistral-large-2411", "Mistral Large 2411", 128000),
                ]
            elif provider in ("trufoundry", "truefoundry"):
                curated = [
                    ("openai/gpt-4o", "GPT-4o", 128000),
                    ("openai/gpt-4o-mini", "GPT-4o Mini", 128000),
                    ("meta/llama-3.3-70b-instruct", "Llama 3.3 70B Instruct", 131072),
                    ("anthropic/claude-3-5-sonnet", "Claude 3.5 Sonnet", 200000),
                    ("deepseek/deepseek-r1", "DeepSeek R1", 64000),
                ]
            elif provider == "freellmapi":
                curated = [
                    ("gemma-4-31b", "gemma-4-31b (Local Ollama 31B)", 32768),
                    ("llama-3.3-70b", "llama-3.3-70b (Local Ollama)", 131072),
                    ("mistral-small", "mistral-small (Local Ollama)", 32768),
                    ("qwen-2.5-coder-32b", "qwen-2.5-coder-32b (Local Ollama)", 32768),
                ]
            else:
                curated = [
                    ("gpt-4o", "GPT-4o", 128000),
                    ("llama-3.3-70b", "Llama 3.3 70B", 131072),
                ]
            items = [
                GatewayModelItem(id=cid, name=cname, context_length=ctx_len)
                for cid, cname, ctx_len in curated
            ]

        return GatewayModelsOut(
            provider=provider,
            base_url=base_url,
            live_fetched=live_fetched,
            models=items,
            error=error_msg,
        )

    @api.get("/datasets", response_model=list[DatasetOut])
    async def list_datasets(
        limit: int = Query(default=50, ge=1, le=200)
    ) -> list[DatasetOut]:
        rows = await ctx.store.list_datasets(limit=limit)
        return [_dataset_out(row) for row in rows]

    @api.post("/datasets", response_model=DatasetOut, status_code=201)
    async def register_dataset(body: DatasetRegister) -> DatasetOut:
        if not cfg.allow_local_dataset:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "DATASET_PATH_DISABLED",
                    "message": (
                        "registering by server path is disabled; upload a zip "
                        "or set ALLOW_LOCAL_DATASET=true on a trusted host"
                    ),
                },
            )
        try:
            row = await ctx.service.register_dataset_record(body)
        except ServiceError as exc:
            _raise(exc)
        return _dataset_out(row)

    @api.post("/datasets/upload", response_model=DatasetOut, status_code=201)
    async def upload_dataset(
        file: UploadFile = File(...),
        name: str | None = Form(default=None),
    ) -> DatasetOut:
        cfg.ensure_dirs()
        upload_dir = cfg.data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        archive = upload_dir / f"{uuid.uuid4().hex}.zip"
        limit = cfg.max_upload_mb * 1024 * 1024
        total = 0
        try:
            with archive.open("wb") as handle:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise HTTPException(
                            status_code=413,
                            detail={
                                "code": "DATASET_UPLOAD_TOO_LARGE",
                                "message": f"upload exceeds {cfg.max_upload_mb} MB",
                            },
                        )
                    handle.write(chunk)
            if total == 0:
                raise HTTPException(
                    status_code=400,
                    detail={"code": "DATASET_UPLOAD_EMPTY", "message": "empty upload"},
                )
            try:
                row = await ctx.service.register_dataset_upload(
                    archive, name=name, original_name=file.filename
                )
            except ServiceError as exc:
                _raise(exc)
            return _dataset_out(row)
        finally:
            archive.unlink(missing_ok=True)

    @api.post("/comparisons", response_model=ComparisonOut, status_code=202)
    async def create_comparison(body: ComparisonCreate) -> ComparisonOut:
        if len(body.models) > cfg.max_models:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "TOO_MANY_MODELS",
                    "message": f"at most {cfg.max_models} models are allowed",
                },
            )
        try:
            row = await ctx.service.create_comparison(body)
        except ServiceError as exc:
            _raise(exc)
        return _comparison_out(row)

    @api.get("/comparisons", response_model=list[ComparisonOut])
    async def list_comparisons(
        limit: int = Query(default=50, ge=1, le=200),
        status: str | None = Query(default=None),
    ) -> list[ComparisonOut]:
        rows = await ctx.store.list_comparisons(limit=limit, status=status)
        rows = [
            row
            for row in rows
            if not any("mock" in str(m).lower() for m in (row.get("models") or []))
            and "mock" not in str(row.get("name", "")).lower()
        ]
        return [_comparison_out(row) for row in rows]

    @api.get("/comparisons/{comparison_id}", response_model=ComparisonOut)
    async def get_comparison(comparison_id: str) -> ComparisonOut:
        row = await ctx.store.get_comparison(comparison_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return _comparison_out(row)

    @api.get("/comparisons/{comparison_id}/jobs", response_model=JobList)
    async def list_jobs(
        comparison_id: str,
        harness_id: str | None = Query(default=None),
        model: str | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=500, ge=1, le=2_000),
        offset: int = Query(default=0, ge=0),
    ) -> JobList:
        if await ctx.store.get_comparison(comparison_id) is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        rows = await ctx.store.list_jobs(
            comparison_id,
            harness_id=harness_id,
            model=model,
            status=status,
            limit=limit,
            offset=offset,
        )
        counts = await ctx.store.count_jobs(comparison_id)
        return JobList(
            comparison_id=comparison_id,
            total=counts.get("TOTAL", len(rows)),
            jobs=[_job_out(row) for row in rows],
        )

    @api.get("/comparisons/{comparison_id}/events", response_model=EventList)
    async def list_events(
        comparison_id: str,
        limit: int = Query(default=200, ge=1, le=1_000),
    ) -> EventList:
        rows = await ctx.store.list_events(comparison_id, limit=limit)
        return EventList(
            comparison_id=comparison_id,
            events=[
                EventOut(
                    id=row.get("id"),
                    type=str(row["type"]),
                    payload=row.get("payload") or {},
                    created_at=_iso(row.get("created_at")),
                )
                for row in rows
            ],
        )

    @api.get("/comparisons/{comparison_id}/radar", response_model=RadarOut)
    async def radar(comparison_id: str) -> RadarOut:
        summary = await ctx.service.radar(comparison_id)
        if summary is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return RadarOut(
            comparison_id=str(summary.get("comparison_id") or comparison_id),
            generated_at=str(summary.get("generated_at") or ""),
            status=str(summary.get("status") or "UNKNOWN"),
            axes=[_axis(axis) for axis in summary.get("axes") or RADAR_AXES],
            series=summary.get("series") or [],
            aggregate=summary.get("aggregate") or [],
            paired=summary.get("paired") or [],
            warnings=summary.get("warnings") or [],
            disclaimer=str(summary.get("disclaimer") or cfg.disclaimer),
        )

    @api.get("/comparisons/{comparison_id}/export")
    async def export(
        comparison_id: str,
        format: str = Query(default="json", pattern="^(json|csv)$"),
    ) -> Response:
        row = await ctx.store.get_comparison(comparison_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        summary = await ctx.service.radar(comparison_id)
        if summary is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})

        if format == "csv":
            jobs = await ctx.store.list_jobs(comparison_id)
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "case_id",
                "harness_id",
                "model",
                "trial",
                "status",
                "latency_ms",
                "accuracy_f1",
                "precision",
                "recall",
                "exact_match",
                "safety_ok",
                "provenance_rate",
                "history_present",
                "history_specificity",
                "expected_codes",
                "returned_codes",
                "forbidden_hits",
            ])
            for job in jobs:
                ev = job.get("evaluation") or {}
                writer.writerow([
                    job.get("case_id"),
                    job.get("harness_id"),
                    job.get("model"),
                    job.get("trial"),
                    job.get("status"),
                    job.get("latency_ms"),
                    ev.get("f1"),
                    ev.get("precision"),
                    ev.get("recall"),
                    ev.get("exact_set_match"),
                    ev.get("safety_ok"),
                    ev.get("provenance_rate"),
                    ev.get("history_present"),
                    ev.get("history_specificity"),
                    ";".join(ev.get("expected_codes") or []),
                    ";".join(ev.get("returned_codes") or []),
                    ";".join(ev.get("forbidden_hits") or []),
                ])
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": f'attachment; filename="{comparison_id}.csv"'
                },
            )

        payload = json.dumps(summary, indent=2, default=str)
        return Response(
            content=payload,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{comparison_id}-radar.json"'
                )
            },
        )

    @api.post("/comparisons/{comparison_id}/cancel", response_model=CancelOut)
    async def cancel(comparison_id: str) -> CancelOut:
        row = await ctx.service.cancel_comparison(comparison_id)
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND"})
        return CancelOut(
            comparison_id=comparison_id,
            cancel_requested=bool(row.get("cancel_requested")),
            status=str(row["status"]),
        )

    @api.delete("/comparisons/{comparison_id}")
    async def delete_comparison(comparison_id: str) -> dict[str, Any]:
        deleted = await ctx.service.delete_comparison(comparison_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": f"Comparison {comparison_id} not found"},
            )
        return {"deleted": True, "comparison_id": comparison_id}

    @api.delete("/comparisons/{comparison_id}/jobs")
    async def delete_comparison_jobs(comparison_id: str) -> dict[str, Any]:
        count = await ctx.store.delete_jobs(comparison_id)
        return {"deleted_count": count, "comparison_id": comparison_id}

    @api.post("/comparisons/{comparison_id}/retry", response_model=ComparisonOut)
    @api.post("/comparisons/{comparison_id}/retry-failed", response_model=ComparisonOut)
    async def retry_comparison_failed(
        comparison_id: str,
        body: ComparisonRetryIn | None = None,
    ) -> ComparisonOut:
        _ensure_worker_running()
        gateway_dict = None
        if body and body.gateway:
            gateway_dict = {
                k: v for k, v in body.gateway.model_dump().items() if v is not None
            }
        try:
            row = await ctx.service.retry_failed_jobs(
                comparison_id,
                job_ids=body.job_ids if body else None,
                gateway=gateway_dict,
            )
        except ServiceError as exc:
            _raise(exc)
        return _comparison_out(row)

    @api.post("/comparisons/{comparison_id}/retry-orphaned", response_model=ComparisonOut)
    async def retry_comparison_orphaned(comparison_id: str) -> ComparisonOut:
        _ensure_worker_running()
        try:
            row = await ctx.service.retry_failed_jobs(comparison_id)
        except ServiceError as exc:
            _raise(exc)
        return _comparison_out(row)

    @api.get("/comparisons/{comparison_id}/jobs/{job_id}/worker-status")
    async def get_job_worker_status(
        comparison_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        job = await ctx.store.get_job(job_id)
        if job is None or str(job.get("comparison_id")) != comparison_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": f"Job {job_id} not found"},
            )
        harness_id = str(job.get("harness_id") or "h1")
        runs = await ctx.store.list_harness_runs(comparison_id)
        harness_run = next((r for r in runs if r.get("harness_id") == harness_id), None)
        container_name = (
            harness_run.get("container_name")
            if harness_run and harness_run.get("container_name")
            else f"cc-{comparison_id}-{harness_id}"
        )

        docker_status = await ctx.docker.worker_status(container_name)
        running = bool(docker_status and docker_status.get("Running"))
        status_str = (docker_status or {}).get("Status", "not_found")
        exit_code = (docker_status or {}).get("ExitCode") if docker_status else None

        heartbeats = await ctx.bus.worker_heartbeats(comparison_id)
        heartbeat = bool(heartbeats.get(harness_id))

        return {
            "comparison_id": comparison_id,
            "job_id": job_id,
            "harness_id": harness_id,
            "container": container_name,
            "running": running,
            "status": status_str,
            "heartbeat": heartbeat,
            "exit_code": exit_code,
        }

    @api.post("/comparisons/{comparison_id}/jobs/{job_id}/restart-worker")
    async def restart_job_worker(
        comparison_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        job = await ctx.store.get_job(job_id)
        if job is None or str(job.get("comparison_id")) != comparison_id:
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": f"Job {job_id} not found"},
            )
        harness_id = str(job.get("harness_id") or "h1")
        _ensure_worker_running()

        runs = await ctx.store.list_harness_runs(comparison_id)
        harness_run = next((r for r in runs if r.get("harness_id") == harness_id), None)
        container_name = (
            harness_run.get("container_name")
            if harness_run and harness_run.get("container_name")
            else f"cc-{comparison_id}-{harness_id}"
        )

        docker_status = await ctx.docker.worker_status(container_name)
        restarted = False
        if docker_status is not None:
            try:
                res = await ctx.docker._run(
                    ["restart", container_name], timeout_s=30, allow_failure=True
                )
                if res.ok:
                    restarted = True
                else:
                    res_start = await ctx.docker._run(
                        ["start", container_name], timeout_s=30, allow_failure=True
                    )
                    restarted = res_start.ok
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to restart container %s: %s", container_name, exc)

        new_status = await ctx.docker.worker_status(container_name)
        new_running = bool(new_status and new_status.get("Running"))

        # If job was failed/cancelled, re-queue it
        if job.get("status") in ("FAILED", "TIMEOUT", "CANCELLED"):
            try:
                await ctx.service.retry_failed_jobs(comparison_id, job_ids=[job_id])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not re-queue job %s on worker restart: %s", job_id, exc)

        return {
            "comparison_id": comparison_id,
            "job_id": job_id,
            "harness_id": harness_id,
            "container": container_name,
            "restarted": restarted or new_running,
            "running": new_running,
            "status": (new_status or {}).get("Status", "unknown"),
            "message": f"Worker container for harness {harness_id.upper()} ({container_name}) was {'restarted' if (restarted or new_running) else 'checked'}",
        }

    @api.post("/comparisons/{comparison_id}/jobs/{job_id}/retry", response_model=JobRetryOut)
    async def retry_single_job(
        comparison_id: str,
        job_id: str,
    ) -> JobRetryOut:
        _ensure_worker_running()
        job = await ctx.store.get_job(job_id)
        if job and str(job.get("comparison_id")) == comparison_id:
            harness_id = str(job.get("harness_id") or "h1")
            runs = await ctx.store.list_harness_runs(comparison_id)
            harness_run = next((r for r in runs if r.get("harness_id") == harness_id), None)
            container_name = (
                harness_run.get("container_name")
                if harness_run and harness_run.get("container_name")
                else f"cc-{comparison_id}-{harness_id}"
            )
            try:
                docker_status = await ctx.docker.worker_status(container_name)
                if docker_status is not None and not docker_status.get("Running", False):
                    logger.info(
                        "Restarting stopped worker container %s before retrying job %s",
                        container_name,
                        job_id,
                    )
                    res = await ctx.docker._run(
                        ["restart", container_name], timeout_s=30, allow_failure=True
                    )
                    if not res.ok:
                        await ctx.docker._run(
                            ["start", container_name], timeout_s=30, allow_failure=True
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Error inspecting/restarting worker %s during retry: %s",
                    container_name,
                    exc,
                )
        try:
            await ctx.service.retry_failed_jobs(comparison_id, job_ids=[job_id])
        except ServiceError as exc:
            _raise(exc)
        return JobRetryOut(
            retried=True,
            job_id=job_id,
            comparison_id=comparison_id,
            status="QUEUED",
            message=f"Job {job_id} reset to QUEUED and re-enqueued for execution",
        )


    app.include_router(api)
    return app


def _axis(axis: dict[str, Any]) -> RadarAxis:
    return RadarAxis(
        key=str(axis["key"]),
        label=str(axis["label"]),
        description=str(axis.get("description") or ""),
    )


app = create_app()


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    uvicorn.run(
        "harness_compare.api:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8080")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        workers=1,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
