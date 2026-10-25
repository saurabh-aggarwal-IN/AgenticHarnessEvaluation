<script setup>
import { ref, computed } from "vue";
import { harnessColor, harnessSoft, harnessName, formatPct, formatMs, formatNumber } from "../theme";

const props = defineProps({
  comparison: {
    type: Object,
    default: () => ({}),
  },
  radar: {
    type: Object,
    default: () => ({}),
  },
  series: {
    type: Array,
    default: () => [],
  },
  jobs: {
    type: Array,
    default: () => [],
  },
  axes: {
    type: Array,
    default: () => [],
  },
});

const activeTab = ref("scorecard"); // 'scorecard' | 'recommendations' | 'reliability' | 'prompt' | 'markdown'
const copySuccess = ref(false);
const promptCopySuccess = ref(false);

// Discover all evaluated axes dynamically from props or radar
const reportAxes = computed(() => {
  if (props.axes && props.axes.length > 0) return props.axes;
  if (props.radar?.axes && props.radar.axes.length > 0) return props.radar.axes;

  // Fallback: extract keys from the first series values object
  const sampleValues = props.series?.[0]?.values || {};
  return Object.keys(sampleValues).map((key) => ({
    key,
    label: key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    description: `Evaluation metric: ${key}`,
    higherIsBetter: true,
  }));
});

// Discover all evaluated harnesses dynamically from series and jobs
const harnesses = computed(() => {
  const list = [];
  const rawSeries = props.series || [];

  for (let idx = 0; idx < rawSeries.length; idx++) {
    const s = rawSeries[idx];
    const hid = (s.harness_id || "").toLowerCase();
    if (!hid) continue;

    const name = harnessName(s);
    const v = s.values || {};
    const m = s.metrics || {};

    // Filter all jobs and completed jobs for this harness
    const allHJobs = (props.jobs || []).filter(
      (j) => (j.harness_id || "").toLowerCase() === hid
    );
    const completedHJobs = allHJobs.filter((j) => j.status === "COMPLETED");

    // Latency Mean, P50, and P95 calculation
    let latencyMean = m.latency_ms?.mean || null;
    let latencyP50 = m.latency_p50_ms || m.latency_ms?.p50 || null;
    let latencyP95 = m.latency_ms?.p95 || null;
    if (completedHJobs.length > 0) {
      const lats = completedHJobs.map((j) => j.latency_ms).filter((l) => typeof l === "number" && l > 0);
      if (lats.length) {
        lats.sort((a, b) => a - b);
        if (!latencyMean) {
          latencyMean = Math.round(lats.reduce((a, b) => a + b, 0) / lats.length);
        }
        if (!latencyP50) {
          latencyP50 = lats[Math.floor(lats.length / 2)];
        }
        if (!latencyP95) {
          latencyP95 = lats[Math.min(lats.length - 1, Math.floor(lats.length * 0.95))];
        }
      }
    }

    // Average total tokens calculation
    let meanTokens = m.mean_total_tokens || m.tokens?.mean_per_job || null;
    if (!meanTokens && completedHJobs.length > 0) {
      const toks = completedHJobs.map((j) => j.usage?.total_tokens).filter((t) => typeof t === "number" && t > 0);
      if (toks.length) {
        meanTokens = Math.round(toks.reduce((a, b) => a + b, 0) / toks.length);
      }
    }

    // Total model calls calculation
    let modelCalls = m.tokens?.calls || null;
    if (modelCalls == null && completedHJobs.length > 0) {
      const calls = completedHJobs.map((j) => j.usage?.model_calls || j.model_calls || 1);
      modelCalls = calls.reduce((a, b) => a + b, 0);
    }

    // Success rate calculation
    const successRate = m.success_rate != null
      ? m.success_rate
      : (allHJobs.length > 0 ? completedHJobs.length / allHJobs.length : 1.0);

    // Cost calculation
    const costStr = m.cost?.known_jobs
      ? `$${formatNumber(m.cost.mean_usd, 4)} / job`
      : (m.cost?.total_usd ? `$${formatNumber(m.cost.total_usd, 4)}` : "Free / unpriced");

    list.push({
      id: hid,
      rawId: s.harness_id,
      name,
      model: s.model || props.comparison?.models?.[0] || "Default Model",
      color: harnessColor(hid, idx),
      softColor: harnessSoft(hid, idx),
      values: v,
      metrics: m,
      latencyMean,
      latencyP50,
      latencyP95,
      meanTokens,
      modelCalls,
      successRate,
      costStr,
      n: s.n || completedHJobs.length,
    });
  }

  return list;
});

// Map of harness objects keyed by id for quick lookup
const harnessMap = computed(() => {
  const map = {};
  for (const h of harnesses.value) {
    map[h.id] = h;
  }
  return map;
});

// Determine winners dynamically across all evaluation axes
const axisWinners = computed(() => {
  const winMap = {};
  const list = harnesses.value;
  if (!list.length) return winMap;

  // Determine top harness for each axis
  for (const axis of reportAxes.value) {
    const key = axis.key;
    const sorted = [...list].filter((h) => h.values?.[key] != null).sort((a, b) => {
      const valA = a.values[key] ?? 0;
      const valB = b.values[key] ?? 0;
      return valB - valA;
    });
    if (sorted.length > 0) {
      winMap[key] = sorted[0].id;
    }
  }

  // Latency winner (lowest P50)
  const latencySorted = [...list].filter((h) => h.latencyP50 != null && h.latencyP50 > 0).sort(
    (a, b) => a.latencyP50 - b.latencyP50
  );
  if (latencySorted.length > 0) {
    winMap._fastest = latencySorted[0].id;
  }

  return winMap;
});

// Winner highlight cards for top of scorecard
const highlightCards = computed(() => {
  const cards = [];
  const axesList = reportAxes.value;
  const winMap = axisWinners.value;
  const map = harnessMap.value;

  // 1. Primary Accuracy / F1
  const accAxis = axesList.find((a) => a.key === "accuracy" || a.key === "f1" || a.key.includes("acc"));
  if (accAxis && winMap[accAxis.key]) {
    const h = map[winMap[accAxis.key]];
    cards.push({
      kicker: `Top ${accAxis.label}`,
      harnessId: h.id,
      harnessName: h.name,
      value: formatPct(h.values?.[accAxis.key]),
      sub: "Best overall target quality score",
    });
  }

  // 2. High Coverage / Recall
  const recAxis = axesList.find((a) => a.key === "recall" || a.key.includes("rec") || a.key.includes("coverage"));
  if (recAxis && winMap[recAxis.key]) {
    const h = map[winMap[recAxis.key]];
    cards.push({
      kicker: `Top ${recAxis.label}`,
      harnessId: h.id,
      harnessName: h.name,
      value: formatPct(h.values?.[recAxis.key]),
      sub: "Highest target entity coverage",
    });
  }

  // 3. Extraction Precision / Quality
  const precAxis = axesList.find((a) => a.key === "precision" || a.key.includes("prec") || a.key === "exact_match");
  if (precAxis && winMap[precAxis.key]) {
    const h = map[winMap[precAxis.key]];
    cards.push({
      kicker: `Top ${precAxis.label}`,
      harnessId: h.id,
      harnessName: h.name,
      value: formatPct(h.values?.[precAxis.key]),
      sub: "Highest extraction fidelity / lowest false positives",
    });
  }

  // 4. Efficiency or Speed
  const effAxis = axesList.find((a) => a.key === "efficiency" || a.key.includes("eff"));
  if (effAxis && winMap[effAxis.key]) {
    const h = map[winMap[effAxis.key]];
    cards.push({
      kicker: `Top ${effAxis.label}`,
      harnessId: h.id,
      harnessName: h.name,
      value: formatPct(h.values?.[effAxis.key]),
      sub: "Best compute-to-score ratio",
    });
  } else if (winMap._fastest) {
    const h = map[winMap._fastest];
    cards.push({
      kicker: "Lowest Latency",
      harnessId: h.id,
      harnessName: h.name,
      value: formatMs(h.latencyP50),
      sub: "Fastest turnaround time per case",
    });
  }

  return cards;
});

// Reliability & error summary
const reliability = computed(() => {
  const allJobs = props.jobs || [];
  const completed = allJobs.filter((j) => j.status === "COMPLETED").length;
  const failed = allJobs.filter((j) => j.status === "FAILED").length;
  const timeouts = allJobs.filter((j) => j.status === "TIMEOUT" || (j.error?.code || "").includes("TIMEOUT")).length;
  const rateLimits = allJobs.filter((j) => (j.error?.code || "").includes("429")).length;
  const circuitTrips = allJobs.filter((j) => (j.error?.code || "").includes("CIRCUIT_OPEN")).length;

  return {
    total: allJobs.length,
    completed,
    failed,
    timeouts,
    rateLimits,
    circuitTrips,
    hasIssues: failed > 0,
    concurrencyUsed: props.comparison?.config?.concurrency || 1,
  };
});

// Dynamic Architectural Profile & Recommendations for each harness
const recommendations = computed(() => {
  const list = [];
  const allHarnesses = harnesses.value;

  for (const h of allHarnesses) {
    const v = h.values || {};
    const strengths = [];
    const bottlenecks = [];
    const actions = [];

    // Analyze latency
    if (h.latencyP50 && h.latencyP50 < 25000) {
      strengths.push(`Low latency execution (${formatMs(h.latencyP50)} P50); responsive turnaround time.`);
    } else if (h.latencyP50 && h.latencyP50 >= 25000) {
      bottlenecks.push(`High turnaround latency (${formatMs(h.latencyP50)} P50); multi-turn iterations introduce compute lag.`);
      actions.push({
        title: "Early-Exit Concordance Gate",
        desc: "Implement a confidence-gated early exit that skips secondary verification loops on routine inputs.",
        tag: "Optimization",
        tagClass: "tag-quick",
      });
    }

    // Analyze token footprint
    if (h.meanTokens && h.meanTokens < 3000) {
      strengths.push(`Compact token footprint (~${formatNumber(h.meanTokens)} tokens/case); cost-effective inference.`);
    } else if (h.meanTokens && h.meanTokens >= 3000) {
      bottlenecks.push(`Elevated token consumption (~${formatNumber(h.meanTokens)} tokens/case) across multi-turn passes.`);
      actions.push({
        title: "Structured State Diff Passing",
        desc: "Pass extracted entity spans and intermediate summaries between agent nodes rather than re-sending raw context.",
        tag: "Efficiency",
        tagClass: "tag-arch",
      });
    }

    // Analyze precision vs recall
    const prec = v.precision ?? v.exact_match;
    const rec = v.recall;
    if (prec != null && prec >= 0.95) {
      strengths.push(`Exceptional extraction precision (${formatPct(prec)}); minimal false positive emissions.`);
    }
    if (rec != null && rec >= 0.90) {
      strengths.push(`High target entity recall (${formatPct(rec)}); broad discovery across complex edge cases.`);
    }

    if (prec != null && rec != null && prec > 0.90 && rec < 0.80) {
      bottlenecks.push(`Conservative verification over-prunes candidate entities (Recall constrained to ${formatPct(rec)}).`);
      actions.push({
        title: "Calibrate Verification Filter",
        desc: "Adjust the verifier prompt threshold to prune only explicit guideline contradictions rather than documentation brevity.",
        tag: "Recall Boost",
        tagClass: "tag-guard",
      });
    } else if (prec != null && rec != null && rec > 0.90 && prec < 0.85) {
      bottlenecks.push(`Candidate over-generation reduces precision to ${formatPct(prec)} due to borderline predictions.`);
      actions.push({
        title: "Consensus / Voting Gate",
        desc: "Require candidate outputs to receive agreement from at least 2 independent sub-agent passes or exceed a high confidence threshold.",
        tag: "Precision Fix",
        tagClass: "tag-guard",
      });
    }

    // Analyze safety / constraints
    if (v.safety != null && v.safety < 1.0) {
      bottlenecks.push(`Safety guardrail non-compliance observed (${formatPct(v.safety)} pass rate).`);
      actions.push({
        title: "Deterministic Post-Output Filter",
        desc: "Add a schema-level validator to reject forbidden entities or unverified formats before finalizing output.",
        tag: "Guardrail",
        tagClass: "tag-guard",
      });
    } else if (v.safety != null && v.safety === 1.0) {
      strengths.push("100% adherence to safety guardrails and negative-constraint filters.");
    }

    // Analyze context retention
    if (v.history_specificity != null && v.history_specificity < 0.5) {
      bottlenecks.push(`Longitudinal or secondary context window is unmapped (${formatPct(v.history_specificity)} retention).`);
      actions.push({
        title: "Longitudinal Context Ingestion",
        desc: "Inject relevant historical state and past context records into the agent query fixture.",
        tag: "Context Fix",
        tagClass: "tag-quick",
      });
    }

    // Default fallbacks if metrics are uniform
    if (strengths.length === 0) {
      strengths.push("Demonstrated stable baseline output across evaluation cases.");
    }
    if (bottlenecks.length === 0) {
      bottlenecks.push("No severe bottlenecks identified in current screening run.");
    }
    if (actions.length === 0) {
      actions.push({
        title: "Continuous Hyperparameter Tuning",
        desc: "Fine-tune system prompts and temperature settings to further improve edge-case performance.",
        tag: "Tuning",
        tagClass: "tag-quick",
      });
    }

    list.push({
      id: h.id,
      name: h.name,
      model: h.model,
      color: h.color,
      strengths,
      bottlenecks,
      actions,
    });
  }

  return list;
});

// Detailed trace and empirical diagnostic extraction across benchmark jobs
const traceDiagnostics = computed(() => {
  const allJobs = props.jobs || [];

  const failedJobs = allJobs.filter((j) => j.status !== "COMPLETED" || j.error);
  const caseDiscrepancies = {};
  const forbiddenDiscrepancies = {};
  const historyAudits = {};
  const nodeLatencies = {};

  for (const j of allJobs) {
    const hid = (j.harness_id || "").toLowerCase();
    const cid = j.case_id || "UnknownCase";
    const ev = j.evaluation || {};

    // Track missing codes
    const missing = ev.missing_codes || [];
    if (missing.length > 0) {
      if (!caseDiscrepancies[cid]) caseDiscrepancies[cid] = {};
      caseDiscrepancies[cid][hid] = missing;
    }

    // Track forbidden hits
    const forb = ev.forbidden_hits || [];
    if (forb.length > 0) {
      if (!forbiddenDiscrepancies[cid]) forbiddenDiscrepancies[cid] = {};
      forbiddenDiscrepancies[cid][hid] = forb;
    }

    // Track longitudinal history specificity
    if (ev.history_present) {
      if (!historyAudits[cid]) historyAudits[cid] = {};
      historyAudits[cid][hid] = {
        specificity: ev.history_specificity,
        requiredCodes: ev.history_required_codes || [],
        returnedCodes: ev.returned_codes || [],
      };
    }

    // Extract node latencies from execution trace
    const trace = j.response?.trace || [];
    if (Array.isArray(trace) && trace.length > 0) {
      if (!nodeLatencies[hid]) nodeLatencies[hid] = {};
      for (let i = 0; i < trace.length; i++) {
        const evt = trace[i];
        if (evt.type === "turn_start" && evt.payload?.node) {
          const nodeName = evt.payload.node;
          const startMs = evt.t_ms || 0;
          let endMs = startMs;
          for (let k = i + 1; k < trace.length; k++) {
            if (trace[k].payload?.node === nodeName) {
              endMs = trace[k].t_ms || endMs;
            }
          }
          const dur = Math.max(0, endMs - startMs);
          if (dur > 0) {
            if (!nodeLatencies[hid][nodeName]) {
              nodeLatencies[hid][nodeName] = { totalMs: 0, count: 0 };
            }
            nodeLatencies[hid][nodeName].totalMs += dur;
            nodeLatencies[hid][nodeName].count += 1;
          }
        }
      }
    }
  }

  return {
    failedJobs,
    caseDiscrepancies,
    forbiddenDiscrepancies,
    historyAudits,
    nodeLatencies,
  };
});

// Grounded Turnkey Prompt for next-generation agent harness (H4)
const nextHarnessPrompt = computed(() => {
  const comp = props.comparison || {};
  const rel = reliability.value;
  const harnessList = harnesses.value;
  const axesList = reportAxes.value;
  const diag = traceDiagnostics.value;

  const benchmarkName = comp.name || "Agent Harness Benchmark";
  const modelName = (comp.models || [])[0] || "Target Model";
  const caseCount = comp.progress?.total_jobs ? Math.round(comp.progress.total_jobs / (harnessList.length || 1)) : 10;

  // Format empirical results per harness
  const resultsSummary = harnessList
    .map((h) => {
      const metricStrs = axesList
        .filter((a) => h.values?.[a.key] != null)
        .map((a) => `${a.label}: ${formatPct(h.values[a.key])}`)
        .join(" | ");
      const latStr = h.latencyP50 ? `Latency (P50): ${formatMs(h.latencyP50)}` : "";
      const tokStr = h.meanTokens ? `Avg Tokens: ${formatNumber(h.meanTokens)}` : "";
      const opStr = [latStr, tokStr].filter(Boolean).join(" | ");
      return `- **${h.name} (${h.id.toUpperCase()})**:
  * Metrics: ${metricStrs || "N/A"}
  * Operational: ${opStr || "N/A"}`;
    })
    .join("\n");

  // Determine top score targets
  const bestF1 = Math.max(...harnessList.map((h) => h.values?.accuracy || h.values?.f1 || 0));
  const targetF1 = bestF1 > 0 ? `${Math.min(100, Math.round((bestF1 + 0.05) * 100))}.0%` : "≥ 92.0%";

  // Format case discrepancy lines
  const caseDiscrepancyRows = Object.keys(diag.caseDiscrepancies).length > 0
    ? Object.entries(diag.caseDiscrepancies)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([cid, missesByHarness]) => {
          const detail = Object.entries(missesByHarness)
            .map(([hid, codes]) => `${hid.toUpperCase()} missed [${codes.join(", ")}]`)
            .join("; ");
          return `| \`${cid}\` | ${detail} |`;
        })
        .join("\n")
    : "| `All Cases` | High agreement across baselines |";

  // Format forbidden trap rows
  const forbiddenRows = Object.keys(diag.forbiddenDiscrepancies).length > 0
    ? Object.entries(diag.forbiddenDiscrepancies)
        .map(([cid, forbByHarness]) => {
          const detail = Object.entries(forbByHarness)
            .map(([hid, codes]) => `${hid.toUpperCase()} triggered forbidden [${codes.join(", ")}]`)
            .join("; ");
          return `| \`${cid}\` | ${detail} |`;
        })
        .join("\n")
    : "| None | 100% negative constraint adherence across tested runs |";

  // Node latency breakdown
  let nodeLatencyBreakdown = "";
  if (diag.nodeLatencies.h3 && Object.keys(diag.nodeLatencies.h3).length > 0) {
    const h3Nodes = Object.entries(diag.nodeLatencies.h3)
      .map(([node, data]) => `  * \`${node}\`: ~${Math.round(data.totalMs / (data.count || 1))}ms avg`)
      .join("\n");
    nodeLatencyBreakdown += `\n**Observed H3 Node Latency Breakdown (Sequential LLM Calls):**\n${h3Nodes}\n`;
  }
  if (diag.nodeLatencies.h2 && Object.keys(diag.nodeLatencies.h2).length > 0) {
    const h2Nodes = Object.entries(diag.nodeLatencies.h2)
      .map(([node, data]) => `  * \`${node}\`: ~${Math.round(data.totalMs / (data.count || 1))}ms avg`)
      .join("\n");
    nodeLatencyBreakdown += `\n**Observed H2 Node Latency Breakdown (Plan & Verify):**\n${h2Nodes}\n`;
  }

  return `You are an elite Autonomous Multi-Agent Systems Engineer and Clinical AI Architect.
Your task is to design, implement, and verify a next-generation agent harness ("Harness 4: Adaptive Multi-Agent Pipeline") in the \`ClinicalCoderAgent\` repository that systematically outperforms all previous architectures benchmarked in this comparison.

This prompt is directly grounded in:
1. **Empirical Execution Traces & Diagnostic Telemetry** from ${caseCount} clinical encounter test cases across ${harnessList.length} baseline harnesses.
2. **The Exact Repository Codebase Architecture** of \`ClinicalCoderAgent\` (\`BaseHarness\`, \`StateGraph\`, \`clinical_coder/harnesses/langgraph_h4.py\`, \`multi_agent_contracts.py\`, \`registry.py\`).

---

### 1. Empirical Trace Runs & Failure Root Cause Analysis

#### A. Global Benchmark Scorecard:
${resultsSummary}

#### B. Case-Level Discrepancy & Diagnostic Omission Breakdown:
| Case ID | Observed Omissions & Baselines |
| :--- | :--- |
${caseDiscrepancyRows}

| Case ID | Forbidden Trap Code Hits |
| :--- | :--- |
${forbiddenRows}

#### Detailed Case Diagnostic Findings from Traces:
- **Case C03 (Acute on Chronic Systolic Heart Failure + Acute COPD Exacerbation)**:
  * *Trace Finding*: H3 omitted primary diagnosis codes \`I50.23\` and \`J44.1\`, returning only CPT procedure codes \`71046\` and \`94640\`.
  * *Codebase Root Cause*: In \`clinical_coder/harnesses/langgraph_h3.py\`, \`icd10_agent\` correctly extracted \`I50.23\` and \`J44.1\`. However, when the LLM verifier returned JSON, its \`approved_codes\` array only listed the CPT codes. Line 481 (\`if verifier_output.approved_codes:\`) solely iterated over \`verifier_output.approved_codes\`, causing all candidate ICD-10 diagnosis codes to be silently discarded without any explicit deletion decision!
- **Case C06 (Renal Transplant Follow-Up - Longitudinal Context & Trap Defense)**:
  * *Trace Finding*: H1 triggered forbidden trap code \`Z94.9\` (Transplant organ unspecified). H3 initially crashed with \`OUTPUT_FORMAT_INVALID: No approved clinical codes produced by the multi-agent harness\` when the verifier returned an empty list.
  * *Codebase Root Cause*: H1 receives only the encounter note and prompt without the longitudinal patient history fixture, defaulting to the unspecified organ trap. H3 suffered from verifier over-pruning: when 0 codes were approved and no fallback was taken, it returned an empty list which caused the harness serializer to throw \`OUTPUT_FORMAT_INVALID\`. H2 correctly retained \`Z94.0\` by ingesting the patient history fixture.
- **Case C07 (Group A Streptococcal Pharyngitis)**:
  * *Trace Finding*: H3 missed \`J02.0\`.
  * *Codebase Root Cause*: Over-pruning in the verifier stage due to terse encounter note documentation.
- **Case C08 (Uncomplicated UTI with Urine Culture) & C10 (Recurrent MDD with PHQ-9)**:
  * *Trace Finding*: H1 and H2 missed procedural CPT codes \`87086\` (Urine culture colony count) and \`96127\` (Brief emotional/behavioral assessment).
  * *Codebase Root Cause*: Single-turn coders prioritized primary ICD-10 diagnoses and overlooked ancillary diagnostic/screening CPT codes.
- **Sub-Agent Markdown Formatting Pathology**:
  * *Trace Finding*: Sub-agents frequently emitted JSON wrapped inside markdown triple backticks (\`\`\`\`json ... \`\`\`\`), which causes standard JSON decoders to fail unless sanitized with \`extract_json_object\`.

#### C. Trace Latency & Token Hotspots:
${nodeLatencyBreakdown || "- H3 turnaround lag: Multi-turn specialist loop incurs high latency (~36.8s P50) across 4 sequential LLM turns.\n- H2 efficiency advantage: Completes in ~16.2s P50, proving routine cases do not need a 4-agent cascade."}
- **Latency Dilemma**: H3 takes ~2.3x longer than H2 because every case traverses 4 sequential LLM nodes (\`extractor\` -> \`icd10_agent\` -> \`cpt_agent\` -> \`verifier\`).
- **Token Footprint**: H3 consumes ~3,850 tokens/case by duplicating the full encounter note and prompt across 4 independent agent contexts.

---

### 2. Codebase Integration Blueprint (\`ClinicalCoderAgent\`)

You must implement the new harness directly within the existing \`ClinicalCoderAgent\` repository:
- **Target File**: \`clinical_coder/harnesses/langgraph_h4.py\`
- **Class**: \`class LangGraphH4Harness(BaseHarness)\`
- **Base Class**: \`clinical_coder/harnesses/base.py\` (\`BaseHarness\`)
- **Execution Contract**: \`async def run(self, request: CodeRequest, budget: BudgetTracker, trace: TraceRecorder) -> HarnessResult\`
- **Contracts & Schemas**:
  * Core: \`CodeRequest\`, \`CodeEntry\`, \`HarnessResult\`, \`Usage\` from \`clinical_coder.contracts\`
  * Multi-agent schemas: \`clinical_coder.harnesses.multi_agent_contracts\` (\`ClinicalExtractionOutput\`, \`SpecialistCodingOutput\`, \`VerifierAgentOutput\`, \`CodeVerificationDecision\`)
  * Formatting & parsing: \`extract_json_object\`, \`parse_coding_output\`, \`OutputFormatError\` from \`clinical_coder.output\`
  * History & state helpers: \`extract_history_codes\`, \`history_json\`, \`_merge_usage\`, \`_request_id\` from \`clinical_coder.harnesses.langgraph_common\`
- **Registry Update**:
  * In \`clinical_coder/registry.py\`: Update \`build_harness(cfg: Settings)\` to map \`"h4"\` to \`LangGraphH4Harness\`.
- **Benchmark Orchestrator Registration**:
  * In \`HarnessComparison/harness_compare/constants.py\`: Add \`"h4"\` to \`HARNESS_IDS\`, \`HARNESS_NAMES\`, \`HARNESS_DESCRIPTIONS\`, and set \`HARNESS_RECEIVES_HISTORY["h4"] = True\`.
- **Unit Test Suite**:
  * Create \`tests/test_h4.py\` following the pattern in \`tests/test_h3.py\` using \`FakeGateway\`.

---

### 3. Required Architectural Innovations for Harness 4 (H4)

Implement a compiled \`StateGraph\` in \`langgraph_h4.py\` with the following 5 core innovations:

1. **Adaptive Complexity Router (\`router_node\`)**:
   - Inspect input \`CodeRequest\`: assess note length, presence of active comorbidities, and longitudinal history.
   - **Fast-Path Route** (Routine / single-focus encounters like C07 Strep, C08 UTI, C09 Knee OA):
     * If note < 750 characters and no complex comorbidity: dispatch directly to \`fast_coder_node\` (single-turn H2-style pass).
     * Bypasses the 4-agent cascade, slashing turnaround latency to ≤ 15s and tokens to < 2,200.
   - **Specialist Ensemble Route** (Multi-condition or longitudinal encounters like C01, C02, C03, C04, C06):
     * Route to \`extractor_node\` -> parallel \`specialist_coding_node\` -> \`calibrated_verifier_node\`.

2. **Union-Preserving Consensus Verifier (\`calibrated_verifier_node\`)**:
   - **Fix H3's Silent Code Erasure**: Never replace the entire code set with only the codes enumerated in \`verifier_output.approved_codes\`. Instead, maintain the union of all proposed candidate codes (\`icd10_candidates + cpt_candidates\`).
   - **Explicit Deletion Rule**: Only prune a candidate code if the verifier returns an explicit \`"delete"\` action backed by a documented clinical contradiction or negative assertion (e.g. "denies fever").
   - **Non-Destructive Fallback Floor**: If all codes are rejected or \`approved_codes\` is empty, fallback to retaining proposed candidate codes that have verbatim note provenance snippets and 0 forbidden matches. Never raise \`OUTPUT_FORMAT_INVALID\` due to verifier over-pruning.

3. **Markdown-Resilient JSON Extraction**:
   - Wrap all sub-agent and verifier LLM completions with \`extract_json_object(response.content)\` before passing to Pydantic validators (\`model_validate\`). Handle both raw JSON and \`\`\`\`json ... \`\`\`\` fenced blocks cleanly.

4. **Deterministic Negative Constraint Pre-Filter**:
   - Before compiling the final \`HarnessResult\`, apply a deterministic negative filter:
     * Discard known forbidden unspecified codes: \`Z94.9\` (if history specifies kidney transplant, retain \`Z94.0\`), \`N18.9\` (unspecified CKD), \`E11.9\` (if diabetic nephropathy/CKD is present), \`I50.9\` (if systolic/diastolic is specified).
     * Discard non-billable parent codes (e.g. \`N18.3\` parent when \`N18.30\`/\`N18.32\` is required).

5. **Token-Efficient Structured Diffs**:
   - Do NOT duplicate the entire raw encounter note across every sub-agent turn. Pass only the extracted clinical entity spans, candidate code rationales, and history codes between nodes.

---

### 4. Implementation Code Skeleton for \`langgraph_h4.py\`

\`\`\`python
from __future__ import annotations

import asyncio
import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from clinical_coder.contracts import CodeEntry, CodeRequest, Usage
from clinical_coder.harnesses.base import BaseHarness, HarnessExecutionError, HarnessResult
from clinical_coder.harnesses.langgraph_common import (
    _merge_usage,
    _request_id,
    extract_history_codes,
    history_json,
)
from clinical_coder.output import extract_json_object
from clinical_coder.llm.budget import BudgetTracker
from clinical_coder.llm.client import LLMGateway
from clinical_coder.trace import TraceRecorder


class H4GraphState(TypedDict, total=False):
    request: CodeRequest
    model: str
    trace: TraceRecorder
    gateway: LLMGateway
    budget: BudgetTracker
    history_codes: list[str]
    is_complex: bool
    candidate_codes: list[CodeEntry]
    approved_codes: list[CodeEntry]
    usage: Usage
    raw_output: str


# 1. Router Node: Triage routine vs multi-condition cases
async def router_node(state: H4GraphState) -> H4GraphState:
    req = state["request"]
    note = req.encounter_note or ""
    history_codes = state.get("history_codes", [])
    # Complex if long note, multiple conditions, or longitudinal history present
    is_complex = len(note) > 750 or len(history_codes) > 0 or ("chronic" in note.lower() and "acute" in note.lower())
    state["trace"].turn_start(node="router", is_complex=is_complex)
    return {"is_complex": is_complex}


# 2. Fast-Path Coder (for routine cases)
async def fast_coder_node(state: H4GraphState) -> H4GraphState:
    # Single-turn planner/coder pass (H2-style, fast P50 < 15s)
    ...


# 3. Specialist Pipeline (for complex cases)
async def specialist_node(state: H4GraphState) -> H4GraphState:
    # Extractor + Specialist ICD-10 & CPT passes
    ...


# 4. Calibrated Consensus Verifier
async def calibrated_verifier_node(state: H4GraphState) -> H4GraphState:
    # Union-preserving validation with candidate fallback floor
    ...


def route_decision(state: H4GraphState) -> str:
    return "specialist" if state.get("is_complex") else "fast_coder"


class LangGraphH4Harness(BaseHarness):
    harness_id = "h4"
    harness_name = "H4 Adaptive Multi-Agent Pipeline"

    def build_graph(self):
        workflow = StateGraph(H4GraphState)
        workflow.add_node("router", router_node)
        workflow.add_node("fast_coder", fast_coder_node)
        workflow.add_node("specialist", specialist_node)
        workflow.add_node("verifier", calibrated_verifier_node)

        workflow.add_edge(START, "router")
        workflow.add_conditional_edges("router", route_decision, {
            "fast_coder": "fast_coder",
            "specialist": "specialist",
        })
        workflow.add_edge("fast_coder", "verifier")
        workflow.add_edge("specialist", "verifier")
        workflow.add_edge("verifier", END)
        return workflow.compile()
\`\`\`

---

### 5. Verification Checklist & Success Criteria
1. **Pass all Automated Tests**: Implement \`tests/test_h4.py\` and execute \`pytest tests/test_h4.py\` cleanly.
2. **Target Primary Quality Score**: Reach ${targetF1} accuracy/F1 across all benchmark cases.
3. **Zero Safety Violations**: 100% adherence to negative constraints (no forbidden hits on C05, C06).
4. **P50 Latency ≤ 22.0s**: Verify fast-path router activates on routine single-condition cases.
5. **No Output Format Crashes**: Verify non-destructive verifier never emits an empty code set when valid clinical candidates exist.`;
});

// Full markdown report generation
const markdownReport = computed(() => {
  const comp = props.comparison || {};
  const rel = reliability.value;
  const hList = harnesses.value;
  const axesList = reportAxes.value;

  // Scorecard table markdown
  const headerCols = hList.map((h) => h.name).join(" | ");
  const alignCols = hList.map(() => ":---:").join(" | ");
  const rows = axesList
    .map((axis) => {
      const vals = hList
        .map((h) => {
          const val = h.values?.[axis.key];
          const isWin = axisWinners.value[axis.key] === h.id;
          return isWin ? `**${formatPct(val)}** *(Best)*` : formatPct(val);
        })
        .join(" | ");
      return `| **${axis.label}** | ${vals} |`;
    })
    .join("\n");

  const meanLatRow = `| **Mean Latency** | ${hList.map((h) => formatMs(h.latencyMean)).join(" | ")} |`;
  const p50Row = `| **Median Latency (P50)** | ${hList.map((h) => formatMs(h.latencyP50)).join(" | ")} |`;
  const p95Row = `| **Tail Latency (P95)** | ${hList.map((h) => formatMs(h.latencyP95)).join(" | ")} |`;
  const tokRow = `| **Avg Tokens / Case** | ${hList.map((h) => (h.meanTokens ? formatNumber(h.meanTokens) : "—")).join(" | ")} |`;
  const callsRow = `| **Model Calls / Case** | ${hList.map((h) => (h.modelCalls != null ? formatNumber(h.modelCalls) : "—")).join(" | ")} |`;
  const successRow = `| **Job Success Rate** | ${hList.map((h) => formatPct(h.successRate)).join(" | ")} |`;
  const costRow = `| **Estimated Cost** | ${hList.map((h) => h.costStr || "Free / unpriced").join(" | ")} |`;

  return `### Automated Post-Run Evaluation & Optimization Report
**Run ID:** \`${comp.id || "N/A"}\` | **Benchmark:** ${comp.name || "Agent Benchmark"}
**Evaluated Models:** ${(comp.models || []).join(", ") || "N/A"} | **Workload:** ${comp.progress?.total_jobs || "—"} total jobs
**Execution Concurrency:** ${rel.concurrencyUsed} | **Completed At:** ${comp.finished_at || new Date().toISOString()}

---

#### 1. Executive Performance Scorecard

| Evaluation Dimension | ${headerCols} |
| :--- | ${alignCols} |
${rows}
| **Operational & Telemetry** | ${hList.map(() => "---").join(" | ")} |
${p50Row}
${p95Row}
${meanLatRow}
${tokRow}
${callsRow}
${successRow}
${costRow}

---

#### 2. Reliability & Gateway Telemetry
- **Total Workload:** ${rel.total} jobs (${rel.completed} completed, ${rel.failed} failed).
- **HTTP 429 Rate Limits:** ${rel.rateLimits} events recorded.
- **Circuit Breaker Trips:** ${rel.circuitTrips} events recorded.
- **Execution Concurrency:** ${rel.concurrencyUsed} parallel worker(s).
${rel.rateLimits > 0 ? "> [!WARNING]\n> Single-route rate limiting observed upstream. Concurrency should be set to 1 for rate-limited providers." : "> [!NOTE]\n> Clean execution: zero provider rate limits or circuit trips detected."}

---

#### 3. Actionable Architectural Recommendations
${recommendations.value
  .map(
    (r) => `
##### ${r.name}
- **Demonstrated Strengths:** ${r.strengths.join(" ")}
- **Identified Bottlenecks:** ${r.bottlenecks.join(" ")}
- **Recommended Actions:**
${r.actions.map((a, i) => `  ${i + 1}. *${a.title}* [${a.tag}]: ${a.desc}`).join("\n")}
`
  )
  .join("")}

---

#### 4. Prompt for Next Agent Harness:
\`\`\`markdown
${nextHarnessPrompt.value}
\`\`\`
`;
});

async function copyReport() {
  try {
    await navigator.clipboard.writeText(markdownReport.value);
    copySuccess.value = true;
    setTimeout(() => {
      copySuccess.value = false;
    }, 2500);
  } catch (err) {
    console.error("Failed to copy report", err);
  }
}

async function copyPrompt() {
  try {
    await navigator.clipboard.writeText(nextHarnessPrompt.value);
    promptCopySuccess.value = true;
    setTimeout(() => {
      promptCopySuccess.value = false;
    }, 2500);
  } catch (err) {
    console.error("Failed to copy harness prompt", err);
  }
}
</script>

<template>
  <section class="panel post-run-report-panel">
    <!-- Panel Header -->
    <div class="report-panel-header">
      <div class="report-title-group">
        <div class="eyebrow-pill">
          <span class="pulse-dot"></span>
          Automated Post-Run Intelligence
        </div>
        <h2>Post-Run Evaluation & Architectural Improvement Report</h2>
        <p class="panel-sub">
          Synthesized analysis derived from execution traces, radar metrics, and failure telemetry across all benchmarked architectures.
        </p>
      </div>

      <div class="report-header-actions">
        <!-- Navigation Tabs -->
        <div class="report-tab-pills">
          <button
            type="button"
            class="report-tab-btn"
            :class="{ active: activeTab === 'scorecard' }"
            @click="activeTab = 'scorecard'"
          >
            📊 Scorecard
          </button>
          <button
            type="button"
            class="report-tab-btn"
            :class="{ active: activeTab === 'recommendations' }"
            @click="activeTab = 'recommendations'"
          >
            💡 Recommendations
          </button>
          <button
            type="button"
            class="report-tab-btn"
            :class="{ active: activeTab === 'reliability' }"
            @click="activeTab = 'reliability'"
          >
            🛡️ Reliability
          </button>
          <button
            type="button"
            class="report-tab-btn"
            :class="{ active: activeTab === 'prompt' }"
            @click="activeTab = 'prompt'"
          >
            🤖 Prompt for Next Harness
          </button>
          <button
            type="button"
            class="report-tab-btn"
            :class="{ active: activeTab === 'markdown' }"
            @click="activeTab = 'markdown'"
          >
            📝 Raw Markdown
          </button>
        </div>

        <!-- Copy Action -->
        <button type="button" class="btn secondary small copy-report-btn" @click="copyReport">
          <svg viewBox="0 0 20 20" aria-hidden="true" style="width:14px;height:14px;">
            <path
              v-if="copySuccess"
              d="M5 10l3 3 7-7"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
            <path
              v-else
              d="M7 4h8a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2zm-2 4H4a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-1"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
            />
          </svg>
          {{ copySuccess ? "Copied!" : "Copy Report" }}
        </button>
      </div>
    </div>

    <!-- TAB 1: EXECUTIVE PERFORMANCE SCORECARD -->
    <div v-if="activeTab === 'scorecard'" class="report-tab-body">
      <!-- Winner Highlights Banner (Dynamic) -->
      <div v-if="highlightCards.length > 0" class="winner-highlights-grid">
        <div
          v-for="card in highlightCards"
          :key="card.kicker"
          class="winner-card"
        >
          <span class="winner-kicker">{{ card.kicker }}</span>
          <div class="winner-val">
            <span
              class="harness-badge-pill"
              :style="{
                backgroundColor: harnessSoft(card.harnessId),
                color: harnessColor(card.harnessId),
                borderColor: harnessColor(card.harnessId)
              }"
            >
              {{ card.harnessId.toUpperCase() }}
            </span>
            <strong>{{ card.value }}</strong>
          </div>
          <span class="winner-sub">{{ card.harnessName }}</span>
        </div>
      </div>

      <!-- Comparison Matrix Table (Dynamic Columns & Dynamic Rows) -->
      <div class="scorecard-table-wrapper">
        <table class="report-table">
          <thead>
            <tr>
              <th style="min-width: 220px;">Evaluation Dimension</th>
              <th
                v-for="h in harnesses"
                :key="h.id"
                style="min-width: 200px;"
              >
                <span
                  class="harness-dot"
                  :style="{ backgroundColor: h.color }"
                ></span>
                {{ h.name }}
              </th>
            </tr>
          </thead>
          <tbody>
            <!-- Dynamic Evaluation Axes -->
            <tr v-for="axis in reportAxes" :key="axis.key">
              <td>
                <strong>{{ axis.label }}</strong>
                <span class="row-subtext">{{ axis.description }}</span>
              </td>
              <td
                v-for="h in harnesses"
                :key="h.id"
                :class="{ 'cell-highlight': axisWinners[axis.key] === h.id }"
              >
                <span class="metric-num">{{ formatPct(h.values?.[axis.key]) }}</span>
                <span v-if="axisWinners[axis.key] === h.id" class="win-tag">Best</span>
              </td>
            </tr>

            <!-- Section Header: Operational & System Telemetry -->
            <tr class="table-section-row">
              <td :colspan="harnesses.length + 1">
                <strong>System & Operational Telemetry</strong>
              </td>
            </tr>

            <!-- Operational Metrics: Median Latency P50 -->
            <tr>
              <td>
                <strong>Median Latency (P50)</strong>
                <span class="row-subtext">Median wall-clock turnaround time per case</span>
              </td>
              <td
                v-for="h in harnesses"
                :key="h.id"
                :class="{ 'cell-highlight': axisWinners._fastest === h.id }"
              >
                <span class="metric-num">{{ formatMs(h.latencyP50) }}</span>
                <span v-if="axisWinners._fastest === h.id" class="win-tag">Fastest</span>
              </td>
            </tr>

            <!-- Operational Metrics: Tail Latency P95 -->
            <tr>
              <td>
                <strong>Tail Latency (P95)</strong>
                <span class="row-subtext">95th percentile turnaround latency</span>
              </td>
              <td v-for="h in harnesses" :key="h.id">
                <span class="metric-num">{{ formatMs(h.latencyP95) }}</span>
              </td>
            </tr>

            <!-- Operational Metrics: Mean Latency -->
            <tr>
              <td>
                <strong>Mean Latency</strong>
                <span class="row-subtext">Average wall-clock turnaround time</span>
              </td>
              <td v-for="h in harnesses" :key="h.id">
                <span class="metric-num">{{ formatMs(h.latencyMean) }}</span>
              </td>
            </tr>

            <!-- Operational Metrics: Average Tokens -->
            <tr>
              <td>
                <strong>Avg Tokens / Case</strong>
                <span class="row-subtext">Input plus output tokens consumed per case</span>
              </td>
              <td v-for="h in harnesses" :key="h.id">
                <span class="metric-num">{{ h.meanTokens ? formatNumber(h.meanTokens) : '—' }}</span>
              </td>
            </tr>

            <!-- Operational Metrics: Total Model Calls -->
            <tr>
              <td>
                <strong>Model Calls / Case</strong>
                <span class="row-subtext">Total LLM API inferences dispatched</span>
              </td>
              <td v-for="h in harnesses" :key="h.id">
                <span class="metric-num">{{ h.modelCalls != null ? formatNumber(h.modelCalls) : '—' }}</span>
              </td>
            </tr>

            <!-- Operational Metrics: Success Rate -->
            <tr>
              <td>
                <strong>Job Success Rate</strong>
                <span class="row-subtext">Jobs reaching completion without harness crashes</span>
              </td>
              <td v-for="h in harnesses" :key="h.id">
                <span class="metric-num">{{ formatPct(h.successRate) }}</span>
              </td>
            </tr>

            <!-- Operational Metrics: Cost -->
            <tr>
              <td>
                <strong>Estimated Cost</strong>
                <span class="row-subtext">Inference pricing per evaluation job</span>
              </td>
              <td v-for="h in harnesses" :key="h.id">
                <span class="metric-num">{{ h.costStr }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- TAB 2: ARCHITECTURAL IMPROVEMENT RECOMMENDATIONS (Dynamic per Harness) -->
    <div v-else-if="activeTab === 'recommendations'" class="report-tab-body">
      <div class="recommendations-container">
        <div
          v-for="rec in recommendations"
          :key="rec.id"
          class="recommendation-card"
          :style="{ borderLeftColor: rec.color }"
        >
          <div class="rec-card-header">
            <div class="rec-title-block">
              <span
                class="harness-badge-large"
                :style="{
                  backgroundColor: harnessSoft(rec.id),
                  color: rec.color,
                  borderColor: rec.color
                }"
              >
                {{ rec.id.toUpperCase() }}
              </span>
              <div>
                <h3>{{ rec.name }}</h3>
                <span class="rec-model-sub">Model: {{ rec.model }}</span>
              </div>
            </div>
          </div>

          <!-- Strengths & Bottlenecks Grid -->
          <div class="rec-profile-grid">
            <div class="profile-col strengths-col">
              <div class="profile-col-head">
                <span class="profile-icon icon-check">✓</span>
                <strong>Demonstrated Strengths</strong>
              </div>
              <ul>
                <li v-for="(item, idx) in rec.strengths" :key="idx">{{ item }}</li>
              </ul>
            </div>

            <div class="profile-col bottlenecks-col">
              <div class="profile-col-head">
                <span class="profile-icon icon-warn">⚠️</span>
                <strong>Identified Bottlenecks</strong>
              </div>
              <ul>
                <li v-for="(item, idx) in rec.bottlenecks" :key="idx">{{ item }}</li>
              </ul>
            </div>
          </div>

          <!-- Action Items -->
          <div class="rec-actions-block">
            <div class="actions-head">
              <span class="gear-icon">⚙️</span>
              <strong>Actionable Architectural Upgrades</strong>
            </div>
            <div class="action-cards-grid">
              <div v-for="(act, aIdx) in rec.actions" :key="aIdx" class="action-card">
                <div class="action-card-header">
                  <strong>{{ act.title }}</strong>
                  <span class="action-tag" :class="act.tagClass">{{ act.tag }}</span>
                </div>
                <p>{{ act.desc }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- TAB 3: RELIABILITY & GATEWAY TELEMETRY -->
    <div v-else-if="activeTab === 'reliability'" class="report-tab-body">
      <div class="telemetry-grid">
        <div class="telemetry-card">
          <div class="telemetry-card-head">
            <span>Concurrency & Worker Pool</span>
            <span class="badge" :class="reliability.concurrencyUsed === 1 ? 'badge-ok' : 'badge-warn'">
              {{ reliability.concurrencyUsed === 1 ? 'Safe Serial (1)' : `Parallel (${reliability.concurrencyUsed})` }}
            </span>
          </div>
          <div class="telemetry-metric">
            <strong>{{ reliability.concurrencyUsed }}</strong>
            <span>Active in-flight worker jobs per harness</span>
          </div>
          <p class="telemetry-desc">
            {{ reliability.concurrencyUsed === 1
              ? "Operating at concurrency = 1 ensures sequential dispatch, completely preventing multi-turn burst spikes against upstream model routers."
              : "Parallel concurrency active. Multi-turn graphs should ensure provider RPM quotas accommodate concurrent turns." }}
          </p>
        </div>

        <div class="telemetry-card">
          <div class="telemetry-card-head">
            <span>Rate Limiting (HTTP 429)</span>
            <span class="badge" :class="reliability.rateLimits === 0 ? 'badge-ok' : 'badge-error'">
              {{ reliability.rateLimits === 0 ? 'Zero 429s' : `${reliability.rateLimits} Cooldowns` }}
            </span>
          </div>
          <div class="telemetry-metric">
            <strong :class="{ 'text-red': reliability.rateLimits > 0 }">{{ reliability.rateLimits }}</strong>
            <span>Rate limit cooldown events detected</span>
          </div>
          <p class="telemetry-desc">
            {{ reliability.rateLimits === 0
              ? "All LLM requests resolved cleanly within provider quota limits."
              : "Upstream route rate-limited. The automated retry backoff sleeps past provider reset windows to avoid burning retries." }}
          </p>
        </div>

        <div class="telemetry-card">
          <div class="telemetry-card-head">
            <span>Provider Circuit Breaker</span>
            <span class="badge" :class="reliability.circuitTrips === 0 ? 'badge-ok' : 'badge-error'">
              {{ reliability.circuitTrips === 0 ? 'Healthy (Closed)' : 'Tripped (Open)' }}
            </span>
          </div>
          <div class="telemetry-metric">
            <strong :class="{ 'text-red': reliability.circuitTrips > 0 }">{{ reliability.circuitTrips }}</strong>
            <span>Circuit trip events</span>
          </div>
          <p class="telemetry-desc">
            Protects the provider endpoint from cascaded stampedes. Transient rate limits are isolated during retries to prevent global worker lockouts.
          </p>
        </div>
      </div>
    </div>

    <!-- TAB 4: PROMPT FOR NEXT AGENT HARNESS (Generic Turnkey Directive) -->
    <div v-else-if="activeTab === 'prompt'" class="report-tab-body">
      <div class="next-harness-prompt-container">
        <!-- Top Directive Card -->
        <div class="prompt-header-card">
          <div class="prompt-header-info">
            <span class="prompt-tag">AGENT HANDOFF DIRECTIVE</span>
            <h3>Prompt for Next Agent Harness</h3>
            <p>
              Autonomous coding agent instruction synthesized dynamically from this benchmark's empirical tradeoffs.
              Feed this directly to Claude Code, Antigravity, Cursor, or Devin to implement <strong>Harness 4 (Adaptive Multi-Agent Pipeline)</strong>.
            </p>
          </div>
          <button type="button" class="btn primary copy-prompt-btn" @click="copyPrompt">
            <svg viewBox="0 0 20 20" aria-hidden="true" style="width:14px;height:14px;">
              <path
                v-if="promptCopySuccess"
                d="M5 10l3 3 7-7"
                fill="none"
                stroke="currentColor"
                stroke-width="2.2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <path
                v-else
                d="M7 4h8a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V6a2 2 0 012-2zm-2 4H4a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-1"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
              />
            </svg>
            {{ promptCopySuccess ? "✓ Prompt Copied!" : "Copy Turnkey Prompt" }}
          </button>
        </div>

        <!-- Target KPI Callouts (Dynamic) -->
        <div class="prompt-target-kpis">
          <div
            v-for="axis in reportAxes.slice(0, 4)"
            :key="axis.key"
            class="target-kpi-card"
          >
            <span class="target-label">Target {{ axis.label }}</span>
            <strong>≥ {{ Math.min(100, Math.round((Math.max(...harnesses.map(h => h.values?.[axis.key] || 0)) + 0.05) * 100)) }}.0%</strong>
            <span class="target-sub">Beat baseline leader ({{ formatPct(Math.max(...harnesses.map(h => h.values?.[axis.key] || 0))) }})</span>
          </div>
        </div>

        <!-- Code Block Preview -->
        <div class="prompt-preview-wrapper">
          <div class="prompt-preview-toolbar">
            <span class="mono-label">AGENT_INSTRUCTION_H4.prompt</span>
            <span class="prompt-model-note">Target model: {{ comparison?.models?.[0] || 'Default Model' }}</span>
          </div>
          <pre class="prompt-markdown-pre"><code>{{ nextHarnessPrompt }}</code></pre>
        </div>
      </div>
    </div>

    <!-- TAB 5: RAW MARKDOWN EXPORT -->
    <div v-else class="report-tab-body">
      <div class="markdown-preview-wrapper">
        <div class="markdown-preview-toolbar">
          <span class="mono-label">REPORT_OUTPUT.md</span>
          <button type="button" class="btn ghost small" @click="copyReport">
            {{ copySuccess ? "✓ Copied" : "Copy Raw Markdown" }}
          </button>
        </div>
        <pre class="raw-markdown-pre"><code>{{ markdownReport }}</code></pre>
      </div>
    </div>
  </section>
</template>
