"""P8 metadata preparation and guarded development-only entry points.

No full-corpus export, test split option, synthetic-to-real switch, holdout
authorization editor, or holdout execution command is provided here.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .p8_e0 import freeze_e0_run, run_e0
from .p8_safety import (EventLedger, build_access_manifest, read_metadata,
                        require_development_ready, validate_access_manifest, write_private)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P8 metadata and isolated validation workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("prepare-access", help="Bind existing metadata; never read clinical content")
    build.add_argument("--frozen-split-metadata", type=Path, required=True)
    build.add_argument("--reservation-metadata", type=Path, required=True)
    build.add_argument("--independence-review-metadata", type=Path, required=True)
    build.add_argument("--artifact-registry-metadata", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("check-access", help="Validate metadata and the P8.1 development gate")
    check.add_argument("--access-manifest", type=Path, required=True)
    prepare = sub.add_parser("prepare-e0", help="Freeze all E0 identities before reading predictions/gold")
    prepare.add_argument("--access-manifest", type=Path, required=True)
    for name in ("rules", "structured", "long-context", "evidence", "gold"):
        prepare.add_argument(f"--{name}-artifact-id", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run-e0", help="Evaluate the frozen raw E0 candidate set on validation only")
    run.add_argument("--access-manifest", type=Path, required=True)
    run.add_argument("--run-contract", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--event-ledger", type=Path, required=True)
    run.add_argument("--attempt-id", required=True)
    for command in (build, check, prepare, run):
        command.add_argument("--acknowledge-restricted-data-local-only", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare-access":
            registry = read_metadata(args.artifact_registry_metadata)
            if set(registry) != {"artifacts"}:
                raise ValueError("Expected artifact registry metadata")
            manifest = build_access_manifest(
                read_metadata(args.frozen_split_metadata), read_metadata(args.reservation_metadata),
                review=read_metadata(args.independence_review_metadata), artifacts=registry["artifacts"])
            write_private(manifest, args.output)
            print("P8 access metadata written; holdout remains unauthorized.")
        else:
            manifest = read_metadata(args.access_manifest)
            validate_access_manifest(manifest)
            require_development_ready(manifest)
            if args.command == "check-access":
                print("P8.1 metadata gate passed; no clinical content was opened.")
            elif args.command == "prepare-e0":
                inputs = {name: getattr(args, f"{name}_artifact_id") for name in
                          ("rules", "structured", "long_context", "evidence", "gold")}
                write_private(freeze_e0_run(manifest, inputs), args.output)
                print("E0 run identities frozen; no clinical content was opened.")
            else:
                run_e0(read_metadata(args.run_contract), manifest, output_root=args.output_root,
                       ledger=EventLedger(args.event_ledger), attempt_id=args.attempt_id)
                print("E0 completed; all results remain in owner-only artifacts.")
        return 0
    except Exception:
        # jsonschema exceptions can contain clinical values. Never print them,
        # nested causes, arbitrary paths, raw model text, or source IDs.
        print("P8 operation rejected or failed. No restricted content is displayed; inspect authorized metadata and the private attempt log.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
