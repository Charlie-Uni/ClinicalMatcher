"""P8 metadata preparation and guarded development-only entry points.

A separate 1.1.0 one-shot mechanical export is available. There is no test
split option, synthetic-to-real switch, reset or holdout execution command.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

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
    build.add_argument("--export-receipt", type=Path)
    prep_export = sub.add_parser("prepare-export", help="Freeze authorized one-shot mechanical export; no source read")
    do_export = sub.add_parser("export-once", help="Consume mechanical export; never evaluate or read holdout back")
    for command in (prep_export, do_export):
        command.add_argument("--frozen-split-metadata", type=Path, required=True)
        command.add_argument("--reservation-metadata", type=Path, required=True)
    prep_export.add_argument("--benchmark", type=Path, required=True)
    prep_export.add_argument("--staging", type=Path, required=True)
    prep_export.add_argument("--owner-decision-record-id", required=True)
    prep_export.add_argument("--output", type=Path, required=True)
    do_export.add_argument("--export-contract", type=Path, required=True)
    do_export.add_argument("--output-root", type=Path, required=True)
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
    for command in (build, check, prepare, run, prep_export, do_export):
        command.add_argument("--acknowledge-restricted-data-local-only", action="store_true", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in {"prepare-export", "export-once"}:
            from .p8_export import freeze_export_contract, export_once
            split = read_metadata(args.frozen_split_metadata)
            reservation = read_metadata(args.reservation_metadata)
            if args.command == "prepare-export":
                contract = freeze_export_contract(split, reservation,
                    {"benchmark": args.benchmark, "staging": args.staging},
                    owner_decision_record_id=args.owner_decision_record_id)
                write_private(contract, args.output)
                print("Mechanical export contract frozen; no clinical source opened.")
            else:
                export_once(read_metadata(args.export_contract), split, reservation,
                            output_root=args.output_root)
                print("Mechanical export completed; all outputs remain private and holdout sealed.")
        elif args.command == "prepare-access":
            registry = read_metadata(args.artifact_registry_metadata)
            if set(registry) != {"artifacts"}:
                raise ValueError("Expected artifact registry metadata")
            manifest = build_access_manifest(
                read_metadata(args.frozen_split_metadata), read_metadata(args.reservation_metadata),
                review=read_metadata(args.independence_review_metadata), artifacts=registry["artifacts"],
                export_receipt=read_metadata(args.export_receipt) if args.export_receipt else None)
            write_private(manifest, args.output)
            print("P8 access metadata written; holdout remains unauthorized.")
        else:
            from .p8_e0 import freeze_e0_run, run_e0
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
