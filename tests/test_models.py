from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness_compare.models import CommitSet, ComparisonCreate, JobBudget


def _body(**overrides):
    payload = {
        "repo_url": "https://github.com/example/repo",
        "commits": {
            "h1": "938bb8b",
            "h2": "6c94476",
            "h3": "517120c",
        },
        "dataset_id": "ds-1",
        "models": ["model/a"],
        "trials": 1,
    }
    payload.update(overrides)
    return payload


def test_commit_set_normalizes_and_keeps_order() -> None:
    commits = CommitSet(h1="ABCDEF1", h2="abcdef2", h3="ABCDEF3")
    assert commits.h1 == "abcdef1"
    assert commits.h2 == "abcdef2"
    assert commits.as_dict()["h3"] == "abcdef3"


def test_commit_set_requires_three_distinct_commits() -> None:
    with pytest.raises(ValidationError):
        CommitSet(h1="abcdef1", h2="abcdef1", h3="abcdef2")


def test_commit_set_rejects_non_hex_and_short_values() -> None:
    for bad in ("xyz1234", "abc", "1234567890" * 5):
        with pytest.raises(ValidationError):
            CommitSet(h1=bad, h2="abcdef2", h3="abcdef3")


def test_models_are_trimmed_and_deduplicated() -> None:
    body = ComparisonCreate(**_body(models=[" model/a ", "model/b", "model/a"]))
    assert body.models == ["model/a", "model/b"]


@pytest.mark.parametrize(
    "model",
    ["", "../evil", "-flag", "bad model", "x" * 201, "model\n/a"],
)
def test_invalid_model_slugs_are_rejected(model: str) -> None:
    with pytest.raises(ValidationError):
        ComparisonCreate(**_body(models=[model]))


def test_router_style_model_slugs_are_accepted() -> None:
    body = ComparisonCreate(
        **_body(
            models=[
                "openai/gpt-5",
                "anthropic/claude-sonnet-4.5",
                "meta-llama/Llama-4:70b",
                "vendor/model@2026-01",
                "vendor/model[beta]",
            ]
        )
    )
    assert len(body.models) == 5


def test_budget_is_serialized_without_none_values() -> None:
    budget = JobBudget(max_llm_calls=3, max_wall_seconds=30)
    assert budget.as_request() == {"max_llm_calls": 3, "max_wall_seconds": 30}


def test_budget_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        JobBudget(max_llm_calls=0)
    with pytest.raises(ValidationError):
        JobBudget(max_llm_calls=100)
    with pytest.raises(ValidationError):
        JobBudget(max_wall_seconds=1)
    with pytest.raises(ValidationError):
        JobBudget(max_cost_usd=-1)


def test_comparison_rejects_control_characters_in_repo_url() -> None:
    with pytest.raises(ValidationError):
        ComparisonCreate(**_body(repo_url="https://github.com/x\nrepo"))


def test_trials_are_bounded() -> None:
    with pytest.raises(ValidationError):
        ComparisonCreate(**_body(trials=0))
    with pytest.raises(ValidationError):
        ComparisonCreate(**_body(trials=6))
