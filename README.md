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

## One-Click Platform Scripts (`start_platform` / `stop_platform`)

The evaluation platform includes unified automation scripts to launch or terminate the entire ecosystem with a single command.

### Scripts Location

You can run the platform launchers from either the root workspace or the repository scripts folder:

| Location | Start Platform | Stop Platform |
| --- | --- | --- |
| **Workspace Root** (`E:\HighLevel`) | `.\start_platform.bat` *(or `.\start_platform.ps1`)* | `.\stop_platform.bat` *(or `.\stop_platform.ps1`)* |
| **Repo Scripts** (`HarnessComparison\scripts`) | `.\scripts\start_platform.bat` | `.\scripts\stop_platform.bat` |

### What the Launcher Does Automatically

Running `.\start_platform.bat`:
1. **Validates Python & Node Environments**: Verifies virtual environments and UI packages with automatic workspace root resolution.
2. **Starts Infrastructure Services**: Spins up **PostgreSQL 16** (`:55432`) and **Redis 7** (`:56379`) via Docker/Podman (`cc-postgres` and `cc-redis`).
3. **Launches FreeLLMAPI Gateway & Router**: Starts the local OpenAI-compatible inference router on `http://127.0.0.1:3001` (serving models such as `gemma-4-31b`).
4. **Starts Control Plane API**: Runs FastAPI orchestrator service on `http://127.0.0.1:8080`.
5. **Starts Async Worker Runner**: Starts background Redis Streams consumer for distributed evaluation jobs.
6. **Starts Local Agent Service**: Starts `clinical_coder.api:app` on `http://127.0.0.1:8081` if the agent repository is present on disk.
7. **Launches Vue 3 + Vite Dashboard**: Boots the interactive web dashboard on `http://localhost:5173/`.
8. **Health Check & Verification**: Polls all endpoints, prints a readiness summary, and tracks PIDs in `.platform_pids.json`.

### Zero-Prerequisite Launch Resilience

> [!NOTE]
> **No Pre-Cloned Repositories Required**: Evaluators do **not** need to pre-download the agent repository or know its GitHub link before starting the platform. The launcher automatically boots in **Standalone Evaluation Mode**. When you specify the GitHub link and commit IDs in the UI, the platform automatically clones and downloads the specific commit code trees onto disk on demand!

To gracefully shut down all services, containers, and background processes:
```powershell
.\stop_platform.bat
```

---

## Interactive Task Studio Guide (Step 1, Step 2, and Step 3)

The **Task Studio** (`http://localhost:5173/`) allows evaluators to configure, initiate, and run multi-harness benchmarks.

### Quick Preset Shortcut
In the top-right corner of Task Studio, click **🏥 Clinical Preset** to instantly populate all fields with canonical ClinicalCoderApp settings.

---

### Step 1: GitHub Repository & Architecture Commits

In **Step 1**, specify the target agent repository and the architecture commit revisions to benchmark.

1. **GitHub Repository Link**:
   ```text
   https://github.com/saurabh-aggarwal-IN/ClinicalCoderApp.git
   ```
2. **Project / Evaluation Name**:
   ```text
   Clinical Coding Multi-Harness Evaluation
   ```
3. **Architecture Commit Revisions (Up to 3 Slots: H1, H2, H3)**:

   | Harness Slot | Architecture Label | Canonical Commit ID | Architectural Characteristics |
   | --- | --- | --- | --- |
   | **H1 (Baseline)** | `Baseline: Direct Hermes CLI` | `938bb8b` | Single-pass Hermes CLI direct prompt execution. Zero access to patient history fixtures. Minimal latency, high omission rate on chronic conditions. |
   | **H2 (Evolution 1)** | `Evolution 1: LangGraph Planner -> Coder -> Verifier` | `517120c` *(or `6c94476`)* | Two/three-stage LangGraph state machine with patient history fixture access, intermediate reasoning plan, and verifier pass. |
   | **H3 (Evolution 2)** | `Evolution 2: LangGraph Multi-Agent Specialist Loop` | `5bf9d16` *(or `c325be7`)* | Full multi-agent specialist architecture with standardized schema notation, strict ICD-10 trap avoidance, and HTTP 429 adaptive backoff. |

4. **Instant Commit Verification & Auto-Download to Disk**:
   - As soon as you enter a commit SHA and click **🔍 Verify** (or move focus away from the input field), the platform:
     - Validates the commit locally against disk cache (`E:\HighLevel\ClinicalCoderAgent` or `data/repo_cache`).
     - If the code is not on disk, it queries GitHub and runs `git fetch --depth 1 origin <sha>` automatically.
     - Displays status badges:
       - `⚡ Downloaded to disk`: Code tree fetched from GitHub directly into local disk cache.
       - `💾 Ready on disk`: Code already present locally.
       - Displays verified author, date, commit message, and local directory path.
     - Automatically launches the local agent API on port `:8081` once downloaded.
5. **Initiate Docker & Redis Workers**:
   - Click **🚀 Initiate Redis & Docker Workers** (or individual **▶ Start** buttons per slot).
   - The platform allocates an isolated container sandbox for each commit and binds it to private Redis stream queues (`cc:jobs:<cmp>:h1`, etc.).

---

### Step 2: Target Agent API Endpoint (Universal Contract)

In **Step 2**, configure the universal API endpoint common across all commit revisions.

1. **API Link / Endpoint URL**:
   ```text
   /v1/code
   ```
   *(or full URL: `http://127.0.0.1:8081/v1/code`)*
2. **HTTP Method**:
   ```text
   POST
   ```
3. **Universal Contract Principle**:
   - The caller sends an identical payload regardless of harness:
     ```json
     {
       "case_id": "C01",
       "note": "Patient presents with...",
       "patient_history": "History of...",
       "model": "gemma-4-31b"
     }
     ```
   - Standardized expected response:
     ```json
     {
       "approved_codes": ["E11.9", "I50.9"],
       "rejected_codes": ["I10"],
       "rationale": "..."
     }
     ```
   - The checked-out commit inside each worker container determines whether H1, H2, or H3 architecture executes.

---

### Step 3: Synthetic Test Dataset & Ground Truth Reference Paths

In **Step 3**, supply the file paths to the synthetic clinical encounter cases and ground truth evaluations.

1. **Synthetic Dataset (Cases) Folder**:
   ```text
   Dataset/cases
   ```
   *(or absolute path: `E:\HighLevel\Dataset\cases`)*
2. **Ground Truth Reference Folder**:
   ```text
   Dataset/expected
   ```
   *(or absolute path: `E:\HighLevel\Dataset\expected`)*

---

### Step 4 & Step 5: Metrics & LLM Gateway Configuration

- **Step 4 (Evaluation Metrics)**: Select benchmark dimensions including **Output Accuracy & F1**, **Exact Set Match**, **Guardrail Safety & Trap Avoidance**, **Source Provenance**, **Execution Latency**, **Token Cost**, and domain custom metrics (**Longitudinal Context Retention**, **Medical Upcoding Traps**).
- **Step 5 (Gateway & Model)**:
  - Select **FreeLLMAPI** (`http://192.168.29.43:3001/v1` or `http://127.0.0.1:3001/v1`), **OpenRouter**, or **TrueFoundry**.
  - Select model: `gemma-4-31b` (or pass per-harness models).
- Click **⚡ Run Multi-Harness Analysis** to start the evaluation.

---

## Synthetic Clinical Coding Dataset Reference

The benchmark dataset is located at `E:\HighLevel\Dataset` and structured as follows:

```
Dataset/
├── cases/                     # Synthetic clinical encounter notes (JSON)
│   ├── C01-t2dm-chf-ekg.json
│   ├── C02-t2dm-ckd-stage3.json
│   ├── C03-chf-copd-exacerbation.json
│   ├── C04-htn-ckd-stage2.json
│   ├── C05-historical-copd-trap.json
│   ├── C06-kidney-transplant-followup.json
│   ├── C07-strep-pharyngitis.json
│   ├── C08-uncomplicated-uti.json
│   ├── C09-right-knee-oa.json
│   └── C10-recurrent-mdd.json
├── expected/                  # Ground truth gold codes & safety traps (JSON)
│   ├── C01.expected.json ... C10.expected.json
├── schema/                    # JSON Schema specifications
│   ├── case.schema.json
│   ├── expected-output.schema.json
│   └── agent-output.schema.json
├── manifest.json              # Dataset versioning and metadata
└── checksums.json             # SHA256 integrity checksums
```

### Key Evaluation Cases & Traps

- **C01 (`C01-t2dm-chf-ekg`)**: Multi-morbidity case with Type 2 Diabetes Mellitus, congestive heart failure, and routine EKG. Tests CPT code capture and secondary diagnosis sequencing.
- **C02 (`C02-t2dm-ckd-stage3`)**: Chronic Kidney Disease (CKD Stage 3a) in a diabetic patient. Requires proper ICD-10 combination coding (`E11.22`, `N18.31`).
- **C03 (`C03-chf-copd-exacerbation`)**: Acute cardiopulmonary decompensation testing exclusion guidelines between acute pulmonary edema and acute exacerbation of chronic obstructive bronchitis.
- **C04 (`C04-htn-ckd-stage2`)**: Hypertensive nephropathy testing causal relationship rules between hypertension and mild CKD (`I12.9`, `N18.2`).
- **C05 (`C05-historical-copd-trap`)**: **Safety Trap Case**. Encounter note mentions past resolved condition. Strictly tests whether the agent falls into the trap of coding historical/inactive diagnoses.
- **C06 (`C06-kidney-transplant-followup`)**: **Longitudinal History Retention**. Tests whether the agent properly incorporates status code `Z94.0` (kidney transplant status) available only in the patient history fixture.
- **C07–C10**: Outpatient and specialty cases covering acute pharyngitis, uncomplicated cystitis, unilateral degenerative osteoarthritis, and recurrent major depressive disorder.

---

## Manual CLI Execution (Alternative to UI)

Requirements: Python 3.11+, Docker CLI (or Podman shim), git, and an OpenAI-compatible gateway.

```powershell
cd E:\HighLevel\HarnessComparison
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
Copy-Item .env.example .env   # edit credentials and URLs
.\.venv\Scripts\python -m harness_compare.cli infra up
```

Register the synthetic dataset:
```powershell
$env:ALLOW_LOCAL_DATASET = "true"
.\.venv\Scripts\python -m harness_compare.cli dataset E:\HighLevel\Dataset --name synthetic-clinical-coding-v1
```

Run comparison via CLI:
```powershell
$env:ALLOW_LOCAL_REPO = "true"
$env:LLM_BASE_URL = "http://192.168.29.43:3001/v1"
$env:LLM_API_KEY = "freellmapi-25c013abda015aebe25ddfe5e5911208d11a243079111ef6"

.\.venv\Scripts\python -m harness_compare.cli compare `
  --repo E:\HighLevel\ClinicalCoderAgent `
  --h1 938bb8b --h2 517120c --h3 5bf9d16 `
  --dataset-path E:\HighLevel\Dataset `
  --model gemma-4-31b `
  --trials 1 `
  --out comparison.json
```

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
