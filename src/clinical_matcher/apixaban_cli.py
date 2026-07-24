import argparse
from pathlib import Path
from typing import Optional, Sequence

from .ingestion.apixaban import (
    OFFICIAL_TERMS_URL,
    build_apixaban_staging_corpus,
    generate_pseudonym_key,
    write_apixaban_staging_corpus,
)


def _acknowledged(args: argparse.Namespace) -> None:
    if not args.acknowledge_restricted_data_local_only:
        raise ValueError(
            "--acknowledge-restricted-data-local-only is required"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import the credentialed MIMIC-IV-Ext Apixaban release into a "
            "pseudonymized, evidence-chunked local staging corpus."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    key = commands.add_parser(
        "generate-key",
        help="Generate a 32-byte local HMAC pseudonym key.",
    )
    key.add_argument("--output", type=Path, required=True)
    key.add_argument(
        "--acknowledge-restricted-data-local-only",
        action="store_true",
    )

    import_data = commands.add_parser(
        "import",
        help="Verify and import the official restricted CSV.",
    )
    import_data.add_argument("--input-csv", type=Path, required=True)
    import_data.add_argument("--checksums", type=Path)
    import_data.add_argument("--license-file", type=Path)
    import_data.add_argument(
        "--terms-url",
        default=OFFICIAL_TERMS_URL,
        help="Official credentialed dataset or license URL.",
    )
    import_data.add_argument(
        "--pseudonym-key-file",
        type=Path,
        required=True,
    )
    import_data.add_argument(
        "--pseudonym-key-id",
        required=True,
        help="Non-secret identifier for the retained local key.",
    )
    import_data.add_argument(
        "--evidence-chunk-max-characters",
        type=int,
        default=2000,
    )
    import_data.add_argument("--output", type=Path, required=True)
    import_data.add_argument(
        "--acknowledge-restricted-data-local-only",
        action="store_true",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _acknowledged(args)
    if args.command == "generate-key":
        generate_pseudonym_key(args.output)
        print(
            "Generated a local pseudonym key with owner-only permissions at "
            f"{args.output}"
        )
        return 0

    checksum_path = args.checksums or args.input_csv.with_name(
        "SHA256SUMS.txt"
    )
    license_path = args.license_file or args.input_csv.with_name(
        "LICENSE.txt"
    )
    corpus, id_map, manifest = build_apixaban_staging_corpus(
        source_csv=args.input_csv,
        checksum_path=checksum_path,
        license_path=license_path,
        pseudonym_key_path=args.pseudonym_key_file,
        pseudonym_key_id=args.pseudonym_key_id,
        terms_url=args.terms_url,
        evidence_chunk_max_characters=(
            args.evidence_chunk_max_characters
        ),
    )
    output_path, id_map_path, manifest_path = (
        write_apixaban_staging_corpus(
            corpus,
            id_map,
            manifest,
            args.output,
        )
    )
    counts = manifest["counts"]
    print(
        f"Imported {counts['patient_count']} patient note(s), "
        f"{counts['criterion_count']} legacy criteria, and "
        f"{counts['evidence_chunk_count']} evidence chunk(s)."
    )
    print(
        f"Mapped {counts['source_anomaly_label_count']} source anomaly "
        "label(s) to unresolved status without guessing."
    )
    print(f"Wrote restricted corpus: {output_path}")
    print(f"Wrote owner-only raw-ID map: {id_map_path}")
    print(f"Wrote aggregate import manifest: {manifest_path}")
    print(
        "Runtime patient source remains blocked until authorized MIMIC "
        "metadata supplies real index dates."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
