"""Fail CI when common restricted/generated artifacts enter public Git."""

import subprocess
import sys
from pathlib import Path
from typing import List

from clinical_matcher.public_safety import scan_public_file


def tracked_files() -> List[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], text=False
    ).decode("utf-8")
    return [Path(item) for item in output.split("\0") if item]


def main() -> int:
    problems = []
    for path in tracked_files():
        problems.extend(scan_public_file(path))

    if problems:
        print("Public-data guard failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1

    print("Public-data guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
