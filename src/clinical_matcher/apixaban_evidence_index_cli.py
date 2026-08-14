import argparse
from pathlib import Path
from typing import Optional, Sequence

from .apixaban_evidence_index import (
    build_evidence_index_manifest_from_paths,
    verify_evidence_index_manifest_from_paths,
    write_evidence_index_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify a restricted, patient-isolated Apixaban "
            "evidence-index input manifest."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--frozen-split", type=Path, required=True)
    build.add_argument("--staging-corpus", type=Path, required=True)
    build.add_argument(
        "--split", choices=("train", "validation", "test"), required=True
    )
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    build.add_argument(
        "--acknowledge-locked-test-indexing", action="store_true"
    )

    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--frozen-split", type=Path, required=True)
    verify.add_argument("--staging-corpus", type=Path, required=True)
    verify.add_argument(
        "--acknowledge-restricted-data-local-only", action="store_true"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError("--acknowledge-restricted-data-local-only is required")
    if args.command == "build":
        if args.split == "test" and not args.acknowledge_locked_test_indexing:
            raise ValueError(
                "Locked test indexing requires explicit acknowledgement; "
                "do not inspect or tune against test text"
            )
        document = build_evidence_index_manifest_from_paths(
            args.frozen_split,
            args.staging_corpus,
            args.split,
        )
        output = write_evidence_index_manifest(document, args.output)
        print(
            "Built restricted patient-isolated evidence index manifest: "
            f"patients={document['counts']['patient_count']}, "
            f"chunks={document['counts']['evidence_chunk_count']}."
        )
        print(f"Local output: {output}")
        return 0

    document = verify_evidence_index_manifest_from_paths(
        args.manifest,
        args.frozen_split,
        args.staging_corpus,
    )
    print(
        "Verified restricted evidence index manifest: "
        f"{document['index']['index_id']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
