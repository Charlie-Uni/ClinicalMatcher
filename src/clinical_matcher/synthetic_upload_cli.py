import argparse
from pathlib import Path
from typing import Optional, Sequence

from .synthetic_upload import (
    build_synthetic_upload_manifest,
    load_and_verify_synthetic_upload_bundle,
    write_synthetic_upload_manifest,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify an exact synthetic-only upload bundle manifest."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--bundle-dir", type=Path, required=True)
    build.add_argument(
        "--acknowledge-independently-authored-synthetic-only",
        action="store_true",
    )

    verify = subparsers.add_parser("verify")
    verify.add_argument("--bundle-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        if not args.acknowledge_independently_authored_synthetic_only:
            raise ValueError(
                "--acknowledge-independently-authored-synthetic-only is required"
            )
        document = build_synthetic_upload_manifest(
            args.bundle_dir,
            generation_command=(
                "clinical-matcher-check-synthetic-upload build "
                "--bundle-dir . "
                "--acknowledge-independently-authored-synthetic-only"
            ),
        )
        output = write_synthetic_upload_manifest(document, args.bundle_dir)
        load_and_verify_synthetic_upload_bundle(args.bundle_dir)
        print(f"Synthetic upload manifest written: {output}")
    else:
        document = load_and_verify_synthetic_upload_bundle(args.bundle_dir)
        print(
            "Synthetic upload bundle verified: "
            f"files={len(document['files'])}, "
            f"manifest_sha256={document['manifest_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
