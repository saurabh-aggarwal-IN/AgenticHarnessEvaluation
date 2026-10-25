from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mock() -> ModuleType:
    path = ROOT / "dev" / "mock_llm.py"
    spec = importlib.util.spec_from_file_location("cc_dev_mock_llm", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ask(mock: ModuleType, kind: str, case: dict, history: dict | None) -> dict:
    system = {
        "planner": mock.PLANNER_MARK,
        "coder": mock.CODER_MARK,
        "verifier": mock.VERIFIER_MARK,
    }[kind]
    history_text = json.dumps(history) if history is not None else "{}"
    user = (
        f"ENCOUNTER NOTE\n{case['marker']}\n\n"
        f"HISTORY FIXTURE\n{history_text}"
    )
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    }
    return json.loads(mock.response_for(payload))


def test_every_case_marker_resolves(mock: ModuleType) -> None:
    for case in mock.CASES:
        assert mock.find_case(case["marker"])["case_id"] == case["case_id"]


def test_c06_needs_history_for_the_kidney_specific_code(mock: ModuleType) -> None:
    case = mock.BY_ID["C06"]
    without = _ask(mock, "coder", case, None)
    with_history = _ask(
        mock,
        "coder",
        case,
        {
            "patientId": "SYN-C06",
            "activeConditions": [{"label": "Kidney transplant status"}],
        },
    )
    assert [item["code"] for item in without["codes"]] == ["Z94.9"]
    assert [item["code"] for item in with_history["codes"]] == ["Z94.0"]


def test_c05_trap_is_removed_by_the_verifier(mock: ModuleType) -> None:
    case = mock.BY_ID["C05"]
    history = {"patientId": "SYN-C05", "activeHccs": [{"label": "COPD"}]}
    draft = _ask(mock, "coder", case, history)
    assert [item["code"] for item in draft["codes"]] == ["E11.9", "J44.1"]

    base = (
        f"ENCOUNTER NOTE\n{case['marker']}\n\n"
        f"HISTORY FIXTURE\n{json.dumps(history)}\n\n"
        "PLANNER FINDINGS\n{}\n\nCODER DRAFT\n"
    )

    def verify(codes: dict) -> dict:
        user = (
            base
            + json.dumps(codes)
            + "\n\nProduce the verifier JSON now."
        )
        payload = {
            "messages": [
                {"role": "system", "content": mock.VERIFIER_MARK},
                {"role": "user", "content": user},
            ]
        }
        return json.loads(mock.response_for(payload))

    rejected = verify(draft)
    assert rejected["approved"] is False
    assert [item["code"] for item in rejected["codes"]] == ["E11.9"]

    approved = verify({"codes": rejected["codes"]})
    assert approved["approved"] is True
    assert [item["code"] for item in approved["codes"]] == ["E11.9"]
