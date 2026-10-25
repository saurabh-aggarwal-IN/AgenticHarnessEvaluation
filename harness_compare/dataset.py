from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
FORBIDDEN_CASE_KEYS = {
    "expectedCodes",
    "forbiddenCodes",
    "historySnippet",
    "coding_key",
    "codingKey",
    "goldCodes",
}


class DatasetError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CaseRecord:
    case_id: str
    title: str
    encounter_note: str
    prompt: str
    history: dict[str, Any] | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DatasetBundle:
    id: str
    name: str
    digest: str
    root: Path
    cases: list[CaseRecord]
    expected: dict[str, dict[str, Any]]
    manifest: dict[str, Any]

    @property
    def case_count(self) -> int:
        return len(self.cases)

    def case(self, case_id: str) -> CaseRecord:
        for item in self.cases:
            if item.case_id == case_id:
                return item
        raise KeyError(case_id)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(
            "DATASET_INVALID_JSON", f"{path.name}: {exc}"
        ) from exc


def _scan_keys(value: Any, path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_CASE_KEYS:
                hits.append(f"{path}.{key}".lstrip("."))
            hits.extend(_scan_keys(item, f"{path}.{key}".lstrip(".")))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            hits.extend(_scan_keys(item, f"{path}[{index}]"))
    return hits


def _validate_case(data: dict[str, Any], path: Path, max_note_chars: int) -> CaseRecord:
    case_id = str(data.get("caseId") or "").strip()
    if not CASE_ID_RE.match(case_id):
        raise DatasetError(
            "DATASET_CASE_ID", f"{path.name}: invalid caseId {case_id!r}"
        )
    if data.get("synthetic") is not True:
        raise DatasetError(
            "DATASET_NOT_SYNTHETIC", f"{case_id}: synthetic must be true"
        )
    if data.get("phi") is not False:
        raise DatasetError("DATASET_PHI", f"{case_id}: phi must be false")
    note = data.get("encounterNote")
    if not isinstance(note, str) or len(note.strip()) < 20:
        raise DatasetError(
            "DATASET_NOTE", f"{case_id}: encounterNote is missing or too short"
        )
    if len(note) > max_note_chars:
        raise DatasetError(
            "DATASET_NOTE_TOO_LARGE",
            f"{case_id}: encounterNote exceeds {max_note_chars} characters",
        )
    task = data.get("task")
    prompt = task.get("prompt") if isinstance(task, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        raise DatasetError(
            "DATASET_PROMPT", f"{case_id}: task.prompt is required"
        )
    history = data.get("history")
    if history is not None and not isinstance(history, dict):
        raise DatasetError(
            "DATASET_HISTORY", f"{case_id}: history must be an object or null"
        )
    leaked = _scan_keys(data)
    if leaked:
        raise DatasetError(
            "DATASET_GRADING_KEY_IN_CASE",
            f"{case_id}: case input contains grading-key fields: {leaked}",
        )
    metadata = data.get("metadata")
    return CaseRecord(
        case_id=case_id,
        title=str(data.get("title") or case_id),
        encounter_note=note,
        prompt=prompt.strip(),
        history=history,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _validate_expected(
    data: dict[str, Any], path: Path, case_id: str
) -> dict[str, Any]:
    actual = str(data.get("caseId") or "").strip()
    if actual != case_id:
        raise DatasetError(
            "DATASET_EXPECTED_CASE_MISMATCH",
            f"{path.name}: caseId {actual!r} does not match {case_id!r}",
        )
    expected_codes = data.get("expectedCodes")
    if not isinstance(expected_codes, list) or not expected_codes:
        raise DatasetError(
            "DATASET_EXPECTED_CODES",
            f"{case_id}: expectedCodes must be a non-empty list",
        )
    seen: set[str] = set()
    for item in expected_codes:
        if not isinstance(item, dict):
            raise DatasetError(
                "DATASET_EXPECTED_CODES", f"{case_id}: expectedCodes entry is not an object"
            )
        code = str(item.get("code") or "").strip().upper()
        if not code:
            raise DatasetError(
                "DATASET_EXPECTED_CODES", f"{case_id}: expected code is empty"
            )
        if code in seen:
            raise DatasetError(
                "DATASET_EXPECTED_CODES", f"{case_id}: duplicate expected code {code}"
            )
        seen.add(code)
        item["code"] = code
        system = item.get("system")
        if system not in {"ICD-10-CM", "CPT"}:
            raise DatasetError(
                "DATASET_EXPECTED_SYSTEM",
                f"{case_id}: unsupported expected system {system!r}",
            )
    forbidden_codes = data.get("forbiddenCodes") or []
    if not isinstance(forbidden_codes, list):
        raise DatasetError(
            "DATASET_FORBIDDEN_CODES",
            f"{case_id}: forbiddenCodes must be a list",
        )
    forbidden: set[str] = set()
    for item in forbidden_codes:
        if not isinstance(item, dict):
            raise DatasetError(
                "DATASET_FORBIDDEN_CODES",
                f"{case_id}: forbiddenCodes entry is not an object",
            )
        code = str(item.get("code") or "").strip().upper()
        if not code:
            raise DatasetError(
                "DATASET_FORBIDDEN_CODES", f"{case_id}: forbidden code is empty"
            )
        if code in seen:
            raise DatasetError(
                "DATASET_EXPECTED_FORBIDDEN_OVERLAP",
                f"{case_id}: {code} is both expected and forbidden",
            )
        item["code"] = code
        forbidden.add(code)
    grading = data.get("grading") or {}
    if not isinstance(grading, dict):
        raise DatasetError(
            "DATASET_GRADING", f"{case_id}: grading must be an object"
        )
    for pattern in grading.get("ignoreCodePatterns") or []:
        try:
            re.compile(str(pattern))
        except re.error as exc:
            raise DatasetError(
                "DATASET_GRADING", f"{case_id}: bad ignore pattern {pattern!r}: {exc}"
            ) from exc
    return data


def _verify_checksums(root: Path) -> None:
    path = root / "checksums.json"
    if not path.is_file():
        return
    data = _read_json(path)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise DatasetError(
            "DATASET_CHECKSUMS", "checksums.json is missing a files map"
        )
    algorithm = str(data.get("algorithm") or "sha256").lower()
    if algorithm != "sha256":
        raise DatasetError(
            "DATASET_CHECKSUMS", f"unsupported checksum algorithm {algorithm!r}"
        )
    for relative, expected in files.items():
        target = (root / relative).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            raise DatasetError(
                "DATASET_CHECKSUMS", f"checksum path escapes dataset: {relative}"
            )
        if not target.is_file():
            raise DatasetError(
                "DATASET_CHECKSUMS", f"checksummed file is missing: {relative}"
            )
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != expected:
            raise DatasetError(
                "DATASET_CHECKSUM_MISMATCH",
                f"checksum mismatch for {relative}",
            )


def _digest(root: Path) -> str:
    digest = hashlib.sha256()
    files: list[Path] = []
    for pattern in ("cases/*.json", "expected/*.json", "manifest.json"):
        files.extend(sorted(root.glob(pattern)))
    if not files:
        raise DatasetError("DATASET_EMPTY", f"no dataset files under {root}")
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_dataset(
    root: Path,
    *,
    name: str | None = None,
    max_cases: int = 100,
    max_note_chars: int = 50_000,
    verify_checksums: bool = True,
) -> DatasetBundle:
    root = root.resolve()
    if not root.is_dir():
        raise DatasetError("DATASET_NOT_FOUND", f"dataset path is not a directory: {root}")
    cases_dir = root / "cases"
    expected_dir = root / "expected"
    if not cases_dir.is_dir():
        raise DatasetError("DATASET_CASES", f"missing cases/ under {root}")
    if not expected_dir.is_dir():
        raise DatasetError("DATASET_EXPECTED", f"missing expected/ under {root}")
    if verify_checksums:
        _verify_checksums(root)

    case_files = sorted(cases_dir.glob("*.json"))
    if not case_files:
        raise DatasetError("DATASET_EMPTY", "cases/ contains no JSON files")
    if len(case_files) > max_cases:
        raise DatasetError(
            "DATASET_TOO_MANY_CASES",
            f"{len(case_files)} cases exceeds the limit of {max_cases}",
        )

    cases: list[CaseRecord] = []
    seen: set[str] = set()
    for path in case_files:
        data = _read_json(path)
        if not isinstance(data, dict):
            raise DatasetError(
                "DATASET_INVALID_JSON", f"{path.name}: case must be a JSON object"
            )
        case = _validate_case(data, path, max_note_chars)
        if case.case_id in seen:
            raise DatasetError(
                "DATASET_DUPLICATE_CASE", f"duplicate caseId {case.case_id}"
            )
        seen.add(case.case_id)
        cases.append(case)

    expected: dict[str, dict[str, Any]] = {}
    for path in sorted(expected_dir.glob("*.json")):
        data = _read_json(path)
        if not isinstance(data, dict):
            raise DatasetError(
                "DATASET_INVALID_JSON", f"{path.name}: expected must be a JSON object"
            )
        case_id = str(data.get("caseId") or "").strip()
        if case_id not in seen:
            raise DatasetError(
                "DATASET_EXPECTED_ORPHAN",
                f"{path.name}: expected output has no matching case {case_id!r}",
            )
        expected[case_id] = _validate_expected(data, path, case_id)

    missing = sorted(seen - set(expected))
    if missing:
        raise DatasetError(
            "DATASET_EXPECTED_MISSING",
            f"missing expected output for: {', '.join(missing)}",
        )

    manifest = {}
    if (root / "manifest.json").is_file():
        loaded = _read_json(root / "manifest.json")
        if isinstance(loaded, dict):
            manifest = loaded

    digest = _digest(root)
    dataset_id = f"ds-{digest[:16]}"
    return DatasetBundle(
        id=dataset_id,
        name=name or str(manifest.get("datasetId") or root.name),
        digest=digest,
        root=root,
        cases=sorted(cases, key=lambda item: item.case_id),
        expected=expected,
        manifest=manifest,
    )


def build_harness_request(
    case: CaseRecord,
    *,
    model: str,
    budget: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Build the only payload a harness receives.

    Expected codes, forbidden codes, snippets, and every other grading artifact
    stay in the orchestrator process and are never placed in this object.
    """
    payload: dict[str, Any] = {
        "case_id": case.case_id,
        "encounter_note": case.encounter_note,
        "prompt": case.prompt,
        "history": case.history,
        "model": model,
    }
    if budget:
        payload["budget"] = budget
    if request_id:
        payload["request_id"] = request_id
    return payload


def safe_extract_zip(
    archive: Path,
    destination: Path,
    *,
    max_bytes: int,
    max_files: int = 500,
) -> Path:
    """Extract a dataset zip without allowing traversal, symlinks, or bombs."""
    destination.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > max_files:
                raise DatasetError(
                    "DATASET_ZIP_TOO_MANY_FILES",
                    f"zip contains {len(members)} entries; limit {max_files}",
                )
            for item in members:
                name = item.filename
                if (
                    not name
                    or name.startswith(("/", "\\"))
                    or re.match(r"^[A-Za-z]:", name)
                    or "\\" in name
                    or ".." in Path(name).parts
                    or "\x00" in name
                ):
                    raise DatasetError(
                        "DATASET_ZIP_PATH", f"unsafe zip entry: {name!r}"
                    )
                mode = (item.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise DatasetError(
                        "DATASET_ZIP_SYMLINK", f"symlink entry is not allowed: {name}"
                    )
                if item.flag_bits & 0x1:
                    raise DatasetError(
                        "DATASET_ZIP_ENCRYPTED", "encrypted zip entries are not allowed"
                    )
                if item.is_dir():
                    continue
                total += item.file_size
                if total > max_bytes:
                    raise DatasetError(
                        "DATASET_ZIP_TOO_LARGE",
                        f"uncompressed zip exceeds {max_bytes} bytes",
                    )
                target = (destination / name).resolve()
                if destination.resolve() not in target.parents:
                    raise DatasetError(
                        "DATASET_ZIP_PATH", f"zip entry escapes destination: {name!r}"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except zipfile.BadZipFile as exc:
        raise DatasetError("DATASET_ZIP_INVALID", "upload is not a valid zip") from exc
    return destination


def find_dataset_root(root: Path) -> Path:
    """Locate the directory that contains cases/ and expected/."""
    if (root / "cases").is_dir() and (root / "expected").is_dir():
        return root
    candidates: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "cases").is_dir() and (child / "expected").is_dir():
            candidates.append(child)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise DatasetError(
            "DATASET_LAYOUT",
            "zip must contain cases/ and expected/ at the root or one level down",
        )
    raise DatasetError(
        "DATASET_LAYOUT", "zip contains more than one dataset root"
    )


def snapshot_dataset(bundle: DatasetBundle, destination: Path) -> Path:
    """Copy only the evaluation inputs into an immutable run snapshot."""
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("cases", "expected"):
        shutil.copytree(
            bundle.root / name,
            destination / name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in ("manifest.json", "checksums.json", "README.md"):
        source = bundle.root / name
        if source.is_file():
            shutil.copy2(source, destination / name)
    return destination
