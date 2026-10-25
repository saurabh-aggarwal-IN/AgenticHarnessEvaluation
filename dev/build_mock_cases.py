#!/usr/bin/env python3
"""Build ``mock_cases.json`` for the local deterministic mock router.

The mock is a test double, not a model. It exists so the orchestrator's Docker,
Redis, Postgres, evaluation, and radar paths can be exercised without a real
router. It returns the canonical gold codes and scripts two behaviors that make
the harness differences visible:

* C05: when a history fixture is present, the scripted coder recaptures the
  historical COPD HCC and the H3 verifier removes it.
* C06: only harnesses that receive history can return the kidney-specific
  ``Z94.0``; without history the scripted coder returns ``Z94.9``.

Do not use these outputs to rank harness quality. They are deliberately
scripted and are labeled as such in the generated file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(dataset_dir: Path) -> dict:
    manifest = _load(dataset_dir / "manifest.json")
    cases: list[dict] = []

    for case_path in sorted((dataset_dir / "cases").glob("*.json")):
        case = _load(case_path)
        case_id = case["caseId"]
        expected = _load(dataset_dir / "expected" / f"{case_id}.expected.json")

        codes = [
            {
                "code": item["code"],
                "system": item["system"],
                "snippet": item["snippet"],
                "label": item.get("label") or item["code"],
                "rationale": item.get("rationale") or "Documented in the note.",
            }
            for item in expected.get("expectedCodes") or []
        ]
        entry: dict = {
            "case_id": case_id,
            "title": case.get("title") or case_id,
            "marker": " ".join(case["encounterNote"].split())[:96],
            "codes": codes,
            "forbidden": [
                item["code"] for item in expected.get("forbiddenCodes") or []
            ],
        }

        if case_id == "C05":
            # The historical HCC is in the history fixture but nowhere in the
            # current note. The scripted coder recaptures it when history is
            # visible; the verifier removes it.
            entry["trap_code"] = "J44.1"
            entry["trap_label"] = "COPD with (acute) exacerbation"
            entry["trap_snippet"] = "type 2 diabetes mellitus"

        if case_id == "C06":
            history_snippet = next(
                (
                    item["historySnippet"]
                    for item in expected.get("expectedCodes") or []
                    if item.get("historySnippet")
                ),
                "",
            )
            without_history = next(
                (
                    item["code"]
                    for item in expected.get("forbiddenCodes") or []
                    if item["code"] == "Z94.9"
                ),
                "",
            )
            entry["history_marker"] = history_snippet
            entry["history_code"] = codes[0]["code"]
            entry["without_history_code"] = without_history
            entry["without_history_label"] = "Unspecified organ transplant status"

        cases.append(entry)

    return {
        "datasetId": manifest.get("datasetId"),
        "version": manifest.get("version"),
        "disclaimer": (
            "Deterministic mock router for orchestration tests only. "
            "Outputs are scripted from the synthetic dataset; do not rank "
            "harness quality from them."
        ),
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "Dataset",
        help="path to the canonical synthetic dataset directory",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().with_name("mock_cases.json"),
        help="output JSON path",
    )
    args = parser.parse_args()
    payload = build(args.dataset.resolve())
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out} ({len(payload['cases'])} cases)")


if __name__ == "__main__":
    main()
