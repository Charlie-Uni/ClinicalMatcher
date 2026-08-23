import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .apixaban_silver_audit import (
    build_silver_audit_package,
    build_silver_quality_gate,
    finalize_silver_judgments,
    write_silver_audit_package,
)
from .apixaban_split import write_private_json
from .ingestion.patients import assert_restricted_local_path


def _load_private_json(path: Path) -> Dict[str, Any]:
    assert_restricted_local_path(path)
    if path.stat().st_mode & 0o077:
        raise ValueError(f"Restricted silver-audit input is not owner-only: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Restricted input must be a JSON object: {path}")
    return document


def _common_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--staging-corpus", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--frozen-split", type=Path, required=True)
    parser.add_argument("--calibration-reservation", type=Path, required=True)
    parser.add_argument("--input-plan", type=Path, required=True)


def _restricted_acknowledgement(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--i-understand-restricted-data-stays-local",
        action="store_true",
        help="Required acknowledgement; all inputs and outputs remain local",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and evaluate owner-only Apixaban silver citation audits"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    package = subparsers.add_parser("package", help="Build deterministic audit package")
    _restricted_acknowledgement(package)
    _common_inputs(package)
    package.add_argument("--candidate", type=Path, required=True)
    package.add_argument("--output-dir", type=Path, required=True)

    finalize = subparsers.add_parser(
        "finalize", help="Hash and validate filled judgments"
    )
    _restricted_acknowledgement(finalize)
    finalize.add_argument("--audit-package", type=Path, required=True)
    finalize.add_argument("--filled-judgments", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)

    gate = subparsers.add_parser("gate", help="Apply frozen source and coverage gates")
    _restricted_acknowledgement(gate)
    _common_inputs(gate)
    gate.add_argument("--d-candidate", type=Path, required=True)
    gate.add_argument("--d-audit-package", type=Path, required=True)
    gate.add_argument("--d-judgments", type=Path, required=True)
    gate.add_argument("--e-candidate", type=Path)
    gate.add_argument("--e-audit-package", type=Path)
    gate.add_argument("--e-judgments", type=Path)
    gate.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.i_understand_restricted_data_stays_local:
        raise ValueError(
            "Refusing silver audit without explicit acknowledgement that "
            "restricted data and derived artifacts stay local"
        )
    if args.command == "package":
        package, pending = build_silver_audit_package(
            _load_private_json(args.staging_corpus),
            _load_private_json(args.benchmark),
            _load_private_json(args.frozen_split),
            _load_private_json(args.calibration_reservation),
            _load_private_json(args.input_plan),
            _load_private_json(args.candidate),
            generation_command="clinical-matcher-apixaban-silver-audit package",
        )
        paths = write_silver_audit_package(package, pending, args.output_dir)
        print(
            f"Wrote {package['population']['sample_count']} owner-only review rows: {paths[0]}, {paths[1]}"
        )
        return 0
    if args.command == "finalize":
        package = _load_private_json(args.audit_package)
        filled = _load_private_json(args.filled_judgments)
        completed = finalize_silver_judgments(filled, package)
        write_private_json(completed, args.output)
        print(f"Validated completed owner-only judgments: {args.output}")
        return 0

    e_paths = (args.e_candidate, args.e_audit_package, args.e_judgments)
    if any(e_paths) and not all(e_paths):
        raise ValueError(
            "E backoff requires candidate, audit package, and judgments together"
        )
    e_bundle = (
        tuple(_load_private_json(path) for path in e_paths) if all(e_paths) else None
    )
    report, accepted_d, accepted_e = build_silver_quality_gate(
        _load_private_json(args.staging_corpus),
        _load_private_json(args.benchmark),
        _load_private_json(args.frozen_split),
        _load_private_json(args.calibration_reservation),
        _load_private_json(args.input_plan),
        _load_private_json(args.d_candidate),
        _load_private_json(args.d_audit_package),
        _load_private_json(args.d_judgments),
        e_bundle,  # type: ignore[arg-type]
        generation_command="clinical-matcher-apixaban-silver-audit gate",
    )
    assert_restricted_local_path(args.output_dir)
    outputs = [(report, args.output_dir / "quality-audit.json")]
    if accepted_d is not None:
        outputs.append((accepted_d, args.output_dir / "accepted-d-silver.json"))
    if accepted_e is not None:
        outputs.append((accepted_e, args.output_dir / "accepted-e-silver.json"))
    written = []
    try:
        for document, path in outputs:
            write_private_json(document, path)
            written.append(path)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    print(
        f"Silver gate status: {report['status']}; outputs: {', '.join(str(path) for path in written)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
