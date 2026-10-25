<script setup>
import { computed, ref } from "vue";
import { harnessColor, formatPct } from "../theme";

const props = defineProps({
  axes: { type: Array, default: () => [] },
  series: { type: Array, default: () => [] },
  activeKey: { type: String, default: "" },
});

const emit = defineEmits(["hover"]);

const WIDTH = 700;
const HEIGHT = 600;
const CX = WIDTH / 2;
const CY = HEIGHT / 2 + 4;
const RADIUS = 205;
const LABEL_RADIUS = RADIUS + 44;
const LEVELS = [0.2, 0.4, 0.6, 0.8, 1];

const hovered = ref("");

const count = computed(() => Math.max(props.axes.length, 3));

function angle(index) {
  return (Math.PI * 2 * index) / count.value - Math.PI / 2;
}

function pointFor(index, value) {
  const scaled = Math.max(0, Math.min(1, Number(value) || 0));
  const a = angle(index);
  return {
    x: CX + Math.cos(a) * RADIUS * scaled,
    y: CY + Math.sin(a) * RADIUS * scaled,
  };
}

function ringPoints(level) {
  return props.axes
    .map((_, index) => {
      const p = pointFor(index, level);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    })
    .join(" ");
}

function seriesPoints(item) {
  return props.axes
    .map((axis, index) => {
      const p = pointFor(index, item.values?.[axis.key] ?? 0);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    })
    .join(" ");
}

function labelPos(index) {
  const a = angle(index);
  return {
    x: CX + Math.cos(a) * LABEL_RADIUS,
    y: CY + Math.sin(a) * LABEL_RADIUS,
    anchor:
      Math.abs(Math.cos(a)) < 0.2
        ? "middle"
        : Math.cos(a) > 0
          ? "start"
          : "end",
  };
}

function labelLines(axis) {
  const text = axis.label || axis.key;
  if (text.length <= 14) return [text];
  const words = text.split(" ");
  if (words.length === 1) return [text];
  const midpoint = Math.ceil(words.length / 2);
  return [words.slice(0, midpoint).join(" "), words.slice(midpoint).join(" ")];
}

function axisValue(item, axis) {
  const value = item.values?.[axis.key];
  return value === null || value === undefined ? "n/a" : formatPct(value);
}

function onHover(key) {
  hovered.value = key;
  emit("hover", key);
}

function onLeave() {
  hovered.value = "";
  emit("hover", "");
}

function color(item, index) {
  return harnessColor(item.harness_id, index);
}

function pointTitle(item, axis) {
  return `${item.harness_name || item.harness_id} · ${axis.label}: ${axisValue(item, axis)}`;
}
</script>

<template>
  <div class="radar-wrap">
    <svg
      class="radar-svg"
      :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
      role="img"
      aria-label="Harness comparison radar chart across eight evaluation axes"
    >
      <defs>
        <radialGradient id="radarGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stop-color="rgba(45, 212, 191, 0.08)" />
          <stop offset="100%" stop-color="rgba(45, 212, 191, 0)" />
        </radialGradient>
      </defs>

      <circle :cx="CX" :cy="CY" :r="RADIUS + 24" fill="url(#radarGlow)" />

      <g class="radar-grid">
        <polygon
          v-for="level in LEVELS"
          :key="level"
          :points="ringPoints(level)"
          fill="none"
          :stroke="level === 1 ? 'rgba(148,163,184,0.30)' : 'rgba(148,163,184,0.12)'"
          :stroke-width="level === 1 ? 1.4 : 1"
        />
        <line
          v-for="(axis, index) in axes"
          :key="`spoke-${axis.key}`"
          :x1="CX"
          :y1="CY"
          :x2="pointFor(index, 1).x"
          :y2="pointFor(index, 1).y"
          :stroke="activeKey === axis.key ? 'rgba(45,212,191,0.55)' : 'rgba(148,163,184,0.14)'"
          :stroke-width="activeKey === axis.key ? 1.5 : 1"
        />
      </g>

      <g class="radar-levels">
        <text
          v-for="level in LEVELS"
          :key="`level-${level}`"
          :x="CX + 8"
          :y="CY - RADIUS * level + 4"
          class="radar-level-label"
        >
          {{ Math.round(level * 100) }}
        </text>
      </g>

      <g
        v-for="(item, seriesIndex) in series"
        :key="item.harness_id + '-' + (item.model || '') + '-' + seriesIndex"
        class="radar-series"
      >
        <polygon
          :points="seriesPoints(item)"
          :fill="color(item, seriesIndex) + '1f'"
          :stroke="color(item, seriesIndex)"
          stroke-width="2.4"
          stroke-linejoin="round"
        />
        <circle
          v-for="(axis, index) in axes"
          :key="`point-${item.harness_id}-${axis.key}`"
          :cx="pointFor(index, item.values?.[axis.key] ?? 0).x"
          :cy="pointFor(index, item.values?.[axis.key] ?? 0).y"
          r="3.4"
          :fill="color(item, seriesIndex)"
          stroke="#0b1220"
          stroke-width="1.4"
        >
          <title>{{ pointTitle(item, axis) }}</title>
        </circle>
      </g>

      <g class="radar-axis-labels">
        <g
          v-for="(axis, index) in axes"
          :key="`axis-${axis.key}`"
          class="radar-axis-label"
          :class="{ active: activeKey === axis.key }"
          tabindex="0"
          role="button"
          :aria-label="`${axis.label}. Hover or focus to inspect values.`"
          @mouseenter="onHover(axis.key)"
          @mouseleave="onLeave"
          @focus="onHover(axis.key)"
          @blur="onLeave"
        >
          <text
            v-for="(line, lineIndex) in labelLines(axis)"
            :key="line"
            :x="labelPos(index).x"
            :y="labelPos(index).y + (lineIndex - (labelLines(axis).length - 1) / 2) * 15"
            :text-anchor="labelPos(index).anchor"
            :class="{ emphasized: activeKey === axis.key }"
          >
            {{ line }}
          </text>
        </g>
      </g>

      <circle :cx="CX" :cy="CY" r="3" fill="rgba(148,163,184,0.55)" />

      <text
        v-if="!series.length"
        :x="CX"
        :y="CY"
        text-anchor="middle"
        class="radar-empty"
      >
        No radar series for this comparison.
      </text>
    </svg>
  </div>
</template>
