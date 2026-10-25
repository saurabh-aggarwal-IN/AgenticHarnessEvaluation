from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-\[\]]{0,199}$")
HARNESS_IDS = ("h1", "h2", "h3")

JobStatus = Literal[
    "QUEUED", "DISPATCHED", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"
]
ComparisonStatus = Literal[
    "QUEUED",
    "RUNNING",
    "CANCELLING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]


class CommitSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    h1: str
    h2: str
    h3: str

    @field_validator("h1", "h2", "h3")
    @classmethod
    def _valid_commit(cls, value: str) -> str:
        value = value.strip().lower()
        if not COMMIT_RE.match(value):
            raise ValueError("commit must be a 7-40 character hexadecimal SHA")
        return value

    @model_validator(mode="after")
    def _distinct(self) -> "CommitSet":
        if len({self.h1, self.h2, self.h3}) != 3:
            raise ValueError("h1, h2, and h3 must be three distinct commits")
        return self

    def as_dict(self) -> dict[str, str]:
        return {"h1": self.h1, "h2": self.h2, "h3": self.h3}


class JobBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_llm_calls: int | None = Field(default=None, ge=1, le=32)
    max_input_tokens: int | None = Field(default=None, ge=1, le=500_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=64_000)
    max_total_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    max_wall_seconds: float | None = Field(default=None, ge=5, le=3_600)
    max_cost_usd: float | None = Field(default=None, ge=0)

    def as_request(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.model_dump().items()
            if value is not None
        }


class ComparisonCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=120)
    repo_url: str = Field(min_length=1, max_length=500)
    commits: CommitSet
    dataset_id: str = Field(min_length=1, max_length=80)
    models: list[str] = Field(min_length=1, max_length=12)
    harness_models: dict[str, str] | None = None
    gateway: dict[str, Any] | None = None
    trials: int = Field(default=1, ge=1, le=5)
    concurrency: int | None = Field(default=None, ge=1, le=16)
    budget: JobBudget | None = None
    notes: str | None = Field(default=None, max_length=2_000)

    @field_validator("repo_url")
    @classmethod
    def _clean_repo(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("repo_url must not be empty")
        if any(ord(char) < 32 for char in value):
            raise ValueError("repo_url must not contain control characters")
        return value

    @field_validator("models")
    @classmethod
    def _clean_models(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            value = value.strip()
            if not MODEL_RE.match(value):
                raise ValueError(
                    f"invalid model id {value!r}; expected a router model slug"
                )
            if value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise ValueError("at least one model is required")
        return cleaned


class GatewayModelsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(default="openrouter")
    base_url: str | None = None
    api_key: str | None = None


class GatewayModelItem(BaseModel):
    id: str
    name: str | None = None
    context_length: int | None = None
    pricing: dict[str, Any] | None = None


class GatewayModelsOut(BaseModel):
    provider: str
    base_url: str
    live_fetched: bool
    models: list[GatewayModelItem]
    error: str | None = None


class DatasetRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_000)
    name: str | None = Field(default=None, max_length=120)


class DatasetOut(BaseModel):
    id: str
    name: str
    digest: str
    case_count: int
    created_at: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)


class ComparisonOut(BaseModel):
    id: str
    name: str
    status: str
    stage: str | None = None
    repo_url: str
    commits: dict[str, str]
    dataset_id: str
    dataset_name: str | None = None
    dataset_digest: str | None = None
    models: list[str]
    trials: int
    config: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False
    notes: str | None = None


class JobOut(BaseModel):
    id: str
    comparison_id: str
    harness_id: str
    model: str
    case_id: str
    trial: int
    status: str
    request_id: str | None = None
    attempts: int = 0
    error: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    guardrails: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    latency_ms: int | None = None
    dispatched_at: str | None = None
    completed_at: str | None = None
    response: dict[str, Any] | None = None


class JobList(BaseModel):
    comparison_id: str
    total: int
    jobs: list[JobOut]


class RadarAxis(BaseModel):
    key: str
    label: str
    description: str


class RadarSeries(BaseModel):
    harness_id: str
    harness_name: str
    model: str | None
    label: str
    n: int
    values: dict[str, float | None]
    metrics: dict[str, Any]


class RadarOut(BaseModel):
    comparison_id: str
    generated_at: str
    status: str
    axes: list[RadarAxis]
    series: list[RadarSeries]
    aggregate: list[RadarSeries]
    paired: list[dict[str, Any]]
    warnings: list[str]
    disclaimer: str


class EventOut(BaseModel):
    id: int | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class EventList(BaseModel):
    comparison_id: str
    events: list[EventOut]


class CancelOut(BaseModel):
    comparison_id: str
    cancel_requested: bool
    status: str


class HealthOut(BaseModel):
    status: str
    database: str
    redis: str
    docker: str
    version: str
    disclaimer: str


class MetaOut(BaseModel):
    axes: list[RadarAxis]
    harness_ids: list[str]
    max_models: int
    max_trials: int
    max_cases: int
    default_models: list[str]
    harnesses: list[dict[str, str]] = Field(default_factory=list)
    disclaimer: str


class WorkerInitiateIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repo_url: str = Field(min_length=1, max_length=500)
    commits: list[str] = Field(min_length=1, max_length=10)
    git_token: str | None = None


class WorkerStatus(BaseModel):
    slot: str
    harness_id: str
    commit: str
    status: str
    container: str
    redis_channel: str
    message: str


class WorkerInitiateOut(BaseModel):
    status: str
    docker: str
    redis: str
    repo_url: str
    workers: list[WorkerStatus]


class GitVerifyCommitIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repo_url: str = Field(min_length=1, max_length=500)
    commit: str = Field(min_length=1, max_length=100)
    git_token: str | None = None


class GitVerifyCommitOut(BaseModel):
    accessible: bool
    commit: str
    full_sha: str | None = None
    author: str | None = None
    date: str | None = None
    message: str | None = None
    error: str | None = None


class WorkerUnitStartIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repo_url: str = Field(min_length=1, max_length=500)
    commit: str = Field(min_length=1, max_length=100)
    harness_id: str = Field(default="h1", max_length=20)
    slot_name: str | None = None
    git_token: str | None = None


class WorkerUnitOut(BaseModel):
    status: str
    harness_id: str
    commit: str
    container: str
    redis_channel: str
    message: str
    started_at: str
    docker: str
    redis: str


class WorkerUnitStopIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    harness_id: str = Field(default="h1", max_length=20)
    commit: str | None = None
    container: str | None = None


class WorkerUnitStopOut(BaseModel):
    status: str
    harness_id: str
    message: str


class RetryGatewayConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ComparisonRetryIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_ids: list[str] | None = None
    gateway: RetryGatewayConfig | None = None


class JobRetryOut(BaseModel):
    retried: bool
    job_id: str
    comparison_id: str
    status: str
    message: str

