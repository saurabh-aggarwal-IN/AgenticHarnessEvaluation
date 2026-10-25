export const HARNESS_COLORS = {
  h1: "#fbbf24",
  h2: "#2dd4bf",
  h3: "#a78bfa",
};

export const HARNESS_SOFT = {
  h1: "rgba(251, 191, 36, 0.16)",
  h2: "rgba(45, 212, 191, 0.16)",
  h3: "rgba(167, 139, 250, 0.16)",
};

export const HARNESS_NAMES = {
  h1: "H1 · Direct Hermes",
  h2: "H2 · Planner + Verifier",
  h3: "H3 · Multi-Agent Specialist",
};

export const HARNESS_SHORT = {
  h1: "H1",
  h2: "H2",
  h3: "H3",
};

export function harnessColor(id, index = 0) {
  if (HARNESS_COLORS[id]) return HARNESS_COLORS[id];
  const palette = ["#38bdf8", "#fb7185", "#f472b6", "#4ade80", "#facc15"];
  return palette[index % palette.length];
}

export function harnessSoft(id, index = 0) {
  if (HARNESS_SOFT[id]) return HARNESS_SOFT[id];
  const color = harnessColor(id, index);
  return color.replace(")", ", 0.14)").replace("rgb", "rgba");
}

export function harnessName(series) {
  return series?.harness_name || HARNESS_NAMES[series?.harness_id] || series?.harness_id || "Harness";
}

export function formatPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

export function formatScore(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

export function formatMs(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  const ms = Number(value);
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

export function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatShortDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function statusClass(status) {
  return String(status || "unknown").toLowerCase().replace(/[^a-z]+/g, "-");
}
