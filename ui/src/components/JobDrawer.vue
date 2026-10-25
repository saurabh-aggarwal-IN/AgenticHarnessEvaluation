<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import StatusBadge from "./StatusBadge.vue";
import { api } from "../api";
import {
  formatMs,
  formatNumber,
  formatPct,
  harnessColor,
  harnessName,
} from "../theme";

const props = defineProps({
  job: { type: Object, required: true },
});

const emit = defineEmits(["close", "retry"]);

const canRetry = computed(() =>
  ["FAILED", "TIMEOUT", "CANCELLED"].includes(props.job.status),
);

const tab = ref("overview");
const copied = ref(false);

const workerLoading = ref(false);
const workerInfo = ref(null);
const restartingWorker = ref(false);
const restartFeedback = ref("");
const restartFeedbackClass = ref("");

async function checkWorkerStatus() {
  if (!props.job?.comparison_id || !props.job?.id) return;
  workerLoading.value = true;
  restartFeedback.value = "";
  try {
    const res = await api.jobWorkerStatus(props.job.comparison_id, props.job.id);
    workerInfo.value = res;
  } catch (err) {
    console.warn("Failed to check worker status:", err);
  } finally {
    workerLoading.value = false;
  }
}

async function restartWorker() {
  if (!props.job?.comparison_id || !props.job?.id) return;
  restartingWorker.value = true;
  restartFeedback.value = "";
  try {
    const res = await api.restartJobWorker(props.job.comparison_id, props.job.id);
    workerInfo.value = res;
    if (res.running) {
      restartFeedback.value = `Worker for ${props.job.harness_id.toUpperCase()} successfully restarted! Task re-queued.`;
      restartFeedbackClass.value = "worker-feedback-ok";
      emit("retry", props.job);
    } else {
      restartFeedback.value = `Worker restart status: ${res.status || "initiated"}`;
      restartFeedbackClass.value = "worker-feedback-warn";
    }
  } catch (err) {
    restartFeedback.value = `Failed to restart worker: ${err.message}`;
    restartFeedbackClass.value = "worker-feedback-bad";
  } finally {
    restartingWorker.value = false;
  }
}

watch(
  () => [props.job?.id, props.job?.status],
  () => {
    if (canRetry.value || props.job?.error) {
      checkWorkerStatus();
    } else {
      workerInfo.value = null;
    }
  },
  { immediate: true },
);

const evaluation = computed(() => props.job.evaluation || {});
const response = computed(() => props.job.response || {});
const returned = computed(() => response.value.codes || []);
const trace = computed(() => response.value.trace || []);
const expected = computed(() => evaluation.value.expected_codes || []);
const missing = computed(() => evaluation.value.missing_codes || []);
const extra = computed(() => evaluation.value.extra_codes || []);
const forbidden = computed(() => evaluation.value.forbidden_hits || []);
const notes = computed(() => evaluation.value.notes || []);
const historyRequired = computed(() => evaluation.value.history_required_codes || []);
const historyHits = computed(() => evaluation.value.history_specificity_hits || []);

const isSafetyFlag = computed(
  () => evaluation.value.safety_ok === false || forbidden.value.length > 0,
);

const historyLabel = computed(() => {
  if (!historyRequired.value.length) return "not applicable";
  if (evaluation.value.history_present === false) return "not available to this harness";
  if (historyHits.value.length) return "history-specific code used";
  return "history-specific code missed";
});

const historyClass = computed(() => {
  if (!historyRequired.value.length) return "neutral";
  if (evaluation.value.history_present === false) return "muted";
  if (historyHits.value.length) return "ok";
  return "bad";
});

const prettyJob = computed(() => JSON.stringify(props.job, null, 2));

const traceMessages = computed(() =>
  trace.value.filter((event) => event.type === "message" || event.type === "turn_start" || event.type === "turn_end" || event.type === "history_read" || event.type === "guardrail" || event.type === "usage" || event.type === "budget" || event.type === "error"),
);

function isForbidden(code) {
  return forbidden.value.includes(code);
}

function isMissing(code) {
  return missing.value.includes(code);
}

function isExtra(code) {
  return extra.value.includes(code);
}

function traceText(event) {
  const value = event.payload?.text ?? event.payload?.content ?? event.payload?.message;
  if (value === undefined || value === null) return "";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function traceRole(event) {
  return event.payload?.node || event.payload?.role || event.payload?.harness || "harness";
}

function traceSummary(event) {
  if (event.type === "turn_start") return `${traceRole(event)} started`;
  if (event.type === "turn_end") return `${traceRole(event)} finished`;
  if (event.type === "history_read") return "History fixture read";
  if (event.type === "guardrail") return "Guardrail check";
  if (event.type === "usage") return "Usage report";
  if (event.type === "budget") return "Budget report";
  if (event.type === "error") return "Error";
  return traceRole(event);
}

function traceBadge(event) {
  if (event.type === "message") return event.payload?.role || "message";
  return event.type.replace(/_/g, " ");
}

function tracePayloadPreview(event) {
  if (event.type === "history_read") {
    const codes = event.payload?.codes || [];
    return `${event.payload?.source || "history"} · ${codes.length} codes${codes.length ? ": " + codes.join(", ") : ""}`;
  }
  if (event.type === "guardrail") {
    const flags = [
      ...(event.payload?.input_flags || []),
      ...(event.payload?.output_flags || []),
    ];
    return flags.length ? flags.join(", ") : "no flags";
  }
  if (event.type === "turn_end") {
    const bits = [];
    if (event.payload?.model_actual) bits.push(`actual ${event.payload.model_actual}`);
    if (event.payload?.code_count !== undefined) bits.push(`${event.payload.code_count} codes`);
    if (event.payload?.approved !== undefined) bits.push(event.payload.approved ? "approved" : "not approved");
    if (event.payload?.parse_error) bits.push("parse error");
    return bits.join(" · ");
  }
  if (event.type === "usage") {
    const p = event.payload || {};
    return `in ${p.input_tokens ?? 0} · out ${p.output_tokens ?? 0} · total ${p.total_tokens ?? 0}`;
  }
  return "";
}

async function copyJson() {
  try {
    await navigator.clipboard.writeText(prettyJob.value);
    copied.value = true;
    window.setTimeout(() => (copied.value = false), 1600);
  } catch {
    copied.value = false;
  }
}

function onKeydown(event) {
  if (event.key === "Escape") emit("close");
}

onMounted(() => {
  document.addEventListener("keydown", onKeydown);
  document.body.classList.add("drawer-open");
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown);
  document.body.classList.remove("drawer-open");
});
</script>

<template>
  <Teleport to="body">
    <div class="drawer-overlay" @click.self="$emit('close')">
      <aside class="drawer" role="dialog" aria-modal="true" :aria-label="`${job.case_id} job detail`">
        <header class="drawer-head">
          <div>
            <div class="drawer-kicker">
              {{ job.case_id }} · {{ job.harness_id.toUpperCase() }} · {{ job.model }}
            </div>
            <div class="drawer-title-row">
              <h2>{{ job.case_id }} <span>{{ harnessName(job) }}</span></h2>
              <StatusBadge :status="job.status" />
              <button
                v-if="canRetry"
                type="button"
                class="btn secondary small retry-drawer-btn"
                title="Retry this failed task"
                @click="$emit('retry', job)"
              >
                🔄 Retry Task
              </button>
            </div>
          </div>
          <button type="button" class="icon-button" aria-label="Close details" @click="$emit('close')">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" />
            </svg>
          </button>
        </header>

        <div class="drawer-kpis">
          <div class="drawer-kpi">
            <span>F1</span>
            <strong>{{ evaluation.f1 === undefined ? "—" : formatPct(evaluation.f1) }}</strong>
          </div>
          <div class="drawer-kpi">
            <span>Exact set</span>
            <strong :class="evaluation.exact_set_match ? 'positive-text' : ''">
              {{ evaluation.exact_set_match ? "Yes" : "No" }}
            </strong>
          </div>
          <div class="drawer-kpi">
            <span>Safety</span>
            <strong :class="isSafetyFlag ? 'negative-text' : 'positive-text'">
              {{ evaluation.safety_ok === undefined ? "—" : isSafetyFlag ? "Flag" : "Clear" }}
            </strong>
          </div>
          <div class="drawer-kpi">
            <span>Latency</span>
            <strong>{{ formatMs(job.latency_ms) }}</strong>
          </div>
          <div class="drawer-kpi">
            <span>Tokens</span>
            <strong>{{ formatNumber(job.usage?.total_tokens) }}</strong>
          </div>
        </div>

        <div class="drawer-tabs" role="tablist" aria-label="Job detail view">
          <button type="button" :class="{ active: tab === 'overview' }" @click="tab = 'overview'">Overview</button>
          <button type="button" :class="{ active: tab === 'trace' }" @click="tab = 'trace'">
            Trace <span class="tab-count">{{ traceMessages.length }}</span>
          </button>
          <button type="button" :class="{ active: tab === 'raw' }" @click="tab = 'raw'">Raw JSON</button>
        </div>

        <div class="drawer-body">
          <template v-if="tab === 'overview'">
            <div v-if="job.error || canRetry" class="callout callout-bad">
              <div class="callout-error-head">
                <strong>{{ job.error?.code || (job.status === "FAILED" ? "Task Failed" : "Task Incomplete") }}</strong>
                <button
                  v-if="canRetry"
                  type="button"
                  class="btn small primary retry-inline-btn"
                  @click="$emit('retry', job)"
                >
                  🔄 Retry Task
                </button>
              </div>
              <p v-if="job.error?.message">{{ job.error.message }}</p>

              <!-- Concerned Worker Status Section -->
              <div class="worker-status-banner">
                <div class="worker-status-info">
                  <span class="worker-status-label">Concerned Worker ({{ job.harness_id.toUpperCase() }}):</span>
                  <span v-if="workerLoading" class="worker-pill worker-pill-loading">
                    <span class="pulse-dot"></span> Checking status...
                  </span>
                  <span v-else-if="workerInfo?.running" class="worker-pill worker-pill-running">
                    ● Running
                  </span>
                  <span v-else class="worker-pill worker-pill-stopped">
                    ● Stopped {{ workerInfo?.status ? `(${workerInfo.status})` : "" }}
                  </span>
                  <span v-if="workerInfo?.container" class="worker-container-name" :title="workerInfo.container">
                    {{ workerInfo.container }}
                  </span>
                </div>
                <div class="worker-status-actions">
                  <button
                    v-if="!workerLoading && !workerInfo?.running"
                    type="button"
                    class="btn small warning restart-worker-btn"
                    :disabled="restartingWorker"
                    @click="restartWorker"
                  >
                    <span v-if="restartingWorker">⏳ Restarting...</span>
                    <span v-else>🔄 Restart Worker</span>
                  </button>
                  <button
                    v-else-if="!workerLoading"
                    type="button"
                    class="btn small ghost restart-worker-btn"
                    title="Check worker status"
                    :disabled="restartingWorker"
                    @click="checkWorkerStatus"
                  >
                    Refresh
                  </button>
                </div>
              </div>

              <div v-if="restartFeedback" :class="['worker-feedback', restartFeedbackClass]">
                {{ restartFeedback }}
              </div>
            </div>

            <div v-if="forbidden.length" class="callout callout-bad">
              <strong>Forbidden item{{ forbidden.length > 1 ? "s" : "" }} returned</strong>
              <p>{{ forbidden.join(", ") }}. This job is a safety flag, violating benchmark guardrail constraints.</p>
            </div>

            <div class="drawer-section">
              <div class="drawer-section-head">
                <h3>Target items</h3>
                <span class="drawer-section-note">Expected {{ expected.length }} · returned {{ returned.length }}</span>
              </div>

              <div class="code-columns">
                <div class="code-column">
                  <h4>Expected</h4>
                  <div class="code-chip-list">
                    <span v-for="code in expected" :key="'e-' + code" class="code-chip code-expected">{{ code }}</span>
                    <span v-if="!expected.length" class="muted-mark">No expected items</span>
                  </div>
                </div>
                <div class="code-column">
                  <h4>Returned</h4>
                  <div class="code-chip-list">
                    <span
                      v-for="code in returned"
                      :key="'r-' + code.code"
                      class="code-chip"
                      :class="{
                        'code-forbidden': isForbidden(code.code),
                        'code-miss': isMissing(code.code),
                        'code-extra': isExtra(code.code),
                      }"
                    >
                      {{ code.code }}
                    </span>
                    <span v-if="!returned.length" class="muted-mark">No items returned</span>
                  </div>
                </div>
              </div>

              <div v-if="missing.length || extra.length || forbidden.length" class="diff-block">
                <div v-if="missing.length" class="diff-row">
                  <span class="diff-label">Missing</span>
                  <span v-for="code in missing" :key="'m-' + code" class="code-pill code-miss">{{ code }}</span>
                </div>
                <div v-if="extra.length" class="diff-row">
                  <span class="diff-label">Extra</span>
                  <span v-for="code in extra" :key="'x-' + code" class="code-pill code-extra">{{ code }}</span>
                </div>
                <div v-if="forbidden.length" class="diff-row">
                  <span class="diff-label">Forbidden</span>
                  <span v-for="code in forbidden" :key="'f-' + code" class="code-pill code-forbidden">{{ code }}</span>
                </div>
              </div>
            </div>

            <div v-if="returned.length" class="drawer-section">
              <div class="drawer-section-head">
                <h3>Evidence</h3>
                <span class="drawer-section-note">Snippet + rationale per returned item</span>
              </div>
              <article
                v-for="code in returned"
                :key="'ev-' + code.code"
                class="evidence-card"
                :class="{ 'evidence-bad': isForbidden(code.code) }"
              >
                <header>
                  <span class="code-chip" :class="{ 'code-forbidden': isForbidden(code.code) }">{{ code.code }}</span>
                  <span class="system-tag">{{ code.system }}</span>
                  <span v-if="isForbidden(code.code)" class="pill pill-bad">forbidden</span>
                </header>
                <p class="evidence-snippet">“{{ code.snippet }}”</p>
                <p class="evidence-rationale">{{ code.rationale || "No rationale provided." }}</p>
              </article>
            </div>

            <div class="drawer-section">
              <div class="drawer-section-head">
                <h3>History &amp; provenance</h3>
              </div>
              <div class="detail-grid">
                <div class="detail-card">
                  <span class="detail-label">History fixture</span>
                  <strong>{{ evaluation.history_present ? "Present" : "Not provided" }}</strong>
                  <p>{{ evaluation.history_read ? "History was read by the harness." : "No history read recorded." }}</p>
                </div>
                <div class="detail-card">
                  <span class="detail-label">History specificity</span>
                  <strong :class="`history-${historyClass}`">{{ historyLabel }}</strong>
                  <p v-if="historyRequired.length">
                    Required: {{ historyRequired.join(", ") }} · hits:
                    {{ historyHits.length ? historyHits.join(", ") : "none" }}
                  </p>
                  <p v-else>No history-specific gold code on this case.</p>
                </div>
                <div class="detail-card">
                  <span class="detail-label">Provenance</span>
                  <strong :class="evaluation.provenance_ok ? 'positive-text' : 'negative-text'">
                    {{ evaluation.provenance_ok ? "Verbatim" : "Not verbatim" }}
                  </strong>
                  <p>Grounded codes: {{ evaluation.grounded_codes ?? "—" }} · ungrounded: {{ (evaluation.ungrounded_codes || []).length }}</p>
                </div>
                <div class="detail-card">
                  <span class="detail-label">Schema</span>
                  <strong :class="evaluation.schema_valid ? 'positive-text' : 'negative-text'">
                    {{ evaluation.schema_valid ? "Valid" : "Invalid" }}
                  </strong>
                  <p>Invalid entries: {{ evaluation.invalid_entries ?? "—" }} · ignored E/M: {{ evaluation.em_ignored ?? "—" }}</p>
                </div>
              </div>
            </div>

            <div class="drawer-section">
              <div class="drawer-section-head">
                <h3>Usage &amp; budget</h3>
              </div>
              <div class="usage-grid">
                <div><span>Calls</span><strong>{{ job.usage?.calls ?? "—" }}</strong></div>
                <div><span>Input</span><strong>{{ formatNumber(job.usage?.input_tokens) }}</strong></div>
                <div><span>Output</span><strong>{{ formatNumber(job.usage?.output_tokens) }}</strong></div>
                <div><span>Total</span><strong>{{ formatNumber(job.usage?.total_tokens) }}</strong></div>
                <div><span>Cost</span><strong>{{ job.usage?.cost_usd === null || job.usage?.cost_usd === undefined ? "not priced" : `$${formatNumber(job.usage.cost_usd, 4)}` }}</strong></div>
                <div><span>Budget</span><strong>{{ job.budget?.exceeded ? "exceeded" : "within limits" }}</strong></div>
              </div>
              <div v-if="job.budget?.stop_reason" class="budget-note">
                Stop reason: {{ job.budget.stop_reason }}
              </div>
            </div>

            <div v-if="notes.length" class="drawer-section">
              <div class="drawer-section-head">
                <h3>Evaluator notes</h3>
              </div>
              <ul class="notes-list">
                <li v-for="note in notes" :key="note">{{ note }}</li>
              </ul>
            </div>

            <div v-if="job.guardrails" class="drawer-section">
              <div class="drawer-section-head">
                <h3>Guardrails</h3>
              </div>
              <div class="guardrail-line">
                <span class="pill" :class="job.guardrails.blocked ? 'pill-bad' : 'pill-ok'">
                  {{ job.guardrails.blocked ? "blocked" : "not blocked" }}
                </span>
                <span class="muted-mark">{{ (job.guardrails.violations || []).length }} violations</span>
                <span class="muted-mark">{{ (job.guardrails.output_flags || []).length }} output flags</span>
              </div>
            </div>
          </template>

          <template v-else-if="tab === 'trace'">
            <div class="trace-list">
              <article
                v-for="event in traceMessages"
                :key="event.seq"
                class="trace-event"
                :class="`trace-${event.type}`"
              >
                <template v-if="event.type === 'message'">
                  <details class="trace-message">
                    <summary>
                      <span class="trace-badge">{{ traceBadge(event) }}</span>
                      <span class="trace-summary">{{ traceSummary(event) }}</span>
                      <span class="trace-chars">{{ traceText(event).length.toLocaleString() }} chars</span>
                    </summary>
                    <pre>{{ traceText(event) }}</pre>
                  </details>
                </template>
                <template v-else>
                  <div class="trace-row">
                    <span class="trace-badge">{{ traceBadge(event) }}</span>
                    <span class="trace-summary">{{ traceSummary(event) }}</span>
                    <span v-if="tracePayloadPreview(event)" class="trace-detail">{{ tracePayloadPreview(event) }}</span>
                  </div>
                </template>
              </article>
            </div>
            <div v-if="!traceMessages.length" class="table-empty">No trace events recorded.</div>
          </template>

          <template v-else>
            <div class="raw-head">
              <span>Full job payload</span>
              <button type="button" class="btn ghost small" @click="copyJson">
                {{ copied ? "Copied" : "Copy JSON" }}
              </button>
            </div>
            <pre class="raw-json">{{ prettyJob }}</pre>
          </template>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<style scoped>
.worker-status-banner {
  margin-top: 10px;
  padding: 8px 12px;
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

[data-theme="dark"] .worker-status-banner {
  background: rgba(255, 255, 255, 0.04);
  border-color: rgba(255, 255, 255, 0.08);
}

.worker-status-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  flex-wrap: wrap;
}

.worker-status-label {
  font-weight: 600;
  color: var(--text-muted, #64748b);
}

.worker-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}

.worker-pill-running {
  background: rgba(16, 185, 129, 0.15);
  color: #059669;
}

[data-theme="dark"] .worker-pill-running {
  background: rgba(16, 185, 129, 0.2);
  color: #34d399;
}

.worker-pill-stopped {
  background: rgba(239, 68, 68, 0.15);
  color: #dc2626;
}

[data-theme="dark"] .worker-pill-stopped {
  background: rgba(239, 68, 68, 0.2);
  color: #f87171;
}

.worker-pill-loading {
  background: rgba(107, 114, 128, 0.15);
  color: var(--text-soft, #94a3b8);
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  animation: pulse 1s infinite alternate;
}

@keyframes pulse {
  from { opacity: 0.4; }
  to { opacity: 1; }
}

.worker-container-name {
  font-family: monospace;
  font-size: 10.5px;
  color: var(--text-soft, #94a3b8);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.worker-status-actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.restart-worker-btn {
  font-size: 11px !important;
  padding: 3px 10px !important;
  font-weight: 600;
}

.worker-feedback {
  margin-top: 8px;
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 11px;
  line-height: 1.4;
}

.worker-feedback-ok {
  background: rgba(16, 185, 129, 0.12);
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.25);
}

[data-theme="dark"] .worker-feedback-ok {
  color: #34d399;
}

.worker-feedback-warn {
  background: rgba(245, 158, 11, 0.12);
  color: #d97706;
  border: 1px solid rgba(245, 158, 11, 0.25);
}

[data-theme="dark"] .worker-feedback-warn {
  color: #fbbf24;
}

.worker-feedback-bad {
  background: rgba(239, 68, 68, 0.12);
  color: #dc2626;
  border: 1px solid rgba(239, 68, 68, 0.25);
}

[data-theme="dark"] .worker-feedback-bad {
  color: #f87171;
}
</style>
