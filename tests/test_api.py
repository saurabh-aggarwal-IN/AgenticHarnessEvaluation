from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import FakeContext, register_dataset
from harness_compare.api import create_app


def make_client(ctx: FakeContext) -> TestClient:
    return TestClient(create_app(ctx, start_worker=False))


def test_meta_lists_axes_and_harnesses(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        response = client.get("/v1/meta")
    assert response.status_code == 200
    body = response.json()
    assert len(body["axes"]) == 8
    assert body["harness_ids"] == ["h1", "h2", "h3"]
    assert [item["id"] for item in body["harnesses"]] == ["h1", "h2", "h3"]
    assert "benchmark" in body["disclaimer"].lower()


def test_health_aggregates_dependencies(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["redis"] == "ok"
    assert body["docker"] == "ok: test"


def test_auth_required_when_token_configured(fake_ctx: FakeContext) -> None:
    ctx = FakeContext(replace(fake_ctx.cfg, api_token="secret"))
    with make_client(ctx) as client:
        assert client.get("/v1/meta").status_code == 401
        assert (
            client.get(
                "/v1/meta", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert (
            client.get(
                "/v1/meta", headers={"Authorization": "Bearer secret"}
            ).status_code
            == 200
        )
        assert client.get("/health").status_code == 200


def test_create_comparison_returns_queued(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
        "trials": 1,
    }
    with make_client(fake_ctx) as client:
        response = client.post("/v1/comparisons", json=payload)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["models"] == ["model/a"]
    assert body["progress"]["total_jobs"] == 6
    assert fake_ctx.bus.comparisons == [body["id"]]


def test_create_comparison_validates_commits(fake_ctx: FakeContext) -> None:
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "abc", "h2": "def", "h3": "ghi"},
        "dataset_id": "ds-1",
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        response = client.post("/v1/comparisons", json=payload)
    assert response.status_code == 422


def test_create_comparison_rejects_missing_dataset(fake_ctx: FakeContext) -> None:
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": "ds-missing",
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        response = client.post("/v1/comparisons", json=payload)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "DATASET_NOT_FOUND"


def test_model_limit_is_enforced(fake_ctx: FakeContext, dataset_dir: Path) -> None:
    ctx = FakeContext(replace(fake_ctx.cfg, max_models=1))
    dataset_id = register_dataset(ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a", "model/b"],
    }
    with make_client(ctx) as client:
        response = client.post("/v1/comparisons", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "TOO_MANY_MODELS"


def test_dataset_path_registration_is_gated(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    disabled = FakeContext(replace(fake_ctx.cfg, allow_local_dataset=False))
    with make_client(disabled) as client:
        response = client.post("/v1/datasets", json={"path": str(dataset_dir)})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DATASET_PATH_DISABLED"

    with make_client(fake_ctx) as client:
        response = client.post(
            "/v1/datasets", json={"path": str(dataset_dir), "name": "api"}
        )
    assert response.status_code == 201
    assert response.json()["case_count"] == 2


def test_dataset_upload(
    fake_ctx: FakeContext, uploaded_zip: Path
) -> None:
    with make_client(fake_ctx) as client:
        response = client.post(
            "/v1/datasets/upload",
            files={"file": ("dataset.zip", uploaded_zip.read_bytes(), "application/zip")},
            data={"name": "uploaded"},
        )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "uploaded"
    assert response.json()["case_count"] == 2


def test_upload_rejects_empty_file(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        response = client.post(
            "/v1/datasets/upload",
            files={"file": ("empty.zip", b"", "application/zip")},
        )
    assert response.status_code == 400


def test_dataset_list(fake_ctx: FakeContext, dataset_dir: Path) -> None:
    register_dataset(fake_ctx.store, dataset_dir)
    with make_client(fake_ctx) as client:
        response = client.get("/v1/datasets")
    assert response.status_code == 200
    assert response.json()[0]["id"] == "ds-test"


def test_radar_and_cancel_for_queued_comparison(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        created = client.post("/v1/comparisons", json=payload).json()
        radar = client.get(f"/v1/comparisons/{created['id']}/radar")
        cancelled = client.post(f"/v1/comparisons/{created['id']}/cancel")
        detail = client.get(f"/v1/comparisons/{created['id']}")
    assert radar.status_code == 200
    assert radar.json()["status"] == "QUEUED"
    assert len(radar.json()["series"]) == 3
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"
    assert detail.json()["status"] == "CANCELLED"


def test_jobs_endpoint_filters_and_counts(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        created = client.post("/v1/comparisons", json=payload).json()
        fake_ctx.store.jobs["job-1"] = {
            "id": "job-1",
            "comparison_id": created["id"],
            "harness_id": "h1",
            "model": "model/a",
            "case_id": "C01",
            "trial": 1,
            "status": "COMPLETED",
            "attempts": 1,
            "evaluation": {"f1": 1.0},
            "latency_ms": 1200,
        }
        response = client.get(
            f"/v1/comparisons/{created['id']}/jobs",
            params={"harness_id": "h1"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["jobs"][0]["evaluation"] == {"f1": 1.0}


def test_export_returns_attachment(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        created = client.post("/v1/comparisons", json=payload).json()
        response = client.get(f"/v1/comparisons/{created['id']}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.json()["comparison_id"] == created["id"]


def test_unknown_comparison_is_404(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        assert client.get("/v1/comparisons/cmp-nope").status_code == 404
        assert client.get("/v1/comparisons/cmp-nope/radar").status_code == 404
        assert client.post("/v1/comparisons/cmp-nope/cancel").status_code == 404


def test_gateway_models_endpoint(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        res = client.post("/v1/gateway/models", json={"provider": "freellmapi"})
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "freellmapi"
        assert len(data["models"]) >= 1


def test_create_comparison_with_harness_models(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "name": "Per-Harness Model Test",
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["anthropic/claude-3.5-sonnet", "meta-llama/llama-3.3-70b-instruct"],
        "harness_models": {
            "h1": "anthropic/claude-3.5-sonnet",
            "h2": "meta-llama/llama-3.3-70b-instruct",
            "h3": "deepseek/deepseek-chat",
        },
        "gateway": {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-or-v1-test",
        },
    }
    with make_client(fake_ctx) as client:
        res = client.post("/v1/comparisons", json=payload)
        assert res.status_code == 202
        created = res.json()
        assert created["config"]["harness_models"]["h1"] == "anthropic/claude-3.5-sonnet"
        assert created["config"]["gateway"]["provider"] == "openrouter"


def test_delete_comparison(fake_ctx: FakeContext, dataset_dir: Path) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        created = client.post("/v1/comparisons", json=payload).json()
        cid = created["id"]
        # Verify it exists
        assert client.get(f"/v1/comparisons/{cid}").status_code == 200

        # Delete it
        res = client.delete(f"/v1/comparisons/{cid}")
        assert res.status_code == 200
        assert res.json()["deleted"] is True
        assert res.json()["comparison_id"] == cid

        # Verify it is gone
        assert client.get(f"/v1/comparisons/{cid}").status_code == 404

        # Deleting again returns 404
        assert client.delete(f"/v1/comparisons/{cid}").status_code == 404


def test_delete_jobs(fake_ctx: FakeContext, dataset_dir: Path) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        created = client.post("/v1/comparisons", json=payload).json()
        cid = created["id"]
        res = client.delete(f"/v1/comparisons/{cid}/jobs")
        assert res.status_code == 200
        assert "deleted_count" in res.json()


def test_verify_git_commit(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        # Accessible commit in ClinicalCoderAgent
        res = client.post(
            "/v1/git/verify-commit",
            json={
                "repo_url": "https://github.com/saurabh-aggarwal-IN/ClinicalCoderApp",
                "commit": "938bb8b",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["accessible"] is True
        assert data["full_sha"].startswith("938bb8b")
        assert "Add shared harness foundation" in (data["message"] or "")
        assert data["cached_locally"] is True
        assert data["local_path"] is not None
        assert "ClinicalCoderAgent" in data["local_path"]

        # Inaccessible commit
        res_bad = client.post(
            "/v1/git/verify-commit",
            json={
                "repo_url": "https://github.com/saurabh-aggarwal-IN/ClinicalCoderApp",
                "commit": "nonexistent_commit_12345",
            },
        )
        assert res_bad.status_code == 200
        assert res_bad.json()["accessible"] is False
        assert "not found" in res_bad.json()["error"].lower()


def test_worker_unit_start_and_stop(fake_ctx: FakeContext) -> None:
    with make_client(fake_ctx) as client:
        # Start worker unit
        res_start = client.post(
            "/v1/workers/unit/start",
            json={
                "repo_url": "https://github.com/saurabh-aggarwal-IN/ClinicalCoderApp",
                "commit": "938bb8b",
                "harness_id": "h1",
            },
        )
        assert res_start.status_code == 200
        start_data = res_start.json()
        assert start_data["status"] == "running"
        assert start_data["harness_id"] == "h1"
        assert start_data["container"] == "worker-sandbox-h1-938bb8b"
        assert start_data["redis_channel"] == "cc:jobs:h1"

        # Stop worker unit
        res_stop = client.post(
            "/v1/workers/unit/stop",
            json={
                "harness_id": "h1",
                "container": start_data["container"],
            },
        )
        assert res_stop.status_code == 200
        assert res_stop.json()["status"] == "stopped"


def test_retry_comparison_endpoints(
    fake_ctx: FakeContext, dataset_dir: Path
) -> None:
    dataset_id = register_dataset(fake_ctx.store, dataset_dir)
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {"h1": "938bb8b", "h2": "6c94476", "h3": "517120c"},
        "dataset_id": dataset_id,
        "models": ["model/a"],
    }
    with make_client(fake_ctx) as client:
        created = client.post("/v1/comparisons", json=payload).json()
        cmp_id = created["id"]

        # Insert a failed job into store
        fake_ctx.store.jobs["job-f1"] = {
            "id": "job-f1",
            "comparison_id": cmp_id,
            "case_id": "C01",
            "harness_id": "h1",
            "model": "model/a",
            "status": "FAILED",
            "trial": 1,
            "error": {"code": "LLM_429", "message": "rate limit"},
        }

        # Test POST /v1/comparisons/{id}/retry
        res_retry = client.post(
            f"/v1/comparisons/{cmp_id}/retry",
            json={
                "gateway": {"base_url": "http://127.0.0.1:3001/v1"},
            },
        )
        assert res_retry.status_code == 200
        assert res_retry.json()["status"] == "QUEUED"
        assert res_retry.json()["id"] == cmp_id

        # Mark job-f1 as TIMEOUT
        fake_ctx.store.jobs["job-f1"]["status"] = "TIMEOUT"

        # Test GET /v1/comparisons/{id}/jobs/{job_id}/worker-status
        res_ws = client.get(f"/v1/comparisons/{cmp_id}/jobs/job-f1/worker-status")
        assert res_ws.status_code == 200
        assert res_ws.json()["job_id"] == "job-f1"
        assert res_ws.json()["harness_id"] == "h1"
        assert res_ws.json()["running"] is True

        # Test POST /v1/comparisons/{id}/jobs/{job_id}/restart-worker
        res_rw = client.post(f"/v1/comparisons/{cmp_id}/jobs/job-f1/restart-worker")
        assert res_rw.status_code == 200
        assert res_rw.json()["job_id"] == "job-f1"
        assert res_rw.json()["restarted"] is True

        # Test POST /v1/comparisons/{id}/jobs/{job_id}/retry
        res_single = client.post(f"/v1/comparisons/{cmp_id}/jobs/job-f1/retry")
        assert res_single.status_code == 200
        assert res_single.json()["job_id"] == "job-f1"
        assert res_single.json()["status"] == "QUEUED"



