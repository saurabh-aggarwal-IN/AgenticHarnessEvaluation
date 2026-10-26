# Harness Comparison Orchestrator

Build three commits of the clinical-coding harness repo, run the same synthetic
dataset through all of them (optionally across several router models), evaluate
each response deterministically, and expose radar data and dynamic post-run reports for a Vue UI.

> **Screening only.** This is not clinical advice, not a billing determination,
> and not validated for patient care. The dataset is synthetic and small; the
> gold codes are author-validated, not certified-coder-reviewed. H1 differs
> from H2/H3 in orchestration, history access, and execution path, so harness
> contrasts are descriptive, not causal. There is no claim that any score here
> generalizes to production coding or to real patient records.

## What it runs

| Harness | Commit input | Execution |
| --- | --- | --- |
| `h1` | commit SHA | Real Hermes CLI, one isolated run per encounter, no history fixture |
| `h2` | commit SHA | LangGraph planner -> coder, with history fixture |
| `h3` | commit SHA | LangGraph planner -> coder -> verifier, one optional revision, with history fixture |

Each harness commit must expose the same frozen HTTP/Redis contract and a
`Dockerfile`. The orchestrator builds one isolated worker image per commit,
starts one container per harness on a private Docker network, and sends only
task inputs (`case_id`, encounter note, prompt, history, model) over Redis
Streams. Grading keys never leave the orchestrator process.

## Architecture

```
FastAPI / CLI / Vue UI (Task Studio)
    |  comparison record          Redis Stream cc:comparisons
    v                                      |
Postgres  <---- runner / consumer <--------+
    |                |
    |                +--> git worktrees at h1/h2/h3 commits
    |                +--> docker build (one image per commit)
    |                +--> private bridge network + worker containers per harness
    |                              |
    |                              +--> Redis Stream cc:jobs:<cmp>:<h>
    |                              +--> Redis Stream cc:results:<cmp>
    |
    +--> jobs, responses, deterministic evaluations, events, radar summary, dynamic report
```

- **Postgres** owns comparison/job state, raw responses, evaluations, audit events,
  and the final radar summary.
- **Redis Streams** carries queued comparisons, per-harness jobs, results, and
  worker heartbeats. Job consumers use consumer groups; the orchestrator reads
  results with a group and acknowledges every message.
- **Docker CLI** is called directly. `docker compose` is not required, because
  the development machine exposes a Podman `docker` shim without Compose. Container
  cleanups and safety ceilings are handled automatically.
- **Web UI (Vue 3 + Vite)** provides interactive comparison management (Task Studio),
  real-time progress tracking, 8-axis radar visualization, case-level trace inspector,
  selective retry for failed tasks, and dynamic post-run reports.

## Core Capabilities & New Features

### 1. Granular & Bulk Failed Task Retries
When LLM upstream rate limits (HTTP 429), quota limits, or network timeouts occur:
- **Preserves Completed Tasks**: Already completed tasks (`COMPLETED`) and their metrics/token usages are never lost or re-executed.
- **Bulk Retry**: Retry all failed, timed-out, or cancelled tasks across the entire comparison run with a single click.
- **Single Task Retry**: Retry an individual failed task directly from the Case Table (🔄 button) or the Case Drawer error view.
- **Credential & Gateway Update on Retry**: Update the gateway `base_url`, `api_key`, or `model` at retry time so retried jobs succeed on an alternative route or fresh key without starting from scratch.

### 2. Dynamic In-UI Post-Run Report & Next-Harness Prompt
- Automatically generated upon run completion or when completed tasks exist.
- **Executive Summary & Radar Breakdown**: Synthesizes cross-harness strengths, accuracy (micro F1), safety flags, and latency/cost trade-offs.
- **Prompt for Next Agent Harness**: Generates an exhaustive, actionable prompt customized to the run's empirical error modes, hallucinated codes, and architectural weaknesses—ready to hand off to coding agents (e.g. Claude, Cursor, Antigravity) to build the next iteration of the agentic harness.
- Completely dynamic and generic: adapts to any dataset, case set, and model configuration without hardcoded mocks.

### 3. Task Studio & Commit Verification
- **Task Studio**: Create new comparison runs with customized commit SHAs, model selections, concurrency settings, and trials.
- **Git Commit Verification**: Instant validation of commit SHAs locally or remotely against GitHub before kicking off builds.
- **Worker Management**: Real-time worker health, container lifecycle management, and automatic dead-worker reaping.

## Quick start (host orchestrator)

Requirements: Python 3.11+, Docker CLI (or Podman shim), git, and a reachable
OpenAI-compatible gateway (e.g. FreeLLMAPI, LiteLLM, Ollama, or direct API providers).

```powershell
cd E:\HighLevel\HarnessComparison
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # edit credentials and URLs
.\.venv\Scripts\python -m harness_compare.cli infra up
```

Register the canonical synthetic dataset:

```powershell
$env:ALLOW_LOCAL_DATASET = "true"
.\.venv\Scripts\python -m harness_compare.cli dataset E:\HighLevel\Dataset --name synthetic-clinical-coding-v1
```

Run a comparison via CLI:

```powershell
$env:ALLOW_LOCAL_REPO = "true"
$env:LLM_BASE_URL = "http://192.168.29.43:3001/v1"
$env:LLM_API_KEY = "..."

.\.venv\Scripts\python -m harness_compare.cli compare `
  --repo E:\HighLevel\ClinicalCoderAgent `
  --h1 938bb8b --h2 6c94476 --h3 517120c `
  --dataset-path E:\HighLevel\Dataset `
  --model gemma-4-31b `
  --trials 1 `
  --out comparison.json
```

For the GitHub path, replace `--repo` with the URL and pass the full commit
SHAs. The default allowlist rejects non-GitHub hosts.

Start the API and Background Worker:

```powershell
.\.venv\Scripts\python -m harness_compare.cli infra up
.\.venv\Scripts\python -m harness_compare.api
# In another terminal:
.\.venv\Scripts\python -m harness_compare.cli worker
```

Launch the Vue UI:

```powershell
cd E:\HighLevel\HarnessComparison\ui
npm install
npm run dev
```

The Web UI will be available at `http://localhost:5173/`, and API docs live at `http://127.0.0.1:8080/docs`.

## API Endpoints

Set `ORCH_API_TOKEN` to require `Authorization: Bearer <token>` on every `/v1`
route. `/health` is unauthenticated.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | DB, Redis, Docker health status |
| `GET` | `/v1/meta` | Radar axes, harness descriptions, limits |
| `GET` | `/v1/datasets` | List registered datasets |
| `POST` | `/v1/datasets` | Register a server path (requires `ALLOW_LOCAL_DATASET=true`) |
| `POST` | `/v1/datasets/upload` | Upload a dataset zip file |
| `POST` | `/v1/comparisons` | Create and queue a comparison run |
| `GET` | `/v1/comparisons` | List comparisons |
| `GET` | `/v1/comparisons/{id}` | Status, progress, and summary |
| `DELETE` | `/v1/comparisons/{id}` | Permanently delete comparison, jobs, events, and metrics |
| `POST` | `/v1/comparisons/{id}/retry` | Retry failed/timed-out tasks (optional gateway override) |
| `POST` | `/v1/comparisons/{id}/retry-failed` | Alias for retry comparison endpoint |
| `POST` | `/v1/comparisons/{id}/retry-orphaned`| Re-enqueue orphaned or interrupted comparison |
| `POST` | `/v1/comparisons/{id}/jobs/{job_id}/retry` | Retry an individual failed task |
| `GET` | `/v1/comparisons/{id}/jobs` | Filterable case-level job results |
| `DELETE` | `/v1/comparisons/{id}/jobs` | Delete all jobs for a comparison |
| `GET` | `/v1/comparisons/{id}/events` | Audit/timeline events |
| `GET` | `/v1/comparisons/{id}/radar` | Radar series, 8-axis normalized metrics, paired deltas |
| `GET` | `/v1/comparisons/{id}/export` | Export comparison (JSON or CSV via `?format=csv`) |
| `POST` | `/v1/comparisons/{id}/cancel` | Cooperative cancellation of active run |
| `POST` | `/v1/git/verify-commit` | Check if a git commit SHA is accessible locally/remotely |
| `POST` | `/v1/gateway/models` | List models available on the configured LLM gateway |
| `POST` | `/v1/workers/unit/start` | Manually start a standalone worker container |
| `POST` | `/v1/workers/unit/stop` | Stop a standalone worker container |
| `POST` | `/v1/workers/kill-all` | Clean up all active worker containers |

### Retry Request Payload

To retry failed tasks with updated credentials or endpoints:

```json
POST /v1/comparisons/cmp-12345/retry
Content-Type: application/json

{
  "job_ids": ["job-abc"],       // Optional: omit to retry all failed/timeout tasks
  "gateway": {                  // Optional: update gateway configuration
    "base_url": "http://192.168.29.43:3001/v1",
    "api_key": "new-secret-key",
    "model": "gemma-4-31b"
  }
}
```

## Evaluation Metrics

Scores are deterministic and do not call an LLM judge.

- **Accuracy**: micro F1 over expected and returned graded codes.
- **Recall / Precision**: pooled across jobs.
- **Provenance**: returned codes whose snippet is verbatim in the encounter
  note after whitespace normalization.
- **Safety**: jobs with no forbidden code and no harness error.
- **Exact set**: returned graded set equals the gold set.
- **History specificity**: on jobs where a gold code is annotated with
  `historySnippet`, the share of those gold codes that were returned.
  Applicability comes from that annotation, not `requiredHistory.mustRead`;
  retrieval-only cases do not contribute. In the canonical dataset only C06
  has a history-specific gold code, so that axis is C06-only. A harness is
  never credited with having history merely because the dataset case carries a
  fixture: `history_present` records what that harness actually received.
- **Efficiency**: a bounded reference score
  `0.5 * 60s/(60s+mean latency) + 0.5 * 4000/(4000+mean tokens)`.
  It is not a cost or quality measure; H1 pays a fixed Hermes startup cost.

E/M codes matching `^99[2-4]` are ignored per the dataset grading notes.
Forbidden codes are hard safety failures. Provenance is evaluated against the
encounter note only; H2/H3 can additionally read the history fixture but a
snippet sourced only from history does not count as note provenance.

Paired deltas are descriptive (b minus a on identical case/trial pairs) and
carry no significance claim. The UI shows the disclaimer and the H1
confounding note next to every radar and report.

## Isolation and safety

- Each harness worker runs read-only with dropped capabilities,
  `no-new-privileges`, memory/CPU/PID limits, a tmpfs `/tmp`, and no host
  mounts. If the host runs rootless Podman without cgroup controllers, set
  `WORKER_MEMORY=`, `WORKER_CPUS=`, and `WORKER_PIDS=0`; the other isolation
  flags stay on.
- Workers receive only task inputs. Expected codes, forbidden codes,
  history snippets, and coding keys are never placed in a job payload.
- Harnesses receive bounded budgets; every response is clamped again inside
  the harness. Structured budget/guardrail errors are preserved as failed
  jobs, never silently dropped.
- LLM calls use the harness-side gateway: timeouts, retries with jitter,
  rate limits, per-request call/token/cost ceilings, circuit breaking, input
  and output guardrails, and trace events.
- `ALLOW_LOCAL_REPO` and `ALLOW_LOCAL_DATASET` are false by default. In
  production only allowlisted repository hosts should be accepted.
- Git and Docker commands are executed as argument arrays, never through a
  shell. ZIP extraction rejects traversal, symlinks, encryption, and bombs.

## Docker deployment of the orchestrator

The image contains the Docker CLI, git, and the Python app:

```powershell
docker build -t harness-compare:latest .
docker run -d --name harness-compare `
  --network cc-infra `
  -p 8080:8080 `
  -v /var/run/docker.sock:/var/run/docker.sock `
  -v harness-compare-data:/app/data `
  --env-file .env `
  harness-compare:latest
```

On Docker Desktop / Podman, mount the actual socket path for that runtime.
When the orchestrator runs on `cc-infra`, set its `DATABASE_URL` and `REDIS_URL`
to the container names (`cc-postgres`, `cc-redis`) rather than localhost.

The Postgres and Redis containers can be created with
`python -m harness_compare.cli infra up`; they persist in the `cc-pgdata` and
`cc-redisdata` named volumes.

## Live LLM Execution & Gateway Configuration

This platform enforces a strict **Zero-Mock Policy** for evaluations:
- All harness runs make live inference calls against an OpenAI-compatible gateway (e.g. [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi), LiteLLM, Ollama, or direct model providers).
- Worker containers route directly to the host network address.
- All comparisons, metrics, and radar charts in the dashboard reflect genuine, non-deterministic agent tool executions.

Example `.env` gateway configuration:
```text
LLM_BASE_URL=http://192.168.29.43:3001/v1
WORKER_LLM_BASE_URL=http://192.168.29.43:3001/v1
LLM_API_KEY=your-api-key
DEFAULT_MODELS=gemma-4-31b
```

## Tests

```powershell
cd E:\HighLevel\HarnessComparison
.\.venv\Scripts\python -m pytest -q
```

The test suite covers:
- Dataset validation and zip traversal safety
- Deterministic evaluation and 8-axis radar aggregation
- History-availability and provenance semantics
- API request validation and full retry endpoints
- Docker and Git command construction
- Failure isolation, worker recovery, and retry job preservation
- Deterministic mock router and live LLM gateway contract
