<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { api, setApiToken } from "./api";
import {
  HARNESS_NAMES,
  formatDate,
  formatMs,
  formatNumber,
  formatPct,
  harnessColor,
  harnessName,
  harnessSoft,
} from "./theme";
import ComparisonList from "./components/ComparisonList.vue";
import RadarChart from "./components/RadarChart.vue";
import JobTable from "./components/JobTable.vue";
import JobDrawer from "./components/JobDrawer.vue";
import StatusBadge from "./components/StatusBadge.vue";
import RunComparisonModal from "./components/RunComparisonModal.vue";
import TaskStudio from "./components/TaskStudio.vue";
import PostRunReport from "./components/PostRunReport.vue";

const comparisons = ref([]);
const selectedId = ref("");
const comparison = ref(null);
const radar = ref(null);
const jobs = ref([]);
const meta = ref(null);
const health = ref(null);

const loading = ref(true);
const detailLoading = ref(false);
const refreshing = ref(false);
const cancelling = ref(false);
const retrying = ref(false);
const showRunModal = ref(false);
const error = ref("");
const needsToken = ref(false);
const tokenInput = ref("");

const theme = ref(localStorage.getItem("app_theme") || "light");

function toggleTheme() {
  theme.value = theme.value === "light" ? "dark" : "light";
  localStorage.setItem("app_theme", theme.value);
  applyTheme();
}

function applyTheme() {
  if (theme.value === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
}

function openTaskStudio() {
  selectedId.value = "new";
  comparison.value = null;
  selectedJob.value = null;
  const url = new URL(window.location.href);
  url.searchParams.set("c", "new");
  window.history.pushState({}, "", url);
}

const selectedModel = ref("");
const selectedJob = ref(null);
const hoveredAxisKey = ref("");

let pollTimer = null;
let loadSequence = 0;

const axes = computed(() => {
  if (radar.value?.axes?.length) return radar.value.axes;
  return meta.value?.axes || [];
});

const models = computed(() => comparison.value?.models || []);

const hiddenHarnesses = ref(new Set());

function toggleHarnessVisibility(id) {
  const next = new Set(hiddenHarnesses.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    // Keep at least 1 harness visible
    if (next.size < allHarnessSeries.value.length - 1) {
      next.add(id);
    }
  }
  hiddenHarnesses.value = next;
}

const allHarnessSeries = computed(() => {
  const all = radar.value?.series || [];
  if (!selectedModel.value) return all;
  return all.filter((item) => !item.model || item.model === selectedModel.value);
});

const series = computed(() => {
  return allHarnessSeries.value.filter((item) => !hiddenHarnesses.value.has(item.harness_id));
});

const hoveredAxis = computed(
  () => axes.value.find((axis) => axis.key === hoveredAxisKey.value) || null,
);

const bestAccuracy = computed(() => {
  if (!series.value.length) return null;
  return [...series.value].sort(
    (a, b) => (b.values?.accuracy ?? -1) - (a.values?.accuracy ?? -1),
  )[0];
});

const fastest = computed(() => {
  if (!series.value.length) return null;
  return [...series.value].sort(
    (a, b) =>
      (a.metrics?.latency_ms?.mean ?? Number.POSITIVE_INFINITY) -
      (b.metrics?.latency_ms?.mean ?? Number.POSITIVE_INFINITY),
  )[0];
});

const safetyFlagJobs = computed(() =>
  jobs.value.filter(
    (job) =>
      job.evaluation?.safety_ok === false ||
      (job.evaluation?.forbidden_hits || []).length > 0,
  ),
);

const historyJobs = computed(() =>
  jobs.value.filter((job) => (job.evaluation?.history_required_codes || []).length),
);

const historySummary = computed(() => {
  const caseId = historyJobs.value[0]?.case_id;
  if (!caseId) return null;
  const rows = series.value.map((item) => {
    const job = historyJobs.value.find((entry) => entry.harness_id === item.harness_id);
    const evaluation = job?.evaluation || {};
    const present = evaluation.history_present;
    const hits = evaluation.history_specificity_hits || [];
    const returned = evaluation.returned_codes || [];
    let status = "no history available";
    if (present) status = hits.length ? `used ${hits.join(", ")}` : `missed (${returned.join(", ") || "no code"})`;
    return {
      id: item.harness_id,
      status,
      present,
      hit: hits.length > 0,
    };
  });
  return { caseId, rows };
});

const kpis = computed(() => {
  const progress = comparison.value?.progress || {};
  const total = progress.total_jobs ?? jobs.value.length;
  const failed = progress.failed ?? jobs.value.filter((job) => job.status === "FAILED" || job.status === "TIMEOUT").length;
  const cases = new Set(jobs.value.map((job) => job.case_id)).size;
  const flags = safetyFlagJobs.value.length;
  return [
    {
      label: "Jobs",
      value: formatNumber(total),
      sub: `${cases || "—"} synthetic cases × ${series.value.length || 3} harnesses`,
      tone: "neutral",
    },
    {
      label: "Failures",
      value: formatNumber(failed),
      sub: failed ? "Inspect failed jobs below" : "All jobs reached a terminal state",
      tone: failed ? "bad" : "ok",
    },
    {
      label: "Safety flags",
      value: formatNumber(flags),
      sub: flags ? "Forbidden code returned in at least one job" : "No forbidden codes returned",
      tone: flags ? "bad" : "ok",
    },
    {
      label: "Best accuracy",
      value: bestAccuracy.value ? `${bestAccuracy.value.harness_id.toUpperCase()} · ${formatPct(bestAccuracy.value.values?.accuracy)}` : "—",
      sub: "Micro F1 on this run; descriptive only",
      tone: "highlight",
    },
  ];
});

const insights = computed(() => {
  if (!series.value.length) return [];
  const items = [];
  if (bestAccuracy.value) {
    items.push(
      `${bestAccuracy.value.harness_id.toUpperCase()} leads accuracy on this run at ${formatPct(bestAccuracy.value.values?.accuracy)} micro F1.`,
    );
  }
  if (fastest.value) {
    items.push(
      `${fastest.value.harness_id.toUpperCase()} is fastest at ${formatMs(fastest.value.metrics?.latency_ms?.mean)} mean latency${fastest.value.harness_id === "h1" ? " (includes Hermes startup)" : ""}.`,
    );
  }
  const flags = series.value
    .map((item) => ({
      id: item.harness_id,
      n: item.metrics?.forbidden_hits || 0,
    }))
    .filter((item) => item.n > 0);
  if (flags.length) {
    items.push(
      `Safety flags: ${flags.map((item) => `${item.id.toUpperCase()} ${item.n}`).join(", ")} disallowed output job${flags.reduce((sum, item) => sum + item.n, 0) === 1 ? "" : "s"}.`,
    );
  }
  if (historySummary.value) {
    const rows = historySummary.value.rows
      .map((row) => `${row.id.toUpperCase()} ${row.status}`)
      .join("; ");
    items.push(`Context-dependent case ${historySummary.value.caseId}: ${rows}.`);
  }
  if (series.value[0]?.n) {
    items.push(
      `n=${series.value[0].n} evaluation cases. Comparative screening benchmark.`,
    );
  }
  return items;
});

function friendlyError(cause) {
  if (cause?.unauthorized) {
    needsToken.value = true;
    return "This API requires a bearer token. Enter ORCH_API_TOKEN below.";
  }
  if (cause?.offline) {
    return "Cannot reach the orchestrator API at 127.0.0.1:8080. Start it with `.venv\\Scripts\\python -m harness_compare.api`.";
  }
  return cause?.message || "Something went wrong while loading the comparison.";
}

function saveToken() {
  setApiToken(tokenInput.value);
  needsToken.value = false;
  error.value = "";
  bootstrap();
}

async function selectComparison(id) {
  if (!id) return;
  const sequence = ++loadSequence;
  selectedId.value = id;
  detailLoading.value = true;
  error.value = "";
  selectedJob.value = null;
  try {
    const [nextComparison, nextRadar, nextJobs] = await Promise.all([
      api.comparison(id),
      api.radar(id),
      api.jobs(id),
    ]);
    if (sequence !== loadSequence) return;
    comparison.value = nextComparison;
    radar.value = nextRadar;
    jobs.value = nextJobs.jobs || [];
    if (!models.value.includes(selectedModel.value)) {
      selectedModel.value = models.value[0] || "";
    }
    window.history.replaceState(null, "", `?c=${encodeURIComponent(id)}`);
    loading.value = false;
    schedulePoll();
  } catch (cause) {
    if (sequence !== loadSequence) return;
    error.value = friendlyError(cause);
    loading.value = false;
  } finally {
    if (sequence === loadSequence) detailLoading.value = false;
  }
}

async function refresh({ silent = false } = {}) {
  if (!selectedId.value) return;
  const sequence = ++loadSequence;
  if (!silent) refreshing.value = true;
  try {
    const [nextComparison, nextRadar, nextJobs, nextComparisons] = await Promise.all([
      api.comparison(selectedId.value),
      api.radar(selectedId.value),
      api.jobs(selectedId.value),
      api.comparisons(),
    ]);
    if (sequence !== loadSequence) return;
    comparison.value = nextComparison;
    radar.value = nextRadar;
    jobs.value = nextJobs.jobs || [];
    comparisons.value = nextComparisons;
    if (!models.value.includes(selectedModel.value)) {
      selectedModel.value = models.value[0] || "";
    }
    error.value = "";
    schedulePoll();
  } catch (cause) {
    if (sequence !== loadSequence) return;
    if (!silent) error.value = friendlyError(cause);
  } finally {
    if (sequence === loadSequence && !silent) refreshing.value = false;
  }
}

function schedulePoll() {
  window.clearTimeout(pollTimer);
  const status = comparison.value?.status;
  if (["QUEUED", "RUNNING", "CANCELLING"].includes(status)) {
    pollTimer = window.setTimeout(() => refresh({ silent: true }), 4000);
  } else if (["COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"].includes(status)) {
    // Kill any lingering workers as soon as analysis finishes
    api.killAllWorkers().catch(() => {});
  }
}

async function bootstrap() {
  loading.value = true;
  error.value = "";
  try {
    const [nextHealth, nextMeta, nextComparisons] = await Promise.all([
      api.health(),
      api.meta(),
      api.comparisons(),
    ]);
    health.value = nextHealth;
    meta.value = nextMeta;
    comparisons.value = nextComparisons;

    const fromUrl = new URLSearchParams(window.location.search).get("c");
    if (fromUrl === "new" || !fromUrl) {
      openTaskStudio();
      loading.value = false;
    } else {
      const match = nextComparisons.find((item) => item.id === fromUrl);
      if (match) {
        await selectComparison(match.id);
      } else {
        openTaskStudio();
        loading.value = false;
      }
    }
  } catch (cause) {
    error.value = friendlyError(cause);
    loading.value = false;
  }
}

function onAxisHover(key) {
  hoveredAxisKey.value = key;
}

function openJob(job) {
  selectedJob.value = job;
}

function closeJob() {
  selectedJob.value = null;
}

const isRunning = computed(() =>
  ["QUEUED", "RUNNING", "CANCELLING"].includes(comparison.value?.status),
);

const isFailed = computed(() =>
  ["FAILED", "TIMEOUT"].includes(comparison.value?.status),
);

const failedJobs = computed(() =>
  jobs.value.filter((job) =>
    ["FAILED", "TIMEOUT", "CANCELLED"].includes(job.status),
  ),
);

const harnessProgressList = computed(() => {
  const p = comparison.value?.progress;
  const list = [];
  const byHarness = p?.by_harness || {};

  const rawIds = Object.keys(byHarness).length > 0
    ? Object.keys(byHarness)
    : (comparison.value?.harnesses || ["h1", "h2", "h3"]);

  for (const rawId of rawIds) {
    const id = rawId.toLowerCase();
    const entry = byHarness[rawId] || byHarness[id] || {};
    const harnessJobs = jobs.value.filter(
      (j) => (j.harness_id || "").toLowerCase() === id,
    );

    const total = entry.total ?? (harnessJobs.length || 10);
    const done = entry.done ?? harnessJobs.filter((j) => ["COMPLETED", "FAILED", "TIMEOUT", "CANCELLED"].includes(j.status)).length;
    const completed = entry.completed ?? harnessJobs.filter((j) => j.status === "COMPLETED").length;
    const failed = entry.failed ?? harnessJobs.filter((j) => ["FAILED", "TIMEOUT", "CANCELLED"].includes(j.status)).length;
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    const color = harnessColor(id);

    list.push({
      id,
      name: HARNESS_NAMES[id] || id.toUpperCase(),
      total,
      done,
      completed,
      failed,
      pct,
      color,
      softColor: harnessSoft(id),
      borderColor: color.replace(")", ", 0.35)").replace("rgb", "rgba"),
    });
  }
  return list;
});

async function onComparisonCreated(newRunOrId) {
  const targetId = typeof newRunOrId === "object" ? newRunOrId?.id : newRunOrId;
  await bootstrap();
  if (targetId) {
    await selectComparison(targetId);
  }
}

async function cancelActiveRun() {
  if (!comparison.value?.id) return;
  cancelling.value = true;
  try {
    await api.cancelComparison(comparison.value.id);
    await api.killAllWorkers().catch(() => {});
    await refresh();
  } catch (cause) {
    error.value = cause?.message || "Failed to cancel run.";
  } finally {
    cancelling.value = false;
  }
}

// Retry logic
const retryModalOpen = ref(false);
const retryTargetJob = ref(null);
const retryError = ref("");
const retryGateway = ref({
  base_url: "",
  api_key: "",
  model: "",
});

function openRetryModal(targetJob = null) {
  retryTargetJob.value = targetJob;
  retryError.value = "";
  const currentGw = comparison.value?.gateway || {};
  retryGateway.value = {
    base_url: currentGw.base_url || "",
    api_key: "",
    model: currentGw.model || "",
  };
  retryModalOpen.value = true;
}

async function executeRetry(updateCredentials = false) {
  if (!comparison.value?.id) return;
  retrying.value = true;
  retryError.value = "";
  try {
    const jobIds = retryTargetJob.value ? [retryTargetJob.value.id] : null;
    const gatewayPayload =
      updateCredentials && (retryGateway.value.base_url || retryGateway.value.api_key)
        ? {
            base_url: retryGateway.value.base_url ? retryGateway.value.base_url.trim() : undefined,
            api_key: retryGateway.value.api_key ? retryGateway.value.api_key.trim() : undefined,
            model: retryGateway.value.model ? retryGateway.value.model.trim() : undefined,
          }
        : undefined;

    await api.retryComparison(comparison.value.id, {
      job_ids: jobIds,
      gateway: gatewayPayload,
    });
    retryModalOpen.value = false;
    if (selectedJob.value && (!jobIds || jobIds.includes(selectedJob.value.id))) {
      selectedJob.value = null;
    }
    await refresh();
  } catch (cause) {
    retryError.value = cause?.message || "Failed to retry tasks.";
    error.value = cause?.message || "Failed to retry tasks.";
  } finally {
    retrying.value = false;
  }
}

async function retryOrphanedRun() {
  await executeRetry(false);
}

// Delete comparison task
const deleteModalOpen = ref(false);
const targetToDelete = ref(null);
const deleting = ref(false);
const deleteError = ref("");

function promptDelete(target) {
  targetToDelete.value = target || comparison.value;
  deleteError.value = "";
  deleteModalOpen.value = true;
}

async function confirmDelete() {
  if (!targetToDelete.value?.id) return;
  deleting.value = true;
  deleteError.value = "";
  const id = targetToDelete.value.id;
  try {
    await api.deleteComparison(id);
    deleteModalOpen.value = false;
    comparisons.value = comparisons.value.filter((c) => c.id !== id);
    if (selectedId.value === id) {
      if (comparisons.value.length > 0) {
        await selectComparison(comparisons.value[0].id);
      } else {
        openTaskStudio();
      }
    }
  } catch (err) {
    deleteError.value = err?.message || "Failed to delete comparison task.";
  } finally {
    deleting.value = false;
  }
}

onMounted(bootstrap);
onBeforeUnmount(() => window.clearTimeout(pollTimer));
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32">
            <path d="M16 2.5 27.5 9v14L16 29.5 4.5 23V9z" fill="none" stroke="currentColor" stroke-width="1.5" />
            <path d="M9 17h4l2-4 3 8 2-4h3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </div>
        <div>
          <div class="brand-name">Agent Harness Lab</div>
          <div class="brand-sub">Multi-commit agent evals</div>
        </div>
      </div>

      <div class="sidebar-section">
        <div class="section-label">
          <span>Comparisons</span>
          <span class="section-count">{{ comparisons.length }}</span>
        </div>
        <button type="button" class="btn btn-sidebar-new" @click="openTaskStudio">
          <svg viewBox="0 0 20 20" aria-hidden="true" style="width:14px;height:14px;">
            <path d="M10 4v12m-6-6h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
          + New Compare Task
        </button>
        <ComparisonList
          :items="comparisons"
          :selected-id="selectedId"
          @select="selectComparison"
          @delete="promptDelete"
        />
      </div>

      <div v-if="comparison" class="sidebar-context">
        <div class="section-label"><span>Run context</span></div>
        <div class="context-row"><span>Dataset</span><strong>{{ comparison.dataset_name || comparison.dataset_id }}</strong></div>
        <div class="context-row"><span>Cases</span><strong>{{ new Set(jobs.map((job) => job.case_id)).size }}</strong></div>
        <div class="context-row"><span>Trials</span><strong>{{ comparison.trials }}</strong></div>
        <div class="context-row"><span>Model</span><strong>{{ comparison.models?.join(", ") }}</strong></div>
      </div>

      <div class="sidebar-footer">
        <div class="health-row">
          <span class="health-dot" :class="health?.status === 'ok' ? 'ok' : health?.status === 'degraded' ? 'warn' : 'bad'"></span>
          <span>API {{ health?.status || "unknown" }}</span>
        </div>
        <p>Live LLM sandbox execution. Multi-commit architecture benchmarks. Zero mock data.</p>
      </div>
    </aside>

    <main class="main" :class="{ 'is-loading': detailLoading }">
      <header class="topbar">
        <div class="breadcrumb">
          <span>Agent harness platform</span>
          <span class="breadcrumb-sep">/</span>
          <span class="breadcrumb-current">{{ comparison?.name || "Task Studio" }}</span>
        </div>
        <div class="topbar-actions">
          <button type="button" class="theme-toggle-btn" @click="toggleTheme" :title="theme === 'light' ? 'Switch to Dark Mode' : 'Switch to Light Mode'">
            <span>{{ theme === 'light' ? '🌙' : '☀️' }}</span>
            <span>{{ theme === 'light' ? 'Dark Theme' : 'Light Theme' }}</span>
          </button>
          <button v-if="comparison" type="button" class="btn ghost small" @click="openTaskStudio">
            + Compare Studio
          </button>
          <span v-if="detailLoading" class="updating-pill">updating…</span>
          <span class="api-pill" :class="health?.status === 'ok' ? 'api-ok' : 'api-warn'">
            <span class="health-dot" :class="health?.status === 'ok' ? 'ok' : 'warn'"></span>
            {{ health?.status === "ok" ? "API connected" : "API degraded" }}
          </span>
          <button type="button" class="btn ghost" :disabled="refreshing" @click="refresh()">
            <svg viewBox="0 0 20 20" aria-hidden="true" :class="{ spin: refreshing }">
              <path d="M16 10a6 6 0 1 1-1.8-4.3M16 3.5V7h-3.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Refresh
          </button>
          <a v-if="comparison" class="btn ghost" :href="api.exportUrl(comparison.id)" target="_blank" rel="noreferrer">
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5M4 14.5V16h12v-1.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Export JSON
          </a>
          <a v-if="comparison" class="btn ghost" :href="api.exportCsvUrl(comparison.id)" target="_blank" rel="noreferrer">
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5M4 14.5V16h12v-1.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Export CSV
          </a>
          <button
            v-if="comparison"
            type="button"
            class="btn ghost btn-danger-action"
            title="Delete this comparison run"
            @click="promptDelete(comparison)"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true" style="width:14px;height:14px;">
              <path d="M4 6h12M8 6V4h4v2m2 0v10a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6h10zM9 9v5m2-5v5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            Delete Task
          </button>
        </div>
      </header>

      <div v-if="loading && !comparison" class="state loading-state">
        <div class="spinner"></div>
        <p>Connecting to the orchestrator…</p>
      </div>

      <div v-else-if="error && !comparison" class="state error-state">
        <div class="state-icon">!</div>
        <h2>Could not load the comparison</h2>
        <p>{{ error }}</p>
        <div v-if="needsToken" class="token-box">
          <input v-model="tokenInput" type="password" placeholder="ORCH_API_TOKEN" aria-label="API token" @keydown.enter="saveToken" />
          <button type="button" class="btn primary" @click="saveToken">Use token</button>
        </div>
        <button type="button" class="btn primary" @click="bootstrap">Retry</button>
      </div>

      <div v-else-if="!comparison || selectedId === 'new'" class="task-studio-wrapper">
        <TaskStudio
          :meta="meta"
          :recent-comparisons="comparisons"
          @created="onComparisonCreated"
          @select-recent="selectComparison"
        />
      </div>

      <template v-else>
        <div v-if="error" class="error-banner">
          <strong>Refresh failed.</strong> {{ error }}
          <button type="button" class="btn ghost small" @click="refresh()">Retry</button>
        </div>

        <div v-if="isRunning" class="run-banner">
          <div class="run-banner-main">
            <div class="run-banner-header">
              <div class="run-banner-title">
                <span class="run-spinner"></span>
                <strong>Run in progress ({{ comparison.status }}).</strong>
                <span class="run-count">
                  {{ comparison.progress?.done_jobs ?? 0 }} / {{ comparison.progress?.total_jobs ?? '—' }} jobs complete
                  ({{ Math.round(comparison.progress?.percent ?? ((comparison.progress?.done_jobs ?? 0) / (comparison.progress?.total_jobs || 1) * 100)) }}%)
                </span>
              </div>
              <button type="button" class="btn ghost small" :disabled="cancelling" @click="cancelActiveRun">
                {{ cancelling ? "Cancelling..." : "Cancel Run" }}
              </button>
            </div>

            <div class="progress-bar-track overall">
              <div
                class="progress-bar-fill"
                :style="{
                  width: `${Math.min(100, Math.round(comparison.progress?.percent ?? ((comparison.progress?.done_jobs ?? 0) / (comparison.progress?.total_jobs || 1) * 100)))}%`
                }"
              ></div>
            </div>

            <div v-if="harnessProgressList.length" class="harness-progress-grid">
              <div v-for="hp in harnessProgressList" :key="hp.id" class="harness-progress-card">
                <div class="harness-progress-header">
                  <span
                    class="harness-badge"
                    :style="{
                      backgroundColor: hp.softColor,
                      color: hp.color,
                      borderColor: hp.borderColor
                    }"
                  >
                    {{ hp.id.toUpperCase() }}
                  </span>
                  <span class="harness-name-label">{{ hp.name }}</span>
                  <div class="harness-progress-stats">
                    <span class="stat-done">{{ hp.done }}/{{ hp.total }}</span>
                    <span v-if="hp.failed > 0" class="stat-failed">({{ hp.failed }} failed)</span>
                    <span class="stat-pct">{{ hp.pct }}%</span>
                  </div>
                </div>
                <div class="progress-bar-track">
                  <div
                    class="progress-bar-fill"
                    :style="{
                      width: `${hp.pct}%`,
                      backgroundColor: hp.color
                    }"
                  ></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="(failedJobs.length > 0 || isFailed) && !isRunning" class="failed-banner">
          <div class="failed-banner-info">
            <div class="failed-banner-title">
              <span class="failed-icon" aria-hidden="true">⚠️</span>
              <strong>{{ failedJobs.length }} task{{ failedJobs.length === 1 ? "" : "s" }} failed or timed out.</strong>
              <span v-if="isFailed" class="failed-status-tag">({{ comparison.status }})</span>
            </div>
            <p class="failed-banner-desc">
              Completed tasks and metrics are preserved. You can retry failed tasks with existing settings or update the gateway/keys.
            </p>
          </div>
          <div class="failed-banner-actions">
            <button
              type="button"
              class="btn primary small"
              :disabled="retrying"
              @click="executeRetry(false)"
            >
              <svg v-if="retrying" viewBox="0 0 20 20" aria-hidden="true" style="width:13px;height:13px;margin-right:4px;" class="spin">
                <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
              {{ retrying ? "Retrying..." : `🔄 Retry All Failed (${failedJobs.length})` }}
            </button>
            <button
              type="button"
              class="btn ghost small"
              :disabled="retrying"
              @click="openRetryModal(null)"
            >
              ⚙️ Change Gateway / Keys &amp; Retry...
            </button>
          </div>
        </div>

        <section class="hero">
          <div class="hero-head">
            <div class="hero-copy">
              <div class="eyebrow">Screening run · {{ comparison.models?.join(", ") }}</div>
              <h1>{{ comparison.name }}</h1>
              <div class="meta-row">
                <StatusBadge :status="comparison.status" />
                <code class="id-chip">{{ comparison.id }}</code>
                <span>{{ comparison.dataset_name || comparison.dataset_id }}</span>
                <span class="meta-dot">·</span>
                <span>{{ new Set(jobs.map((job) => job.case_id)).size }} cases</span>
                <span class="meta-dot">·</span>
                <span>{{ comparison.trials }} trial{{ comparison.trials === 1 ? "" : "s" }}</span>
                <span class="meta-dot">·</span>
                <span>{{ formatDate(comparison.finished_at || comparison.created_at) }}</span>
              </div>
            </div>
            <div class="hero-side">
              <label v-if="models.length > 1" class="model-select">
                <span>Model</span>
                <select v-model="selectedModel" class="select">
                  <option v-for="model in models" :key="model" :value="model">{{ model }}</option>
                </select>
              </label>
              <div class="screening-pill">Screening signal only</div>
            </div>
          </div>

          <div class="kpi-grid">
            <article v-for="kpi in kpis" :key="kpi.label" class="kpi-card" :class="`kpi-${kpi.tone}`">
              <span class="kpi-label">{{ kpi.label }}</span>
              <strong class="kpi-value">{{ kpi.value }}</strong>
              <span class="kpi-sub">{{ kpi.sub }}</span>
            </article>
          </div>
        </section>

        <section class="panel radar-panel">
          <div class="panel-head">
            <div>
              <div class="eyebrow">Eight-axis profile</div>
              <h2>Harness radar</h2>
              <p class="panel-sub">
                All axes are normalized 0–100. Hover an axis label on the radar to inspect values across harnesses.
              </p>
            </div>
            <div class="radar-badges">
              <span class="soft-badge">{{ series[0]?.model || selectedModel || "model" }}</span>
              <span class="soft-badge">n = {{ series[0]?.n || 0 }}</span>
            </div>
          </div>

          <div class="radar-layout">
            <RadarChart
              :axes="axes"
              :series="series"
              :active-key="hoveredAxisKey"
              @hover="onAxisHover"
            />

            <div class="radar-side">
              <div class="legend">
                <div
                  v-for="(item, index) in allHarnessSeries"
                  :key="item.harness_id"
                  class="legend-item"
                  :class="{ 'is-dimmed': hiddenHarnesses.has(item.harness_id) }"
                  role="button"
                  tabindex="0"
                  :title="hiddenHarnesses.has(item.harness_id) ? 'Click to show on radar' : 'Click to hide from radar'"
                  @click="toggleHarnessVisibility(item.harness_id)"
                  @keydown.enter="toggleHarnessVisibility(item.harness_id)"
                >
                  <span
                    class="legend-swatch"
                    :style="{ background: hiddenHarnesses.has(item.harness_id) ? '#475569' : harnessColor(item.harness_id, index) }"
                  ></span>
                  <div class="legend-copy">
                    <strong>{{ harnessName(item) }}</strong>
                    <span>{{ item.model || selectedModel }}</span>
                  </div>
                  <span class="legend-score">{{ formatPct(item.values?.accuracy) }}</span>
                  <div class="legend-metrics">
                    <span><em>Safety</em>{{ formatPct(item.values?.safety) }}</span>
                    <span><em>Exact</em>{{ formatPct(item.values?.exact_match) }}</span>
                    <span><em>Mean</em>{{ formatMs(item.metrics?.latency_ms?.mean) }}</span>
                  </div>
                  <span class="legend-eye-toggle">
                    {{ hiddenHarnesses.has(item.harness_id) ? "OFF" : "ON" }}
                  </span>
                </div>
              </div>

              <div class="axis-inspector" :class="{ active: hoveredAxis }">
                <template v-if="hoveredAxis">
                  <div class="inspector-label">{{ hoveredAxis.label }}</div>
                  <p>{{ hoveredAxis.description }}</p>
                  <div class="inspector-values">
                    <div v-for="item in series" :key="item.harness_id" class="inspector-value">
                      <span class="chip-line" :style="{ background: harnessColor(item.harness_id) }"></span>
                      <span>{{ item.harness_id.toUpperCase() }}</span>
                      <strong>{{ formatPct(item.values?.[hoveredAxis.key]) }}</strong>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <div class="inspector-label">Axis inspector</div>
                  <p>Hover or focus an axis label on the radar to see all three values, or read the exact table below.</p>
                </template>
              </div>

              <details class="axis-guide">
                <summary>How to read this radar</summary>
                <ul>
                  <li>Higher is better on every axis. Efficiency is a bounded reference score, not a cost or quality claim.</li>
                  <li>Harnesses differ across agent orchestration, context access, and execution flow; contrasts are descriptive benchmarks.</li>
                  <li>Evaluate qualitative traces and logs alongside aggregate scores when assessing harness architectures.</li>
                </ul>
              </details>

            </div>
          </div>
        </section>

        <!-- Post-Run In-UI Report generated upon completion of run -->
        <PostRunReport
          v-if="comparison && (comparison.status === 'COMPLETED' || jobs.some((j) => j.status === 'COMPLETED'))"
          :comparison="comparison"
          :radar="radar"
          :series="series"
          :jobs="jobs"
          :axes="axes"
        />

        <JobTable :jobs="jobs" @open="openJob" @retry-job="openRetryModal" />

        <footer class="page-footer">
          <div>
            <strong>Evaluation benchmark.</strong> Comparative agent harness lab for autonomous system testing, telemetry inspection, and iterative harness improvement.
          </div>
          <div>
            Harness metrics reflect empirical runs on the designated test dataset. Check case-level logs and execution traces for qualitative verification.
          </div>
        </footer>
      </template>
    </main>

    <JobDrawer
      v-if="selectedJob"
      :job="selectedJob"
      @close="closeJob"
      @retry="openRetryModal"
    />
    <RunComparisonModal
      :open="showRunModal"
      :meta="meta"
      @close="showRunModal = false"
      @created="onComparisonCreated"
    />

    <!-- Delete Task Confirmation Modal -->
    <div v-if="deleteModalOpen" class="modal-backdrop" @click.self="deleteModalOpen = false">
      <div class="delete-modal-card">
        <div class="delete-modal-header">
          <div class="delete-modal-icon">
            <svg viewBox="0 0 24 24" aria-hidden="true" style="width:24px;height:24px;">
              <path d="M12 9v4m0 4h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" fill="none" stroke="#ef4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>
          <div>
            <h3>Delete Comparison Task?</h3>
            <span class="delete-target-id"><code>{{ targetToDelete?.id }}</code></span>
          </div>
        </div>

        <div class="delete-modal-body">
          <p>
            Are you sure you want to permanently delete <strong>{{ targetToDelete?.name }}</strong>?
          </p>
          <div class="delete-warning-box">
            <span>⚠️ This will permanently remove this evaluation run, all execution jobs, and all generated radar metrics from PostgreSQL and storage. This action cannot be undone.</span>
          </div>
          <div v-if="deleteError" class="error-banner small">
            {{ deleteError }}
          </div>
        </div>

        <div class="delete-modal-actions">
          <button type="button" class="btn ghost" :disabled="deleting" @click="deleteModalOpen = false">
            Cancel
          </button>
          <button type="button" class="btn btn-danger" :disabled="deleting" @click="confirmDelete">
            <svg v-if="deleting" viewBox="0 0 20 20" aria-hidden="true" style="width:14px;height:14px;margin-right:6px;" class="spin">
              <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            </svg>
            {{ deleting ? "Deleting..." : "Yes, Delete Task Permanently" }}
          </button>
        </div>
      </div>
    </div>

    <!-- Retry Tasks Modal -->
    <div v-if="retryModalOpen" class="modal-backdrop" @click.self="retryModalOpen = false">
      <div class="retry-modal-card">
        <div class="retry-modal-header">
          <div class="retry-modal-icon">
            <span>🔄</span>
          </div>
          <div>
            <h3>{{ retryTargetJob ? 'Retry Failed Task' : 'Retry Failed Tasks' }}</h3>
            <span class="retry-target-id">
              <code>{{ retryTargetJob ? `${retryTargetJob.case_id} (${retryTargetJob.harness_id.toUpperCase()})` : `${failedJobs.length} tasks in ${comparison?.name}` }}</code>
            </span>
          </div>
        </div>

        <div class="retry-modal-body">
          <p v-if="retryTargetJob">
            Retry execution for case <strong>{{ retryTargetJob.case_id }}</strong> on harness <strong>{{ retryTargetJob.harness_id.toUpperCase() }}</strong>.
            Existing completed tasks remain untouched.
          </p>
          <p v-else>
            Retry all <strong>{{ failedJobs.length }} failed or timed out tasks</strong>.
            All {{ jobs.filter(j => j.status === 'COMPLETED').length }} successfully completed tasks and their metrics will be preserved.
          </p>

          <div class="retry-config-box">
            <div class="retry-config-head">
              <strong>Optional: Update Gateway / API Credentials</strong>
              <span>If tasks failed due to rate limits (429) or quota exhaustion, supply a fresh key or endpoint.</span>
            </div>
            
            <div class="retry-form-grid">
              <label class="form-label">
                <span>Gateway Base URL</span>
                <input
                  v-model="retryGateway.base_url"
                  type="text"
                  class="input"
                  placeholder="e.g. http://192.168.29.43:3001/v1 or https://generativelanguage.googleapis.com/v1beta/openai"
                />
              </label>

              <label class="form-label">
                <span>API Key</span>
                <input
                  v-model="retryGateway.api_key"
                  type="password"
                  class="input"
                  placeholder="Leave empty to reuse existing key"
                  autocomplete="off"
                />
              </label>
            </div>
          </div>

          <div v-if="retryError" class="error-banner small">
            {{ retryError }}
          </div>
        </div>

        <div class="retry-modal-actions">
          <button type="button" class="btn ghost" :disabled="retrying" @click="retryModalOpen = false">
            Cancel
          </button>
          <button
            type="button"
            class="btn secondary"
            :disabled="retrying"
            @click="executeRetry(false)"
          >
            {{ retrying ? "Retrying..." : "Retry with Existing Settings" }}
          </button>
          <button
            type="button"
            class="btn primary"
            :disabled="retrying"
            @click="executeRetry(true)"
          >
            {{ retrying ? "Retrying..." : (retryGateway.api_key || retryGateway.base_url ? "Update Credentials & Retry" : "Confirm Retry") }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
