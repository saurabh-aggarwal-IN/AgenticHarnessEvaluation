from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

import pytest

from harness_compare.dataset import (
    DatasetError,
    build_harness_request,
    find_dataset_root,
    load_dataset,
    safe_extract_zip,
)


def test_loads_minimal_dataset(dataset_dir: Path) -> None:
    bundle = load_dataset(dataset_dir)
    assert bundle.case_count == 2
    assert [case.case_id for case in bundle.cases] == ["C01", "C02"]
    assert set(bundle.expected) == {"C01", "C02"}
    assert bundle.id.startswith("ds-")
    assert len(bundle.digest) == 64


def test_loads_canonical_dataset(canonical_dataset: Path) -> None:
    bundle = load_dataset(canonical_dataset)
    assert bundle.case_count == 10
    assert len(bundle.expected) == 10
    expected_codes = sum(
        len(item["expectedCodes"]) for item in bundle.expected.values()
    )
    assert expected_codes == 22
    assert bundle.manifest.get("datasetId")


def test_harness_request_never_contains_grading_keys(dataset_dir: Path) -> None:
    bundle = load_dataset(dataset_dir)
    case = bundle.case("C01")
    payload = build_harness_request(
        case,
        model="router/model",
        budget={"max_llm_calls": 2},
        request_id="job-1",
    )
    serialized = json.dumps(payload)
    for forbidden in (
        "expectedCodes",
        "forbiddenCodes",
        "historySnippet",
        "coding_key",
        "codingKey",
        "goldCodes",
    ):
        assert forbidden not in serialized
    assert set(payload) == {
        "case_id",
        "encounter_note",
        "prompt",
        "history",
        "model",
        "budget",
        "request_id",
    }


def test_case_with_grading_key_is_rejected(dataset_dir: Path) -> None:
    path = dataset_dir / "cases" / "C02-uti.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["forbiddenCodes"] = [{"code": "X"}]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(dataset_dir, verify_checksums=False)
    assert exc.value.code == "DATASET_GRADING_KEY_IN_CASE"


def test_non_synthetic_case_is_rejected(dataset_dir: Path) -> None:
    path = dataset_dir / "cases" / "C02-uti.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["synthetic"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(dataset_dir, verify_checksums=False)
    assert exc.value.code == "DATASET_NOT_SYNTHETIC"


def test_phi_case_is_rejected(dataset_dir: Path) -> None:
    path = dataset_dir / "cases" / "C02-uti.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["phi"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(dataset_dir, verify_checksums=False)
    assert exc.value.code == "DATASET_PHI"


def test_missing_expected_output_is_rejected(dataset_dir: Path) -> None:
    (dataset_dir / "expected" / "C02.expected.json").unlink()
    with pytest.raises(DatasetError) as exc:
        load_dataset(dataset_dir, verify_checksums=False)
    assert exc.value.code == "DATASET_EXPECTED_MISSING"


def test_forbidden_code_may_not_also_be_expected(dataset_dir: Path) -> None:
    path = dataset_dir / "expected" / "C02.expected.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["forbiddenCodes"].append(
        {"code": "N39.0", "system": "ICD-10-CM", "reason": "bad"}
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(dataset_dir, verify_checksums=False)
    assert exc.value.code == "DATASET_EXPECTED_FORBIDDEN_OVERLAP"


def test_checksum_mismatch_is_rejected(dataset_dir: Path) -> None:
    import hashlib

    checksums = {"algorithm": "sha256", "files": {}}
    for path in sorted(dataset_dir.rglob("*")):
        if path.is_file() and path.name != "checksums.json":
            relative = path.relative_to(dataset_dir).as_posix()
            checksums["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    (dataset_dir / "checksums.json").write_text(
        json.dumps(checksums), encoding="utf-8"
    )
    (dataset_dir / "cases" / "C02-uti.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetError) as exc:
        load_dataset(dataset_dir)
    assert exc.value.code == "DATASET_CHECKSUM_MISMATCH"


def test_zip_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "nope")
    with pytest.raises(DatasetError) as exc:
        safe_extract_zip(archive, tmp_path / "out", max_bytes=1_000)
    assert exc.value.code == "DATASET_ZIP_PATH"


def test_zip_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "target")
    with pytest.raises(DatasetError) as exc:
        safe_extract_zip(archive, tmp_path / "out", max_bytes=1_000)
    assert exc.value.code == "DATASET_ZIP_SYMLINK"


def test_zip_bomb_size_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("cases/big.json", "x" * 2_000)
    with pytest.raises(DatasetError) as exc:
        safe_extract_zip(archive, tmp_path / "out", max_bytes=1_000)
    assert exc.value.code == "DATASET_ZIP_TOO_LARGE"


def test_find_dataset_root_allows_one_nested_directory(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    (root / "my-dataset" / "cases").mkdir(parents=True)
    (root / "my-dataset" / "expected").mkdir(parents=True)
    assert find_dataset_root(root) == root / "my-dataset"


def test_find_dataset_root_rejects_ambiguous_layout(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name / "cases").mkdir(parents=True)
        (tmp_path / name / "expected").mkdir(parents=True)
    with pytest.raises(DatasetError) as exc:
        find_dataset_root(tmp_path)
    assert exc.value.code == "DATASET_LAYOUT"
