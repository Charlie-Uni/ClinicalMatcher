"""Shared content checks for files that may leave the restricted boundary."""

import re
from pathlib import Path
from typing import Optional


FORBIDDEN_SUFFIXES = {
    ".csv",
    ".jsonl",
    ".xlsx",
    ".xls",
    ".pptx",
    ".zip",
    ".pt",
    ".pth",
    ".index",
    ".faiss",
    ".db",
    ".sqlite",
}
FORBIDDEN_NAMES = {
    "apixaban_processed.csv",
    "annotated_apixaban_combined.xlsx",
}
IDENTIFIER_PATTERNS = {
    "numeric MIMIC identifier": re.compile(
        r"\b(?:hadm_id|subject_id)[\"']?\s*[,=:]\s*[\"']?[0-9]{5,}\b",
        re.I,
    ),
    "MIMIC note identifier": re.compile(r"\b[0-9]+-[A-Z]{2}-[0-9]+\b"),
}
TEXT_SUFFIXES = {
    "",
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_PUBLIC_FILE_BYTES = 1_000_000


def scan_public_file(
    path: Path,
    *,
    logical_path: Optional[str] = None,
    allow_synthetic_jsonl: bool = False,
    require_synthetic_marker: bool = False,
) -> list[str]:
    """Return disclosure problems without claiming to prove PHI absence."""

    display = logical_path or path.as_posix()
    problems: list[str] = []
    if path.is_symlink():
        return [f"{display}: symbolic links are not allowed"]
    if not path.is_file():
        return [f"{display}: is not a regular file"]

    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    suffix_forbidden = suffix in FORBIDDEN_SUFFIXES and not (
        allow_synthetic_jsonl and suffix == ".jsonl"
    )
    if suffix_forbidden:
        problems.append(f"{display}: forbidden public artifact type")
    if lower_name in FORBIDDEN_NAMES:
        problems.append(f"{display}: known restricted/generated filename")
    if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
        problems.append(f"{display}: exceeds public file size guard")

    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if require_synthetic_marker and "synthetic" not in text.casefold():
            problems.append(f"{display}: missing explicit synthetic marker")
        for label, pattern in IDENTIFIER_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{display}: possible {label}")
    return problems
