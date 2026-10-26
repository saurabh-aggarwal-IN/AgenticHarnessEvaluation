from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

DISCLAIMER = (
    "Empirical agent harness evaluation benchmark developed by Dr. Saurabh Aggarwal "
    "for the purpose of recruitment evaluation by HighLevel (https://www.gohighlevel.com). "
    "Contrasts between harnesses are descriptive; evaluate qualitative traces alongside quantitative "
    "metrics when making agent architectural and deployment decisions."
)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


def _str(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


def _optional_int(name: str, minimum: int = 1) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a float, got {raw!r}") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Config:
    database_url: str
    redis_url: str

    base_dir: Path
    data_dir: Path
    runs_dir: Path

    docker_bin: str
    infra_network: str
    network_prefix: str
    postgres_container: str
    redis_container: str
    router_container: str
    keep_workers: bool
    keep_network: bool
    keep_worktrees: bool
    keep_images: bool
    worker_restarts: int
    harness_subdir: str
    worker_redis_url: str
    worker_llm_base_url: str
    worker_consumer_group: str

    repo_allowed_hosts: tuple[str, ...]
    allow_local_repo: bool
    git_token: str
    git_timeout_s: float

    llm_base_url: str
    llm_api_key: str
    default_models: tuple[str, ...]
    llm_json_mode: bool
    llm_max_retries: int
    llm_timeout_s: float
    llm_rpm: int
    llm_max_concurrency: int

    server_max_llm_calls: int
    server_max_input_tokens: int
    server_max_output_tokens: int
    server_max_total_tokens: int
    server_max_wall_seconds: float
    server_max_cost_usd: float | None
    job_budget_wall_seconds: float

    api_token: str
    cors_origins: tuple[str, ...]
    allow_local_dataset: bool
    run_orchestrator_worker: bool
    max_active_comparisons: int
    max_upload_mb: int
    max_cases: int
    max_models: int
    max_trials: int
    max_jobs: int

    build_timeout_s: float
    worker_ready_timeout_s: float
    job_timeout_s: float
    comparison_timeout_s: float
    poll_interval_s: float
    progress_interval_s: float
    max_inflight_per_harness: int

    worker_memory: str
    worker_cpus: str
    worker_pids: int
    worker_tmpfs_mb: int
    worker_read_only: bool

    comparison_stream: str
    comparison_group: str
    results_group: str
    run_consumer: str

    log_level: str
    disclaimer: str = DISCLAIMER
    max_inflight_h1: int | None = None
    max_inflight_h2: int | None = None
    max_inflight_h3: int | None = None

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Config":
        root = (base_dir or Path(__file__).resolve().parent.parent).resolve()
        _load_dotenv(root / ".env")
        data_dir = Path(_str("DATA_DIR", str(root / "data"))).resolve()
        runs_dir = Path(_str("RUNS_DIR", str(data_dir / "runs"))).resolve()
        max_cost_raw = _str("SERVER_MAX_COST_USD", "1.00")
        max_cost = (
            None
            if max_cost_raw.lower() in {"", "none", "null"}
            else float(max_cost_raw)
        )
        default_models = _list("DEFAULT_MODELS")
        return cls(
            database_url=_str(
                "DATABASE_URL",
                "postgresql://cc:cc@127.0.0.1:55432/harness_compare",
            ),
            redis_url=_str("REDIS_URL", "redis://127.0.0.1:56379/0"),
            base_dir=root,
            data_dir=data_dir,
            runs_dir=runs_dir,
            docker_bin=_str("DOCKER_BIN", "docker"),
            infra_network=_str("INFRA_NETWORK", "cc-infra"),
            network_prefix=_str("NETWORK_PREFIX", "cc-cmp"),
            postgres_container=_str("POSTGRES_CONTAINER", "cc-postgres"),
            redis_container=_str("REDIS_CONTAINER", "cc-redis"),
            router_container=_str("ROUTER_CONTAINER", ""),
            keep_workers=_bool("KEEP_WORKERS", False),
            keep_network=_bool("KEEP_NETWORK", False),
            keep_worktrees=_bool("KEEP_WORKTREES", False),
            keep_images=_bool("KEEP_IMAGES", False),
            worker_restarts=_int("WORKER_RESTARTS", 2, 0),
            harness_subdir=_str("HARNESS_SUBDIR", ""),
            worker_redis_url=_str("WORKER_REDIS_URL", ""),
            worker_llm_base_url=_str("WORKER_LLM_BASE_URL", ""),
            worker_consumer_group=_str("WORKER_CONSUMER_GROUP", "workers"),
            repo_allowed_hosts=_list(
                "REPO_ALLOWED_HOSTS", ("github.com", "www.github.com")
            ),
            allow_local_repo=_bool("ALLOW_LOCAL_REPO", False),
            git_token=_str("GIT_TOKEN", ""),
            git_timeout_s=_float("GIT_TIMEOUT_S", 300.0, 10.0),
            llm_base_url=_str("LLM_BASE_URL", ""),
            llm_api_key=_str("LLM_API_KEY", ""),
            default_models=default_models,
            llm_json_mode=_bool("LLM_JSON_MODE", False),
            llm_max_retries=_int("LLM_MAX_RETRIES", 3, 0),
            llm_timeout_s=_float("LLM_TIMEOUT_S", 90.0, 1.0),
            llm_rpm=_int("LLM_RPM", 60, 0),
            llm_max_concurrency=_int("LLM_MAX_CONCURRENCY", 4, 1),
            server_max_llm_calls=_int("SERVER_MAX_LLM_CALLS", 8, 1),
            server_max_input_tokens=_int("SERVER_MAX_INPUT_TOKENS", 60_000, 1_000),
            server_max_output_tokens=_int("SERVER_MAX_OUTPUT_TOKENS", 8_000, 256),
            server_max_total_tokens=_int("SERVER_MAX_TOTAL_TOKENS", 80_000, 1_000),
            server_max_wall_seconds=_float("SERVER_MAX_WALL_SECONDS", 180.0, 10.0),
            server_max_cost_usd=max_cost,
            job_budget_wall_seconds=_float(
                "JOB_BUDGET_WALL_SECONDS", 180.0, 10.0
            ),
            api_token=_str("ORCH_API_TOKEN", ""),
            cors_origins=_list(
                "CORS_ORIGINS",
                ("http://localhost:5173", "http://127.0.0.1:5173"),
            ),
            allow_local_dataset=_bool("ALLOW_LOCAL_DATASET", False),
            run_orchestrator_worker=_bool("RUN_ORCHESTRATOR_WORKER", True),
            max_active_comparisons=_int("MAX_ACTIVE_COMPARISONS", 2, 1),
            max_upload_mb=_int("MAX_UPLOAD_MB", 50, 1),
            max_cases=_int("MAX_CASES", 100, 1),
            max_models=_int("MAX_MODELS", 6, 1),
            max_trials=_int("MAX_TRIALS", 5, 1),
            max_jobs=_int("MAX_JOBS", 2_000, 1),
            build_timeout_s=_float("BUILD_TIMEOUT_S", 1_800.0, 30.0),
            worker_ready_timeout_s=_float("WORKER_READY_TIMEOUT_S", 120.0, 10.0),
            job_timeout_s=_float("JOB_TIMEOUT_S", 600.0, 10.0),
            comparison_timeout_s=_float(
                "COMPARISON_TIMEOUT_S", 7_200.0, 60.0
            ),
            poll_interval_s=_float("POLL_INTERVAL_S", 1.0, 0.1),
            progress_interval_s=_float("PROGRESS_INTERVAL_S", 3.0, 0.5),
            max_inflight_per_harness=_int("MAX_INFLIGHT_PER_HARNESS", 2, 1),
            max_inflight_h1=_optional_int("MAX_INFLIGHT_H1", 1),
            max_inflight_h2=_optional_int("MAX_INFLIGHT_H2", 1),
            max_inflight_h3=_optional_int("MAX_INFLIGHT_H3", 1),
            worker_memory=_str("WORKER_MEMORY", "2g"),
            worker_cpus=_str("WORKER_CPUS", "2"),
            worker_pids=_int("WORKER_PIDS", 256, 0),
            worker_tmpfs_mb=_int("WORKER_TMPFS_MB", 512, 32),
            worker_read_only=_bool("WORKER_READ_ONLY", True),
            comparison_stream=_str("COMPARISON_STREAM", "cc:comparisons"),
            comparison_group=_str("COMPARISON_GROUP", "orchestrators"),
            results_group=_str("RESULTS_GROUP", "orchestrator"),
            run_consumer=_str(
                "RUN_CONSUMER",
                f"runner-{socket.gethostname()}-{os.getpid()}",
            ),
            log_level=_str("LOG_LEVEL", "INFO").upper(),
        )

    def max_inflight_for_harness(
        self, harness_id: str, comparison_concurrency: int | None = None
    ) -> int:
        """Return the maximum allowed concurrent in-flight jobs for a specific harness.

        If a comparison specifies an explicit concurrency parameter, that override is used.
        Otherwise per-harness or global defaults are applied.
        """
        if comparison_concurrency is not None and comparison_concurrency >= 1:
            return max(1, comparison_concurrency)
        h = harness_id.lower().strip()
        if h == "h1" and self.max_inflight_h1 is not None:
            return max(1, self.max_inflight_h1)
        if h == "h2" and self.max_inflight_h2 is not None:
            return max(1, self.max_inflight_h2)
        if h == "h3" and self.max_inflight_h3 is not None:
            return max(1, self.max_inflight_h3)
        return max(1, self.max_inflight_per_harness)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def job_stream(self, comparison_id: str, harness_id: str) -> str:
        return f"cc:jobs:{comparison_id}:{harness_id}"

    def results_stream(self, comparison_id: str) -> str:
        return f"cc:results:{comparison_id}"

    def network(self, comparison_id: str) -> str:
        return f"{self.network_prefix}-{comparison_id}"

    def image_tag(self, comparison_id: str, harness_id: str, commit: str) -> str:
        return f"clinical-coder-{harness_id}:{comparison_id}-{commit[:12]}"

    def effective_worker_redis_url(self) -> str:
        """Redis URL as seen from a worker container.

        Workers run on an isolated bridge network, so a host-local Redis URL
        cannot be used inside them. When Redis runs as the configured infra
        container, address it by container name on the comparison network.
        """
        if self.worker_redis_url:
            return self.worker_redis_url
        if self.redis_container:
            return f"redis://{self.redis_container}:6379/0"
        return self.redis_url

    def effective_worker_llm_url(self) -> str:
        """LLM base URL as seen from a worker container."""
        if self.worker_llm_base_url:
            return self.worker_llm_base_url
        return self.llm_base_url
