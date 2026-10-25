from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asyncpg

logger = logging.getLogger("harness_compare.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_VERSION = "001_init"

JSONB_COLUMNS = {
    "manifest",
    "models",
    "config",
    "progress",
    "error",
    "summary",
    "response",
    "evaluation",
    "usage",
    "guardrails",
    "budget",
    "payload",
}
TIMESTAMP_COLUMNS = {
    "created_at",
    "started_at",
    "finished_at",
    "updated_at",
    "dispatched_at",
    "completed_at",
}


class StoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt(value: Any) -> Any:
    """Accept ISO strings at the store boundary and bind real datetimes.

    asyncpg's timestamptz codec only accepts datetime objects. API and runner
    code carry timestamps as ISO strings so they stay JSON-serializable, so the
    conversion happens here instead of being repeated at every call site.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text)
        except ValueError as exc:
            raise StoreError(f"invalid timestamp {value!r}") from exc
    raise StoreError(f"expected a timestamp, got {type(value).__name__}")


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    if record is None:
        return None
    result = dict(record)
    for key, value in list(result.items()):
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


async def _init_connection(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await connection.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class PostgresStore:
    """Durable comparison, dataset, job, and event records.

    The store is intentionally raw SQL: the schema is small, JSONB keeps
    harness payloads auditable, and there is no ORM identity map to surprise
    the runner when it reloads jobs.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=10,
            init=_init_connection,
            command_timeout=30,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise StoreError("database pool is not connected")
        return self.pool

    async def ping(self) -> str:
        try:
            await self._pool().fetchval("SELECT 1")
            return "ok"
        except Exception as exc:  # noqa: BLE001 - health must not raise
            return f"error: {type(exc).__name__}"

    async def migrate(self) -> None:
        pool = self._pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                "SELECT pg_advisory_lock(hashtext('harness_compare_migrations'))"
            )
            try:
                applied = await conn.fetchval(
                    "SELECT version FROM schema_migrations WHERE version = $1",
                    MIGRATION_VERSION,
                )
                if applied:
                    return
                sql = (MIGRATIONS_DIR / f"{MIGRATION_VERSION}.sql").read_text(
                    encoding="utf-8"
                )
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES ($1) "
                    "ON CONFLICT (version) DO NOTHING",
                    MIGRATION_VERSION,
                )
                logger.info("applied migration %s", MIGRATION_VERSION)
            finally:
                await conn.execute(
                    "SELECT pg_advisory_unlock(hashtext('harness_compare_migrations'))"
                )

    async def create_dataset(self, record: dict[str, Any]) -> dict[str, Any]:
        created_at = record.get("created_at") or _now()
        row = await self._pool().fetchrow(
            """
            INSERT INTO datasets
                (id, name, digest, case_count, path, source, manifest, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::timestamptz)
            ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    path = EXCLUDED.path,
                    source = EXCLUDED.source,
                    manifest = EXCLUDED.manifest,
                    case_count = EXCLUDED.case_count
            RETURNING *
            """,
            record["id"],
            record["name"],
            record["digest"],
            int(record["case_count"]),
            record["path"],
            record.get("source", "register"),
            record.get("manifest") or {},
            _dt(created_at),
        )
        return _row(row) or {}

    async def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        return _row(
            await self._pool().fetchrow(
                "SELECT * FROM datasets WHERE id = $1", dataset_id
            )
        )

    async def list_datasets(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._pool().fetch(
            "SELECT * FROM datasets ORDER BY created_at DESC LIMIT $1", limit
        )
        return [item for item in (_row(row) for row in rows) if item]

    async def create_comparison(self, record: dict[str, Any]) -> dict[str, Any]:
        row = await self._pool().fetchrow(
            """
            INSERT INTO comparisons
                (id, name, status, stage, repo_url, commit_h1, commit_h2,
                 commit_h3, dataset_id, dataset_name, dataset_digest, models,
                 trials, config, progress, notes, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb,
                    $13, $14::jsonb, $15::jsonb, $16, $17::timestamptz)
            RETURNING *
            """,
            record["id"],
            record["name"],
            record.get("status", "QUEUED"),
            record.get("stage"),
            record["repo_url"],
            record["commit_h1"],
            record["commit_h2"],
            record["commit_h3"],
            record["dataset_id"],
            record.get("dataset_name"),
            record.get("dataset_digest"),
            record.get("models") or [],
            int(record.get("trials", 1)),
            record.get("config") or {},
            record.get("progress") or {},
            record.get("notes"),
            _dt(record.get("created_at") or _now()),
        )
        return _row(row) or {}

    async def get_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        return _row(
            await self._pool().fetchrow(
                "SELECT * FROM comparisons WHERE id = $1", comparison_id
            )
        )

    async def delete_comparison(self, comparison_id: str) -> bool:
        result = await self._pool().execute(
            "DELETE FROM comparisons WHERE id = $1", comparison_id
        )
        return str(result).endswith("1")

    async def list_comparisons(
        self, *, limit: int = 50, status: str | None = None
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._pool().fetch(
                "SELECT * FROM comparisons WHERE status = $1 "
                "ORDER BY created_at DESC LIMIT $2",
                status,
                limit,
            )
        else:
            rows = await self._pool().fetch(
                "SELECT * FROM comparisons ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [item for item in (_row(row) for row in rows) if item]

    async def claim_comparison(self, comparison_id: str) -> dict[str, Any] | None:
        row = await self._pool().fetchrow(
            """
            UPDATE comparisons
               SET status = 'RUNNING',
                   stage = 'preparing',
                   started_at = COALESCE(started_at, now())
             WHERE id = $1 AND status = 'QUEUED'
            RETURNING *
            """,
            comparison_id,
        )
        return _row(row)

    async def list_queued_comparisons(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = await self._pool().fetch(
            "SELECT * FROM comparisons WHERE status = 'QUEUED' "
            "ORDER BY created_at ASC LIMIT $1",
            limit,
        )
        return [item for item in (_row(row) for row in rows) if item]

    async def fail_orphaned_runs(self) -> int:
        rows = await self._pool().fetch(
            """
            WITH updated AS (
                UPDATE comparisons
                   SET status = 'FAILED',
                       stage = 'orphaned',
                       finished_at = now(),
                       error = $1::jsonb
                 WHERE status = 'RUNNING'
                RETURNING 1
            )
            SELECT count(*) AS n FROM updated
            """,
            {
                "code": "ORPHANED_RUN",
                "message": (
                    "The orchestrator restarted while this comparison was "
                    "running. Its containers were left for inspection; "
                    "start a new comparison."
                ),
                "retryable": True,
            },
        )
        return int(rows[0]["n"] if rows else 0)

    async def update_comparison(
        self, comparison_id: str, **fields: Any
    ) -> dict[str, Any] | None:
        if not fields:
            return await self.get_comparison(comparison_id)
        assignments: list[str] = []
        values: list[Any] = [comparison_id]
        index = 2
        for key, value in fields.items():
            if key not in {
                "name",
                "status",
                "stage",
                "models",
                "trials",
                "config",
                "progress",
                "error",
                "summary",
                "cancel_requested",
                "notes",
                "started_at",
                "finished_at",
            }:
                raise StoreError(f"cannot update comparisons.{key}")
            if key in JSONB_COLUMNS:
                assignments.append(f"{key} = ${index}::jsonb")
                values.append(value)
            elif key in TIMESTAMP_COLUMNS:
                assignments.append(f"{key} = ${index}::timestamptz")
                values.append(_dt(value))
            else:
                assignments.append(f"{key} = ${index}")
                values.append(value)
            index += 1
        row = await self._pool().fetchrow(
            f"UPDATE comparisons SET {', '.join(assignments)} "
            "WHERE id = $1 RETURNING *",
            *values,
        )
        return _row(row)

    async def count_active_comparisons(self) -> int:
        return int(
            await self._pool().fetchval(
                "SELECT count(*) FROM comparisons "
                "WHERE status IN ('QUEUED', 'RUNNING', 'CANCELLING')"
            )
        )

    async def upsert_harness_run(
        self, comparison_id: str, harness_id: str, **fields: Any
    ) -> dict[str, Any]:
        commit = fields.get("commit")
        if not commit:
            existing = await self._pool().fetchrow(
                "SELECT commit FROM harness_runs WHERE comparison_id = $1 "
                "AND harness_id = $2",
                comparison_id,
                harness_id,
            )
            if existing is None:
                raise StoreError("commit is required for a new harness run")
            commit = existing["commit"]
        row = await self._pool().fetchrow(
            """
            INSERT INTO harness_runs
                (comparison_id, harness_id, commit, image, container_name,
                 status, log_tail, error, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, now())
            ON CONFLICT (comparison_id, harness_id) DO UPDATE
                SET image = COALESCE(EXCLUDED.image, harness_runs.image),
                    container_name = COALESCE(
                        EXCLUDED.container_name, harness_runs.container_name
                    ),
                    status = EXCLUDED.status,
                    log_tail = COALESCE(EXCLUDED.log_tail, harness_runs.log_tail),
                    error = COALESCE(EXCLUDED.error, harness_runs.error),
                    updated_at = now()
            RETURNING *
            """,
            comparison_id,
            harness_id,
            commit,
            fields.get("image"),
            fields.get("container_name"),
            fields.get("status", "PENDING"),
            fields.get("log_tail"),
            fields.get("error"),
        )
        return _row(row) or {}

    async def list_harness_runs(self, comparison_id: str) -> list[dict[str, Any]]:
        rows = await self._pool().fetch(
            "SELECT * FROM harness_runs WHERE comparison_id = $1 "
            "ORDER BY harness_id",
            comparison_id,
        )
        return [item for item in (_row(row) for row in rows) if item]

    async def create_jobs(self, jobs: Iterable[dict[str, Any]]) -> int:
        records = list(jobs)
        if not records:
            return 0
        created = 0
        async with self._pool().acquire() as conn:
            async with conn.transaction():
                for job in records:
                    result = await conn.execute(
                        """
                        INSERT INTO jobs
                            (id, comparison_id, harness_id, model, case_id,
                             trial, status, request_id, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, 'QUEUED', $7,
                                $8::timestamptz)
                        ON CONFLICT (comparison_id, harness_id, model, case_id, trial)
                        DO NOTHING
                        """,
                        job["id"],
                        job["comparison_id"],
                        job["harness_id"],
                        job["model"],
                        job["case_id"],
                        int(job["trial"]),
                        job.get("request_id"),
                        _dt(job.get("created_at") or _now()),
                    )
                    if result.endswith("1"):
                        created += 1
        return created

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        return _row(
            await self._pool().fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
        )

    async def delete_jobs(self, comparison_id: str) -> int:
        result = await self._pool().execute(
            "DELETE FROM jobs WHERE comparison_id = $1", comparison_id
        )
        try:
            return int(str(result).split()[-1])
        except (ValueError, IndexError):
            return 0

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
        clauses = ["comparison_id = $1"]
        values: list[Any] = [comparison_id]
        if harness_id:
            values.append(harness_id)
            clauses.append(f"harness_id = ${len(values)}")
        if model:
            values.append(model)
            clauses.append(f"model = ${len(values)}")
        if status:
            values.append(status)
            clauses.append(f"status = ${len(values)}")
        values.extend([limit, offset])
        rows = await self._pool().fetch(
            "SELECT * FROM jobs WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY harness_id, model, case_id, trial "
            f"LIMIT ${len(values) - 1} OFFSET ${len(values)}",
            *values,
        )
        return [item for item in (_row(row) for row in rows) if item]

    async def count_jobs(self, comparison_id: str) -> dict[str, int]:
        rows = await self._pool().fetch(
            "SELECT status, count(*) AS n FROM jobs WHERE comparison_id = $1 "
            "GROUP BY status",
            comparison_id,
        )
        counts = {str(row["status"]): int(row["n"]) for row in rows}
        counts["TOTAL"] = sum(
            value for key, value in counts.items() if key != "TOTAL"
        )
        return counts

    async def update_job(
        self, job_id: str, *, only_unfinished: bool = True, **fields: Any
    ) -> dict[str, Any] | None:
        if not fields:
            return await self.get_job(job_id)
        allowed = {
            "status",
            "attempts",
            "response",
            "evaluation",
            "error",
            "usage",
            "guardrails",
            "budget",
            "latency_ms",
            "request_id",
            "dispatched_at",
            "completed_at",
        }
        assignments: list[str] = []
        values: list[Any] = [job_id]
        index = 2
        for key, value in fields.items():
            if key not in allowed:
                raise StoreError(f"cannot update jobs.{key}")
            if key in JSONB_COLUMNS:
                assignments.append(f"{key} = ${index}::jsonb")
                values.append(value)
            elif key in TIMESTAMP_COLUMNS:
                assignments.append(f"{key} = ${index}::timestamptz")
                values.append(_dt(value))
            else:
                assignments.append(f"{key} = ${index}")
                values.append(value)
            index += 1
        sql = f"UPDATE jobs SET {', '.join(assignments)} WHERE id = $1"
        if only_unfinished:
            sql += (
                " AND status NOT IN ('COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED')"
            )
        sql += " RETURNING *"
        return _row(await self._pool().fetchrow(sql, *values))

    async def mark_job_dispatched(
        self, job_id: str, dispatched_at: str | None = None
    ) -> dict[str, Any] | None:
        return _row(
            await self._pool().fetchrow(
                """
                UPDATE jobs
                   SET status = 'DISPATCHED',
                       attempts = attempts + 1,
                       dispatched_at = $2::timestamptz
                 WHERE id = $1 AND status = 'QUEUED'
                RETURNING *
                """,
                job_id,
                _dt(dispatched_at or _now()),
            )
        )

    async def add_event(
        self,
        comparison_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._pool().execute(
            "INSERT INTO events (comparison_id, type, payload, created_at) "
            "VALUES ($1, $2, $3::jsonb, $4::timestamptz)",
            comparison_id,
            event_type,
            payload or {},
            _dt(_now()),
        )

    async def list_events(
        self, comparison_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        rows = await self._pool().fetch(
            "SELECT * FROM events WHERE comparison_id = $1 "
            "ORDER BY id DESC LIMIT $2",
            comparison_id,
            limit,
        )
        return [item for item in (_row(row) for row in rows) if item]
