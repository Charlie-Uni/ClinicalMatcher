"""CLI for the frozen P7 locked-test batch.

This command intentionally exposes neither a split selector nor arm selectors.
The checked-in contract remains non-executable until a separate owner decision
freezes P7.1 and explicitly authorizes P7.2.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_benchmark import file_sha256
from .p7_locked_test_runner import execute_locked_test_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Execute the one frozen, non-interactive P7 locked-test batch. "
            "No metrics or intermediate results are printed."
        )
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--benchmark-manifest", type=Path, required=True)
    parser.add_argument("--mentor-results", type=Path, required=True)
    parser.add_argument("--mentor-candidate-csv", type=Path, required=True)
    parser.add_argument("--id-map", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-only-locked-test-exposure", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-no-arm-or-threshold-changes", action="store_true"
    )
    parser.add_argument(
        "--acknowledge-post-gold-failure-is-terminal", action="store_true"
    )
    parser.add_argument(
        "--allow-single-pre-gold-retry",
        action="store_true",
        help="Use only after the first attempt recorded pre_gold_failed.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    acknowledgements = {
        "--acknowledge-restricted-data-local-only": (
            args.acknowledge_restricted_data_local_only
        ),
        "--acknowledge-only-locked-test-exposure": (
            args.acknowledge_only_locked_test_exposure
        ),
        "--acknowledge-no-arm-or-threshold-changes": (
            args.acknowledge_no_arm_or_threshold_changes
        ),
        "--acknowledge-post-gold-failure-is-terminal": (
            args.acknowledge_post_gold_failure_is_terminal
        ),
    }
    missing = [name for name, accepted in acknowledgements.items() if not accepted]
    if missing:
        raise ValueError("Missing mandatory acknowledgement: " + ", ".join(missing))

    manifest_path = execute_locked_test_batch(
        repository_root=args.repository_root,
        output_root=args.output_root,
        frozen_split_path=args.frozen_split,
        staging_corpus_path=args.staging_corpus,
        benchmark_path=args.benchmark,
        benchmark_manifest_path=args.benchmark_manifest,
        mentor_results_path=args.mentor_results,
        mentor_candidate_csv_path=args.mentor_candidate_csv,
        id_map_path=args.id_map,
        allow_pre_gold_retry=args.allow_single_pre_gold_retry,
    )
    print("P7 locked-test batch completed without displaying result values.")
    print(f"Owner-only manifest: {manifest_path}")
    print(f"Manifest file SHA-256: {file_sha256(manifest_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
