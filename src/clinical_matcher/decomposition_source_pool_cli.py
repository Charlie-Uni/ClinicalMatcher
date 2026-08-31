import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .ingestion.decomposition_source_pool import (
    load_decomposition_source_pool_contract,
    select_decomposition_source_trials,
    source_pool_selection_document,
)
from .ingestion.snapshots import (
    DECOMPOSITION_SNAPSHOT_VERSION,
    build_decomposition_trial_snapshot,
    validate_trial_snapshot,
)
from .ingestion.trials import ClinicalTrialsClient


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the frozen public AF decomposition source pool."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--output-dir", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--snapshot-dir", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "verify":
        manifest = validate_trial_snapshot(args.snapshot_dir)
        if manifest["snapshot_version"] != DECOMPOSITION_SNAPSHOT_VERSION:
            raise ValueError("Snapshot is not a decomposition source pool")
        print(
            f"Verified {manifest['snapshot_id']} with "
            f"{len(manifest['records'])} frozen source trials."
        )
        return 0

    contract = load_decomposition_source_pool_contract()
    queried_at = _utc_now()
    client = ClinicalTrialsClient()
    studies, version, metadata = client.search(
        contract["query"]["parameters"],
        max_studies=None,
    )
    reported_total = metadata.get("reported_total_count")
    if not isinstance(reported_total, int):
        raise ValueError("Registry did not report totalCount")
    if metadata.get("selection_truncated"):
        raise ValueError("Registry fetch was truncated")
    selected, audit = select_decomposition_source_trials(
        studies=studies,
        registry_reported_total_count=reported_total,
        pages_fetched=metadata["pages_fetched"],
        version_payload=version,
        queried_at=queried_at,
        contract=contract,
    )
    manifest = build_decomposition_trial_snapshot(
        studies=selected,
        version_payload=version,
        registry_reported_total_count=reported_total,
        pages_fetched=metadata["pages_fetched"],
        selection=source_pool_selection_document(contract),
        selection_audit=audit,
        output_dir=args.output_dir,
        queried_at=queried_at,
    )
    imported = sum(record["status"] == "imported" for record in manifest["records"])
    print(
        f"Built {manifest['snapshot_id']}: {reported_total} complete hits, "
        f"40 hash-selected trials, {imported} parser imports."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
