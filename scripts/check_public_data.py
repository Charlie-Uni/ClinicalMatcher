"""Fail CI when common restricted/generated artifacts enter public Git."""

import re
import subprocess
import sys
from pathlib import Path
from typing import List


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
    "numeric hadm_id": re.compile(r"\bhadm_id\s*[,=:]\s*[0-9]{5,}\b", re.I),
    "MIMIC note identifier": re.compile(r"\b[0-9]+-[A-Z]{2}-[0-9]+\b"),
}
MAX_PUBLIC_FILE_BYTES = 1_000_000


def tracked_files() -> List[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], text=False
    ).decode("utf-8")
    return [Path(item) for item in output.split("\0") if item]


def main() -> int:
    problems = []
    for path in tracked_files():
        lower_name = path.name.lower()
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            problems.append(f"{path}: forbidden public artifact type")
        if lower_name in FORBIDDEN_NAMES:
            problems.append(f"{path}: known restricted/generated filename")
        if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            problems.append(f"{path}: exceeds public file size guard")

        if path.suffix.lower() in {".py", ".md", ".toml", ".json", ""}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for label, pattern in IDENTIFIER_PATTERNS.items():
                if pattern.search(text):
                    problems.append(f"{path}: possible {label}")

    if problems:
        print("Public-data guard failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1

    print("Public-data guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
