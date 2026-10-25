from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from harness_compare.config import Config
from harness_compare.service import ComparisonService

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DATASET = REPO_ROOT / "Dataset"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    base = Config.from_env(base_dir=tmp_path)
    return replace(
        base,
        database_url="postgresql://cc:cc@127.0.0.1:55432/test",
        redis_url="redis://127.0.0.1:56379/15",
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        allow_local_repo=True,
        allow_local_dataset=True,
        repo_allowed_hosts=("github.com", "www.github.com"),
        llm_base_url="http://router.test/v1",
        llm_api_key="test-key",
        worker_llm_base_url="http://router.test/v1",
        worker_redis_url="redis://127.0.0.1:56379/15",
        redis_container="cc-redis",
        max_upload_mb=5,
        max_jobs=1_000,
        api_token="",
    )


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


CASE_ONE_NOTE = (
    "Assessment: stable chronic T2DM without complications and stable chronic "
    "systolic heart failure. A 12-lead EKG was performed in the office today."
)
CASE_TWO_NOTE = (
    "Assessment: uncomplicated urinary tract infection. Urine dipstick was "
    "positive for leukocyte esterase."
)


def make_case(
    case_id: str,
    note: str,
    *,
    history: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "caseId": case_id,
        "datasetId": "synthetic-test",
        "version": "1.0.0",
        "title": f"Case {case_id}",
        "synthetic": True,
        "phi": False,
        "encounterNote": note,
        "history": history,
        "task": {"prompt": "Assign ICD-10-CM and CPT codes for this encounter."},
        "metadata": {"historyRequired": history is not None},
    }


def make_expected(
    case_id: str,
    codes: list[tuple[str, str]],
    *,
    forbidden: list[tuple[str, str]] | None = None,
    history_codes: set[str] | None = None,
) -> dict[str, Any]:
    history_codes = history_codes or set()
    return {
        "caseId": case_id,
        "expectedCodes": [
            {
                "code": code,
                "system": system,
                "rationale": "test gold",
                **(
                    {"historySnippet": "prior claim"}
                    if code in history_codes
                    else {}
                ),
            }
            for code, system in codes
        ],
        "forbiddenCodes": [
            {"code": code, "system": system, "reason": "not supported"}
            for code, system in (forbidden or [])
        ],
        "grading": {"ignoreCodePatterns": [r"^99[2-4]"]},
    }


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    history = {
        "patientId": "SYN-C01",
        "priorClaims": [],
        "activeHccs": [],
        "activeConditions": ["type 2 diabetes"],
    }
    _write(
        root / "cases" / "C01-t2dm-chf-ekg.json",
        make_case("C01", CASE_ONE_NOTE, history=history),
    )
    _write(
        root / "cases" / "C02-uti.json",
        make_case("C02", CASE_TWO_NOTE),
    )
    _write(
        root / "expected" / "C01.expected.json",
        make_expected(
            "C01",
            [("E11.9", "ICD-10-CM"), ("I50.22", "ICD-10-CM"), ("93000", "CPT")],
            forbidden=[("J44.1", "ICD-10-CM")],
            history_codes={"E11.9"},
        ),
    )
    _write(
        root / "expected" / "C02.expected.json",
        make_expected(
            "C02",
            [("N39.0", "ICD-10-CM")],
            forbidden=[("J02.9", "ICD-10-CM")],
        ),
    )
    _write(
        root / "manifest.json",
        {"datasetId": "synthetic-test", "version": "1.0.0"},
    )
    return root


class FakeStore:
    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, Any]] = {}
        self.comparisons: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.harness_runs: dict[tuple[str, str], dict[str, Any]] = {}
        self.connected = False
        self.fail_orphans = 0

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def migrate(self) -> None:
        return None

    async def ping(self) -> str:
        return "ok"

    async def fail_orphaned_runs(self) -> int:
        self.fail_orphans += 1
        return 0

    async def create_dataset(self, record: dict[str, Any]) -> dict[str, Any]:
        row = {
            "source": "register",
            "manifest": {},
            "created_at": _now(),
            **record,
        }
        self.datasets[str(record["id"])] = row
        return dict(row)

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        row = self.datasets.get(dataset_id)
        return dict(row) if row else None

    async def list_datasets(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(row) for row in list(self.datasets.values())[:limit]]

    async def create_comparison(self, record: dict[str, Any]) -> dict[str, Any]:
        row = {
            "stage": "queued",
            "cancel_requested": False,
            "summary": None,
            "error": None,
            "started_at": None,
            "finished_at": None,
            **record,
        }
        self.comparisons[str(record["id"])] = row
        return dict(row)

    async def get_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        row = self.comparisons.get(comparison_id)
        return dict(row) if row else None

    async def delete_comparison(self, comparison_id: str) -> bool:
        if comparison_id in self.comparisons:
            del self.comparisons[comparison_id]
            self.jobs = {
                jid: j for jid, j in self.jobs.items() if j.get("comparison_id") != comparison_id
            }
            self.harness_runs = {
                k: v for k, v in self.harness_runs.items() if k[0] != comparison_id
            }
            self.events.pop(comparison_id, None)
            return True
        return False

    async def list_comparisons(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = list(self.comparisons.values())
        if status:
            rows = [row for row in rows if row["status"] == status]
        return [dict(row) for row in rows[:limit]]

    async def count_active_comparisons(self) -> int:
        return sum(
            1
            for row in self.comparisons.values()
            if row["status"] in {"QUEUED", "RUNNING", "CANCELLING"}
        )

    async def claim_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        row = self.comparisons.get(comparison_id)
        if row is None or row["status"] != "QUEUED":
            return None
        row["status"] = "RUNNING"
        row["stage"] = "preparing"
        row["started_at"] = _now()
        return dict(row)

    async def update_comparison(
        self, comparison_id: str, **fields: Any
    ) -> dict[str, Any] | None:
        row = self.comparisons.get(comparison_id)
        if row is None:
            return None
        row.update(fields)
        return dict(row)

    async def create_jobs(self, jobs) -> int:
        created = 0
        for job in jobs:
            if job["id"] in self.jobs:
                continue
            self.jobs[job["id"]] = {
                "status": "QUEUED",
                "attempts": 0,
                "response": None,
                "evaluation": None,
                "error": None,
                "usage": None,
                "guardrails": None,
                "budget": None,
                "latency_ms": None,
                "dispatched_at": None,
                "completed_at": None,
                **job,
            }
            created += 1
        return created

    async def delete_jobs(self, comparison_id: str) -> int:
        before = len(self.jobs)
        for key in [
            key
            for key, row in self.jobs.items()
            if row["comparison_id"] == comparison_id
        ]:
            del self.jobs[key]
        return before - len(self.jobs)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.jobs.get(job_id)
        return dict(row) if row else None

    async def list_jobs(
        self,
        comparison_id: str,
        *,
        harness_id: str | None = None,
        model: str | None = None,
        status: str | None = None,
        limit: int = 1_000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.jobs.values()
            if row["comparison_id"] == comparison_id
            and (not harness_id or row["harness_id"] == harness_id)
            and (not model or row["model"] == model)
            and (not status or row["status"] == status)
        ]
        rows.sort(key=lambda row: (row["harness_id"], row["model"], row["case_id"], row["trial"]))
        return [dict(row) for row in rows[offset : offset + limit]]

    async def count_jobs(self, comparison_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.jobs.values():
            if row["comparison_id"] != comparison_id:
                continue
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        counts["TOTAL"] = sum(value for key, value in counts.items() if key != "TOTAL")
        return counts

    async def update_job(
        self, job_id: str, *, only_unfinished: bool = True, **fields: Any
    ) -> dict[str, Any] | None:
        row = self.jobs.get(job_id)
        if row is None:
            return None
        if only_unfinished and row["status"] in {
            "COMPLETED",
            "FAILED",
            "TIMEOUT",
            "CANCELLED",
        }:
            return None
        row.update(fields)
        return dict(row)

    async def mark_job_dispatched(
        self, job_id: str, dispatched_at: str | None = None
    ) -> dict[str, Any] | None:
        row = self.jobs.get(job_id)
        if row is None or row["status"] != "QUEUED":
            return None
        row["status"] = "DISPATCHED"
        row["attempts"] += 1
        row["dispatched_at"] = dispatched_at or _now()
        return dict(row)

    async def add_event(
        self,
        comparison_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.events.setdefault(comparison_id, []).append(
            {
                "id": len(self.events.get(comparison_id, [])) + 1,
                "type": event_type,
                "payload": payload or {},
                "created_at": _now(),
            }
        )

    async def list_events(
        self, comparison_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        return list(reversed(self.events.get(comparison_id, [])))[:limit]

    async def upsert_harness_run(
        self, comparison_id: str, harness_id: str, **fields: Any
    ) -> dict[str, Any]:
        key = (comparison_id, harness_id)
        row = self.harness_runs.get(key, {"comparison_id": comparison_id, "harness_id": harness_id})
        row.update(fields)
        self.harness_runs[key] = row
        return dict(row)

    async def list_harness_runs(self, comparison_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for (cid, _), row in sorted(self.harness_runs.items())
            if cid == comparison_id
        ]


class FakeBus:
    def __init__(self) -> None:
        self.connected = False
        self.comparisons: list[str] = []
        self.jobs: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.cancels: list[str] = []
        self.heartbeats: dict[str, list[dict[str, Any]]] = {}
        self.expired = 0
        self.acked: list[str] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def ping(self) -> str:
        return "ok"

    async def init_comparison_queue(self) -> None:
        return None

    async def init_results(self, stream: str) -> None:
        return None

    async def enqueue_comparison(self, comparison_id: str) -> str:
        self.comparisons.append(comparison_id)
        return f"{len(self.comparisons)}-0"

    async def enqueue_job(self, stream: str, job: dict[str, Any]) -> str:
        self.jobs.append({"stream": stream, **job})
        return f"{len(self.jobs)}-0"

    async def read_results(self, stream, consumer, *, block_ms=1_000, count=50):
        items = self.results[:count]
        del self.results[:count]
        return [(f"{index}-0", item) for index, item in enumerate(items)]

    async def ack_result(self, stream: str, message_id: str) -> None:
        self.acked.append(message_id)

    async def read_comparisons(self, consumer, *, block_ms=5_000, count=1):
        if not self.comparisons:
            return []
        return [("1-0", self.comparisons.pop(0))]

    async def read_pending_comparisons(self, consumer, *, count=10):
        return []

    async def ack_comparison(self, message_id: str) -> None:
        self.acked.append(message_id)

    async def worker_heartbeats(self, comparison_id: str):
        return self.heartbeats

    async def worker_ready(self, comparison_id: str, harness_id: str) -> bool:
        return bool(self.heartbeats.get(harness_id))

    async def expire_streams(self, comparison_id, harness_ids, seconds=604_800):
        self.expired += 1

    async def publish_cancel(self, comparison_id: str) -> None:
        self.cancels.append(comparison_id)


class FakeDocker:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.built: list[dict[str, Any]] = []
        self.networks: list[str] = []
        self.removed_images: list[str] = []
        self.connected: list[tuple[str, str]] = []
        self.disconnected: list[tuple[str, str]] = []

    async def version(self) -> str:
        return "ok: test"

    async def ensure_network(self, name: str, comparison_id: str) -> None:
        self.networks.append(name)

    async def remove_network(self, name: str) -> None:
        return None

    async def connect_container(self, network: str, container: str) -> None:
        self.connected.append((network, container))

    async def disconnect_container(self, network: str, container: str) -> None:
        self.disconnected.append((network, container))

    async def build_image(self, **kwargs):
        self.built.append(kwargs)
        return kwargs["tag"]

    async def start_worker(self, **kwargs):
        self.started.append(kwargs)
        return "container-id"

    async def worker_status(self, name: str):
        return {"Running": True}

    async def worker_logs(self, name: str, tail: int = 120) -> str:
        return f"logs for {name}"

    async def stop_worker(self, name: str) -> None:
        self.stopped.append(name)

    async def remove_image(self, tag: str) -> None:
        self.removed_images.append(tag)

    async def _run(self, args: list[str], **kwargs: Any):
        from harness_compare.process import CommandResult
        return CommandResult(argv=args, returncode=0, stdout="", stderr="", duration_s=0.01)


class FakeGit:
    def __init__(self, contexts: dict[str, Path] | None = None) -> None:
        self.contexts = contexts or {}

    async def prepare(self, **kwargs):
        from harness_compare.git_manager import PreparedRepo

        commits = {key: value.ljust(40, "0")[:40] for key, value in kwargs["commits"].items()}
        return PreparedRepo(
            root=kwargs["run_dir"],
            repo_dir=kwargs["run_dir"] / "repo",
            worktrees={key: kwargs["run_dir"] / f"tree-{key}" for key in commits},
            contexts=self.contexts
            or {key: kwargs["run_dir"] / f"tree-{key}" for key in commits},
            commits=commits,
            ref=__import__("harness_compare.git_manager", fromlist=["RepoRef"]).RepoRef(
                url=kwargs["repo_url"], is_local=True
            ),
        )

    async def cleanup(self, prepared, *, keep: bool) -> None:
        return None


class FakeContext:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store = FakeStore()
        self.bus = FakeBus()
        self.docker = FakeDocker()
        self.git = FakeGit()
        self.service = ComparisonService(cfg, self.store, self.bus)
        from harness_compare.runner import ComparisonRunner

        self.runner = ComparisonRunner(cfg, self.store, self.bus, self.docker, self.git)
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False


@pytest.fixture
def fake_ctx(cfg: Config) -> FakeContext:
    return FakeContext(cfg)


@pytest.fixture
def service(fake_ctx: FakeContext) -> ComparisonService:
    return fake_ctx.service


@pytest.fixture(scope="session")
def canonical_dataset() -> Path:
    if not (CANONICAL_DATASET / "cases").is_dir():
        pytest.skip("canonical dataset is not present")
    return CANONICAL_DATASET


@pytest.fixture
def uploaded_zip(dataset_dir: Path, tmp_path: Path) -> Path:
    import zipfile

    archive = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in dataset_dir.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(dataset_dir).as_posix())
    return archive


def register_dataset(store: FakeStore, path: Path, *, name: str = "test") -> str:
    dataset_id = "ds-test"
    store.datasets[dataset_id] = {
        "id": dataset_id,
        "name": name,
        "digest": "a" * 64,
        "case_count": 2,
        "path": str(path),
        "source": "register",
        "manifest": {},
        "created_at": _now(),
    }
    return dataset_id


def copy_dataset(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination
