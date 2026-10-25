<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  models: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: "Select a model..." },
  disabledHint: { type: String, default: "Click 'Fetch Available Models' to unlock" },
});

const emit = defineEmits(["update:modelValue"]);

const isOpen = ref(false);
const searchQuery = ref("");
const dropdownRef = ref(null);

const filteredModels = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return props.models;
  return props.models.filter(
    (m) =>
      m.id.toLowerCase().includes(query) ||
      (m.name && m.name.toLowerCase().includes(query)),
  );
});

const selectedItem = computed(() => {
  return props.models.find((m) => m.id === props.modelValue);
});

function toggleDropdown() {
  if (props.disabled) return;
  isOpen.value = !isOpen.value;
  if (isOpen.value) {
    searchQuery.value = "";
  }
}

function selectModel(modelId) {
  emit("update:modelValue", modelId);
  isOpen.value = false;
  searchQuery.value = "";
}

function closeDropdown(e) {
  if (dropdownRef.value && !dropdownRef.value.contains(e.target)) {
    isOpen.value = false;
  }
}

onMounted(() => {
  document.addEventListener("click", closeDropdown);
});

onBeforeUnmount(() => {
  document.removeEventListener("click", closeDropdown);
});
</script>

<template>
  <div
    ref="dropdownRef"
    class="searchable-model-select"
    :class="{ 'is-disabled': disabled, 'is-open': isOpen }"
  >
    <!-- Display trigger button -->
    <button
      type="button"
      class="sms-trigger"
      :disabled="disabled"
      :title="disabled ? disabledHint : 'Click to search and select model'"
      @click="toggleDropdown"
    >
      <div class="sms-trigger-content">
        <span v-if="disabled" class="sms-lock-icon">🔒</span>
        <span v-else class="sms-model-icon">🧠</span>

        <span v-if="disabled" class="sms-disabled-text">
          {{ disabledHint }}
        </span>
        <span v-else-if="selectedItem" class="sms-selected-name">
          <strong>{{ selectedItem.id }}</strong>
          <span v-if="selectedItem.context_length" class="sms-ctx-badge">
            {{ Math.round(selectedItem.context_length / 1000) }}k ctx
          </span>
        </span>
        <span v-else-if="modelValue" class="sms-selected-name">
          <strong>{{ modelValue }}</strong>
        </span>
        <span v-else class="sms-placeholder">
          {{ placeholder }}
        </span>
      </div>

      <span class="sms-arrow" aria-hidden="true">
        {{ isOpen ? "▲" : "▼" }}
      </span>
    </button>

    <!-- Floating popup menu -->
    <div v-if="isOpen && !disabled" class="sms-dropdown-menu">
      <div class="sms-search-box">
        <span class="sms-search-icon">🔍</span>
        <input
          v-model="searchQuery"
          type="text"
          class="sms-search-input"
          placeholder="Search models (e.g. gemma, claude, llama, gpt-4o)..."
          autofocus
          @click.stop
        />
        <button
          v-if="searchQuery"
          type="button"
          class="sms-search-clear"
          @click="searchQuery = ''"
        >
          ✕
        </button>
      </div>

      <div class="sms-meta-row">
        <span>Showing {{ filteredModels.length }} of {{ models.length }} models</span>
        <span v-if="searchQuery" class="sms-filter-tag">filtered</span>
      </div>

      <div class="sms-list-container">
        <div
          v-for="m in filteredModels"
          :key="m.id"
          class="sms-item"
          :class="{ 'is-active': m.id === modelValue }"
          @click="selectModel(m.id)"
        >
          <div class="sms-item-info">
            <span class="sms-item-id">{{ m.id }}</span>
            <span v-if="m.name && m.name !== m.id" class="sms-item-name">{{ m.name }}</span>
          </div>
          <div class="sms-item-badges">
            <span v-if="m.context_length" class="sms-ctx-pill">
              {{ Math.round(m.context_length / 1000) }}k
            </span>
            <span v-if="m.id === modelValue" class="sms-check">✓</span>
          </div>
        </div>

        <div v-if="!filteredModels.length" class="sms-empty">
          No models matching "<strong>{{ searchQuery }}</strong>"
        </div>
      </div>
    </div>
  </div>
</template>
