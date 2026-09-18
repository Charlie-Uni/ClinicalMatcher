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
    approve = sub.add_parser("approve-v2", help="Persist the sealed dual-review decision for the v2 proposal")
    approve.add_argument("--review-record", required=True)
    approve.add_argument("--output", type=Path, required=True)
    plan = sub.add_parser("plan-examples", help="Persist the metadata-only example source plan before any read")
    plan.add_argument("--access-manifest", type=Path, required=True)
    plan.add_argument("--decision", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    examples = sub.add_parser("build-examples", help="Select frozen train-fit demonstrations; prints counts only")
    examples.add_argument("--access-manifest", type=Path, required=True)
    examples.add_argument("--plan", type=Path, required=True)
    examples.add_argument("--output", type=Path, required=True)
    prep_e1 = sub.add_parser("prepare-e1", help="Freeze the complete E1 run contract; probes local runtime identity")
    prep_e1.add_argument("--access-manifest", type=Path, required=True)
    prep_e1.add_argument("--example-set", type=Path, required=True)
    prep_e1.add_argument("--decision", type=Path, required=True)
    prep_e1.add_argument("--mode", choices=("v2", "v2b", "v2-a4", "v2-a24", "v2-a34"), required=True)
    prep_e1.add_argument("--output", type=Path, required=True)
    pilot = sub.add_parser("pilot-e1", help="Timed first-patient pilot after an explicit model unload")
    run_e1_cmd = sub.add_parser("run-e1", help="Complete frozen validation run reusing the immutable pilot")
    for command in (pilot, run_e1_cmd):
        command.add_argument("--access-manifest", type=Path, required=True)
        command.add_argument("--contract", type=Path, required=True)
        command.add_argument("--example-set", type=Path, required=True)
    pilot.add_argument("--output", type=Path, required=True)
    run_e1_cmd.add_argument("--pilot", type=Path, required=True)
    run_e1_cmd.add_argument("--output", type=Path, required=True)
    run_e3_cmd = sub.add_parser("run-e3", help="Apply the E0-selected arbitration to a sealed E1 run")
    run_e3_cmd.add_argument("--access-manifest", type=Path, required=True)
    run_e3_cmd.add_argument("--e1-run", type=Path, required=True)
    run_e3_cmd.add_argument("--output", type=Path, required=True)
    prep_reader = sub.add_parser("prepare-reader", help="Freeze one reader-input ablation arm (v1 prompt); probes runtime")
    prep_reader.add_argument("--arm", choices=("A", "B3", "B5", "C", "D"), required=True)
    prep_reader.add_argument("--output", type=Path, required=True)
    pilot_reader = sub.add_parser("pilot-reader", help="First-patient pilot for a reader arm after an explicit unload")
    run_reader_cmd = sub.add_parser("run-reader", help="Complete validation run for a reader arm reusing its pilot")
    for command in (prep_reader, pilot_reader, run_reader_cmd):
        command.add_argument("--access-manifest", type=Path, required=True)
        command.add_argument("--retrieval", type=Path, help="Sealed P3 RRF retrieval document (arms B3/B5/C)")
    for command in (pilot_reader, run_reader_cmd):
        command.add_argument("--contract", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    run_reader_cmd.add_argument("--pilot", type=Path, required=True)
    for command in (build, check, prepare, run, prep_export, do_export,
                    approve, plan, examples, prep_e1, pilot, run_e1_cmd, run_e3_cmd,
                    prep_reader, pilot_reader, run_reader_cmd):
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
        elif args.command == "approve-v2":
            from .p8_e1 import v2_dual_review_decision
            write_private(v2_dual_review_decision(args.review_record), args.output)
            print("v2 dual-review decision sealed; no clinical content was opened.")
        elif args.command in {"plan-examples", "build-examples", "prepare-e1",
                              "pilot-e1", "run-e1"}:
            from .p8_e1 import (build_e1_contract, open_runtime, run_e1, run_pilot,
                                write_run)
            from .p8_prompt import example_plan, review_proposal, select_registered_examples
            manifest = read_metadata(args.access_manifest)
            require_development_ready(manifest)
            if args.command == "plan-examples":
                plan = example_plan(review_proposal(), manifest, read_metadata(args.decision))
                write_private(plan, args.output)
                print("Example source plan persisted; no clinical content was opened.")
            elif args.command == "build-examples":
                example_set = select_registered_examples(args.plan, manifest)
                write_private(example_set, args.output)
                counts = {qid: len(items) for qid, items in example_set["examples"].items()}
                print("Example set written (counts only):",
                      sum(counts.values()), "examples;",
                      sum(1 for c in counts.values() if c == 0), "questions with none.")
            elif args.command == "prepare-e1":
                from .apixaban_structured_llm import OllamaLoopbackClient, load_long_context_contract
                parent = load_long_context_contract()
                probe = OllamaLoopbackClient(parent["runtime"]["endpoint"])
                contract = build_e1_contract(
                    manifest, read_metadata(args.example_set), read_metadata(args.decision),
                    mode=args.mode,
                    runtime_identity={"engine_version": probe.version()})
                write_private(contract, args.output)
                print("E1 contract frozen for mode", args.mode)
            elif args.command == "pilot-e1":
                contract = read_metadata(args.contract)
                pilot_doc = run_pilot(open_runtime(contract), contract, manifest,
                                      read_metadata(args.example_set))
                write_private(pilot_doc, args.output)
                print("Pilot complete:", pilot_doc["timing_decision"])
            else:
                contract = read_metadata(args.contract)
                run_doc = run_e1(open_runtime(contract), contract, manifest,
                                 read_metadata(args.example_set), read_metadata(args.pilot))
                write_run(run_doc, args.output.parent, args.output.name)
                print("E1 complete. Aggregates:",
                      {"typed_exact_match": run_doc["evaluation"]["metrics"].get("typed_exact_match"),
                       "unknown_count": run_doc["evaluation"]["unknown_count"],
                       "request_outcomes": run_doc["request_outcomes"],
                       "latency_p50_s": round(run_doc["latency_seconds_p50"], 3),
                       "latency_p95_s": round(run_doc["latency_seconds_p95"], 3)})
        elif args.command in {"prepare-reader", "pilot-reader", "run-reader"}:
            from .apixaban_structured_llm import OllamaLoopbackClient, load_long_context_contract
            from .p8_e1 import open_runtime
            from .p8_reader import (aggregates, build_reader_contract, probe_runtime_identity,
                                    run_pilot as run_reader_pilot, run_reader)
            manifest = read_metadata(args.access_manifest)
            require_development_ready(manifest)
            retrieval = read_metadata(args.retrieval) if args.retrieval else None
            if args.command == "prepare-reader":
                parent = load_long_context_contract()
                probe = OllamaLoopbackClient(parent["runtime"]["endpoint"])
                contract = build_reader_contract(manifest, arm=args.arm, retrieval=retrieval,
                                                 runtime_identity=probe_runtime_identity(probe))
                write_private(contract, args.output)
                print("Reader contract frozen for arm", args.arm)
            elif args.command == "pilot-reader":
                contract = read_metadata(args.contract)
                pilot_doc = run_reader_pilot(open_runtime(contract), contract, manifest, retrieval)
                write_private(pilot_doc, args.output)
                print("Reader pilot complete:", pilot_doc["timing_decision"],
                      {log["outcome"]: 1 for log in pilot_doc["slot_logs"]} and
                      {"outcomes": sorted({log["outcome"] for log in pilot_doc["slot_logs"]})})
            else:
                contract = read_metadata(args.contract)
                run_doc = run_reader(open_runtime(contract), contract, manifest,
                                     read_metadata(args.pilot), retrieval)
                write_private(run_doc, args.output)
                print("Reader run complete. Aggregates:", aggregates(run_doc))
        elif args.command == "run-e3":
            from .p8_e3 import aggregates, build_e3_combination
            manifest = read_metadata(args.access_manifest)
            require_development_ready(manifest)
            document = build_e3_combination(manifest, read_metadata(args.e1_run))
            write_private(document, args.output)
            print("E3 complete. Aggregates:", aggregates(document))
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
