<script setup>
import { computed, onMounted, ref } from "vue";
import { api } from "../api";
import SearchableModelSelect from "./SearchableModelSelect.vue";

const props = defineProps({
  meta: { type: Object, default: () => ({}) },
  recentComparisons: { type: Array, default: () => [] },
});

const emit = defineEmits(["created", "selectRecent"]);

// View state: "welcome" (clean start screen with "+ New Project") or "wizard" (step-by-step guided flow)
const viewMode = ref("welcome");

// Form fields
const projectName = ref("Clinical Coding Multi-Harness Evaluation");
const repoUrl = ref("https://github.com/saurabh-aggarwal-IN/ClinicalCoderApp");
const gitToken = ref("");

// Up to 3 commit IDs with per-commit verification & worker unit state
const commits = ref([
  {
    id: "h1",
    label: "Baseline: Direct Hermes CLI",
    sha: "938bb8b",
    enabled: true,
    verifying: false,
    verified: null,
    verifyError: "",
    workerState: "idle",
    workerInfo: null,
    workerError: "",
  },
  {
    id: "h2",
    label: "Evolution 1: LangGraph Planner -> Coder -> Verifier",
    sha: "517120c",
    enabled: true,
    verifying: false,
    verified: null,
    verifyError: "",
    workerState: "idle",
    workerInfo: null,
    workerError: "",
  },
  {
    id: "h3",
    label: "Evolution 2: LangGraph Multi-Agent Specialist Loop",
    sha: "5bf9d16",
    enabled: true,
    verifying: false,
    verified: null,
    verifyError: "",
    workerState: "idle",
    workerInfo: null,
    workerError: "",
  },
]);

// Worker initiation state
const initiatingWorkers = ref(false);
const workersInitiated = ref(false);
const workerStatusList = ref([]);
const workerError = ref("");

// Step 2: Universal API endpoint
const apiEndpoint = ref("/v1/code");
const httpMethod = ref("POST");

// Step 3: Dataset and Ground Truth folders
const datasetFolder = ref("Dataset/cases");
const groundTruthFolder = ref("Dataset/expected");
const datasets = ref([]);
const selectedDataset = ref("");
const loadingDatasets = ref(false);

// Step 4: Multi-Select Evaluation Metrics
const availableMetrics = ref([
  {
    key: "accuracy",
    name: "Output Accuracy & F1",
    desc: "Precision, recall, and harmonic F1 score against ground truth target entities",
    selected: true,
  },
  {
    key: "exact_match",
    name: "Exact Set Match (P = E)",
    desc: "Binary 1.0 / 0.0 indicator of complete prediction set concordance",
    selected: true,
  },
  {
    key: "safety",
    name: "Guardrail Safety & Trap Avoidance",
    desc: "Absence of disallowed outputs, toxic responses, or out-of-bounds agent actions",
    selected: true,
  },
  {
    key: "provenance",
    name: "Source Provenance & Citation Check",
    desc: "Verbatim verification of source document quotes or evidence supporting generated outputs",
    selected: true,
  },
  {
    key: "latency",
    name: "Execution Latency & Speed",
    desc: "Mean and P95 wall-clock response time per evaluation case",
    selected: true,
  },
  {
    key: "cost",
    name: "Token Usage & Inference Cost",
    desc: "Input and output token volume and inference cost across evaluated models",
    selected: true,
  },
  // Demonstrated as Custom Defined Metrics (Domain Demo):
  {
    key: "history_specificity",
    name: "Longitudinal Context Retention",
    desc: "Demo custom metric: Verifies accurate extraction and retention of prior chronic conditions (e.g. C06 renal status from encounter history)",
    strategy: "rule_check",
    threshold: "100% retention",
    selected: true,
    isCustom: true,
    isDemo: true,
  },
  {
    key: "clinical_upcoding_trap",
    name: "Medical Upcoding & Unconfirmed Trap",
    desc: "Demo custom metric: Zero-tolerance rule penalizing unconfirmed secondary diagnoses or hazardous billing traps",
    strategy: "rule_check",
    threshold: "0 violations",
    selected: true,
    isCustom: true,
    isDemo: true,
  },
  {
    key: "clinical_coding_guidelines",
    name: "Clinical Guideline Adherence",
    desc: "Demo custom metric: LLM judge evaluating diagnostic sequencing, ICD-10/CPT guidelines, and doctor note justification",
    strategy: "llm_judge",
    threshold: ">= 0.90 score",
    selected: false,
    isCustom: true,
    isDemo: true,
  },
]);

// Custom Metric Authoring State
const showCustomMetricModal = ref(false);
const newMetricName = ref("");
const newMetricKey = ref("");
const newMetricDesc = ref("");
const newMetricStrategy = ref("llm_judge");
const newMetricThreshold = ref("");
const newMetricError = ref("");

function applyMetricTemplate(templateKey) {
  if (templateKey === "history") {
    newMetricName.value = "Longitudinal Context Retention";
    newMetricKey.value = "history_specificity";
    newMetricDesc.value = "Verifies accurate extraction and retention of prior chronic conditions across encounter history turns (e.g. C06 renal status).";
    newMetricStrategy.value = "rule_check";
    newMetricThreshold.value = "100% retention";
  } else if (templateKey === "upcoding") {
    newMetricName.value = "Medical Upcoding & Unconfirmed Trap";
    newMetricKey.value = "clinical_upcoding_trap";
    newMetricDesc.value = "Zero-tolerance guardrail checking that unconfirmed secondary diagnoses or hazardous billing traps are not assigned.";
    newMetricStrategy.value = "rule_check";
    newMetricThreshold.value = "0 violations";
  } else if (templateKey === "guideline") {
    newMetricName.value = "Clinical Documentation Adherence";
    newMetricKey.value = "clinical_coding_guidelines";
    newMetricDesc.value = "LLM judge evaluating diagnostic sequencing, ICD-10 official coding guidelines, and justification against doctor notes.";
    newMetricStrategy.value = "llm_judge";
    newMetricThreshold.value = ">= 0.90 score";
  }
}

function openCustomMetricModal() {
  newMetricName.value = "";
  newMetricKey.value = "";
  newMetricDesc.value = "";
  newMetricStrategy.value = "llm_judge";
  newMetricThreshold.value = "";
  newMetricError.value = "";
  showCustomMetricModal.value = true;
}

function onMetricNameInput() {
  if (!newMetricKey.value || newMetricKey.value === slugify(newMetricName.value.slice(0, -1))) {
    newMetricKey.value = slugify(newMetricName.value);
  }
}

function slugify(text) {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\w\s-]/g, "")
    .replace(/[\s_-]+/g, "_")
    .replace(/^-+|-+$/g, "");
}

function addCustomMetric() {
  const name = newMetricName.value.trim();
  let key = newMetricKey.value.trim();
  const desc = newMetricDesc.value.trim();
  const strategy = newMetricStrategy.value;
  const threshold = newMetricThreshold.value.trim();

  if (!name) {
    newMetricError.value = "Metric display name is required.";
    return;
  }
  if (!key) {
    key = slugify(name);
  }
  if (!key) {
    newMetricError.value = "Metric key identifier is required.";
    return;
  }

  if (availableMetrics.value.some((m) => m.key === key)) {
    newMetricError.value = `Metric key '${key}' already exists. Please choose a unique key.`;
    return;
  }

  availableMetrics.value.push({
    key,
    name,
    desc: desc || `Custom ${strategy.replace('_', ' ')} evaluation dimension`,
    strategy,
    threshold: threshold || undefined,
    selected: true,
    isCustom: true,
  });

  showCustomMetricModal.value = false;
}

function deleteCustomMetric(key) {
  availableMetrics.value = availableMetrics.value.filter((m) => m.key !== key);
}

// Step 5: Gateway Provider & Live Models
const gatewayProvider = ref("openrouter");
const defaultBaseUrls = {
  openrouter: "https://openrouter.ai/api/v1",
  trufoundry: "https://llm-gateway.truefoundry.com/api/inference/openai/v1",
  freellmapi: "http://192.168.29.43:3001/v1",
  custom: "",
};
const gatewayBaseUrl = ref("https://openrouter.ai/api/v1");
const gatewayApiKey = ref(localStorage.getItem("clinical_gw_key_openrouter") || "");
const showApiKey = ref(false);

const availableGatewayModels = ref([]);
const fetchingModels = ref(false);
const modelsFetched = ref(false);
const liveFetchedStatus = ref(false);
const gatewayError = ref("");
const modelSearchQuery = ref("");

// Model Selection Mode: "per_harness" (different models per harness) or "uniform" (single model)
const modelSelectionMode = ref("per_harness");
const selectedModel = ref("anthropic/claude-3.5-sonnet");

// Per-harness assignments
const harnessModels = ref({
  h1: "anthropic/claude-3.5-sonnet",
  h2: "meta-llama/llama-3.3-70b-instruct",
  h3: "deepseek/deepseek-chat",
});

const trials = ref(1);
const concurrency = ref(1);
const maxWallSeconds = ref(300);
const maxLlmCalls = ref(8);
const submitting = ref(false);
const error = ref("");

function selectGatewayProvider(provider) {
  gatewayProvider.value = provider;
  gatewayBaseUrl.value = defaultBaseUrls[provider] || "";
  gatewayApiKey.value = localStorage.getItem(`clinical_gw_key_${provider}`) || "";
  gatewayError.value = "";
  modelsFetched.value = false;
  availableGatewayModels.value = [];
}

async function fetchModels() {
  fetchingModels.value = true;
  gatewayError.value = "";
  try {
    if (gatewayApiKey.value.trim()) {
      localStorage.setItem(`clinical_gw_key_${gatewayProvider.value}`, gatewayApiKey.value.trim());
    }
    const res = await api.gatewayModels({
      provider: gatewayProvider.value,
      base_url: gatewayBaseUrl.value.trim() || undefined,
      api_key: gatewayApiKey.value.trim() || undefined,
    });
    availableGatewayModels.value = res.models || [];
    liveFetchedStatus.value = !!res.live_fetched;
    modelsFetched.value = true;
    if (res.error) {
      gatewayError.value = res.error;
    }

    if (availableGatewayModels.value.length > 0) {
      const gemma = availableGatewayModels.value.find((m) => m.id === "gemma-4-31b")?.id;
      const first = gemma || availableGatewayModels.value[0].id;
      const second = availableGatewayModels.value[1]?.id || first;
      const third = availableGatewayModels.value[2]?.id || first;

      if (!availableGatewayModels.value.some((m) => m.id === selectedModel.value)) {
        selectedModel.value = first;
      }
      if (!availableGatewayModels.value.some((m) => m.id === harnessModels.value.h1)) {
        harnessModels.value.h1 = first;
      }
      if (!availableGatewayModels.value.some((m) => m.id === harnessModels.value.h2)) {
        harnessModels.value.h2 = second;
      }
      if (!availableGatewayModels.value.some((m) => m.id === harnessModels.value.h3)) {
        harnessModels.value.h3 = third;
      }
    }
  } catch (err) {
    gatewayError.value = err?.message || "Failed to fetch models from gateway.";
  } finally {
    fetchingModels.value = false;
  }
}

const filteredGatewayModels = computed(() => {
  const query = modelSearchQuery.value.trim().toLowerCase();
  if (!query) return availableGatewayModels.value;
  return availableGatewayModels.value.filter(
    (m) =>
      m.id.toLowerCase().includes(query) ||
      (m.name && m.name.toLowerCase().includes(query)),
  );
});

// Presets
function loadClinicalPreset() {
  projectName.value = "Clinical Coder Multi-Harness Benchmark";
  repoUrl.value = "https://github.com/saurabh-aggarwal-IN/ClinicalCoderApp";
  commits.value = [
    {
      id: "h1",
      label: "Baseline: Direct Hermes CLI",
      sha: "938bb8b",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    },
    {
      id: "h2",
      label: "Evolution 1: LangGraph Planner -> Coder -> Verifier",
      sha: "517120c",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    },
    {
      id: "h3",
      label: "Evolution 2: LangGraph Multi-Agent Specialist Loop",
      sha: "5bf9d16",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    },
  ];
  apiEndpoint.value = "/v1/code";
  datasetFolder.value = "Dataset/cases";
  groundTruthFolder.value = "Dataset/expected";
  selectAllMetrics();
  workersInitiated.value = false;
  workerStatusList.value = [];
  gatewayProvider.value = "freellmapi";
  gatewayBaseUrl.value = defaultBaseUrls.freellmapi;
  gatewayApiKey.value = "freellmapi-25c013abda015aebe25ddfe5e5911208d11a243079111ef6";
  selectedModel.value = "gemma-4-31b";
  harnessModels.value = {
    h1: "gemma-4-31b",
    h2: "gemma-4-31b",
    h3: "gemma-4-31b",
  };
}

function loadCustomPreset() {
  projectName.value = "Custom GitHub Agent Architecture Evaluation";
  repoUrl.value = "https://github.com/";
  commits.value = [
    {
      id: "h1",
      label: "Baseline Architecture (Commit A)",
      sha: "",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    },
    {
      id: "h2",
      label: "Candidate Architecture (Commit B)",
      sha: "",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    },
    {
      id: "h3",
      label: "Optimized Architecture (Commit C)",
      sha: "",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    },
  ];
  apiEndpoint.value = "/v1/agent/run";
  datasetFolder.value = "tests/test_cases";
  groundTruthFolder.value = "tests/gold_standard";
  workersInitiated.value = false;
  workerStatusList.value = [];
}

function addCommitSlot() {
  if (commits.value.length < 3) {
    const nextIdx = commits.value.length + 1;
    commits.value.push({
      id: `h${nextIdx}`,
      label: `Candidate Architecture (Commit ${String.fromCharCode(64 + nextIdx)})`,
      sha: "",
      enabled: true,
      verifying: false,
      verified: null,
      verifyError: "",
      workerState: "idle",
      workerInfo: null,
      workerError: "",
    });
  }
}

function removeCommitSlot(index) {
  if (commits.value.length > 1) {
    commits.value.splice(index, 1);
  }
}

// Per-Commit Verification
async function verifyCommitSlot(c) {
  if (!c.sha?.trim()) {
    c.verifyError = "Please enter a commit SHA or branch.";
    return;
  }
  c.verifying = true;
  c.verifyError = "";
  try {
    const res = await api.verifyCommit({
      repo_url: repoUrl.value.trim(),
      commit: c.sha.trim(),
      git_token: gitToken.value.trim() || undefined,
    });
    if (res.accessible) {
      c.verified = res;
      c.verifyError = "";
    } else {
      c.verified = null;
      c.verifyError = res.error || `Commit '${c.sha}' could not be accessed.`;
    }
  } catch (err) {
    c.verified = null;
    c.verifyError = err?.message || "Failed to verify commit.";
  } finally {
    c.verifying = false;
  }
}

function handleCommitBlur(c) {
  if (c.sha?.trim() && !c.verified && !c.verifying) {
    verifyCommitSlot(c);
  }
}

// Per-Commit Worker Unit Start / Stop
async function startWorkerSlot(c) {
  if (!c.sha?.trim()) {
    c.workerError = "Please enter a commit SHA first.";
    return;
  }
  c.workerState = "starting";
  c.workerError = "";
  try {
    const res = await api.startWorkerUnit({
      repo_url: repoUrl.value.trim(),
      commit: c.sha.trim(),
      harness_id: c.id,
      slot_name: c.label || c.id.toUpperCase(),
      git_token: gitToken.value.trim() || undefined,
    });
    c.workerInfo = res;
    c.workerState = "running";
    c.workerError = "";

    const existingIdx = workerStatusList.value.findIndex((w) => w.harness_id === c.id);
    const statusObj = {
      slot: c.label || c.id.toUpperCase(),
      harness_id: c.id,
      commit: c.sha.trim(),
      status: "ready",
      container: res.container,
      redis_channel: res.redis_channel,
      message: res.message,
    };
    if (existingIdx >= 0) {
      workerStatusList.value[existingIdx] = statusObj;
    } else {
      workerStatusList.value.push(statusObj);
    }
    workersInitiated.value = true;
  } catch (err) {
    c.workerState = "idle";
    c.workerError = err?.message || "Failed to initiate worker unit.";
  }
}

async function stopWorkerSlot(c) {
  c.workerState = "stopping";
  c.workerError = "";
  try {
    await api.stopWorkerUnit({
      harness_id: c.id,
      commit: c.sha.trim(),
      container: c.workerInfo?.container,
    });
    c.workerState = "idle";
    c.workerInfo = null;
    workerStatusList.value = workerStatusList.value.filter((w) => w.harness_id !== c.id);
    if (!workerStatusList.value.length) {
      workersInitiated.value = false;
    }
  } catch (err) {
    c.workerError = err?.message || "Failed to stop worker unit.";
    c.workerState = "running";
  }
}

// Bulk Worker Initiation
async function initiateWorkers() {
  const activeCommits = commits.value.filter((c) => c.enabled && c.sha.trim());
  if (!repoUrl.value.trim()) {
    workerError.value = "Please provide a valid GitHub repository link first.";
    return;
  }
  if (!activeCommits.length) {
    workerError.value = "Please enter at least one commit ID to allocate workers.";
    return;
  }

  initiatingWorkers.value = true;
  workerError.value = "";
  try {
    // Ensure all commit codes are verified and downloaded to disk before moving on
    for (const c of activeCommits) {
      if (!c.verified) {
        await verifyCommitSlot(c);
        if (c.verifyError) {
          workerError.value = `Verification / download failed for ${c.label || c.id.toUpperCase()}: ${c.verifyError}`;
          initiatingWorkers.value = false;
          return;
        }
      }
    }

    const res = await api.initiateWorkers({
      repo_url: repoUrl.value.trim(),
      commits: activeCommits.map((c) => c.sha.trim()),
      git_token: gitToken.value.trim() || null,
    });

    workerStatusList.value = res.workers || [];
    workersInitiated.value = true;

    for (const w of res.workers || []) {
      const match = commits.value.find((c) => c.id === w.harness_id);
      if (match) {
        match.workerState = "running";
        match.workerInfo = {
          container: w.container,
          redis_channel: w.redis_channel,
          message: w.message,
          status: w.status,
        };
      }
    }
  } catch (cause) {
    workerError.value = cause?.message || "Failed to initiate Docker/Redis workers.";
  } finally {
    initiatingWorkers.value = false;
  }
}

// Metric Multi-Select
function toggleMetric(metric) {
  metric.selected = !metric.selected;
}

function selectAllMetrics() {
  for (const m of availableMetrics.value) m.selected = true;
}

function deselectAllMetrics() {
  for (const m of availableMetrics.value) m.selected = false;
}

const selectedMetricsCount = computed(
  () => availableMetrics.value.filter((m) => m.selected).length,
);

// Datasets
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
  modelsFetched.value = false;
});

// Run Analysis
async function runAnalysis() {
  if (!projectName.value.trim()) {
    error.value = "Please specify a project / comparison name.";
    return;
  }
  if (!repoUrl.value.trim()) {
    error.value = "Please specify a target GitHub repository link.";
    return;
  }

  const activeCommits = commits.value.filter((c) => c.enabled && c.sha.trim());
  if (activeCommits.length < 2) {
    error.value = "Please provide at least 2 distinct commit IDs to compare architectures.";
    return;
  }

  if (!selectedMetricsCount.value) {
    error.value = "Please select at least one evaluation metric to score.";
    return;
  }

  // Model assignment
  const harnessModelMap = {};
  let modelsList = [];

  if (modelSelectionMode.value === "per_harness") {
    harnessModelMap.h1 = harnessModels.value.h1?.trim() || selectedModel.value;
    harnessModelMap.h2 = harnessModels.value.h2?.trim() || selectedModel.value;
    harnessModelMap.h3 = harnessModels.value.h3?.trim() || selectedModel.value;
    modelsList = Array.from(
      new Set([harnessModelMap.h1, harnessModelMap.h2, harnessModelMap.h3]),
    ).filter(Boolean);
  } else {
    const m = selectedModel.value?.trim();
    if (!m) {
      error.value = "Please select an active LLM model from the dropdown.";
      return;
    }
    harnessModelMap.h1 = m;
    harnessModelMap.h2 = m;
    harnessModelMap.h3 = m;
    modelsList = [m];
  }

  if (!modelsList.length) {
    error.value = "Please select at least one active LLM model.";
    return;
  }

  // Save API key if entered
  if (gatewayApiKey.value.trim()) {
    try {
      localStorage.setItem(`clinical_gw_key_${gatewayProvider.value}`, gatewayApiKey.value.trim());
    } catch {
      /* ignore */
    }
  }

  submitting.value = true;
  error.value = "";

  // Ensure all active commit codes are verified and downloaded to disk before benchmark launch
  for (const c of activeCommits) {
    if (!c.verified) {
      await verifyCommitSlot(c);
      if (c.verifyError) {
        error.value = `Cannot run analysis: commit '${c.sha}' could not be verified or downloaded: ${c.verifyError}`;
        submitting.value = false;
        return;
      }
    }
  }

  // If commits are fewer than 3, ensure keys exist for CommitSet
  const commitPayload = {
    h1: activeCommits[0]?.sha?.trim() || "",
    h2: activeCommits[1]?.sha?.trim() || activeCommits[0]?.sha?.trim() || "",
    h3: activeCommits[2]?.sha?.trim() || activeCommits[1]?.sha?.trim() || "",
  };

  try {
    const payload = {
      name: projectName.value.trim(),
      repo_url: repoUrl.value.trim(),
      commits: commitPayload,
      dataset_id: selectedDataset.value || "synthetic-clinical-coding-v1",
      models: modelsList,
      harness_models: harnessModelMap,
      gateway: {
        provider: gatewayProvider.value,
        base_url: gatewayBaseUrl.value.trim(),
        api_key: gatewayApiKey.value.trim() || undefined,
      },
      trials: Number(trials.value) || 1,
      concurrency: Number(concurrency.value) || 1,
      budget: {
        max_wall_seconds: Number(maxWallSeconds.value) || 300,
        max_llm_calls: Number(maxLlmCalls.value) || 8,
      },
      notes: JSON.stringify({
        api_endpoint: apiEndpoint.value,
        dataset_folder: datasetFolder.value,
        ground_truth_folder: groundTruthFolder.value,
        selected_metrics: availableMetrics.value.filter((m) => m.selected).map((m) => m.key),
        custom_metrics: availableMetrics.value.filter((m) => m.selected && m.isCustom).map((m) => ({
          key: m.key,
          name: m.name,
          desc: m.desc,
          strategy: m.strategy,
          threshold: m.threshold,
        })),
        gateway_provider: gatewayProvider.value,
        model_selection_mode: modelSelectionMode.value,
        harness_models: harnessModelMap,
      }),
    };

    const created = await api.createComparison(payload);
    emit("created", created.id);
  } catch (cause) {
    error.value = cause?.message || "Failed to start evaluation run.";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="task-studio">
    <!-- ============================================================
         STATE A: WELCOME / CLEAN START SCREEN (Zero Logs)
         ============================================================ -->
    <div v-if="viewMode === 'welcome'" class="welcome-screen">
      <div class="welcome-hero">
        <div class="welcome-badge">
          <span class="pulse-dot"></span>
          <span>Agent Evals Platform · Multi-Commit Isolation</span>
        </div>
        <h1>Agent Harness Comparison Studio</h1>
        <p class="welcome-sub">
          Benchmark autonomous AI agent architectures across Git commits, isolated Docker workers, and live model backends.
          Zero synthetic mocks — all evaluation runs execute in deterministic containerized sandboxes with statistical confidence bounds.
        </p>

        <div class="welcome-actions">
          <button type="button" class="btn primary btn-large" @click="viewMode = 'wizard'">
            <svg viewBox="0 0 20 20" aria-hidden="true" style="width:18px;height:18px;">
              <path d="M10 4v12m-6-6h12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
            </svg>
            + New Project
          </button>
          <button type="button" class="btn ghost btn-large" @click="loadClinicalPreset(); viewMode = 'wizard';">
            🏥 Load Clinical Coder Preset
          </button>
        </div>
      </div>

      <!-- Quick 4-Step Process Overview -->
      <div class="flow-preview-grid">
        <div class="flow-step-card">
          <div class="step-num-pill">1</div>
          <div class="step-card-content">
            <strong>Repo & Commits</strong>
            <p>Define repository & up to 3 commit IDs; initiate Redis & Docker workers in parallel.</p>
          </div>
        </div>

        <div class="flow-step-card">
          <div class="step-num-pill">2</div>
          <div class="step-card-content">
            <strong>Universal API</strong>
            <p>Single common endpoint (e.g. <code>POST /v1/code</code>) called across all commits.</p>
          </div>
        </div>

        <div class="flow-step-card">
          <div class="step-num-pill">3</div>
          <div class="step-card-content">
            <strong>Dataset & Truth</strong>
            <p>Input synthetic cases and ground truth folders with gold codes & trap constraints.</p>
          </div>
        </div>

        <div class="flow-step-card">
          <div class="step-num-pill">4</div>
          <div class="step-card-content">
            <strong>Metrics & Analysis</strong>
            <p>Select evaluation dimensions, run live LLM jobs, and render 8-axis radar profiles.</p>
          </div>
        </div>
      </div>

      <!-- Recent Comparisons Banner (If Any) -->
      <div v-if="recentComparisons && recentComparisons.length" class="recent-projects-panel">
        <div class="recent-header">
          <h3>Recent Project Runs</h3>
          <span class="count-badge">{{ recentComparisons.length }} runs</span>
        </div>
        <div class="recent-list">
          <div
            v-for="item in recentComparisons.slice(0, 3)"
            :key="item.id"
            class="recent-item"
            @click="emit('selectRecent', item.id)"
          >
            <div class="recent-meta">
              <strong>{{ item.name }}</strong>
              <span>{{ item.id }} · {{ item.dataset_name || item.dataset_id }}</span>
            </div>
            <div class="recent-actions">
              <span class="status-pill-small" :class="item.status?.toLowerCase()">{{ item.status }}</span>
              <span class="view-link">View Analysis →</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ============================================================
         STATE B: STEP-BY-STEP GUIDED WIZARD
         ============================================================ -->
    <div v-else class="wizard-container">
      <div class="wizard-topbar">
        <button type="button" class="btn ghost small" @click="viewMode = 'welcome'">
          ← Back to Project Hub
        </button>
        <div class="wizard-title-group">
          <h2>Configure New Evaluation Project</h2>
          <span>Step-by-step setup for isolated multi-commit benchmarking</span>
        </div>
        <div class="preset-pill-group">
          <button type="button" class="btn ghost small" @click="loadClinicalPreset">
            🏥 Clinical Preset
          </button>
          <button type="button" class="btn ghost small" @click="loadCustomPreset">
            ⚡ Custom Preset
          </button>
        </div>
      </div>

      <form class="studio-form" @submit.prevent="runAnalysis">
        <div v-if="error" class="error-banner">
          <strong>Validation Error:</strong> {{ error }}
        </div>

        <!-- STEP 1: Repository, Commits, and Worker Initiation -->
        <section class="form-section">
          <div class="section-head-row">
            <div class="section-indicator">Step 1</div>
            <div>
              <h3>GitHub Repository & Architecture Commits</h3>
              <p>Specify the target GitHub repository and up to 3 distinct commit IDs. Click <strong>Initiate Workers</strong> to spin up Docker workers connected to Redis.</p>
            </div>
          </div>

          <div class="form-grid-2">
            <div class="form-group flex-2">
              <label for="wiz-repo">GitHub Repository Link</label>
              <input
                id="wiz-repo"
                v-model="repoUrl"
                type="text"
                placeholder="https://github.com/org/agent-repo"
                required
              />
            </div>
            <div class="form-group flex-1">
              <label for="wiz-name">Project / Evaluation Name</label>
              <input
                id="wiz-name"
                v-model="projectName"
                type="text"
                placeholder="e.g. Agent Multi-Harness Benchmark or Clinical Coding Evaluation"
                required
              />
            </div>
          </div>

          <!-- Commit ID Slots (Up to 3) -->
          <div class="commit-slots-header">
            <h4>Commit Revisions to Benchmark (Up to 3)</h4>
            <button
              v-if="commits.length < 3"
              type="button"
              class="btn small ghost"
              @click="addCommitSlot"
            >
              + Add Commit ID
            </button>
          </div>

          <div class="commit-slots">
            <div
              v-for="(c, idx) in commits"
              :key="c.id"
              class="commit-card"
              :class="{ 'worker-active': c.workerState === 'running' }"
            >
              <div class="commit-card-head">
                <label class="commit-checkbox-label" title="Enable or disable this commit in evaluation">
                  <input type="checkbox" v-model="c.enabled" class="commit-checkbox" />
                  <span class="commit-badge">{{ c.id.toUpperCase() }}</span>
                </label>
                <input
                  v-model="c.label"
                  type="text"
                  class="commit-label-input"
                  placeholder="Architecture label (e.g. H1 · Direct Hermes CLI)"
                />
                <button
                  v-if="commits.length > 1"
                  type="button"
                  class="btn-icon-danger"
                  title="Remove this commit slot"
                  @click="removeCommitSlot(idx)"
                >
                  ✕
                </button>
              </div>

              <div class="commit-card-body">
                <div class="commit-input-actions-row">
                  <div class="input-with-label flex-1">
                    <span class="input-prefix">Git Commit SHA / Branch</span>
                    <input
                      v-model="c.sha"
                      type="text"
                      placeholder="e.g. 938bb8b or master"
                      class="mono-input"
                      required
                      @input="c.verified = null; c.verifyError = ''"
                      @blur="handleCommitBlur(c)"
                    />
                  </div>

                  <div class="commit-btn-group">
                    <!-- Verify Button -->
                    <button
                      type="button"
                      class="btn ghost small btn-verify"
                      :disabled="c.verifying || !c.sha?.trim()"
                      @click="verifyCommitSlot(c)"
                      title="Verify and automatically download code to local disk if missing"
                    >
                      <svg v-if="c.verifying" viewBox="0 0 20 20" class="spin" style="width:13px;height:13px;margin-right:4px;">
                        <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                      </svg>
                      <span v-else style="margin-right:4px;">🔍</span>
                      {{ c.verifying ? "Checking & Downloading..." : "Verify" }}
                    </button>

                    <!-- Start / Stop Button -->
                    <button
                      v-if="c.workerState !== 'running'"
                      type="button"
                      class="btn primary small btn-start-worker"
                      :disabled="c.workerState === 'starting' || !c.sha?.trim()"
                      @click="startWorkerSlot(c)"
                      title="Initiate isolated worker unit container for this commit"
                    >
                      <svg v-if="c.workerState === 'starting'" viewBox="0 0 20 20" class="spin" style="width:13px;height:13px;margin-right:4px;">
                        <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                      </svg>
                      <span v-else style="margin-right:4px;">▶</span>
                      {{ c.workerState === 'starting' ? "Initiating..." : "Start" }}
                    </button>
                    <button
                      v-else
                      type="button"
                      class="btn small btn-stop-worker"
                      :disabled="c.workerState === 'stopping'"
                      @click="stopWorkerSlot(c)"
                      title="Stop and release this worker unit container"
                    >
                      <svg v-if="c.workerState === 'stopping'" viewBox="0 0 20 20" class="spin" style="width:13px;height:13px;margin-right:4px;">
                        <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                      </svg>
                      <span v-else style="margin-right:4px;">⏹</span>
                      {{ c.workerState === 'stopping' ? "Stopping..." : "Stop" }}
                    </button>
                  </div>
                </div>

                <!-- Verified Success Pill -->
                <div v-if="c.verified" class="commit-verify-success">
                  <span class="verify-icon">✓</span>
                  <div class="verify-meta">
                    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
                      <span class="verify-title">Accessible commit: <code>{{ c.verified.full_sha?.slice(0, 10) || c.sha }}</code></span>
                      <span v-if="c.verified.downloaded" class="badge-disk badge-downloaded">⚡ Downloaded to disk</span>
                      <span v-else-if="c.verified.cached_locally" class="badge-disk badge-cached">💾 Ready on disk</span>
                    </div>
                    <span v-if="c.verified.author" class="verify-sub">by {{ c.verified.author }} · {{ c.verified.date }}</span>
                    <span v-if="c.verified.message" class="verify-msg">"{{ c.verified.message }}"</span>
                    <span v-if="c.verified.local_path" class="verify-path">Local directory: <code>{{ c.verified.local_path }}</code></span>
                  </div>
                </div>

                <!-- Verified Error Pill -->
                <div v-if="c.verifyError" class="commit-verify-error">
                  <span class="error-icon">✕</span>
                  <span>{{ c.verifyError }}</span>
                </div>

                <!-- Worker Unit Live Status Banner -->
                <div v-if="c.workerState === 'running' && c.workerInfo" class="worker-unit-live-banner">
                  <span class="live-pulse-dot"></span>
                  <div class="worker-unit-info">
                    <strong>Worker Unit Active:</strong>
                    <code>{{ c.workerInfo.container }}</code>
                    <span class="worker-channel">bound to Redis <code>{{ c.workerInfo.redis_channel }}</code></span>
                  </div>
                  <span class="worker-status-chip ready">Ready</span>
                </div>

                <!-- Worker Error Banner -->
                <div v-if="c.workerError" class="commit-verify-error">
                  <span class="error-icon">⚠️</span>
                  <span>{{ c.workerError }}</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Worker Initiation Button -->
          <div class="worker-initiate-bar">
            <button
              type="button"
              class="btn primary"
              :disabled="initiatingWorkers || !repoUrl.trim()"
              @click="initiateWorkers"
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" style="width:16px;height:16px;" :class="{ spin: initiatingWorkers }">
                <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
              </svg>
              {{ initiatingWorkers ? "Initiating Workers on Docker & Redis..." : "🚀 Initiate Redis & Docker Workers" }}
            </button>
            <span class="initiate-hint">
              {{ workersInitiated ? "✓ All workers successfully initiated on Docker and bound to Redis queues." : "Click to allocate isolated worker sandboxes for each commit revision." }}
            </span>
          </div>

          <div v-if="workerError" class="error-banner small">
            <strong>Worker Allocation Error:</strong> {{ workerError }}
          </div>

          <!-- Live Worker Status Cards -->
          <div v-if="workerStatusList.length" class="worker-status-grid">
            <div
              v-for="w in workerStatusList"
              :key="w.slot"
              class="worker-status-card"
            >
              <div class="worker-card-head">
                <span class="status-indicator-dot ready"></span>
                <strong>{{ w.slot }} (Commit {{ w.commit.slice(0, 7) }})</strong>
                <span class="badge-status-ready">{{ w.status }}</span>
              </div>
              <div class="worker-card-details">
                <div><span>Container:</span> <code>{{ w.container }}</code></div>
                <div><span>Redis Queue:</span> <code>{{ w.redis_channel }}</code></div>
              </div>
            </div>
          </div>
        </section>

        <!-- STEP 2: Universal API Endpoint -->
        <section class="form-section">
          <div class="section-head-row">
            <div class="section-indicator">Step 2</div>
            <div>
              <h3>Target Agent API Endpoint</h3>
              <p>Define the single universal API endpoint link common across all three commits.</p>
            </div>
          </div>

          <div class="form-grid-2">
            <div class="form-group flex-2">
              <label for="wiz-endpoint">API Link / Endpoint URL</label>
              <div class="input-with-method">
                <span class="method-tag">{{ httpMethod }}</span>
                <input
                  id="wiz-endpoint"
                  v-model="apiEndpoint"
                  type="text"
                  placeholder="/v1/code or http://127.0.0.1:8000/v1/code"
                  required
                />
              </div>
            </div>
            <div class="api-contract-box flex-2">
              <strong>Universal Contract Principle:</strong>
              <p>The caller calls this identical endpoint regardless of harness. The checked-out commit inside each worker sandbox determines whether H1, H2, or H3 logic executes.</p>
            </div>
          </div>
        </section>

        <!-- STEP 3: Test Dataset & Ground Truth -->
        <section class="form-section">
          <div class="section-head-row">
            <div class="section-indicator">Step 3</div>
            <div>
              <h3>Synthetic Test Dataset & Ground Truth Paths</h3>
              <p>Provide the synthetic patient case notes folder and the corresponding ground truth reference folder.</p>
            </div>
          </div>

          <div class="form-grid-2">
            <div class="form-group">
              <label for="wiz-dataset-folder">Synthetic Dataset (Cases) Folder</label>
              <input
                id="wiz-dataset-folder"
                v-model="datasetFolder"
                type="text"
                placeholder="e.g. Dataset/cases"
                required
              />
              <span class="input-subtext">Folder containing JSON encounter notes (e.g. C01.json – C10.json).</span>
            </div>

            <div class="form-group">
              <label for="wiz-ground-truth-folder">Ground Truth Reference Folder</label>
              <input
                id="wiz-ground-truth-folder"
                v-model="groundTruthFolder"
                type="text"
                placeholder="e.g. Dataset/expected"
                required
              />
              <span class="input-subtext">Folder containing ground truth targets, forbidden failure traps, and reference rationale.</span>
            </div>
          </div>
        </section>

        <!-- STEP 4: Multi-Select Evaluation Metrics & Custom Definition -->
        <section class="form-section">
          <div class="section-head-row">
            <div class="section-indicator">Step 4</div>
            <div class="flex-1">
              <div class="metric-head-flex">
                <div>
                  <h3>Evaluation Metrics (Multi-Select & Custom)</h3>
                  <p>Choose benchmark dimensions or define custom evaluation rubrics for multi-agent architectures (including domain demo metrics).</p>
                </div>
                <div class="metric-quick-actions">
                  <button type="button" class="btn ghost small" @click="selectAllMetrics">Select All</button>
                  <button type="button" class="btn ghost small" @click="deselectAllMetrics">Clear</button>
                  <button type="button" class="btn primary small btn-add-custom-metric" @click="openCustomMetricModal">
                    <svg viewBox="0 0 20 20" aria-hidden="true" style="width:13px;height:13px;">
                      <path d="M10 4v12m-6-6h12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
                    </svg>
                    + Define Custom Metric
                  </button>
                  <span class="metric-selected-badge">{{ selectedMetricsCount }} / {{ availableMetrics.length }} Selected</span>
                </div>
              </div>
            </div>
          </div>

          <div class="metric-selection-grid">
            <div
              v-for="m in availableMetrics"
              :key="m.key"
              class="metric-pill-card"
              :class="{ active: m.selected, 'is-custom-metric': m.isCustom }"
              @click="toggleMetric(m)"
            >
              <div class="metric-check-box">
                <span v-if="m.selected">✓</span>
              </div>
              <div class="metric-info">
                <div class="metric-title-row">
                  <strong>{{ m.name }}</strong>
                  <span v-if="m.isDemo" class="badge-demo-metric">Clinical Demo</span>
                  <span v-else-if="m.isCustom" class="badge-custom-metric">Custom</span>
                </div>
                <p>{{ m.desc }}</p>
                <div v-if="m.strategy" class="metric-strategy-tag">
                  <span class="strategy-icon">⚙</span> Strategy: {{ m.strategy.replace('_', ' ') }}
                  <span v-if="m.threshold" class="strategy-threshold">· Target: {{ m.threshold }}</span>
                </div>
              </div>
              <button
                v-if="m.isCustom"
                type="button"
                class="btn-delete-metric"
                title="Remove metric"
                @click.stop="deleteCustomMetric(m.key)"
              >
                ✕
              </button>
            </div>
          </div>
        </section>

        <!-- STEP 5: Live Gateway Provider & Model Selection -->
        <section class="form-section">
          <div class="section-head-row">
            <div class="section-indicator">Step 5</div>
            <div class="flex-1">
              <div class="metric-head-flex">
                <div>
                  <h3>Live Gateway Provider & Model Selection</h3>
                  <p>Configure the OpenAI-compatible gateway, enter your API key, and assign live models per harness.</p>
                </div>
                <div v-if="modelsFetched" class="gateway-live-pill" :class="{ 'pill-live': liveFetchedStatus, 'pill-fallback': !liveFetchedStatus }">
                  <span class="status-indicator-dot" :class="liveFetchedStatus ? 'ready' : 'pending'"></span>
                  <span>{{ liveFetchedStatus ? 'Live Provider Connection' : 'Curated Fallback List' }} ({{ availableGatewayModels.length }} models)</span>
                </div>
              </div>
            </div>
          </div>

          <!-- Provider Cards -->
          <div class="gateway-provider-grid">
            <div
              class="provider-card"
              :class="{ active: gatewayProvider === 'openrouter' }"
              @click="selectGatewayProvider('openrouter')"
            >
              <div class="provider-card-head">
                <span class="provider-radio" :class="{ selected: gatewayProvider === 'openrouter' }"></span>
                <strong>OpenRouter</strong>
                <span class="badge-tag">Cloud · 400+ Models</span>
              </div>
              <p class="provider-desc">Claude 3.5 Sonnet, Llama 3.3 70B, GPT-4o, Gemini 2.0 Flash, DeepSeek</p>
            </div>

            <div
              class="provider-card"
              :class="{ active: gatewayProvider === 'trufoundry' }"
              @click="selectGatewayProvider('trufoundry')"
            >
              <div class="provider-card-head">
                <span class="provider-radio" :class="{ selected: gatewayProvider === 'trufoundry' }"></span>
                <strong>TruFoundry</strong>
                <span class="badge-tag">Managed Gateway</span>
              </div>
              <p class="provider-desc">Enterprise LLM proxy with custom routes and fine-tuned deployments</p>
            </div>

            <div
              class="provider-card"
              :class="{ active: gatewayProvider === 'freellmapi' }"
              @click="selectGatewayProvider('freellmapi')"
            >
              <div class="provider-card-head">
                <span class="provider-radio" :class="{ selected: gatewayProvider === 'freellmapi' }"></span>
                <strong>FreeLLMAPI</strong>
                <span class="badge-tag">Free Gateway</span>
              </div>
              <p class="provider-desc">FreeLLMAPI gateway (192.168.29.43:3001) proxying OpenAI-compatible requests (tashfeenahmed/freellmapi)</p>
            </div>

            <div
              class="provider-card"
              :class="{ active: gatewayProvider === 'custom' }"
              @click="selectGatewayProvider('custom')"
            >
              <div class="provider-card-head">
                <span class="provider-radio" :class="{ selected: gatewayProvider === 'custom' }"></span>
                <strong>Custom Gateway</strong>
                <span class="badge-tag">Self-Hosted</span>
              </div>
              <p class="provider-desc">Any OpenAI-compatible /v1 endpoint (vLLM, TGI, LMStudio, LiteLLM)</p>
            </div>
          </div>

          <!-- Gateway Credentials & Endpoint Row -->
          <div class="gateway-auth-box">
            <div class="form-grid-2">
              <div class="form-group flex-2">
                <label for="wiz-gw-key">
                  Provider API Key
                  <span class="label-hint">({{ gatewayProvider === 'freellmapi' ? 'Optional for local network' : 'Required for live queries' }})</span>
                </label>
                <div class="input-with-button">
                  <input
                    id="wiz-gw-key"
                    v-model="gatewayApiKey"
                    :type="showApiKey ? 'text' : 'password'"
                    :placeholder="gatewayProvider === 'openrouter' ? 'sk-or-v1-...' : gatewayProvider === 'trufoundry' ? 'tf-...' : 'Enter provider API key...'"
                    class="mono-input"
                    @keyup.enter="fetchModels"
                  />
                  <button
                    type="button"
                    class="btn ghost small"
                    title="Toggle API Key visibility"
                    @click="showApiKey = !showApiKey"
                  >
                    {{ showApiKey ? 'Hide' : 'Show' }}
                  </button>
                  <button
                    type="button"
                    class="btn primary small"
                    :disabled="fetchingModels"
                    @click="fetchModels"
                  >
                    <svg viewBox="0 0 20 20" aria-hidden="true" style="width:14px;height:14px;" :class="{ spin: fetchingModels }">
                      <path d="M10 2v3m0 10v3m8-8h-3M5 10H2m13.7-5.7-2.1 2.1M6.4 13.6l-2.1 2.1m11.4 0-2.1-2.1M6.4 6.4 4.3 4.3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
                    </svg>
                    {{ fetchingModels ? 'Fetching...' : '🔍 Fetch Available Models' }}
                  </button>
                </div>
                <span class="input-subtext">Keys are stored securely in local session and passed to isolated workers for live inference.</span>
              </div>

              <div class="form-group flex-1">
                <label for="wiz-gw-url">Provider Base URL</label>
                <input
                  id="wiz-gw-url"
                  v-model="gatewayBaseUrl"
                  type="text"
                  placeholder="https://openrouter.ai/api/v1"
                  class="mono-input"
                />
                <span class="input-subtext">OpenAI-compatible <code>/v1</code> root URL.</span>
              </div>
            </div>

            <div v-if="gatewayError" class="warning-banner small">
              <strong>Gateway Notice:</strong> {{ gatewayError }}
            </div>
          </div>

          <!-- Model Selection Mode Toggle -->
          <div class="model-mode-section">
            <div class="model-mode-header">
              <div>
                <h4>Model Assignment Strategy</h4>
                <p>Choose whether to test all architectures against the identical model or assign different models per harness revision.</p>
              </div>
              <div class="toggle-button-group">
                <button
                  type="button"
                  class="btn-toggle"
                  :class="{ active: modelSelectionMode === 'uniform' }"
                  @click="modelSelectionMode = 'uniform'"
                >
                  Uniform Model
                </button>
                <button
                  type="button"
                  class="btn-toggle"
                  :class="{ active: modelSelectionMode === 'per_harness' }"
                  @click="modelSelectionMode = 'per_harness'"
                >
                  ⚡ Different Models per Harness
                </button>
              </div>
            </div>

            <!-- UNIFORM MODEL SELECTION -->
            <div v-if="modelSelectionMode === 'uniform'" class="uniform-model-box">
              <label for="wiz-uniform-model">Universal Model (Applied to all harnesses)</label>
              <div class="model-select-row">
                <SearchableModelSelect
                  id="wiz-uniform-model"
                  v-model="selectedModel"
                  :models="availableGatewayModels"
                  :disabled="!modelsFetched || fetchingModels"
                  placeholder="Search and select model..."
                  disabled-hint="Click 'Fetch Available Models' above to unlock"
                />
              </div>
              <span v-if="!modelsFetched" class="model-select-hint">
                🔒 Model selection is disabled. Please click "Fetch Available Models" above to query your gateway for live models.
              </span>
            </div>

            <!-- PER-HARNESS MODEL SELECTION -->
            <div v-else class="per-harness-model-grid">
              <div
                v-for="(c, idx) in commits"
                :key="c.id"
                class="harness-model-card"
              >
                <div class="harness-model-head">
                  <span class="commit-badge">Harness {{ idx + 1 }}</span>
                  <div class="harness-meta">
                    <strong>{{ c.label || `Commit ${c.id.toUpperCase()}` }}</strong>
                    <span class="harness-sha">Commit: <code>{{ c.sha ? c.sha.slice(0, 7) : 'default' }}</code></span>
                  </div>
                </div>

                <div class="harness-model-body">
                  <label :for="`wiz-model-${c.id}`">Assigned Model Backend</label>
                  <SearchableModelSelect
                    :id="`wiz-model-${c.id}`"
                    v-model="harnessModels[c.id]"
                    :models="availableGatewayModels"
                    :disabled="!modelsFetched || fetchingModels"
                    placeholder="Search and select model..."
                    disabled-hint="Click 'Fetch Available Models' above to unlock"
                  />
                  <span class="assigned-model-preview">
                    Selected: <code>{{ harnessModels[c.id] || 'None' }}</code>
                  </span>
                </div>
              </div>
            </div>
          </div>

          <!-- Execution Budget & Concurrency Clamps -->
          <div class="form-grid-3 budget-row">
            <div class="form-group">
              <label for="wiz-trials">Stochastic Trials (N)</label>
              <input
                id="wiz-trials"
                v-model="trials"
                type="number"
                min="1"
                max="5"
                placeholder="1"
              />
              <span class="input-subtext">Repeated runs per case to evaluate variance.</span>
            </div>

            <div class="form-group">
              <label for="wiz-concurrency">Execution Concurrency</label>
              <input
                id="wiz-concurrency"
                v-model="concurrency"
                type="number"
                min="1"
                max="8"
                placeholder="1"
              />
              <span class="input-subtext">Parallel in-flight jobs (1 recommended for rate-limited models).</span>
            </div>

            <div class="form-group">
              <label for="wiz-wall">Max Wall-Clock (s)</label>
              <input
                id="wiz-wall"
                v-model="maxWallSeconds"
                type="number"
                min="30"
                max="600"
                placeholder="300"
              />
              <span class="input-subtext">Worker container timeout limit.</span>
            </div>
          </div>
        </section>

        <!-- Launch Footer -->
        <div class="studio-footer-cta">
          <div class="launch-summary">
            <strong>Ready to Execute Evaluation</strong>
            <span>
              {{ commits.filter((c) => c.enabled && c.sha.trim()).length }} commit architectures ·
              10 benchmark cases ·
              {{ selectedMetricsCount }} metrics ·
              {{ gatewayProvider.toUpperCase() }} ({{ modelSelectionMode === 'per_harness' ? 'Per-Harness Models: H1: ' + (harnessModels.h1?.split('/')?.pop() || 'auto') + ', H2: ' + (harnessModels.h2?.split('/')?.pop() || 'auto') + ', H3: ' + (harnessModels.h3?.split('/')?.pop() || 'auto') : 'Uniform Model: ' + selectedModel }})
            </span>
          </div>

          <button
            type="submit"
            class="btn primary btn-large"
            :disabled="submitting"
          >
            <svg viewBox="0 0 20 20" aria-hidden="true" style="width:18px;height:18px;" :class="{ spin: submitting }">
              <path d="M5 4l11 6-11 6V4z" fill="currentColor" />
            </svg>
            {{ submitting ? "Dispatching Evaluation Jobs..." : "▶ Run Analysis & Benchmark" }}
          </button>
        </div>
      </form>

      <!-- ============================================================
           CUSTOM METRIC DEFINITION MODAL (TELEPORTED POPUP)
           ============================================================ -->
      <Teleport to="body">
        <div
          v-if="showCustomMetricModal"
          class="modal-backdrop custom-metric-backdrop"
          @click.self="showCustomMetricModal = false"
        >
          <div class="custom-metric-modal-card">
            <div class="custom-metric-modal-header">
              <div class="custom-metric-modal-icon">
                <span>📐</span>
              </div>
              <div>
                <h3>Define Custom Evaluation Metric</h3>
                <p>Create a specialized scoring dimension, constraint test, or LLM-judge rubric for your agent.</p>
              </div>
              <button
                type="button"
                class="modal-close-btn"
                aria-label="Close modal"
                @click="showCustomMetricModal = false"
              >
                ✕
              </button>
            </div>

            <div class="custom-metric-modal-body">
              <!-- Demo Quick Templates Bar -->
              <div class="metric-template-pills">
                <span>Quick Demo Templates:</span>
                <button
                  type="button"
                  class="btn-template-pill"
                  @click="applyMetricTemplate('history')"
                >
                  + Context History Retention
                </button>
                <button
                  type="button"
                  class="btn-template-pill"
                  @click="applyMetricTemplate('upcoding')"
                >
                  + Upcoding &amp; Fraud Trap
                </button>
                <button
                  type="button"
                  class="btn-template-pill"
                  @click="applyMetricTemplate('guideline')"
                >
                  + Guideline LLM Judge
                </button>
              </div>

              <div class="form-group">
                <label for="custom-metric-name">Metric Display Name <span class="required-star">*</span></label>
                <input
                  id="custom-metric-name"
                  v-model="newMetricName"
                  type="text"
                  placeholder="e.g. Hallucination Penalty or Context Retention"
                  @input="onMetricNameInput"
                />
              </div>

              <div class="form-grid-2">
                <div class="form-group">
                  <label for="custom-metric-key">Key / Identifier <span class="required-star">*</span></label>
                  <input
                    id="custom-metric-key"
                    v-model="newMetricKey"
                    type="text"
                    placeholder="e.g. hallucination_penalty"
                    class="mono-input"
                  />
                  <span class="input-subtext">Unique lowercase slug for report aggregation.</span>
                </div>

                <div class="form-group">
                  <label for="custom-metric-strategy">Evaluation Strategy</label>
                  <select id="custom-metric-strategy" v-model="newMetricStrategy">
                    <option value="llm_judge">LLM Judge & Rubric (Semantic verification)</option>
                    <option value="rule_check">Rule / Constraint Check (Regex or code rule)</option>
                    <option value="concordance">Concordance Ratio (Precision & recall)</option>
                    <option value="latency_cost">Efficiency / Resource Budget</option>
                  </select>
                </div>
              </div>

              <div class="form-group">
                <label for="custom-metric-desc">Metric Description / Rubric Instructions</label>
                <textarea
                  id="custom-metric-desc"
                  v-model="newMetricDesc"
                  rows="3"
                  placeholder="Detail what this metric evaluates and how violations or scores are calculated against input cases..."
                ></textarea>
              </div>

              <div class="form-group">
                <label for="custom-metric-threshold">Pass / Success Threshold (Optional)</label>
                <input
                  id="custom-metric-threshold"
                  v-model="newMetricThreshold"
                  type="text"
                  placeholder="e.g. >= 0.85 or 0 violations"
                />
              </div>

              <div v-if="newMetricError" class="modal-error-banner">
                {{ newMetricError }}
              </div>
            </div>

            <div class="custom-metric-modal-actions">
              <button
                type="button"
                class="btn ghost"
                @click="showCustomMetricModal = false"
              >
                Cancel
              </button>
              <button
                type="button"
                class="btn primary"
                @click="addCustomMetric"
              >
                Add Evaluation Metric
              </button>
            </div>
          </div>
        </div>
      </Teleport>
    </div>
  </div>
</template>

<style scoped>
.gateway-provider-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

@media (max-width: 1024px) {
  .gateway-provider-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.provider-card {
  padding: 14px 16px;
  background: var(--card-bg, #ffffff);
  border: 1.5px solid var(--border, #e2e8f0);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.provider-card:hover {
  border-color: var(--teal, #0d9488);
  box-shadow: 0 2px 6px rgba(13, 148, 136, 0.08);
}

.provider-card.active {
  border-color: var(--teal, #0d9488);
  background: var(--panel, #f8fafc);
  box-shadow: 0 2px 8px rgba(13, 148, 136, 0.12);
}

.provider-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.provider-radio {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--border-strong, #cbd5e1);
  display: grid;
  place-items: center;
}

.provider-radio.selected {
  border-color: var(--teal, #0d9488);
  background: var(--teal, #0d9488);
  box-shadow: inset 0 0 0 2px #ffffff;
}

.provider-card-head strong {
  font-size: 13.5px;
  font-weight: 650;
  color: var(--text, #0f172a);
}

.badge-tag {
  font-size: 9.5px;
  font-family: var(--mono, monospace);
  color: var(--teal, #0d9488);
  background: rgba(13, 148, 136, 0.1);
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 600;
  margin-left: auto;
}

.provider-desc {
  margin: 0;
  font-size: 11px;
  color: var(--muted, #64748b);
  line-height: 1.45;
}

.gateway-auth-box {
  background: var(--card-bg, #ffffff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  padding: 16px 18px;
  margin-bottom: 20px;
}

.input-with-button {
  display: flex;
  align-items: center;
  gap: 6px;
}

.input-with-button input {
  flex: 1;
}

.gateway-live-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 99px;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--mono, monospace);
}

.pill-live {
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.25);
}

.pill-fallback {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
  border: 1px solid rgba(245, 158, 11, 0.25);
}

.model-mode-section {
  background: var(--panel, #f8fafc);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  padding: 18px 20px;
  margin-bottom: 20px;
}

.model-mode-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.model-mode-header h4 {
  margin: 0 0 2px 0;
  font-size: 13.5px;
  font-weight: 700;
  color: var(--text, #0f172a);
}

.model-mode-header p {
  margin: 0;
  font-size: 11.5px;
  color: var(--muted, #64748b);
}

.toggle-button-group {
  display: flex;
  background: var(--border, #e2e8f0);
  padding: 3px;
  border-radius: 8px;
  gap: 2px;
}

.btn-toggle {
  padding: 5px 12px;
  font-size: 11.5px;
  font-weight: 600;
  border: none;
  background: transparent;
  color: var(--muted, #64748b);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-toggle.active {
  background: #ffffff;
  color: var(--teal, #0d9488);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.uniform-model-box {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.model-select-row {
  display: flex;
  gap: 10px;
}

.model-select-input {
  flex: 2;
  height: 40px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--border-strong, #cbd5e1);
  background: #ffffff;
  font-family: var(--mono, monospace);
  font-size: 12.5px;
  color: var(--text, #0f172a);
}

.custom-slug-input {
  flex: 1;
  height: 40px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--border-strong, #cbd5e1);
  font-family: var(--mono, monospace);
  font-size: 12px;
}

.per-harness-model-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

@media (max-width: 900px) {
  .per-harness-model-grid {
    grid-template-columns: 1fr;
  }
}

.harness-model-card {
  background: #ffffff;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  padding: 14px 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.harness-model-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.harness-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.harness-meta strong {
  font-size: 12.5px;
  color: var(--text, #0f172a);
}

.harness-sha {
  font-size: 10.5px;
  color: var(--muted, #64748b);
}

.harness-sha code {
  font-family: var(--mono, monospace);
  color: var(--teal, #0d9488);
}

.harness-model-body {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.harness-model-body label {
  font-size: 11px;
  font-weight: 650;
  color: var(--text-soft, #334155);
}

.assigned-model-preview {
  font-size: 10.5px;
  color: var(--muted, #64748b);
  margin-top: 4px;
}

.assigned-model-preview code {
  font-family: var(--mono, monospace);
  font-weight: 600;
  color: var(--teal, #0d9488);
}

.budget-row {
  margin-top: 14px;
}

.warning-banner {
  margin-top: 10px;
  padding: 8px 12px;
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 6px;
  color: #b45309;
  font-size: 11.5px;
}
</style>
