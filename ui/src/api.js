const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
const TOKEN_KEY = "harness_compare_api_token";

let token = "";
try {
  token = localStorage.getItem(TOKEN_KEY) || "";
} catch {
  token = "";
}

export function setApiToken(value) {
  token = (value || "").trim();
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage can be unavailable in private mode */
  }
}

export function getApiToken() {
  return token;
}

async function request(path, options = {}) {
  const headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (cause) {
    const error = new Error(
      "Cannot reach the orchestrator API. Start it on 127.0.0.1:8080.",
    );
    error.cause = cause;
    error.offline = true;
    throw error;
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      (detail && typeof detail === "object" ? detail.message : detail) ||
      (typeof payload === "string" ? payload : "") ||
      `Request failed with HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.payload = payload;
    if (response.status === 401) {
      error.unauthorized = true;
    }
    throw error;
  }

  return payload;
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  health: () => request("/health"),
  meta: () => request("/v1/meta"),
  datasets: () => request("/v1/datasets"),
  verifyCommit: (body) =>
    request("/v1/git/verify-commit", { method: "POST", body: JSON.stringify(body) }),
  startWorkerUnit: (body) =>
    request("/v1/workers/unit/start", { method: "POST", body: JSON.stringify(body) }),
  stopWorkerUnit: (body) =>
    request("/v1/workers/unit/stop", { method: "POST", body: JSON.stringify(body) }),
  initiateWorkers: (body) =>
    request("/v1/workers/initiate", { method: "POST", body: JSON.stringify(body) }),
  killAllWorkers: () =>
    request("/v1/workers/kill-all", { method: "POST" }),
  gatewayModels: (body) =>
    request("/v1/gateway/models", { method: "POST", body: JSON.stringify(body) }),
  comparisons: (params) => request(`/v1/comparisons${query(params)}`),
  comparison: (id) => request(`/v1/comparisons/${encodeURIComponent(id)}`),
  createComparison: (body) =>
    request("/v1/comparisons", { method: "POST", body: JSON.stringify(body) }),
  cancelComparison: (id) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  deleteComparison: (id) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}`, { method: "DELETE" }),
  deleteJobs: (id) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}/jobs`, { method: "DELETE" }),
  retryOrphaned: (id) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}/retry-orphaned`, { method: "POST" }),
  retryComparison: (id, options = {}) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}/retry`, {
      method: "POST",
      body: JSON.stringify(options),
    }),
  retryJob: (comparisonId, jobId) =>
    request(`/v1/comparisons/${encodeURIComponent(comparisonId)}/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
    }),
  jobWorkerStatus: (comparisonId, jobId) =>
    request(`/v1/comparisons/${encodeURIComponent(comparisonId)}/jobs/${encodeURIComponent(jobId)}/worker-status`),
  restartJobWorker: (comparisonId, jobId) =>
    request(`/v1/comparisons/${encodeURIComponent(comparisonId)}/jobs/${encodeURIComponent(jobId)}/restart-worker`, {
      method: "POST",
    }),
  radar: (id) => request(`/v1/comparisons/${encodeURIComponent(id)}/radar`),
  jobs: (id, params) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}/jobs${query(params)}`),
  events: (id, params) =>
    request(`/v1/comparisons/${encodeURIComponent(id)}/events${query(params)}`),
  exportUrl: (id) => `${API_BASE}/v1/comparisons/${encodeURIComponent(id)}/export?format=json`,
  exportCsvUrl: (id) => `${API_BASE}/v1/comparisons/${encodeURIComponent(id)}/export?format=csv`,
};
