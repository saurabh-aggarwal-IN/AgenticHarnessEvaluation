# Harness Comparison Orchestrator

Build three commits of the clinical-coding harness repo, run the same synthetic
dataset through all of them (optionally across several router models), evaluate
each response deterministically, and expose radar data for a Vue UI.

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
FastAPI / CLI
    |  comparison record          Redis Stream cc:comparisons
    v                                      |
Postgres  <---- runner / consumer <--------+
    |                |
    |                +--> git worktrees at h1/h2/h3 commits
    |                +--> docker build (one image per commit)
    |                +--> private bridge network + one worker container per harness
    |                              |
    |                              +--> Redis Stream cc:jobs:<cmp>:<h>
    |                              +--> Redis Stream cc:results:<cmp>
    |
    +--> jobs, responses, deterministic evaluations, events, radar summary
```

- **Postgres** owns comparison/job state, raw responses, evaluations, events,
  and the final radar summary.
- **Redis Streams** carries queued comparisons, per-harness jobs, results, and
  worker heartbeats. Job consumers use consumer groups; the orchestrator reads
  results with a group and acknowledges every message.
- **Docker CLI** is called directly. `docker compose` is not required, because
  the development machine exposes a Podman `docker` shim without Compose.

## Quick start (host orchestrator)

Requirements: Python 3.11+, Docker CLI (or Podman shim), git, and a reachable
OpenAI-compatible router.

```powershell
cd E:\HighLevel\HarnessComparison
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # edit credentials and URLs
.\\.venv\\Scripts\\python -m harness_compare.cli infra up
```

Register the canonical synthetic dataset:

```powershell
$env:ALLOW_LOCAL_DATASET = "true"
.\.venv\Scripts\python -m harness_compare.cli dataset E:\HighLevel\Dataset --name synthetic-clinical-coding-v1
```

Run a comparison. For a local repository, set `ALLOW_LOCAL_REPO=true`; for
GitHub the host must be in `REPO_ALLOWED_HOSTS` (default `github.com`):

```powershell
$env:ALLOW_LOCAL_REPO = "true"
$env:LLM_BASE_URL = "https://router.example.com/v1"
$env:LLM_API_KEY = "..."

.\.venv\Scripts\python -m harness_compare.cli compare `
  --repo E:\HighLevel\ClinicalCoderAgent `
  --h1 938bb8b --h2 6c94476 --h3 517120c `
  --dataset-path E:\HighLevel\Dataset `
  --model your-router/model-slug `
  --trials 1 `
  --out comparison.json
```

For the GitHub path, replace `--repo` with the URL and pass the full commit
SHAs. The default allowlist rejects non-GitHub hosts.

Start the API instead of running inline:

```powershell
.\.venv\Scripts\python -m harness_compare.cli infra up
.\.venv\Scripts\python -m harness_compare.api
# or: .\.venv\Scripts\python -m harness_compare.worker
```

API docs live at `http://127.0.0.1:8080/docs`.

## API

Set `ORCH_API_TOKEN` to require `Authorization: Bearer <token>` on every `/v1`
route. `/health` is unauthenticated.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | DB, Redis, Docker health |
| `GET` | `/v1/meta` | radar axes, harness descriptions, limits |
| `GET` | `/v1/datasets` | registered datasets |
| `POST` | `/v1/datasets` | register a server path (requires `ALLOW_LOCAL_DATASET=true`) |
| `POST` | `/v1/datasets/upload` | upload a dataset zip |
| `POST` | `/v1/comparisons` | create and queue a comparison |
| `GET` | `/v1/comparisons` | list comparisons |
| `GET` | `/v1/comparisons/{id}` | status, progress, summary |
| `GET` | `/v1/comparisons/{id}/jobs` | filterable job results |
| `GET` | `/v1/comparisons/{id}/events` | audit/timeline events |
| `GET` | `/v1/comparisons/{id}/radar` | radar series, aggregates, paired deltas |
| `GET` | `/v1/comparisons/{id}/export` | summary JSON download |
| `POST` | `/v1/comparisons/{id}/cancel` | cooperative cancellation |

A comparison body:

```json
{
  "name": "synthetic screening run",
  "repo_url": "https://github.com/org/repo",
  "commits": {
    "h1": "938bb8b...",
    "h2": "6c94476...",
    "h3": "517120c..."
  },
  "dataset_id": "ds-...",
  "models": ["router/model-a", "router/model-b"],
  "trials": 2,
  "budget": {
    "max_llm_calls": 8,
    "max_wall_seconds": 180,
    "max_cost_usd": 1.0
  }
}
```

## Evaluation

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
carry no significance claim. The UI should show the disclaimer and the H1
confounding note next to every radar.

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

## Live LLM Execution & Zero-Mock Policy

This platform enforces a strict **Zero-Mock Policy** for evaluations:
- All harness runs make live inference calls against an OpenAI-compatible router or local Ollama endpoint.
- For our live evaluation, the system connects to the local Ollama / freeLLMAPI router at `http://192.168.29.43:3001/v1` serving `gemma-4-31b`.
- The worker containers route directly to the host network address (`http://192.168.29.43:3001/v1`).
- All comparisons, metrics, and radar charts in the dashboard reflect genuine, non-deterministic agent tool executions.

Configure in `.env`:
```text
LLM_BASE_URL=http://192.168.29.43:3001/v1
WORKER_LLM_BASE_URL=http://192.168.29.43:3001/v1
LLM_API_KEY=freellmapi-25c013abda015aebe25ddfe5e5911208d11a243079111ef6
DEFAULT_MODELS=gemma-4-31b
```

## Running Live Comparisons via UI or CLI

1. Push the three harness commits. The three SHAs must all exist in the same
   repository; the orchestrator checks each one out in its own worktree.
2. Configure a real router and keep local-path access off:

   ```text
   ALLOW_LOCAL_REPO=false
   ALLOW_LOCAL_DATASET=false
   REPO_ALLOWED_HOSTS=github.com,www.github.com
   GIT_TOKEN=...                 # only for a private repository
   LLM_BASE_URL=https://router.example.com/v1
   LLM_API_KEY=...
   WORKER_LLM_BASE_URL=          # defaults to LLM_BASE_URL
   DEFAULT_MODELS=provider/model-a,provider/model-b
   ```

3. Start Postgres/Redis (`python -m harness_compare.cli infra up`), the API
   (`python -m harness_compare.api`), and the worker
   (`python -m harness_compare.cli worker`).
4. Register the dataset through `POST /v1/datasets/upload`, not by server path.
5. Create the comparison with `repo_url`, the three commit SHAs, `dataset_id`,
   `models`, and `trials`; poll `/v1/comparisons/{id}/radar` for the result.

Keep the screening disclaimer and the H1-confounding note attached to every
export. The radar is descriptive, not a causal or clinical claim.

## Tests

```powershell
cd E:\HighLevel\HarnessComparison
.\.venv\Scripts\python -m pytest -q
```

The suite covers dataset validation and zip safety, deterministic evaluation
and radar aggregation, history-availability semantics, API request validation,
Docker/git command construction, model/commit validation, the deterministic
mock router, and the runner's job planning and failure isolation helpers.

## Current limitations

- Gold codes are author-validated, not certified-coder-reviewed.
- No certified coder adjudication, no real claims data, no PHI.
- H1 is a real Hermes CLI, but its result bundle also differs from H2/H3 by
  history access and orchestration. It cannot isolate "Hermes vs LangGraph".
- The H1 base image is currently `nousresearch/hermes-agent:latest`, not a
  digest. Pin it before archival or comparison runs that must be reproducible.
- Evaluations run against live LLM endpoints. Non-deterministic sampling can result in variance across runs.
- The canonical dataset has only one history-specific gold code (C06), so the
  history-specificity axis is a single-case signal.
- The dataset is intentionally small. Per-case results are noisy; radar values
  are screening signals.
- The orchestrator assumes one active runner per comparison. Redis consumer
  groups keep work from being duplicated, and orphaned RUNNING comparisons are
  failed on startup.
