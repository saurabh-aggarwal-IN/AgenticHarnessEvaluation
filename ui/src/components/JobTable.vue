<script setup>
import { computed, ref } from "vue";
import StatusBadge from "./StatusBadge.vue";
import {
  formatMs,
  formatNumber,
  formatPct,
  harnessColor,
  harnessName,
} from "../theme";

const props = defineProps({
  jobs: { type: Array, default: () => [] },
});

const emit = defineEmits(["open", "retry-job"]);

const harness = ref("all");
const outcome = ref("all");
const query = ref("");

const harnessOptions = computed(() => {
  const ids = [...new Set(props.jobs.map((job) => job.harness_id))].sort();
  return ["all", ...ids];
});

const harnessCounts = computed(() => {
  const counts = { all: props.jobs.length };
  for (const job of props.jobs) {
    counts[job.harness_id] = (counts[job.harness_id] || 0) + 1;
  }
  return counts;
});

function isExact(job) {
  return job.evaluation?.exact_set_match === true;
}

function isSafetyFlag(job) {
  return job.evaluation?.safety_ok === false || (job.evaluation?.forbidden_hits || []).length > 0;
}

function matchesOutcome(job) {
  if (outcome.value === "all") return true;
  if (outcome.value === "exact") return isExact(job);
  if (outcome.value === "missed") return !isExact(job);
  if (outcome.value === "safety") return isSafetyFlag(job);
  if (outcome.value === "failed") return job.status !== "COMPLETED";
  return true;
}

const sortBy = ref("case_id");
const sortDesc = ref(false);

function toggleSort(col) {
  if (sortBy.value === col) {
    sortDesc.value = !sortDesc.value;
  } else {
    sortBy.value = col;
    sortDesc.value = false;
  }
}

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase();
  return props.jobs
    .filter((job) => {
      if (harness.value !== "all" && job.harness_id !== harness.value) return false;
      if (!matchesOutcome(job)) return false;
      if (!needle) return true;
      const evaluation = job.evaluation || {};
      const haystack = [
        job.case_id,
        job.harness_id,
        job.model,
        ...(evaluation.expected_codes || []),
        ...(evaluation.returned_codes || []),
        ...(evaluation.missing_codes || []),
        ...(evaluation.forbidden_hits || []),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    })
    .sort((a, b) => {
      let valA, valB;
      if (sortBy.value === "case_id") {
        valA = String(a.case_id);
        valB = String(b.case_id);
      } else if (sortBy.value === "harness") {
        valA = String(a.harness_id);
        valB = String(b.harness_id);
      } else if (sortBy.value === "status") {
        valA = String(a.status);
        valB = String(b.status);
      } else if (sortBy.value === "f1") {
        valA = f1(a) ?? -1;
        valB = f1(b) ?? -1;
      } else if (sortBy.value === "exact") {
        valA = a.evaluation?.exact_set_match ? 1 : 0;
        valB = b.evaluation?.exact_set_match ? 1 : 0;
      } else if (sortBy.value === "safety") {
        valA = isSafetyFlag(a) ? 1 : 0;
        valB = isSafetyFlag(b) ? 1 : 0;
      } else if (sortBy.value === "latency") {
        valA = Number(a.latency_ms) || 0;
        valB = Number(b.latency_ms) || 0;
      } else if (sortBy.value === "tokens") {
        valA = Number(a.usage?.total_tokens) || 0;
        valB = Number(b.usage?.total_tokens) || 0;
      } else {
        valA = String(a.case_id);
        valB = String(b.case_id);
      }

      let cmp = 0;
      if (typeof valA === "number" && typeof valB === "number") {
        cmp = valA - valB;
      } else {
        cmp = String(valA).localeCompare(String(valB));
      }

      if (cmp === 0) {
        cmp = String(a.case_id).localeCompare(String(b.case_id)) ||
              String(a.harness_id).localeCompare(String(b.harness_id)) ||
              Number(a.trial) - Number(b.trial);
      }

      return sortDesc.value ? -cmp : cmp;
    });
});

function historyState(job) {
  const evaluation = job.evaluation || {};
  const required = evaluation.history_required_codes || [];
  if (!required.length) return { label: "—", cls: "neutral" };
  if (evaluation.history_present === false) return { label: "not available", cls: "muted" };
  const hits = evaluation.history_specificity_hits || [];
  if (hits.length) return { label: "used", cls: "ok" };
  return { label: "missed", cls: "bad" };
}

function f1(job) {
  const value = job.evaluation?.f1;
  return typeof value === "number" ? value : null;
}

function f1Width(job) {
  const value = f1(job);
  return value === null ? 0 : Math.max(0, Math.min(100, value * 100));
}

function returnedCount(job) {
  return job.evaluation?.returned_count ?? job.response?.codes?.length ?? 0;
}

function expectedCount(job) {
  return job.evaluation?.expected_count ?? 0;
}

function missCodes(job) {
  return job.evaluation?.missing_codes || [];
}

function forbiddenCodes(job) {
  return job.evaluation?.forbidden_hits || [];
}

function open(job) {
  emit("open", job);
}
</script>

<template>
  <section class="panel jobs-panel">
    <div class="panel-head jobs-head">
      <div>
        <div class="eyebrow">Per-case results</div>
        <h2>Case-level evidence</h2>
        <p class="panel-sub">
          Click a row to inspect returned outputs, misses, execution traces, and raw payloads.
        </p>
      </div>
      <div class="job-count">{{ filtered.length }} <span>/ {{ jobs.length }} jobs</span></div>
    </div>

    <div class="job-toolbar">
      <div class="segmented harness-filter" role="group" aria-label="Filter by harness">
        <button
          v-for="id in harnessOptions"
          :key="id"
          type="button"
          :class="{ active: harness === id }"
          @click="harness = id"
        >
          <span v-if="id !== 'all'" class="chip-line" :style="{ background: harnessColor(id) }"></span>
          {{ id === "all" ? "All" : id.toUpperCase() }}
          <span class="filter-count">{{ harnessCounts[id] || 0 }}</span>
        </button>
      </div>

      <div class="toolbar-right">
        <select v-model="outcome" class="select" aria-label="Filter by outcome">
          <option value="all">All outcomes</option>
          <option value="exact">Exact set</option>
          <option value="missed">Missed set</option>
          <option value="safety">Safety flag</option>
          <option value="failed">Failed job</option>
        </select>

        <label class="search">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <circle cx="9" cy="9" r="5.5" fill="none" stroke="currentColor" stroke-width="1.6" />
            <path d="m13.2 13.2 4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
          </svg>
          <input v-model="query" type="search" placeholder="Case or output…" aria-label="Search cases or outputs" />
        </label>
      </div>
    </div>

    <div class="table-scroll jobs-scroll">
      <table class="jobs-table">
        <thead>
          <tr>
            <th class="sortable-th" @click="toggleSort('case_id')">
              Case <span class="sort-icon">{{ sortBy === 'case_id' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th class="sortable-th" @click="toggleSort('harness')">
              Harness <span class="sort-icon">{{ sortBy === 'harness' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th class="sortable-th" @click="toggleSort('status')">
              Status <span class="sort-icon">{{ sortBy === 'status' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th class="sortable-th" @click="toggleSort('f1')">
              F1 <span class="sort-icon">{{ sortBy === 'f1' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th class="sortable-th" @click="toggleSort('exact')">
              Exact <span class="sort-icon">{{ sortBy === 'exact' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th class="sortable-th" @click="toggleSort('safety')">
              Safety <span class="sort-icon">{{ sortBy === 'safety' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th>History</th>
            <th>Outputs</th>
            <th>Misses / forbidden</th>
            <th class="sortable-th num-th" @click="toggleSort('latency')">
              Latency <span class="sort-icon">{{ sortBy === 'latency' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th class="sortable-th num-th" @click="toggleSort('tokens')">
              Tokens <span class="sort-icon">{{ sortBy === 'tokens' ? (sortDesc ? '▼' : '▲') : '' }}</span>
            </th>
            <th aria-label="Open"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="job in filtered"
            :key="job.id"
            class="job-row"
            :class="{ 'safety-row': isSafetyFlag(job) }"
            tabindex="0"
            @click="open(job)"
            @keydown.enter.prevent="open(job)"
          >
            <td>
              <div class="case-cell">
                <span class="case-id">{{ job.case_id }}</span>
                <span v-if="job.trial > 1" class="trial">trial {{ job.trial }}</span>
              </div>
            </td>
            <td>
              <span class="harness-tag" :style="{ '--harness-color': harnessColor(job.harness_id) }">
                <span class="harness-dot"></span>
                {{ job.harness_id.toUpperCase() }}
              </span>
              <span class="harness-name-cell">{{ harnessName(job) }}</span>
            </td>
            <td><StatusBadge :status="job.status" /></td>
            <td>
              <div class="f1-cell">
                <span class="f1-value">{{ f1(job) === null ? "—" : formatPct(f1(job)) }}</span>
                <span class="f1-track"><span :style="{ width: f1Width(job) + '%' }"></span></span>
              </div>
            </td>
            <td>
              <span v-if="job.evaluation?.exact_set_match" class="mark ok-mark" title="Exact set match">✓</span>
              <span v-else class="mark muted-mark" title="Not an exact set match">—</span>
            </td>
            <td>
              <span v-if="isSafetyFlag(job)" class="pill pill-bad">flag</span>
              <span v-else-if="job.evaluation" class="pill pill-ok">clear</span>
              <span v-else class="muted-mark">—</span>
            </td>
            <td>
              <span
                class="pill"
                :class="{
                  'pill-ok': historyState(job).cls === 'ok',
                  'pill-bad': historyState(job).cls === 'bad',
                  'pill-muted': historyState(job).cls === 'muted' || historyState(job).cls === 'neutral',
                }"
              >
                {{ historyState(job).label }}
              </span>
            </td>
            <td>
              <span class="codes-count">{{ returnedCount(job) }}<span class="muted-mark"> / {{ expectedCount(job) }}</span></span>
            </td>
            <td>
              <div class="miss-cell">
                <span v-for="code in missCodes(job).slice(0, 2)" :key="'m-' + code" class="code-pill code-miss">
                  {{ code }}
                </span>
                <span v-if="missCodes(job).length > 2" class="code-more">+{{ missCodes(job).length - 2 }}</span>
                <span
                  v-for="code in forbiddenCodes(job).slice(0, 2)"
                  :key="'f-' + code"
                  class="code-pill code-forbidden"
                >
                  {{ code }}
                </span>
                <span
                  v-if="!missCodes(job).length && !forbiddenCodes(job).length"
                  class="muted-mark"
                >—</span>
              </div>
            </td>
            <td class="num-cell">{{ formatMs(job.latency_ms) }}</td>
            <td class="num-cell">{{ formatNumber(job.usage?.total_tokens) }}</td>
            <td class="action-cell">
              <button
                v-if="['FAILED', 'TIMEOUT', 'CANCELLED'].includes(job.status)"
                type="button"
                class="retry-icon-btn"
                title="Retry this failed task"
                aria-label="Retry this failed task"
                @click.stop="$emit('retry-job', job)"
              >
                🔄
              </button>
              <span v-else class="chevron-cell" aria-hidden="true">›</span>
            </td>
          </tr>
        </tbody>
      </table>

      <div v-if="!filtered.length" class="table-empty">
        No jobs match these filters.
      </div>
    </div>
  </section>
</template>
