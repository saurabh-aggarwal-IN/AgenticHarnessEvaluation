<script setup>
import { onMounted, ref } from "vue";
import { api } from "../api";

const props = defineProps({
  open: { type: Boolean, default: false },
  meta: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["close", "created"]);

const name = ref("Agent Multi-Harness Benchmark");
const repoUrl = ref("");
const enableH1 = ref(true);
const enableH2 = ref(true);
const enableH3 = ref(true);
const commitH1 = ref("");
const commitH2 = ref("");
const commitH3 = ref("");
const selectedDataset = ref("");
const selectedModel = ref("gemma-4-31b");
const customModel = ref("");
const trials = ref(1);
const concurrency = ref(1);
const maxWallSeconds = ref(300);
const maxLlmCalls = ref(8);

const datasets = ref([]);
const loadingDatasets = ref(false);
const submitting = ref(false);
const error = ref("");

function applyBudgetPreset(preset) {
  if (preset === "fast") {
    maxWallSeconds.value = 90;
    maxLlmCalls.value = 4;
  } else if (preset === "standard") {
    maxWallSeconds.value = 300;
    maxLlmCalls.value = 8;
  } else if (preset === "deep") {
    maxWallSeconds.value = 600;
    maxLlmCalls.value = 16;
  }
}

async function loadDatasets() {
  loadingDatasets.value = true;
  try {
    const list = await api.datasets();
    datasets.value = list;
    if (list.length && !selectedDataset.value) {
      selectedDataset.value = list[0].id;
    }
  } catch (cause) {
    console.error("Failed to load datasets", cause);
  } finally {
    loadingDatasets.value = false;
  }
}

onMounted(() => {
  loadDatasets();
});

async function submit() {
  if (!name.value.trim()) {
    error.value = "Comparison name is required.";
    return;
  }
  if (!selectedDataset.value) {
    error.value = "Please select a dataset.";
    return;
  }

  const commits = {};
  if (enableH1.value && commitH1.value.trim()) commits.h1 = commitH1.value.trim();
  if (enableH2.value && commitH2.value.trim()) commits.h2 = commitH2.value.trim();
  if (enableH3.value && commitH3.value.trim()) commits.h3 = commitH3.value.trim();

  if (Object.keys(commits).length < 2) {
    error.value = "Please select at least 2 harnesses to run a comparison.";
    return;
  }

  const modelName = selectedModel.value === "custom"
    ? customModel.value.trim()
    : selectedModel.value.trim();

  if (!modelName) {
    error.value = "Please enter or select a model.";
    return;
  }

  submitting.value = true;
  error.value = "";
  try {
    const payload = {
      name: name.value.trim(),
      repo_url: repoUrl.value.trim(),
      commits,
      dataset_id: selectedDataset.value,
      models: [modelName],
      trials: Number(trials.value) || 1,
      concurrency: Number(concurrency.value) || 1,
      budget: {
        max_wall_seconds: Number(maxWallSeconds.value) || 300,
        max_llm_calls: Number(maxLlmCalls.value) || 8,
      },
    };
    const created = await api.createComparison(payload);
    emit("created", created.id);
    emit("close");
  } catch (cause) {
    error.value = cause?.message || "Failed to start comparison.";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div v-if="open" class="modal-backdrop" @click.self="$emit('close')">
    <div class="modal-dialog">
      <header class="modal-header">
        <h3>Launch Live Harness Comparison</h3>
        <button type="button" class="btn ghost small" @click="$emit('close')">✕</button>
      </header>

      <form @submit.prevent="submit">
        <div class="modal-body">
          <div v-if="error" class="error-banner small">
            {{ error }}
          </div>

          <div class="form-group">
            <label for="cmp-name">Comparison Name</label>
            <input id="cmp-name" v-model="name" type="text" placeholder="e.g. Gemma 4 31B Full Evaluation" required />
          </div>

          <div class="form-group">
            <label for="cmp-repo">Harness Repository</label>
            <input id="cmp-repo" v-model="repoUrl" type="text" required />
          </div>

          <div class="form-group">
            <label class="field-label">Included Harnesses (at least 2 required)</label>
            <div class="checkbox-row">
              <label class="checkbox-chip" :class="{ active: enableH1 }">
                <input v-model="enableH1" type="checkbox" /> H1 Hermes CLI
              </label>
              <label class="checkbox-chip" :class="{ active: enableH2 }">
                <input v-model="enableH2" type="checkbox" /> H2 LangGraph
              </label>
              <label class="checkbox-chip" :class="{ active: enableH3 }">
                <input v-model="enableH3" type="checkbox" /> H3 Verifier
              </label>
            </div>
          </div>

          <div class="form-row">
            <div v-if="enableH1" class="form-group">
              <label for="h1-commit">Commit H1 (Hermes CLI)</label>
              <input id="h1-commit" v-model="commitH1" type="text" required />
            </div>
            <div v-if="enableH2" class="form-group">
              <label for="h2-commit">Commit H2 (LangGraph)</label>
              <input id="h2-commit" v-model="commitH2" type="text" required />
            </div>
            <div v-if="enableH3" class="form-group">
              <label for="h3-commit">Commit H3 (Verifier)</label>
              <input id="h3-commit" v-model="commitH3" type="text" required />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label for="cmp-dataset">Dataset</label>
              <select id="cmp-dataset" v-model="selectedDataset" required>
                <option v-for="ds in datasets" :key="ds.id" :value="ds.id">
                  {{ ds.name || ds.id }} ({{ ds.case_count }} cases)
                </option>
              </select>
            </div>
            <div class="form-group">
              <label for="cmp-trials">Repeated Trials (N)</label>
              <input id="cmp-trials" v-model.number="trials" type="number" min="1" max="5" required />
            </div>
            <div class="form-group">
              <label for="cmp-concurrency">Concurrency</label>
              <input id="cmp-concurrency" v-model.number="concurrency" type="number" min="1" max="8" required />
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label for="cmp-model">Live Model Backend</label>
              <select id="cmp-model" v-model="selectedModel" required>
                <option value="gemma-4-31b">gemma-4-31b (FreeLLMAPI Gateway)</option>
                <option value="custom">Custom Model Slug…</option>
              </select>
            </div>
            <div v-if="selectedModel === 'custom'" class="form-group">
              <label for="custom-model">Model Name / Slug</label>
              <input id="custom-model" v-model="customModel" type="text" placeholder="e.g. llama-3.3-70b" required />
            </div>
          </div>

          <div class="form-group">
            <div class="budget-head">
              <label>Execution Budget per Job</label>
              <div class="budget-presets">
                <button type="button" class="btn small ghost" @click="applyBudgetPreset('fast')">Fast (90s/4 calls)</button>
                <button type="button" class="btn small ghost" @click="applyBudgetPreset('standard')">Standard (300s/8 calls)</button>
                <button type="button" class="btn small ghost" @click="applyBudgetPreset('deep')">Deep (600s/16 calls)</button>
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label for="cmp-timeout">Max Wall Time (s)</label>
                <input id="cmp-timeout" v-model.number="maxWallSeconds" type="number" min="30" max="600" />
              </div>
              <div class="form-group">
                <label for="cmp-calls">Max LLM Calls</label>
                <input id="cmp-calls" v-model.number="maxLlmCalls" type="number" min="1" max="20" />
              </div>
            </div>
          </div>
        </div>

        <footer class="modal-footer">
          <button type="button" class="btn ghost" :disabled="submitting" @click="$emit('close')">
            Cancel
          </button>
          <button type="submit" class="btn primary" :disabled="submitting">
            <span v-if="submitting" class="spinner small"></span>
            {{ submitting ? "Starting Run…" : "Launch Live Comparison" }}
          </button>
        </footer>
      </form>
    </div>
  </div>
</template>
