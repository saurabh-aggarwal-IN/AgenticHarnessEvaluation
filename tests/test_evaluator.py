from __future__ import annotations

from typing import Any

import pytest

from conftest import CASE_ONE_NOTE, make_expected
from harness_compare.dataset import CaseRecord
from harness_compare.evaluator import (
    RADAR_AXES,
    efficiency_score,
    empty_evaluation,
    evaluate_response,
    summarize,
)

CASE = CaseRecord(
    case_id="C01",
    title="T2DM and CHF with EKG",
    encounter_note=CASE_ONE_NOTE,
    prompt="Assign codes.",
    history={"patientId": "SYN-C01", "activeConditions": ["type 2 diabetes"]},
)

EXPECTED = make_expected(
    "C01",
    [("E11.9", "ICD-10-CM"), ("I50.22", "ICD-10-CM"), ("93000", "CPT")],
    forbidden=[("J44.1", "ICD-10-CM")],
    history_codes={"E11.9"},
)

PERFECT_CODES = [
    {
        "code": "E11.9",
        "system": "ICD-10-CM",
        "snippet": "stable chronic T2DM without complications",
        "rationale": "documented",
    },
    {
        "code": "I50.22",
        "system": "ICD-10-CM",
        "snippet": "stable chronic systolic heart failure",
        "rationale": "documented",
    },
    {
        "code": "93000",
        "system": "CPT",
        "snippet": "A 12-lead EKG was performed in the office today",
        "rationale": "procedure documented",
    },
]


def response(codes: list[dict[str, Any]], **overrides: Any) -> dict[str, Any]:
    payload = {
        "request_id": "req-1",
        "case_id": "C01",
        "codes": codes,
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150, "calls": 2},
        "latency_ms": 900,
        "trace": [{"type": "history_read", "payload": {}}],
        "budget": {"used": {"cost_usd": 0.01}, "exceeded": False},
        "guardrails": {"input_flags": [], "output_flags": [], "blocked": False},
        "error": None,
    }
    payload.update(overrides)
    return payload


def test_perfect_response_scores_every_axis() -> None:
    result = evaluate_response(CASE, EXPECTED, response(PERFECT_CODES))
    assert result["f1"] == 1.0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["exact_set_match"] is True
    assert result["provenance_ok"] is True
    assert result["grounded_rate"] == 1.0
    assert result["safety_ok"] is True
    assert result["history_specificity"] == 1.0
    assert result["usage"]["total_tokens"] == 150
    assert result["cost_usd"] == 0.01
    assert result["job_status"] == "COMPLETED"


def test_missing_codes_lower_recall_not_precision() -> None:
    result = evaluate_response(CASE, EXPECTED, response(PERFECT_CODES[:2]))
    assert result["expected_count"] == 3
    assert result["returned_count"] == 2
    assert result["precision"] == 1.0
    assert result["recall"] == round(2 / 3, 6)
    assert result["missing_codes"] == ["93000"]
    assert result["exact_set_match"] is False


def test_extra_code_lowers_precision_and_is_reported() -> None:
    codes = PERFECT_CODES + [
        {
            "code": "R73.09",
            "system": "ICD-10-CM",
            "snippet": "stable chronic T2DM without complications",
            "rationale": "extra",
        }
    ]
    result = evaluate_response(CASE, EXPECTED, response(codes))
    assert result["extra_codes"] == ["R73.09"]
    assert result["false_positives"] == 1
    assert result["precision"] == 0.75
    assert result["exact_set_match"] is False


def test_forbidden_code_is_a_hard_safety_failure() -> None:
    codes = PERFECT_CODES + [
        {
            "code": "J44.1",
            "system": "ICD-10-CM",
            "snippet": "stable chronic systolic heart failure",
            "rationale": "unjustified",
        }
    ]
    result = evaluate_response(CASE, EXPECTED, response(codes))
    assert result["forbidden_hits"] == ["J44.1"]
    assert result["forbidden_free"] is False
    assert result["safety_ok"] is False
    assert any("Forbidden" in note for note in result["notes"])


def test_em_codes_are_ignored_not_counted_as_errors() -> None:
    codes = PERFECT_CODES + [
        {
            "code": "99213",
            "system": "CPT",
            "snippet": "A 12-lead EKG was performed in the office today",
            "rationale": "office visit",
        }
    ]
    result = evaluate_response(CASE, EXPECTED, response(codes))
    assert result["em_ignored"] == 1
    assert result["returned_count"] == 3
    assert result["f1"] == 1.0
    assert result["schema_valid"] is True


def test_provenance_requires_a_verbatim_note_snippet() -> None:
    codes = [dict(PERFECT_CODES[0], snippet="not present in the note")]
    result = evaluate_response(CASE, EXPECTED, response(codes))
    assert result["ungrounded_codes"] == ["E11.9"]
    assert result["provenance_ok"] is False
    assert any("Ungrounded" in note for note in result["notes"])


def test_history_only_snippet_does_not_count_as_note_provenance() -> None:
    case = CaseRecord(
        case_id="C01",
        title="history test",
        encounter_note="Assessment: diabetes follow-up today.",
        prompt="Assign codes.",
        history={"patientId": "SYN-C01", "activeConditions": ["E11.9"]},
    )
    codes = [
        {
            "code": "E11.9",
            "system": "ICD-10-CM",
            "snippet": "E11.9",
            "rationale": "from history",
        }
    ]
    result = evaluate_response(case, EXPECTED, response(codes))
    assert result["provenance_ok"] is False


def test_history_availability_reflects_what_the_harness_received() -> None:
    result = evaluate_response(
        CASE,
        EXPECTED,
        response(PERFECT_CODES),
        history_available=False,
    )
    assert result["history_present"] is False
    assert result["history_read"] is True
    assert result["history_specificity"] == 1.0


def test_history_specificity_requires_a_gold_history_snippet() -> None:
    # requiredHistory.mustRead is a retrieval assertion, not an output
    # obligation. A case can require reading history without any gold code
    # whose specificity depends on it (C01-C04 in the canonical dataset).
    expected = dict(EXPECTED)
    expected["requiredHistory"] = {
        "mustRead": True,
        "historyCodes": ["E11.9", "J44.1"],
    }
    expected["expectedCodes"] = [
        {key: value for key, value in item.items() if key != "historySnippet"}
        for item in EXPECTED["expectedCodes"]
    ]
    result = evaluate_response(CASE, expected, response(PERFECT_CODES))
    assert result["history_required_codes"] == []
    assert result["history_specificity"] is None


def test_forbidden_history_code_is_not_a_specificity_requirement() -> None:
    # C05-style trap: historyCodes names a historical HCC that must NOT be
    # recaptured, so it cannot count as a required history gold code.
    expected = {
        **EXPECTED,
        "expectedCodes": [PERFECT_CODES[0]],
        "forbiddenCodes": [
            {"code": "J44.1", "system": "ICD-10-CM", "reason": "historical HCC"}
        ],
        "requiredHistory": {"mustRead": True, "historyCodes": ["J44.1"]},
    }
    result = evaluate_response(CASE, expected, response(PERFECT_CODES[:1]))
    assert result["forbidden_free"] is True
    assert result["history_required_codes"] == []
    assert result["history_specificity"] is None


def test_history_specificity_counts_missing_gold_code_as_zero() -> None:
    expected = {
        **EXPECTED,
        "expectedCodes": [
            {
                "code": "Z94.0",
                "system": "ICD-10-CM",
                "snippet": "routine follow-up of transplant",
                "historySnippet": "Kidney transplant status",
            }
        ],
        "forbiddenCodes": [],
    }
    result = evaluate_response(CASE, expected, response([]))
    assert result["history_required_codes"] == ["Z94.0"]
    assert result["history_specificity"] == 0.0


def test_empty_response_is_a_failed_job_not_a_perfect_zero_code_set() -> None:
    result = empty_evaluation(
        CASE, EXPECTED, error_code="BUDGET_EXCEEDED", message="calls exhausted"
    )
    assert result["job_status"] == "FAILED"
    assert result["error_code"] == "BUDGET_EXCEEDED"
    assert result["schema_valid"] is False
    assert result["reliability_ok"] is False
    assert result["f1"] == 0.0


def test_schema_invalid_is_flagged_even_without_an_error() -> None:
    result = evaluate_response(
        CASE, EXPECTED, response([{"code": "", "snippet": "x", "system": "CPT"}])
    )
    assert result["schema_valid"] is False
    assert result["invalid_entries"] == 1


def test_efficiency_is_bounded_and_monotonic() -> None:
    fast = efficiency_score(500.0, 1_000.0)
    slow = efficiency_score(50_000.0, 20_000.0)
    assert 0.0 < slow < fast < 1.0


def _job(
    harness: str,
    *,
    result: dict[str, Any],
    status: str = "COMPLETED",
    model: str = "model/a",
) -> dict[str, Any]:
    return {
        "harness_id": harness,
        "model": model,
        "case_id": "C01",
        "trial": 1,
        "status": status,
        "evaluation": result,
    }


def test_summary_metrics_radar_and_paired_deltas() -> None:
    perfect = evaluate_response(CASE, EXPECTED, response(PERFECT_CODES))
    partial = evaluate_response(CASE, EXPECTED, response(PERFECT_CODES[:1]))
    failed = empty_evaluation(CASE, EXPECTED, error_code="WORKER_EXITED")
    jobs = [
        _job("h1", result=partial),
        _job("h2", result=perfect),
        _job("h3", result=failed, status="FAILED"),
    ]
    summary = summarize(
        comparison_id="cmp-1",
        status="COMPLETED",
        harness_names={"h1": "H1", "h2": "H2", "h3": "H3"},
        models=["model/a"],
        jobs=jobs,
    )
    assert len(summary["axes"]) == len(RADAR_AXES) == 8
    assert len(summary["series"]) == 3
    assert len(summary["aggregate"]) == 3
    h1 = next(item for item in summary["series"] if item["harness_id"] == "h1")
    h2 = next(item for item in summary["series"] if item["harness_id"] == "h2")
    assert h1["values"]["accuracy"] < h2["values"]["accuracy"]
    assert h2["values"]["accuracy"] == 1.0
    assert h1["metrics"]["successful_jobs"] == 1
    assert h1["metrics"]["micro"]["expected"] == 3
    assert h2["metrics"]["history_read_rate"] == 1.0
    paired = summary["paired"][0]
    assert paired["a"] == "h1" and paired["b"] == "h2"
    assert paired["mean_delta_f1"] > 0
    assert summary["overall"]["n"] == 3
    assert any("descriptive" in warning.lower() for warning in summary["warnings"])


def test_summary_marks_non_reliable_jobs_and_warns() -> None:
    failed = empty_evaluation(CASE, EXPECTED, error_code="JOB_TIMEOUT")
    summary = summarize(
        comparison_id="cmp-2",
        status="FAILED",
        harness_names={"h1": "H1", "h2": "H2", "h3": "H3"},
        models=["model/a"],
        jobs=[_job("h1", result=failed, status="TIMEOUT")],
    )
    assert summary["aggregate"][0]["metrics"]["success_rate"] == 0.0
    assert summary["aggregate"][0]["metrics"]["error_codes"] == {"JOB_TIMEOUT": 1}
    assert any("error" in warning.lower() for warning in summary["warnings"])


def test_summary_with_no_jobs_is_explicit() -> None:
    summary = summarize(
        comparison_id="cmp-3",
        status="QUEUED",
        harness_names={"h1": "H1", "h2": "H2", "h3": "H3"},
        models=["model/a"],
        jobs=[],
    )
    assert summary["overall"]["n"] == 0
    assert all(item["n"] == 0 for item in summary["series"])
    assert any("No evaluated jobs" in warning for warning in summary["warnings"])


@pytest.mark.parametrize("tokens", [0, 1_000, 100_000])
def test_efficiency_never_exceeds_one(tokens: int) -> None:
    assert 0.0 < efficiency_score(0.0, tokens) <= 1.0
