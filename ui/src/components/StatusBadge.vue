<script setup>
import { computed } from "vue";
import { statusClass } from "../theme";

const props = defineProps({
  status: { type: String, default: "UNKNOWN" },
  label: { type: String, default: "" },
});

const LABELS = {
  QUEUED: "Queued",
  RUNNING: "Running",
  CANCELLING: "Cancelling",
  COMPLETED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
  DISPATCHED: "Dispatched",
  TIMEOUT: "Timed out",
};

const text = computed(() => props.label || LABELS[props.status] || props.status);
const cls = computed(() => `status-${statusClass(props.status)}`);
</script>

<template>
  <span class="status-badge" :class="cls">
    <span class="status-dot" aria-hidden="true"></span>
    {{ text }}
  </span>
</template>
