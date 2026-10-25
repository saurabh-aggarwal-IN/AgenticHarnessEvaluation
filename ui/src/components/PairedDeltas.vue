<script setup>
import { computed } from "vue";
import { formatPct, formatScore, harnessColor, harnessName } from "../theme";

const props = defineProps({
  pairs: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
});

const lookup = computed(() => {
  const map = {};
  for (const item of props.series) {
    if (!map[item.harness_id]) map[item.harness_id] = item;
  }
  return map;
});

function nameFor(id) {
  return harnessName(lookup.value[id] || { harness_id: id });
}

function colorFor(id) {
  return harnessColor(id);
}

function deltaWidth(value) {
  return Math.min(50, Math.abs(Number(value) || 0) * 500);
}

function deltaClass(value) {
  const number = Number(value) || 0;
  if (number > 0.000001) return "positive";
  if (number < -0.000001) return "negative";
  return "neutral";
}

function formatCi(pair) {
  if (pair.ci_95_low === undefined || pair.ci_95_high === undefined) return "—";
  const low = pair.ci_95_low >= 0 ? `+${pair.ci_95_low.toFixed(3)}` : pair.ci_95_low.toFixed(3);
  const high = pair.ci_95_high >= 0 ? `+${pair.ci_95_high.toFixed(3)}` : pair.ci_95_high.toFixed(3);
  return `[${low}, ${high}]`;
}
</script>

<template>
  <section v-if="pairs.length" class="panel paired-panel">
    <div class="panel-head">
      <div>
        <div class="eyebrow">Statistical paired comparisons</div>
        <h2>Paired Deltas & Confidence Intervals</h2>
        <p class="panel-sub">
          Δ is b minus a across identical case-trial pairs. Includes 95% bootstrap confidence intervals
          and exact two-sided McNemar discordance tests to isolate true signal from non-deterministic noise.
        </p>
      </div>
    </div>

    <div class="paired-grid">
      <article v-for="pair in pairs" :key="pair.a + '-' + pair.b" class="paired-card">
        <header class="paired-head">
          <div class="paired-pair">
            <span class="paired-name" :style="{ color: colorFor(pair.a) }">{{ nameFor(pair.a) }}</span>
            <span class="paired-arrow">→</span>
            <span class="paired-name" :style="{ color: colorFor(pair.b) }">{{ nameFor(pair.b) }}</span>
          </div>
          <div class="paired-delta" :class="deltaClass(pair.mean_delta_f1)">
            {{ Number(pair.mean_delta_f1) >= 0 ? "+" : "" }}{{ formatScore(pair.mean_delta_f1, 3) }}
            <span class="paired-delta-label">ΔF1</span>
          </div>
        </header>

        <div v-if="pair.verdict" class="verdict-banner" :class="pair.is_significant ? 'verdict-sig' : 'verdict-noise'">
          <span class="verdict-dot"></span>
          <span class="verdict-text">{{ pair.verdict }}</span>
        </div>

        <div class="delta-track" aria-hidden="true">
          <span class="delta-center"></span>
          <span
            class="delta-fill"
            :class="deltaClass(pair.mean_delta_f1)"
            :style="{
              width: deltaWidth(pair.mean_delta_f1) + '%',
              left: Number(pair.mean_delta_f1) >= 0 ? '50%' : `calc(50% - ${deltaWidth(pair.mean_delta_f1)}%)`,
            }"
          ></span>
        </div>

        <div class="paired-stats">
          <div>
            <span class="stat-label">95% Bootstrap CI</span>
            <span class="stat-value" :class="pair.is_significant ? 'positive-text' : 'muted-text'">
              {{ formatCi(pair) }}
            </span>
          </div>
          <div>
            <span class="stat-label">McNemar test</span>
            <span class="stat-value">
              {{ pair.mcnemar_p_value !== undefined ? `p = ${pair.mcnemar_p_value.toFixed(3)}` : '—' }}
            </span>
          </div>
          <div>
            <span class="stat-label">Head-to-head</span>
            <span class="stat-value">
              <span class="positive-text">{{ pair.wins_b }}W</span> · {{ pair.ties }}T · <span class="negative-text">{{ pair.losses_b }}L</span>
            </span>
          </div>
          <div>
            <span class="stat-label">Δ Exact set</span>
            <span class="stat-value">
              {{ Number(pair.mean_delta_exact_match) >= 0 ? "+" : "" }}{{ formatPct(pair.mean_delta_exact_match) }}
            </span>
          </div>
        </div>
      </article>
    </div>

    <p class="paired-note">
      {{ pairs[0]?.note || "95% bootstrap confidence intervals estimated via 1000 resamples. Differences spanning 0 are flagged as noise." }}
    </p>
  </section>
</template>
