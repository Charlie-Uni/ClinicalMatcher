import hashlib
import json
import re
import unicodedata
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .ingestion.snapshots import (
    load_snapshot_protocols,
    validate_trial_snapshot,
)
from .splits import current_git_commit
from .validation import validate_document


SELECTION_MANIFEST_VERSION = "1.0.0"
PROTOCOL_VERSION = "decomposition-benchmark-protocol/1.0.0"
SELECTION_ALGORITHM = "sha256_trial_isolated_decomposition_sample/1.0.0"
SELECTION_SALT = "clinicalmatcher-public-decomposition-benchmark-v1"
COMPLEXITY_RESOURCE = (
    "resources/decomposition-complexity-contract-1.0.0.json"
)
SELECTION_SCHEMA = (
    "schemas/decomposition-selection-manifest-1.0.0.schema.json"
)
SPLITS = ("dev", "test")
STRATUM_ORDER = (
    "inclusion-low",
    "inclusion-medium",
    "inclusion-high",
    "exclusion-low",
    "exclusion-medium",
    "exclusion-high",
)
QUOTA_PER_SPLIT = {
    "inclusion-low": 5,
    "inclusion-medium": 7,
    "inclusion-high": 8,
    "exclusion-low": 5,
    "exclusion-medium": 7,
    "exclusion-high": 8,
}
MAXIMUM_CRITERIA_PER_TRIAL = 8
MINIMUM_TRIALS_PER_SPLIT = 5


class DecompositionBenchmarkError(ValueError):
    """Raised when the frozen public decomposition contract cannot be met."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_hash(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _digest(*parts: str) -> str:
    if not parts or any(not part or "\0" in part for part in parts):
        raise DecompositionBenchmarkError(
            "Selection digest components must be non-empty and NUL-free"
        )
    return _sha256_bytes("\0".join(parts).encode("utf-8"))


def _resource_bytes(resource: str) -> bytes:
    return files("clinical_matcher").joinpath(resource).read_bytes()


def _complexity_contract() -> Dict[str, Any]:
    contract = json.loads(_resource_bytes(COMPLEXITY_RESOURCE))
    if contract.get("contract_version") != (
        "decomposition-complexity-contract/1.0.0"
    ):
        raise DecompositionBenchmarkError(
            "Unsupported decomposition complexity contract"
        )
    return contract


def normalize_detection_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise DecompositionBenchmarkError("Criterion text must be non-empty")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(normalized.split())


def criterion_complexity(text: str) -> Dict[str, Any]:
    normalized = normalize_detection_text(text)
    contract = _complexity_contract()
    patterns = contract["patterns"]
    codepoints = len(normalized)
    length_points = next(
        rule["points"]
        for rule in contract["length_points"]
        if rule["maximum_codepoints"] is None
        or codepoints <= rule["maximum_codepoints"]
    )
    features = {
        name: bool(re.search(patterns[name], normalized))
        for name in (
            "numeric_or_comparator",
            "connector",
            "negation",
            "temporal",
        )
    }
    score = length_points + sum(features.values())
    matching_tiers = [
        tier
        for tier, bounds in contract["tiers"].items()
        if bounds[0] <= score <= bounds[1]
    ]
    if len(matching_tiers) != 1:
        raise DecompositionBenchmarkError(
            f"Complexity score {score} maps to {len(matching_tiers)} tiers"
        )
    tier = matching_tiers[0]
    return {
        "unicode_codepoints": codepoints,
        "length_points": length_points,
        **features,
        "score": score,
        "tier": tier,
    }


def _protocol_index(
    protocols: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for protocol in protocols:
        nct_id = protocol.get("nct_id")
        if not isinstance(nct_id, str) or nct_id in index:
            raise DecompositionBenchmarkError(
                "Protocols require unique non-empty NCT IDs"
            )
        index[nct_id] = protocol
    return index


def _source_records(
    snapshot_manifest: Dict[str, Any],
    protocols: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    protocols_by_id = _protocol_index(protocols)
    imported = [
        record
        for record in snapshot_manifest.get("records", [])
        if record.get("status") == "imported"
    ]
    expected_ids = {record["nct_id"] for record in imported}
    if set(protocols_by_id) != expected_ids:
        raise DecompositionBenchmarkError(
            "Loaded protocols do not exactly cover imported snapshot records"
        )

    records: List[Dict[str, Any]] = []
    seen_criterion_ids = set()
    for snapshot_record in imported:
        nct_id = snapshot_record["nct_id"]
        protocol = protocols_by_id[nct_id]
        if _canonical_hash(protocol) != snapshot_record["protocol_sha256"]:
            raise DecompositionBenchmarkError(
                f"Protocol hash mismatch for {nct_id}"
            )
        eligibility_text = protocol.get("eligibility_text")
        if not isinstance(eligibility_text, str) or _sha256_bytes(
            eligibility_text.encode("utf-8")
        ) != snapshot_record["eligibility_sha256"]:
            raise DecompositionBenchmarkError(
                f"Eligibility hash mismatch for {nct_id}"
            )
        if protocol.get("criteria") is None:
            raise DecompositionBenchmarkError(f"Protocol has no criteria: {nct_id}")

        for criterion in protocol["criteria"]:
            criterion_id = criterion.get("criterion_id")
            identity = (nct_id, criterion_id)
            if not isinstance(criterion_id, str) or identity in seen_criterion_ids:
                raise DecompositionBenchmarkError(
                    "Criterion identities must be unique within the snapshot"
                )
            seen_criterion_ids.add(identity)
            span = criterion.get("source_span", {})
            start = span.get("start")
            end = span.get("end")
            source_text = criterion.get("source_text")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end <= start
                or end > len(eligibility_text)
                or eligibility_text[start:end] != source_text
            ):
                raise DecompositionBenchmarkError(
                    f"Criterion source span does not reproduce text: {identity}"
                )
            normalized_text = normalize_detection_text(source_text)
            duplicate_resolution_digest = _digest(
                SELECTION_ALGORITHM,
                snapshot_manifest["snapshot_content_sha256"],
                SELECTION_SALT,
                nct_id,
                criterion_id,
            )
            trial_digest = _digest(
                SELECTION_ALGORITHM,
                snapshot_manifest["snapshot_content_sha256"],
                SELECTION_SALT,
                nct_id,
            )
            records.append(
                {
                    "nct_id": nct_id,
                    "source_record_version": protocol["source_record_version"],
                    "last_update_posted": protocol["last_update_posted"],
                    "protocol_sha256": snapshot_record["protocol_sha256"],
                    "eligibility_sha256": snapshot_record[
                        "eligibility_sha256"
                    ],
                    "criterion_id": criterion_id,
                    "criterion_type": criterion["criterion_type"],
                    "source_id": criterion["source_id"],
                    "source_span": {"start": start, "end": end},
                    "source_text": source_text,
                    "normalized_text": normalized_text,
                    "normalized_text_sha256": _sha256_bytes(
                        normalized_text.encode("utf-8")
                    ),
                    "complexity": criterion_complexity(source_text),
                    "duplicate_group_sha256": _sha256_bytes(
                        normalized_text.encode("utf-8")
                    ),
                    "duplicate_resolution_digest": (
                        duplicate_resolution_digest
                    ),
                    "trial_digest": trial_digest,
                    "assigned_split": None,
                    "criterion_digest": None,
                    "selected": False,
                    "selection_reason": "duplicate_normalized_text",
                }
            )
    return records


def _deduplicate(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record["normalized_text"], []).append(record)
    retained = []
    for group in groups.values():
        winner = min(
            group,
            key=lambda record: record["duplicate_resolution_digest"],
        )
        retained.append(winner)
    return retained


def _assign_splits(
    retained: Sequence[Dict[str, Any]],
    snapshot_content_sha256: str,
) -> None:
    trial_digests = {
        record["nct_id"]: record["trial_digest"] for record in retained
    }
    ordered_trials = sorted(
        trial_digests,
        key=lambda nct_id: (trial_digests[nct_id], nct_id),
    )
    split_by_trial = {
        nct_id: SPLITS[index % len(SPLITS)]
        for index, nct_id in enumerate(ordered_trials)
    }
    for record in retained:
        split = split_by_trial[record["nct_id"]]
        record["assigned_split"] = split
        record["criterion_digest"] = _digest(
            SELECTION_ALGORITHM,
            snapshot_content_sha256,
            SELECTION_SALT,
            split,
            record["nct_id"],
            record["criterion_id"],
        )


def _select_quotas(retained: Sequence[Dict[str, Any]]) -> None:
    for split in SPLITS:
        selected_per_trial: Dict[str, int] = {}
        for stratum in STRATUM_ORDER:
            criterion_type, tier = stratum.split("-", 1)
            candidates = sorted(
                (
                    record
                    for record in retained
                    if record["assigned_split"] == split
                    and record["criterion_type"] == criterion_type
                    and record["complexity"]["tier"] == tier
                ),
                key=lambda record: (
                    record["criterion_digest"],
                    record["nct_id"],
                    record["criterion_id"],
                ),
            )
            selected_in_stratum = 0
            for record in candidates:
                if selected_in_stratum >= QUOTA_PER_SPLIT[stratum]:
                    record["selection_reason"] = "hash_rank_outside_quota"
                    continue
                count = selected_per_trial.get(record["nct_id"], 0)
                if count >= MAXIMUM_CRITERIA_PER_TRIAL:
                    record["selection_reason"] = "trial_cap_reached"
                    continue
                record["selected"] = True
                record["selection_reason"] = "selected"
                selected_in_stratum += 1
                selected_per_trial[record["nct_id"]] = count + 1
            if selected_in_stratum != QUOTA_PER_SPLIT[stratum]:
                raise DecompositionBenchmarkError(
                    f"Quota shortage for {split}/{stratum}: selected "
                    f"{selected_in_stratum} of {QUOTA_PER_SPLIT[stratum]}"
                )

        selected_trial_count = len(
            {
                record["nct_id"]
                for record in retained
                if record["assigned_split"] == split and record["selected"]
            }
        )
        if selected_trial_count < MINIMUM_TRIALS_PER_SPLIT:
            raise DecompositionBenchmarkError(
                f"{split} contains only {selected_trial_count} selected trials"
            )


def _counts(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    retained = [record for record in records if record["assigned_split"]]
    selected = [record for record in records if record["selected"]]
    by_split: Dict[str, Dict[str, int]] = {}
    selected_trials: Dict[str, int] = {}
    for split in SPLITS:
        split_records = [
            record for record in selected if record["assigned_split"] == split
        ]
        by_split[split] = {
            "total": len(split_records),
            **{
                stratum: sum(
                    record["criterion_type"] == stratum.split("-", 1)[0]
                    and record["complexity"]["tier"]
                    == stratum.split("-", 1)[1]
                    for record in split_records
                )
                for stratum in STRATUM_ORDER
            },
        }
        selected_trials[split] = len(
            {record["nct_id"] for record in split_records}
        )
    return {
        "candidate_criterion_count": len(records),
        "duplicate_excluded_count": len(records) - len(retained),
        "deduplicated_criterion_count": len(retained),
        "selected_count": len(selected),
        "selected_by_split": by_split,
        "selected_trials_by_split": selected_trials,
    }


def build_decomposition_selection_from_verified_documents(
    snapshot_manifest: Dict[str, Any],
    snapshot_manifest_sha256: str,
    protocols: Sequence[Dict[str, Any]],
    builder_code_commit: str,
) -> Dict[str, Any]:
    """Build the frozen selection from already verified public documents."""
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_manifest_sha256):
        raise DecompositionBenchmarkError("Invalid snapshot manifest SHA-256")
    if not re.fullmatch(r"[0-9a-f]{40}", builder_code_commit):
        raise DecompositionBenchmarkError("Invalid builder Git commit")
    records = _source_records(snapshot_manifest, protocols)
    retained = _deduplicate(records)
    _assign_splits(retained, snapshot_manifest["snapshot_content_sha256"])
    _select_quotas(retained)
    ordered_records = sorted(
        records,
        key=lambda record: (record["nct_id"], record["criterion_id"]),
    )
    complexity_bytes = _resource_bytes(COMPLEXITY_RESOURCE)
    document: Dict[str, Any] = {
        "selection_manifest_version": SELECTION_MANIFEST_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "builder_code_commit": builder_code_commit,
        "source_snapshot": {
            "manifest_path": "snapshot-manifest.json",
            "manifest_sha256": snapshot_manifest_sha256,
            "snapshot_version": snapshot_manifest["snapshot_version"],
            "snapshot_id": snapshot_manifest["snapshot_id"],
            "snapshot_content_sha256": snapshot_manifest[
                "snapshot_content_sha256"
            ],
        },
        "selection_algorithm": {
            "name": SELECTION_ALGORITHM,
            "salt": SELECTION_SALT,
            "complexity_contract_version": (
                "decomposition-complexity-contract/1.0.0"
            ),
            "complexity_contract_sha256": _sha256_bytes(complexity_bytes),
            "split_order": list(SPLITS),
            "stratum_order": list(STRATUM_ORDER),
            "quota_per_split": dict(QUOTA_PER_SPLIT),
            "maximum_criteria_per_trial": MAXIMUM_CRITERIA_PER_TRIAL,
            "minimum_trials_per_split": MINIMUM_TRIALS_PER_SPLIT,
        },
        "records": ordered_records,
        "counts": _counts(ordered_records),
    }
    manifest_sha256 = _canonical_hash(document)
    document["selection_manifest_id"] = (
        f"decomposition-selection-{manifest_sha256[:16]}"
    )
    document["selection_manifest_sha256"] = manifest_sha256
    validate_document(document, SELECTION_SCHEMA)
    return document


def build_decomposition_selection(
    snapshot_dir: Path,
    builder_code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    manifest = validate_trial_snapshot(snapshot_dir)
    manifest_path = snapshot_dir / "snapshot-manifest.json"
    protocols = load_snapshot_protocols(snapshot_dir)
    return build_decomposition_selection_from_verified_documents(
        snapshot_manifest=manifest,
        snapshot_manifest_sha256=_sha256_bytes(manifest_path.read_bytes()),
        protocols=protocols,
        builder_code_commit=builder_code_commit or current_git_commit(),
    )


def validate_decomposition_selection_document(document: Dict[str, Any]) -> None:
    validate_document(document, SELECTION_SCHEMA)
    unsigned = dict(document)
    manifest_id = unsigned.pop("selection_manifest_id")
    manifest_sha256 = unsigned.pop("selection_manifest_sha256")
    expected_sha256 = _canonical_hash(unsigned)
    if manifest_sha256 != expected_sha256:
        raise DecompositionBenchmarkError("Selection manifest hash mismatch")
    if manifest_id != f"decomposition-selection-{expected_sha256[:16]}":
        raise DecompositionBenchmarkError("Selection manifest ID mismatch")


def validate_decomposition_selection(
    snapshot_dir: Path,
    document: Dict[str, Any],
) -> None:
    validate_decomposition_selection_document(document)
    expected = build_decomposition_selection(
        snapshot_dir,
        builder_code_commit=document["builder_code_commit"],
    )
    if document != expected:
        raise DecompositionBenchmarkError(
            "Selection manifest does not reproduce from its source snapshot"
        )


def write_new_json(path: Path, document: Dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
