from __future__ import annotations

import json
import math
import random
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable

from harness_compare.dataset import CaseRecord

RADAR_AXES: list[dict[str, str]] = [
    {
        "key": "accuracy",
        "label": "Accuracy",
        "description": (
            "Micro F1 over expected vs returned target outputs. A failed job "
            "counts as missed expected outputs."
        ),
    },
    {
        "key": "recall",
        "label": "Recall",
        "description": "Share of reference ground truth outputs returned, pooled across jobs.",
    },
    {
        "key": "precision",
        "label": "Precision",
        "description": "Share of returned outputs that match reference ground truth.",
    },
    {
        "key": "provenance",
        "label": "Provenance",
        "description": (
            "Share of returned items whose citation or evidence snippet is verbatim in the "
            "source document after whitespace normalization."
        ),
    },
    {
        "key": "safety",
        "label": "Safety",
        "description": "Share of jobs with no forbidden output, hallucinated keys, or guardrail violation.",
    },
    {
        "key": "exact_match",
        "label": "Exact set",
        "description": "Share of jobs whose returned output set exactly equals the reference set.",
    },
    {
        "key": "history_specificity",
        "label": "History specificity",
        "description": (
            "Share of applicable jobs where historical context annotations were "
            "correctly retained and reflected in the output."
        ),
    },
    {
        "key": "efficiency",
        "label": "Efficiency",
        "description": (
            "Bounded reference score: 0.5 x 60s/(60s+mean latency) + "
            "0.5 x 4000/(4000+mean total tokens). Not a cost or quality measure."
        ),
    },
]

DEFAULT_IGNORE_PATTERNS = [r"^99[2-4]"]


def normalize_code(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().upper()


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _ignore_patterns(expected: dict[str, Any]) -> list[re.Pattern[str]]:
    raw = (expected.get("grading") or {}).get("ignoreCodePatterns")
    patterns = raw if isinstance(raw, list) and raw else DEFAULT_IGNORE_PATTERNS
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(str(pattern)))
        except re.error:
            continue
    return compiled


def _is_ignored(code: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(code) for pattern in patterns)


def _codes_from_response(response: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    raw = response.get("codes")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _trace_types(response: dict[str, Any] | None) -> set[str]:
    if not isinstance(response, dict):
        return set()
    trace = response.get("trace")
    if not isinstance(trace, list):
        return set()
    return {
        str(item.get("type"))
        for item in trace
        if isinstance(item, dict) and item.get("type")
    }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def efficiency_score(mean_latency_ms: float | None, mean_tokens: float | None) -> float:
    latency = max(0.0, mean_latency_ms or 0.0)
    tokens = max(0.0, mean_tokens or 0.0)
    latency_score = 60_000.0 / (60_000.0 + latency)
    token_score = 4_000.0 / (4_000.0 + tokens)
    return round(0.5 * latency_score + 0.5 * token_score, 6)


def evaluate_response(
    case: CaseRecord,
    expected: dict[str, Any],
    response: dict[str, Any] | None,
    *,
    job_error: dict[str, Any] | None = None,
    fallback_latency_ms: int | None = None,
    history_available: bool | None = None,
) -> dict[str, Any]:
    """Score one harness response against one gold case.

    The evaluator is deliberately deterministic and independent of the
    harnesses. It never sees model identity.
    """
    patterns = _ignore_patterns(expected)
    expected_codes = [
        normalize_code(item.get("code"))
        for item in expected.get("expectedCodes") or []
        if isinstance(item, dict)
    ]
    expected_codes = [code for code in expected_codes if code]
    forbidden_codes = [
        normalize_code(item.get("code"))
        for item in expected.get("forbiddenCodes") or []
        if isinstance(item, dict)
    ]
    forbidden_set = {code for code in forbidden_codes if code}
    expected_set = set(expected_codes)

    raw_entries = _codes_from_response(response)
    returned: list[str] = []
    snippets: dict[str, str] = {}
    invalid_entries = 0
    em_ignored = 0
    for item in raw_entries:
        code = normalize_code(item.get("code"))
        if not code:
            invalid_entries += 1
            continue
        if _is_ignored(code, patterns):
            em_ignored += 1
            continue
        if code not in returned:
            returned.append(code)
            snippets[code] = str(item.get("snippet") or "")
        elif not snippets.get(code):
            snippets[code] = str(item.get("snippet") or "")

    returned_set = set(returned)
    tp = len(returned_set & expected_set)
    fp = len(returned_set - expected_set)
    fn = len(expected_set - returned_set)
    precision = tp / len(returned_set) if returned_set else 0.0
    recall = tp / len(expected_set) if expected_set else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall > 0
        else 0.0
    )

    forbidden_hits = sorted(returned_set & forbidden_set)
    extras = sorted(returned_set - expected_set)
    missing = sorted(expected_set - returned_set)

    note_norm = normalize_text(case.encounter_note)
    grounded = 0
    ungrounded: list[str] = []
    for code in returned:
        snippet = normalize_text(snippets.get(code, ""))
        if snippet and snippet in note_norm:
            grounded += 1
        else:
            ungrounded.append(code)
    grounded_rate = grounded / len(returned) if returned else 0.0

    history_required = [
        normalize_code(item.get("code"))
        for item in expected.get("expectedCodes") or []
        if isinstance(item, dict) and item.get("historySnippet")
    ]
    history_required = [code for code in history_required if code]
    history_hits = [code for code in history_required if code in returned_set]
    history_specificity = (
        len(history_hits) / len(history_required) if history_required else None
    )

    response_error = response.get("error") if isinstance(response, dict) else None
    error_code = None
    if isinstance(response_error, dict) and response_error.get("code"):
        error_code = str(response_error["code"])
    elif isinstance(job_error, dict) and job_error.get("code"):
        error_code = str(job_error["code"])

    usage = (response or {}).get("usage") or {}
    budget = (response or {}).get("budget") or {}
    guardrails = (response or {}).get("guardrails") or {}
    used = budget.get("used") if isinstance(budget, dict) else {}
    cost = (used or {}).get("cost_usd") if isinstance(used, dict) else None
    latency = None
    if isinstance(response, dict) and isinstance(response.get("latency_ms"), int):
        latency = response["latency_ms"]
    elif fallback_latency_ms is not None:
        latency = fallback_latency_ms

    schema_valid = (
        isinstance(response, dict)
        and error_code is None
        and len(raw_entries) > 0
        and invalid_entries == 0
        and bool(returned or all(_is_ignored(str(i.get("code") or ""), patterns) for i in raw_entries))
    )
    if isinstance(response_error, dict) and response_error.get("code") == "NO_CODES":
        schema_valid = False

    trace_types = _trace_types(response)
    history_present = (
        bool(history_available)
        if history_available is not None
        else case.history is not None
    )

    return {
        "case_id": case.case_id,
        "expected_codes": sorted(expected_set),
        "returned_codes": returned,
        "expected_count": len(expected_set),
        "returned_count": len(returned),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "exact_set_match": returned_set == expected_set and bool(expected_set),
        "missing_codes": missing,
        "extra_codes": extras,
        "forbidden_codes": sorted(forbidden_set),
        "forbidden_hits": forbidden_hits,
        "forbidden_free": not forbidden_hits,
        "safety_ok": not forbidden_hits,
        "grounded_codes": grounded,
        "ungrounded_codes": ungrounded,
        "grounded_rate": round(grounded_rate, 6),
        "provenance_ok": bool(returned) and not ungrounded,
        "schema_valid": bool(schema_valid),
        "reliability_ok": isinstance(response, dict) and error_code is None,
        "error_code": error_code,
        "guardrail_flags": {
            "input": list(guardrails.get("input_flags") or []),
            "output": list(guardrails.get("output_flags") or []),
            "blocked": bool(guardrails.get("blocked")),
        },
        "budget_exceeded": error_code == "BUDGET_EXCEEDED"
        or bool((budget or {}).get("exceeded")),
        "history_present": history_present,
        "history_read": "history_read" in trace_types,
        "history_required_codes": history_required,
        "history_specificity": (
            round(history_specificity, 6)
            if history_specificity is not None
            else None
        ),
        "history_specificity_hits": history_hits,
        "em_ignored": em_ignored,
        "invalid_entries": invalid_entries,
        "latency_ms": latency,
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "calls": int(usage.get("calls") or 0),
        },
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
        "job_status": "FAILED" if error_code else "COMPLETED",
        "notes": _notes(error_code, forbidden_hits, ungrounded),
    }


def _notes(
    error_code: str | None, forbidden_hits: list[str], ungrounded: list[str]
) -> list[str]:
    notes: list[str] = []
    if error_code:
        notes.append(f"Harness reported {error_code}.")
    if forbidden_hits:
        notes.append("Forbidden code(s): " + ", ".join(forbidden_hits))
    if ungrounded:
        notes.append("Ungrounded snippet(s): " + ", ".join(ungrounded))
    return notes


def empty_evaluation(
    case: CaseRecord,
    expected: dict[str, Any],
    *,
    error_code: str,
    message: str = "",
    history_available: bool | None = None,
) -> dict[str, Any]:
    evaluation = evaluate_response(
        case,
        expected,
        None,
        job_error={"code": error_code, "message": message},
        history_available=history_available,
    )
    evaluation["job_status"] = "FAILED"
    evaluation["error_code"] = error_code
    evaluation["schema_valid"] = False
    evaluation["reliability_ok"] = False
    return evaluation


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 6) if values else None


def _metric_block(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(evaluations)
    tp = sum(item["true_positives"] for item in evaluations)
    returned = sum(item["returned_count"] for item in evaluations)
    expected = sum(item["expected_count"] for item in evaluations)
    fn = sum(item["false_negatives"] for item in evaluations)
    precision = tp / returned if returned else 0.0
    recall = tp / expected if expected else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall > 0
        else 0.0
    )
    completed = [
        item
        for item in evaluations
        if item.get("reliability_ok") and item.get("error_code") is None
    ]
    safe_jobs = sum(
        1 for item in evaluations if item.get("forbidden_free") and item.get("reliability_ok")
    )
    exact = sum(
        1 for item in evaluations if item.get("exact_set_match") and item.get("reliability_ok")
    )
    grounded_codes = sum(item.get("grounded_codes") or 0 for item in evaluations)
    grounded_rate = grounded_codes / returned if returned else 0.0
    history_applicable = [
        item for item in evaluations if item.get("history_specificity") is not None
    ]
    history_rate = (
        statistics.fmean(float(item["history_specificity"]) for item in history_applicable)
        if history_applicable
        else None
    )
    latencies = [
        float(item["latency_ms"])
        for item in evaluations
        if isinstance(item.get("latency_ms"), (int, float))
    ]
    token_totals = [float(item["usage"]["total_tokens"]) for item in evaluations]
    calls = sum(item["usage"]["calls"] for item in evaluations)
    input_tokens = sum(item["usage"]["input_tokens"] for item in evaluations)
    output_tokens = sum(item["usage"]["output_tokens"] for item in evaluations)
    total_tokens = sum(item["usage"]["total_tokens"] for item in evaluations)
    costs = [
        float(item["cost_usd"])
        for item in evaluations
        if isinstance(item.get("cost_usd"), (int, float))
    ]
    error_codes: dict[str, int] = {}
    for item in evaluations:
        code = item.get("error_code")
        if code:
            error_codes[code] = error_codes.get(code, 0) + 1
    input_flags: dict[str, int] = {}
    output_flags: dict[str, int] = {}
    for item in evaluations:
        for flag in item.get("guardrail_flags", {}).get("input") or []:
            input_flags[flag] = input_flags.get(flag, 0) + 1
        for flag in item.get("guardrail_flags", {}).get("output") or []:
            output_flags[flag] = output_flags.get(flag, 0) + 1
    mean_latency = _mean(latencies)
    mean_tokens = _mean(token_totals)
    return {
        "n": total,
        "successful_jobs": len(completed),
        "success_rate": round(len(completed) / total, 6) if total else 0.0,
        "delivery_rate": round(
            sum(1 for item in evaluations if item.get("job_status") == "COMPLETED")
            / total,
            6,
        )
        if total
        else 0.0,
        "micro": {
            "true_positives": tp,
            "returned": returned,
            "expected": expected,
            "false_negatives": fn,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        },
        "exact_match_rate": round(exact / total, 6) if total else 0.0,
        "forbidden_free_rate": round(safe_jobs / total, 6) if total else 0.0,
        "forbidden_hits": sum(len(item.get("forbidden_hits") or []) for item in evaluations),
        "provenance_rate": round(grounded_rate, 6),
        "grounded_code_rate": round(grounded_rate, 6),
        "schema_valid_rate": round(
            sum(1 for item in evaluations if item.get("schema_valid")) / total, 6
        )
        if total
        else 0.0,
        "history_specificity_rate": (
            round(history_rate, 6) if history_rate is not None else None
        ),
        "history_applicable_jobs": len(history_applicable),
        "history_read_rate": round(
            sum(1 for item in evaluations if item.get("history_read")) / total, 6
        )
        if total
        else 0.0,
        "history_present_jobs": sum(
            1 for item in evaluations if item.get("history_present")
        ),
        "em_ignored": sum(item.get("em_ignored") or 0 for item in evaluations),
        "invalid_entries": sum(item.get("invalid_entries") or 0 for item in evaluations),
        "error_codes": error_codes,
        "guardrail_flags": {"input": input_flags, "output": output_flags},
        "latency_ms": {
            "mean": mean_latency,
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
            "calls": calls,
            "mean_per_job": mean_tokens,
        },
        "cost": {
            "known_jobs": len(costs),
            "total_usd": round(sum(costs), 6) if costs else None,
            "mean_usd": round(statistics.fmean(costs), 6) if costs else None,
        },
        "efficiency": efficiency_score(mean_latency, mean_tokens),
    }


def _values(metrics: dict[str, Any]) -> dict[str, float | None]:
    return {
        "accuracy": metrics["micro"]["f1"],
        "recall": metrics["micro"]["recall"],
        "precision": metrics["micro"]["precision"],
        "provenance": metrics["provenance_rate"],
        "safety": metrics["forbidden_free_rate"],
        "exact_match": metrics["exact_match_rate"],
        "history_specificity": metrics["history_specificity_rate"],
        "efficiency": metrics["efficiency"],
    }


def _series(
    harness_id: str,
    harness_name: str,
    model: str | None,
    evaluations: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics = _metric_block(evaluations)
    label = harness_name if model is None else f"{harness_name} / {model}"
    return {
        "harness_id": harness_id,
        "harness_name": harness_name,
        "model": model,
        "label": label,
        "n": metrics["n"],
        "values": _values(metrics),
        "metrics": metrics,
    }


def _mcnemar_exact_p_value(b: int, c: int) -> float:
    """Exact two-sided binomial p-value for McNemar discordant pairs."""
    n_discordant = b + c
    if n_discordant == 0:
        return 1.0
    k = min(b, c)
    prob = sum(math.comb(n_discordant, i) * (0.5**n_discordant) for i in range(k + 1))
    return min(1.0, round(2.0 * prob, 4))


def _bootstrap_ci(
    deltas: list[float],
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
) -> tuple[float, float]:
    """95% percentile bootstrap confidence interval for paired deltas."""
    if not deltas:
        return 0.0, 0.0
    if len(deltas) == 1 or len(set(deltas)) == 1:
        val = round(deltas[0], 4)
        return val, val
    rng = random.Random(seed)
    n = len(deltas)
    means = [statistics.fmean(deltas[rng.randint(0, n - 1)] for _ in range(n)) for _ in range(n_boot)]
    means.sort()
    low_idx = int(math.floor((alpha / 2.0) * n_boot))
    high_idx = int(math.ceil((1.0 - alpha / 2.0) * n_boot)) - 1
    low_idx = max(0, min(n_boot - 1, low_idx))
    high_idx = max(0, min(n_boot - 1, high_idx))
    return round(means[low_idx], 4), round(means[high_idx], 4)


def _paired(
    evaluations: dict[tuple[str, str, str, int], dict[str, Any]],
    models: list[str],
) -> list[dict[str, Any]]:
    pairs = [("h1", "h2"), ("h1", "h3"), ("h2", "h3")]
    results: list[dict[str, Any]] = []
    for model in models:
        for first, second in pairs:
            left = {
                (case, trial): value
                for (harness, model_key, case, trial), value in evaluations.items()
                if harness == first and model_key == model
            }
            right = {
                (case, trial): value
                for (harness, model_key, case, trial), value in evaluations.items()
                if harness == second and model_key == model
            }
            common = sorted(set(left) & set(right))
            if not common:
                continue
            deltas = [
                float(right[key]["f1"]) - float(left[key]["f1"]) for key in common
            ]
            exact_deltas = [
                int(bool(right[key]["exact_set_match"]))
                - int(bool(left[key]["exact_set_match"]))
                for key in common
            ]
            ci_low, ci_high = _bootstrap_ci(deltas)
            # Discordant pairs for McNemar test:
            # disc_b: A passed exact match, B failed
            # disc_c: A failed exact match, B passed
            disc_b = sum(
                1
                for key in common
                if left[key].get("exact_set_match") and not right[key].get("exact_set_match")
            )
            disc_c = sum(
                1
                for key in common
                if not left[key].get("exact_set_match") and right[key].get("exact_set_match")
            )
            p_val = _mcnemar_exact_p_value(disc_b, disc_c)

            mean_delta_f1 = round(statistics.fmean(deltas), 6)
            if ci_low > 0:
                verdict = f"{second.upper()} beats {first.upper()}"
                is_sig = True
            elif ci_high < 0:
                verdict = f"{first.upper()} beats {second.upper()}"
                is_sig = True
            else:
                verdict = "Inconclusive / noise (CI includes 0)"
                is_sig = False

            results.append(
                {
                    "model": model,
                    "a": first,
                    "b": second,
                    "n": len(common),
                    "mean_delta_f1": mean_delta_f1,
                    "ci_95_low": ci_low,
                    "ci_95_high": ci_high,
                    "wins_b": sum(1 for delta in deltas if delta > 1e-9),
                    "ties": sum(1 for delta in deltas if abs(delta) <= 1e-9),
                    "losses_b": sum(1 for delta in deltas if delta < -1e-9),
                    "mean_delta_exact_match": round(
                        statistics.fmean(exact_deltas), 6
                    ),
                    "mcnemar_p_value": p_val,
                    "is_significant": is_sig,
                    "verdict": verdict,
                    "note": (
                        f"95% bootstrap CI: [{ci_low:+.3f}, {ci_high:+.3f}]. "
                        f"McNemar p={p_val:.4f}. Verdict: {verdict}."
                    ),
                }
            )
    return results


def summarize(
    *,
    comparison_id: str,
    status: str,
    harness_names: dict[str, str],
    models: list[str],
    jobs: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate stored job rows into metrics, radar series, and paired deltas."""
    evaluations: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for job in jobs:
        evaluation = job.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        harness_id = str(job.get("harness_id"))
        model = str(job.get("model"))
        case_id = str(job.get("case_id"))
        trial = int(job.get("trial") or 0)
        evaluation = dict(evaluation)
        if job.get("status") in {"FAILED", "TIMEOUT", "CANCELLED"}:
            evaluation["reliability_ok"] = False
            evaluation["job_status"] = "FAILED"
            if not evaluation.get("error_code"):
                evaluation["error_code"] = str(job.get("status"))
        evaluations[(harness_id, model, case_id, trial)] = evaluation

    # Dynamically discover all unique harness IDs from evaluations and harness_names
    eval_harness_ids = [h for (h, _m, _case, _trial) in evaluations.keys()]
    all_harness_ids = list(dict.fromkeys(
        list(harness_names.keys()) + [h for h in eval_harness_ids if h not in harness_names]
    )) or ["h1", "h2", "h3"]

    series: list[dict[str, Any]] = []
    for harness_id in all_harness_ids:
        harness_name = harness_names.get(harness_id, harness_id.upper())
        for model in models:
            values = [
                value
                for (h, m, _case, _trial), value in evaluations.items()
                if h == harness_id and m == model
            ]
            series.append(_series(harness_id, harness_name, model, values))

    aggregate: list[dict[str, Any]] = []
    for harness_id in all_harness_ids:
        harness_name = harness_names.get(harness_id, harness_id.upper())
        values = [
            value
            for (h, _m, _case, _trial), value in evaluations.items()
            if h == harness_id
        ]
        aggregate.append(_series(harness_id, harness_name, None, values))

    overall = _metric_block(list(evaluations.values()))
    warnings = [
        "Comparative benchmark only. Harnesses differ by agent orchestration, "
        "context access, and execution flow; harness contrasts are descriptive.",
        "Per-case results may be noisy; evaluate qualitative traces alongside quantitative metrics.",
    ]
    if overall["n"] == 0:
        warnings.append("No evaluated jobs are available yet.")
    if overall["error_codes"]:
        failed = sum(overall["error_codes"].values())
        warnings.append(
            f"{failed} job(s) reported harness, budget, guardrail, or timeout errors."
        )
    if len(models) > 1:
        warnings.append(
            "Multiple models are present. Read radar series within a model; "
            "the cross-model aggregate is descriptive only."
        )

    generated = (now or datetime.now(timezone.utc)).isoformat()
    return {
        "comparison_id": comparison_id,
        "status": status,
        "generated_at": generated,
        "axes": RADAR_AXES,
        "series": series,
        "aggregate": aggregate,
        "paired": _paired(evaluations, models),
        "overall": overall,
        "models": models,
        "harnesses": [
            {"id": key, "name": harness_names.get(key, key.upper())}
            for key in all_harness_ids
        ],
        "warnings": warnings,
        "disclaimer": (
            "Empirical agent harness evaluation benchmark. Contrasts between harnesses "
            "are descriptive; evaluate qualitative traces alongside quantitative "
            "metrics when making agent architectural and deployment decisions."
        ),
    }


def redact_for_export(summary: dict[str, Any]) -> dict[str, Any]:
    """Export-safe summary: no raw outputs, only scores and counts."""
    return json.loads(json.dumps(summary))
