<script setup>
import { computed, ref } from "vue";
import {
  formatMs,
  formatNumber,
  formatPct,
  formatScore,
  harnessColor,
  harnessName,
} from "../theme";

const props = defineProps({
  axes: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
});

const tab = ref("scores");

const bestByKey = computed(() => {
  const result = {};
  for (const axis of props.axes) {
    const values = props.series
      .map((item) => item.values?.[axis.key])
      .filter((value) => typeof value === "number" && !Number.isNaN(value));
    if (!values.length) {
      result[axis.key] = null;
      continue;
    }
    const max = Math.max(...values);
    const min = Math.min(...values);
    result[axis.key] = max - min > 0.0001 ? max : null;
  }
  return result;
});

function isBest(axis, item) {
  const best = bestByKey.value[axis.key];
  return best !== null && Math.abs((item.values?.[axis.key] ?? -1) - best) < 1e-9;
}

function metricText(key, value) {
  if (value === null || value === undefined) return "n/a";
  if (key === "efficiency") return formatScore(value, 3);
  return formatPct(value);
}

function barWidth(value) {
  if (typeof value !== "number" || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, value * 100));
}

const runRows = computed(() => [
  {
    label: "Jobs",
    hint: "Completed plus failed terminal jobs",
    value: (item) => formatNumber(item.metrics?.n ?? item.n),
  },
  {
    label: "Success rate",
    hint: "Jobs that completed without a harness error",
    value: (item) => formatPct(item.metrics?.success_rate),
  },
  {
    label: "Forbidden hits",
    hint: "Jobs that returned at least one forbidden code",
    value: (item) => formatNumber(item.metrics?.forbidden_hits),
  },
  {
    label: "Mean latency",
    hint: "Average wall-clock time per job",
    value: (item) => formatMs(item.metrics?.latency_ms?.mean),
  },
  {
    label: "p50 latency",
    hint: "Median wall-clock time per job",
    value: (item) => formatMs(item.metrics?.latency_ms?.p50),
  },
  {
    label: "p95 latency",
    hint: "95th percentile wall-clock time per job",
    value: (item) => formatMs(item.metrics?.latency_ms?.p95),
  },
  {
    label: "Mean tokens / job",
    hint: "Input plus output tokens, averaged per job",
    value: (item) => formatNumber(item.metrics?.tokens?.mean_per_job, 0),
  },
  {
    label: "Total calls",
    hint: "Model calls reported by the harness",
    value: (item) => formatNumber(item.metrics?.tokens?.calls),
  },
  {
    label: "Cost",
    hint: "Known cost only; the free router has no published price",
    value: (item) =>
      item.metrics?.cost?.known_jobs
        ? `$${formatNumber(item.metrics.cost.mean_usd, 4)} / job`
        : "not priced",
  },
]);
</script>

<template>
  <section class="panel metrics-panel">
    <div class="panel-head">
      <div>
        <div class="eyebrow">Deterministic evaluation</div>
        <h2>Aggregate metrics</h2>
        <p class="panel-sub">
          Pooled across {{ series[0]?.n ?? 0 }} cases. Higher is better; efficiency is a bounded
          reference score, not a cost or quality claim.
        </p>
      </div>
      <div class="segmented" role="tablist" aria-label="Metric view">
        <button
          type="button"
          :class="{ active: tab === 'scores' }"
          role="tab"
          :aria-selected="tab === 'scores'"
          @click="tab = 'scores'"
        >
          Scores
        </button>
        <button
          type="button"
          :class="{ active: tab === 'runtime' }"
          role="tab"
          :aria-selected="tab === 'runtime'"
          @click="tab = 'runtime'"
        >
          Run metrics
        </button>
      </div>
    </div>

    <div v-if="tab === 'scores'" class="table-scroll">
      <table class="metric-table">
        <thead>
          <tr>
            <th class="metric-name-col">Metric</th>
            <th v-for="(item, index) in series" :key="item.harness_id + (item.model || '')">
              <span class="harness-chip">
                <span class="chip-line" :style="{ background: harnessColor(item.harness_id, index) }"></span>
                {{ harnessName(item) }}
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="axis in axes" :key="axis.key">
            <th class="metric-name-col">
              <span class="metric-label">{{ axis.label }}</span>
              <span class="metric-hint">{{ axis.description }}</span>
            </th>
            <td v-for="(item, index) in series" :key="item.harness_id + '-' + axis.key">
              <div class="metric-cell" :class="{ best: isBest(axis, item) }">
                <div class="metric-value-row">
                  <span class="metric-value">{{ metricText(axis.key, item.values?.[axis.key]) }}</span>
                  <span v-if="isBest(axis, item)" class="best-mark" title="Best on this run">best</span>
                </div>
                <div class="metric-bar">
                  <span
                    :style="{
                      width: barWidth(item.values?.[axis.key]) + '%',
                      background: harnessColor(item.harness_id, index),
                    }"
                  ></span>
                </div>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="table-scroll">
      <table class="metric-table runtime-table">
        <thead>
          <tr>
            <th class="metric-name-col">Run metric</th>
            <th v-for="(item, index) in series" :key="item.harness_id + (item.model || '')">
              <span class="harness-chip">
                <span class="chip-line" :style="{ background: harnessColor(item.harness_id, index) }"></span>
                {{ harnessName(item) }}
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in runRows" :key="row.label">
            <th class="metric-name-col">
              <span class="metric-label">{{ row.label }}</span>
              <span class="metric-hint">{{ row.hint }}</span>
            </th>
            <td v-for="item in series" :key="item.harness_id + '-' + row.label">
              <span class="runtime-value">{{ row.value(item) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
