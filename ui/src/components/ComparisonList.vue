<script setup>
import { computed, ref } from "vue";
import { statusClass, formatShortDate } from "../theme";

const props = defineProps({
  items: { type: Array, default: () => [] },
  selectedId: { type: String, default: "" },
});

defineEmits(["select", "delete"]);

const search = ref("");
const statusFilter = ref("all");

const filteredItems = computed(() => {
  const q = search.value.trim().toLowerCase();
  return props.items.filter((item) => {
    if (statusFilter.value === "completed" && item.status !== "COMPLETED") return false;
    if (statusFilter.value === "failed" && item.status !== "FAILED" && item.status !== "TIMEOUT") return false;
    if (!q) return true;
    return (
      (item.name || "").toLowerCase().includes(q) ||
      (item.id || "").toLowerCase().includes(q) ||
      (item.models || []).some((m) => m.toLowerCase().includes(q))
    );
  });
});
</script>

<template>
  <div class="comparison-list-wrap">
    <div v-if="items.length > 1" class="sidebar-search">
      <input
        v-model="search"
        type="search"
        placeholder="Search runs…"
        aria-label="Search comparisons"
      />
    </div>

    <div class="comparison-list">
      <button
        v-for="item in filteredItems"
        :key="item.id"
        type="button"
        class="comparison-item"
        :class="{ active: item.id === selectedId }"
        @click="$emit('select', item.id)"
      >
        <div class="comparison-top">
          <span class="comparison-name">{{ item.name }}</span>
          <div class="comparison-top-actions">
            <span
              class="status-orb"
              :class="`orb-${statusClass(item.status)}`"
              :title="item.status"
            ></span>
            <button
              type="button"
              class="btn-delete-item"
              title="Delete this comparison run"
              @click.stop="$emit('delete', item)"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" style="width:12px;height:12px;">
                <path d="M4 6h12M8 6V4h4v2m2 0v10a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6h10zM9 9v5m2-5v5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
        </div>
        <div class="comparison-id">{{ item.id }}</div>
        <div class="comparison-meta">
          <span class="comparison-model">{{ item.models?.[0] || "—" }}</span>
          <span class="comparison-jobs">
            {{ item.progress?.done_jobs ?? 0 }}/{{ item.progress?.total_jobs ?? "—" }}
          </span>
        </div>
        <div class="comparison-foot">
          <span>{{ item.dataset_name || "dataset" }}</span>
          <span>{{ formatShortDate(item.created_at) }}</span>
        </div>
      </button>

      <div v-if="!filteredItems.length" class="comparison-empty">
        {{ items.length ? "No matching comparisons." : "No comparisons yet. Click '+ Launch New Run' above." }}
      </div>
    </div>
  </div>
</template>
