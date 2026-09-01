"""Headless replacement of the P5D locked-test source after source exposure."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .decomposition_benchmark import (
    MAXIMUM_CRITERIA_PER_TRIAL,
    MINIMUM_TRIALS_PER_SPLIT,
    QUOTA_PER_SPLIT,
    STRATUM_ORDER,
    criterion_complexity,
    normalize_detection_text,
    validate_decomposition_selection_document,
)
from .ingestion.decomposition_source_pool import (
    validate_decomposition_source_selection_audit,
)
from .ingestion.snapshots import validate_trial_snapshot
from .ingestion.trials import ClinicalTrialsClient, TrialImportError, normalize_study
from .splits import current_git_commit
from .validation import validate_document


CONTRACT_RESOURCE = (
    "resources/decomposition-test-remediation-contract-1.0.0.json"
)
CONTRACT_SCHEMA = (
    "schemas/decomposition-test-remediation-contract-1.0.0.schema.json"
)
SELECTION_SCHEMA = (
    "schemas/decomposition-selection-manifest-1.1.0.schema.json"
)
SNAPSHOT_SCHEMA = (
    "schemas/decomposition-test-source-snapshot-1.0.0.schema.json"
)
DEV_SNAPSHOT_SCHEMA = (
    "schemas/decomposition-dev-source-snapshot-1.0.0.schema.json"
)
SELECTION_VERSION = "1.1.0"
PROTOCOL_VERSION = "decomposition-benchmark-protocol/1.1.0"
SNAPSHOT_VERSION = "decomposition-test-source-snapshot/1.0.0"
DEV_SNAPSHOT_VERSION = "decomposition-dev-source-snapshot/1.0.0"


class DecompositionTestRemediationError(ValueError):
    """Raised when the approved replacement-test contract cannot be met."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_hash(path: Path) -> str:
    return _bytes_hash(path.read_bytes())


def _self_hash(document: Mapping[str, Any], id_field: str, hash_field: str) -> str:
    unsigned = dict(document)
    unsigned.pop(id_field, None)
    unsigned.pop(hash_field, None)
    return _canonical_hash(unsigned)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(contract: Mapping[str, Any], *parts: str) -> str:
    algorithm = contract["selection"]["algorithm"]
    salt = contract["selection"]["salt"]
    components = (algorithm, salt, contract["contract_sha256"], *parts)
    if any(not part or "\0" in part for part in components):
        raise DecompositionTestRemediationError(
            "Digest components must be non-empty and NUL-free"
        )
    return _bytes_hash("\0".join(components).encode("utf-8"))


def load_remediation_contract() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(CONTRACT_RESOURCE)
    document = json.loads(resource.read_text(encoding="utf-8"))
    validate_remediation_contract(document)
    return document


def validate_remediation_contract(document: Dict[str, Any]) -> None:
    validate_document(document, CONTRACT_SCHEMA)
    expected = _self_hash(document, "contract_id", "contract_sha256")
    if document["contract_sha256"] != expected:
        raise DecompositionTestRemediationError("Remediation contract hash mismatch")
    if document["contract_id"] != (
        f"decomposition-test-remediation-{expected[:16]}"
    ):
        raise DecompositionTestRemediationError("Remediation contract ID mismatch")


def _validate_bound_inputs(
    predecessor_path: Path,
    source_audit_path: Path,
    predecessor: Dict[str, Any],
    source_audit: Dict[str, Any],
    contract: Dict[str, Any],
) -> None:
    binding = contract["predecessor"]
    if _file_hash(predecessor_path) != binding["selection_file_sha256"]:
        raise DecompositionTestRemediationError(
            "Predecessor selection file hash mismatch"
        )
    if _file_hash(source_audit_path) != binding["source_audit_file_sha256"]:
        raise DecompositionTestRemediationError("Source audit file hash mismatch")
    validate_decomposition_selection_document(predecessor)
    validate_decomposition_source_selection_audit(source_audit)
    if predecessor["selection_manifest_id"] != binding[
        "selection_manifest_id"
    ] or predecessor["selection_manifest_sha256"] != binding[
        "selection_manifest_sha256"
    ]:
        raise DecompositionTestRemediationError(
            "Predecessor selection identity mismatch"
        )
    if source_audit["selection_audit_id"] != binding[
        "source_audit_id"
    ] or source_audit["selection_audit_sha256"] != binding[
        "source_audit_sha256"
    ]:
        raise DecompositionTestRemediationError("Source audit identity mismatch")


def _selected_records(
    predecessor: Mapping[str, Any], split: str
) -> List[Dict[str, Any]]:
    selected = [
        record
        for record in predecessor["records"]
        if record["selected"] and record["assigned_split"] == split
    ]
    if len(selected) != 40:
        raise DecompositionTestRemediationError(
            f"Predecessor {split} split must contain exactly 40 selected criteria"
        )
    return selected


def _old_record_metadata(record: Mapping[str, Any], status: str) -> Dict[str, Any]:
    text = record["source_text"]
    return {
        "nct_id": record["nct_id"],
        "criterion_id": record["criterion_id"],
        "criterion_type": record["criterion_type"],
        "source_record_version": record["source_record_version"],
        "protocol_sha256": record["protocol_sha256"],
        "eligibility_sha256": record["eligibility_sha256"],
        "source_text_sha256": _bytes_hash(text.encode("utf-8")),
        "normalized_text_sha256": record["normalized_text_sha256"],
        "source_span_length": record["source_span"]["end"]
        - record["source_span"]["start"],
        "complexity": record["complexity"],
        "status": status,
    }


def ordered_unfetched_remainder(
    source_audit: Mapping[str, Any], contract: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    flow = source_audit["flow"]
    expected = contract["remainder"]
    observed = (
        flow["registry_reported_total_count"],
        flow["filter_passed_count"],
        flow["selected_count"],
        flow["eligible_not_sampled_count"],
    )
    frozen = (
        expected["registry_hit_count"],
        expected["filter_passed_count"],
        expected["original_sampled_count"],
        expected["eligible_unfetched_count"],
    )
    if observed != frozen:
        raise DecompositionTestRemediationError(
            "Frozen source-audit flow does not match the remediation contract"
        )
    remainder = [
        dict(record)
        for record in source_audit["records"]
        if record["filter_passed"] and not record["selected"]
    ]
    if len(remainder) != expected["eligible_unfetched_count"]:
        raise DecompositionTestRemediationError(
            "Frozen unfetched remainder count mismatch"
        )
    if any(not record.get("sampling_hash") for record in remainder):
        raise DecompositionTestRemediationError(
            "Every remainder record requires a frozen sampling hash"
        )
    return sorted(
        remainder,
        key=lambda record: (record["sampling_hash"], record["nct_id"]),
    )


def _candidate_records(
    protocol: Mapping[str, Any],
    contract: Mapping[str, Any],
    source_audit_sha256: str,
) -> List[Dict[str, Any]]:
    records = []
    nct_id = protocol["nct_id"]
    protocol_sha256 = _canonical_hash(protocol)
    eligibility_text = protocol["eligibility_text"]
    for criterion in protocol["criteria"]:
        text = criterion["source_text"]
        normalized = normalize_detection_text(text)
        criterion_id = criterion["criterion_id"]
        span = criterion["source_span"]
        if eligibility_text[span["start"] : span["end"]] != text:
            raise DecompositionTestRemediationError(
                "Parsed criterion span does not reproduce source text"
            )
        records.append(
            {
                "nct_id": nct_id,
                "criterion_id": criterion_id,
                "criterion_type": criterion["criterion_type"],
                "source_id": criterion["source_id"],
                "source_record_version": protocol["source_record_version"],
                "last_update_posted": protocol["last_update_posted"],
                "protocol_sha256": protocol_sha256,
                "eligibility_sha256": protocol["eligibility_sha256"],
                "source_span": dict(span),
                "source_span_length": span["end"] - span["start"],
                "source_text": text,
                "source_text_sha256": _bytes_hash(text.encode("utf-8")),
                "normalized_text": normalized,
                "normalized_text_sha256": _bytes_hash(
                    normalized.encode("utf-8")
                ),
                "complexity": criterion_complexity(text),
                "duplicate_digest": _digest(
                    contract,
                    source_audit_sha256,
                    "duplicate",
                    nct_id,
                    criterion_id,
                ),
                "criterion_digest": _digest(
                    contract,
                    source_audit_sha256,
                    "criterion",
                    nct_id,
                    criterion_id,
                ),
            }
        )
    return records


def _select_replacement(
    candidates: Sequence[Dict[str, Any]],
    preserved_dev_normalized_hashes: set[str],
    contract: Mapping[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        if candidate["normalized_text_sha256"] in preserved_dev_normalized_hashes:
            continue
        groups.setdefault(candidate["normalized_text"], []).append(candidate)
    retained = [
        min(group, key=lambda item: item["duplicate_digest"])
        for group in groups.values()
    ]
    selected: List[Dict[str, Any]] = []
    per_trial: Dict[str, int] = {}
    for stratum in contract["selection"]["stratum_order"]:
        criterion_type, tier = stratum.split("-", 1)
        available = sorted(
            (
                record
                for record in retained
                if record["criterion_type"] == criterion_type
                and record["complexity"]["tier"] == tier
            ),
            key=lambda record: (
                record["criterion_digest"],
                record["nct_id"],
                record["criterion_id"],
            ),
        )
        chosen = 0
        for record in available:
            if chosen == contract["selection"]["quota"][stratum]:
                break
            current = per_trial.get(record["nct_id"], 0)
            if current >= contract["selection"]["maximum_criteria_per_trial"]:
                continue
            selected.append(record)
            per_trial[record["nct_id"]] = current + 1
            chosen += 1
        if chosen != contract["selection"]["quota"][stratum]:
            return None
    if len(per_trial) < contract["selection"]["minimum_trials"]:
        return None
    return selected


def _test_record_metadata(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "nct_id",
            "criterion_id",
            "criterion_type",
            "source_id",
            "source_record_version",
            "last_update_posted",
            "protocol_sha256",
            "eligibility_sha256",
            "source_span_length",
            "source_text_sha256",
            "normalized_text_sha256",
            "complexity",
            "criterion_digest",
        )
    }


def collect_replacement_sources(
    *,
    predecessor: Dict[str, Any],
    source_audit: Dict[str, Any],
    fetcher: Callable[[str], Tuple[Dict[str, Any], Dict[str, Any]]],
    contract: Dict[str, Any],
    builder_code_commit: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fetch the first feasible frozen remainder prefix without printing text."""
    validate_remediation_contract(contract)
    if not re.fullmatch(r"[0-9a-f]{40}", builder_code_commit):
        raise DecompositionTestRemediationError("Invalid builder Git commit")
    dev = _selected_records(predecessor, "dev")
    dev_hashes = {record["normalized_text_sha256"] for record in dev}
    candidates: List[Dict[str, Any]] = []
    imported: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []
    remainder = ordered_unfetched_remainder(source_audit, contract)
    selected: Optional[List[Dict[str, Any]]] = None

    for rank, frozen_record in enumerate(remainder, start=1):
        nct_id = frozen_record["nct_id"]
        outcome: Dict[str, Any] = {
            "remainder_rank": rank,
            "nct_id": nct_id,
            "sampling_hash": frozen_record["sampling_hash"],
            "frozen_source_study_sha256": frozen_record[
                "source_study_sha256"
            ],
            "status": "skipped",
            "reason_code": "fetch_error",
        }
        try:
            study, version = fetcher(nct_id)
        except (OSError, TimeoutError, TrialImportError, ValueError):
            outcomes.append(outcome)
            continue
        fetched_sha256 = _canonical_hash(study)
        outcome["fetched_source_study_sha256"] = fetched_sha256
        if fetched_sha256 != frozen_record["source_study_sha256"]:
            outcome["reason_code"] = "source_hash_mismatch"
            outcomes.append(outcome)
            continue
        try:
            protocol = normalize_study(
                study,
                version,
                importer_code_commit=builder_code_commit,
            )
            trial_candidates = _candidate_records(
                protocol,
                contract,
                source_audit["selection_audit_sha256"],
            )
        except TrialImportError as error:
            outcome["reason_code"] = error.code
            outcomes.append(outcome)
            continue
        except (KeyError, TypeError, ValueError):
            outcome["reason_code"] = "unexpected_import_error"
            outcomes.append(outcome)
            continue
        raw_name = f"raw/{nct_id}.json"
        protocol_name = f"protocols/{nct_id}.json"
        raw_bytes = _json_bytes(study)
        protocol_bytes = _json_bytes(protocol)
        outcome.update(
            {
                "status": "imported",
                "reason_code": None,
                "api_version": version.get("apiVersion"),
                "api_data_timestamp": version.get("dataTimestamp"),
                "criterion_count": len(trial_candidates),
                "criterion_type_counts": {
                    kind: sum(
                        item["criterion_type"] == kind
                        for item in trial_candidates
                    )
                    for kind in ("inclusion", "exclusion")
                },
                "source_span_lengths": sorted(
                    item["source_span_length"] for item in trial_candidates
                ),
                "raw_path": raw_name,
                "raw_sha256": _bytes_hash(raw_bytes),
                "protocol_path": protocol_name,
                "protocol_file_sha256": _bytes_hash(protocol_bytes),
                "protocol_sha256": _canonical_hash(protocol),
                "eligibility_sha256": protocol["eligibility_sha256"],
            }
        )
        outcomes.append(outcome)
        imported.append(
            {
                "nct_id": nct_id,
                "study": study,
                "protocol": protocol,
                "outcome": outcome,
            }
        )
        candidates.extend(trial_candidates)
        selected = _select_replacement(candidates, dev_hashes, contract)
        if selected is not None:
            return imported, outcomes, selected

    raise DecompositionTestRemediationError(
        "The complete frozen 506-trial remainder cannot satisfy replacement "
        "test quotas; no selection artifact was created"
    )


def _snapshot_document(
    *,
    imported: Sequence[Dict[str, Any]],
    outcomes: Sequence[Dict[str, Any]],
    contract: Mapping[str, Any],
    created_at: str,
    builder_code_commit: str,
) -> Dict[str, Any]:
    records = [dict(outcome) for outcome in outcomes]
    document: Dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "created_at": created_at,
        "builder_code_commit": builder_code_commit,
        "contract_id": contract["contract_id"],
        "contract_sha256": contract["contract_sha256"],
        "headless": True,
        "criterion_text_in_manifest": False,
        "attempted_remainder_prefix_count": len(records),
        "imported_trial_count": len(imported),
        "skipped_trial_count": len(records) - len(imported),
        "records": records,
    }
    digest = _self_hash(document, "snapshot_id", "snapshot_sha256")
    document["snapshot_id"] = f"decomposition-test-source-{digest[:16]}"
    document["snapshot_sha256"] = digest
    validate_test_source_snapshot_document(document)
    return document


def _dev_snapshot_document(
    *,
    source_snapshot_root: Path,
    predecessor: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[Tuple[Path, str]]]:
    manifest_path = source_snapshot_root / "snapshot-manifest.json"
    binding = contract["predecessor"]
    if _file_hash(manifest_path) != binding["source_snapshot_manifest_sha256"]:
        raise DecompositionTestRemediationError(
            "Original source snapshot manifest file hash mismatch"
        )
    manifest = validate_trial_snapshot(source_snapshot_root)
    if manifest["snapshot_id"] != binding["source_snapshot_id"] or manifest[
        "snapshot_content_sha256"
    ] != binding["source_snapshot_content_sha256"]:
        raise DecompositionTestRemediationError(
            "Original source snapshot identity mismatch"
        )
    dev_records = _selected_records(predecessor, "dev")
    dev_trial_ids = {record["nct_id"] for record in dev_records}
    source_index = {record["nct_id"]: record for record in manifest["records"]}
    if not dev_trial_ids.issubset(source_index):
        raise DecompositionTestRemediationError(
            "A preserved dev trial is absent from the original snapshot"
        )
    records = []
    copies: List[Tuple[Path, str]] = []
    for nct_id in sorted(dev_trial_ids):
        source = source_index[nct_id]
        if source["status"] != "imported":
            raise DecompositionTestRemediationError(
                "Every preserved dev trial must have an imported protocol"
            )
        source_study_path = source_snapshot_root / source["source_study_path"]
        protocol_path = source_snapshot_root / source["protocol_path"]
        records.append(
            {
                "nct_id": nct_id,
                "source_study_path": source["source_study_path"],
                "source_study_sha256": source["source_study_sha256"],
                "source_study_file_sha256": _file_hash(source_study_path),
                "protocol_path": source["protocol_path"],
                "protocol_sha256": source["protocol_sha256"],
                "protocol_file_sha256": _file_hash(protocol_path),
                "eligibility_sha256": source["eligibility_sha256"],
            }
        )
        copies.extend(
            (
                (source_study_path, source["source_study_path"]),
                (protocol_path, source["protocol_path"]),
            )
        )
    document: Dict[str, Any] = {
        "snapshot_version": DEV_SNAPSHOT_VERSION,
        "contract_id": contract["contract_id"],
        "contract_sha256": contract["contract_sha256"],
        "source_snapshot_id": manifest["snapshot_id"],
        "source_snapshot_content_sha256": manifest["snapshot_content_sha256"],
        "selected_dev_criterion_count": len(dev_records),
        "selected_dev_trial_count": len(dev_trial_ids),
        "records": records,
    }
    digest = _self_hash(document, "snapshot_id", "snapshot_sha256")
    document["snapshot_id"] = f"decomposition-dev-source-{digest[:16]}"
    document["snapshot_sha256"] = digest
    validate_dev_source_snapshot_document(document)
    return document, copies


def _selection_document(
    *,
    predecessor: Mapping[str, Any],
    source_audit: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    dev_snapshot: Mapping[str, Any],
    selected: Sequence[Dict[str, Any]],
    contract: Mapping[str, Any],
    builder_code_commit: str,
) -> Dict[str, Any]:
    dev = sorted(
        (
            _old_record_metadata(record, "preserved_dev")
            for record in _selected_records(predecessor, "dev")
        ),
        key=lambda item: (item["nct_id"], item["criterion_id"]),
    )
    retired = sorted(
        (
            {
                **_old_record_metadata(record, "retired_test_source_exposure"),
                "incident_event_id": contract["incident"]["event_id"],
                "incident_record_path": contract["incident"]["record_path"],
            }
            for record in _selected_records(predecessor, "test")
        ),
        key=lambda item: (item["nct_id"], item["criterion_id"]),
    )
    test = sorted(
        (_test_record_metadata(record) for record in selected),
        key=lambda item: (item["nct_id"], item["criterion_id"]),
    )
    by_stratum = {
        stratum: sum(
            item["criterion_type"] == stratum.split("-", 1)[0]
            and item["complexity"]["tier"] == stratum.split("-", 1)[1]
            for item in test
        )
        for stratum in STRATUM_ORDER
    }
    document: Dict[str, Any] = {
        "selection_manifest_version": SELECTION_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "builder_code_commit": builder_code_commit,
        "contract_binding": {
            "contract_id": contract["contract_id"],
            "contract_sha256": contract["contract_sha256"],
        },
        "predecessor_binding": {
            "selection_manifest_id": predecessor["selection_manifest_id"],
            "selection_manifest_sha256": predecessor[
                "selection_manifest_sha256"
            ],
            "source_audit_id": source_audit["selection_audit_id"],
            "source_audit_sha256": source_audit["selection_audit_sha256"],
        },
        "dev_source_snapshot": {
            "root": contract["storage"]["dev_source_root"],
            "manifest_path": "snapshot-manifest.json",
            "snapshot_id": dev_snapshot["snapshot_id"],
            "snapshot_sha256": dev_snapshot["snapshot_sha256"],
        },
        "test_source_snapshot": {
            "root": contract["storage"]["test_source_root"],
            "manifest_path": "snapshot-manifest.json",
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
        },
        "dev_records": dev,
        "test_records": test,
        "retired_test_records": retired,
        "counts": {
            "preserved_dev_count": len(dev),
            "replacement_test_count": len(test),
            "retired_test_count": len(retired),
            "replacement_test_trials": len(
                {item["nct_id"] for item in test}
            ),
            "replacement_test_by_stratum": by_stratum,
            "remainder_prefix_attempted": snapshot[
                "attempted_remainder_prefix_count"
            ],
            "remainder_prefix_imported": snapshot["imported_trial_count"],
            "remainder_prefix_skipped": snapshot["skipped_trial_count"],
        },
    }
    digest = _self_hash(
        document, "selection_manifest_id", "selection_manifest_sha256"
    )
    document["selection_manifest_id"] = (
        f"decomposition-selection-{digest[:16]}"
    )
    document["selection_manifest_sha256"] = digest
    validate_remediated_selection_document(document, contract=contract)
    return document


def validate_test_source_snapshot_document(document: Dict[str, Any]) -> None:
    validate_document(document, SNAPSHOT_SCHEMA)
    expected = _self_hash(document, "snapshot_id", "snapshot_sha256")
    if document["snapshot_sha256"] != expected or document["snapshot_id"] != (
        f"decomposition-test-source-{expected[:16]}"
    ):
        raise DecompositionTestRemediationError(
            "Test-source snapshot identity mismatch"
        )


def validate_dev_source_snapshot_document(document: Dict[str, Any]) -> None:
    validate_document(document, DEV_SNAPSHOT_SCHEMA)
    expected = _self_hash(document, "snapshot_id", "snapshot_sha256")
    if document["snapshot_sha256"] != expected or document["snapshot_id"] != (
        f"decomposition-dev-source-{expected[:16]}"
    ):
        raise DecompositionTestRemediationError(
            "Dev-source snapshot identity mismatch"
        )
    if any(
        key in record
        for record in document["records"]
        for key in ("source_text", "normalized_text", "eligibility_text")
    ):
        raise DecompositionTestRemediationError(
            "Test-source snapshot manifest contains criterion text"
        )


def validate_remediated_selection_document(
    document: Dict[str, Any],
    contract: Optional[Dict[str, Any]] = None,
) -> None:
    frozen = contract or load_remediation_contract()
    validate_remediation_contract(frozen)
    validate_document(document, SELECTION_SCHEMA)
    expected = _self_hash(
        document, "selection_manifest_id", "selection_manifest_sha256"
    )
    if document["selection_manifest_sha256"] != expected or document[
        "selection_manifest_id"
    ] != f"decomposition-selection-{expected[:16]}":
        raise DecompositionTestRemediationError(
            "Remediated selection identity mismatch"
        )
    if document["contract_binding"] != {
        "contract_id": frozen["contract_id"],
        "contract_sha256": frozen["contract_sha256"],
    }:
        raise DecompositionTestRemediationError(
            "Remediated selection contract binding mismatch"
        )
    forbidden = {"source_text", "normalized_text", "eligibility_text"}
    for section in ("test_records", "retired_test_records"):
        if any(forbidden.intersection(record) for record in document[section]):
            raise DecompositionTestRemediationError(
                f"{section} must not contain test criterion text"
            )


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(document))


def build_remediated_selection(
    *,
    predecessor_path: Path,
    source_audit_path: Path,
    source_snapshot_root: Path,
    dev_source_root: Path,
    test_source_root: Path,
    selection_output: Path,
    fetcher: Optional[
        Callable[[str], Tuple[Dict[str, Any], Dict[str, Any]]]
    ] = None,
    contract: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
    builder_code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Build write-once protected test sources and a metadata-only selection."""
    frozen = contract or load_remediation_contract()
    validate_remediation_contract(frozen)
    if (
        dev_source_root.exists()
        or test_source_root.exists()
        or selection_output.exists()
    ):
        raise FileExistsError("Refusing to overwrite remediation artifacts")
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    source_audit = json.loads(source_audit_path.read_text(encoding="utf-8"))
    _validate_bound_inputs(
        predecessor_path,
        source_audit_path,
        predecessor,
        source_audit,
        frozen,
    )
    dev_snapshot, dev_copies = _dev_snapshot_document(
        source_snapshot_root=source_snapshot_root,
        predecessor=predecessor,
        contract=frozen,
    )
    commit = builder_code_commit or current_git_commit()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DecompositionTestRemediationError("Invalid builder Git commit")
    imported, outcomes, selected = collect_replacement_sources(
        predecessor=predecessor,
        source_audit=source_audit,
        fetcher=fetcher or ClinicalTrialsClient().fetch,
        contract=frozen,
        builder_code_commit=commit,
    )
    timestamp = created_at or _now()
    snapshot = _snapshot_document(
        imported=imported,
        outcomes=outcomes,
        contract=frozen,
        created_at=timestamp,
        builder_code_commit=commit,
    )
    selection = _selection_document(
        predecessor=predecessor,
        source_audit=source_audit,
        dev_snapshot=dev_snapshot,
        snapshot=snapshot,
        selected=selected,
        contract=frozen,
        builder_code_commit=commit,
    )

    dev_source_root.parent.mkdir(parents=True, exist_ok=True)
    test_source_root.parent.mkdir(parents=True, exist_ok=True)
    selection_output.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=f".{test_source_root.name}-",
            dir=str(test_source_root.parent),
        )
    )
    temporary_dev_root = Path(
        tempfile.mkdtemp(
            prefix=f".{dev_source_root.name}-",
            dir=str(dev_source_root.parent),
        )
    )
    temporary_selection = selection_output.with_name(
        f".{selection_output.name}.{os.getpid()}.tmp"
    )
    try:
        for source_path, relative_path in dev_copies:
            destination = temporary_dev_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)
        _write_json(
            temporary_dev_root / "snapshot-manifest.json", dev_snapshot
        )
        for item in imported:
            outcome = item["outcome"]
            _write_json(temporary_root / outcome["raw_path"], item["study"])
            _write_json(
                temporary_root / outcome["protocol_path"], item["protocol"]
            )
        _write_json(temporary_root / "snapshot-manifest.json", snapshot)
        _write_json(temporary_selection, selection)
        temporary_dev_root.rename(dev_source_root)
        temporary_root.rename(test_source_root)
        temporary_selection.replace(selection_output)
    except Exception:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)
        if temporary_dev_root.exists():
            shutil.rmtree(temporary_dev_root)
        if temporary_selection.exists():
            temporary_selection.unlink()
        if test_source_root.exists() and not selection_output.exists():
            shutil.rmtree(test_source_root)
        if dev_source_root.exists() and not selection_output.exists():
            shutil.rmtree(dev_source_root)
        raise
    return selection


def validate_remediated_selection(
    *,
    predecessor_path: Path,
    source_audit_path: Path,
    source_snapshot_root: Path,
    dev_source_root: Path,
    test_source_root: Path,
    selection_path: Path,
    contract: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    frozen = contract or load_remediation_contract()
    predecessor = json.loads(predecessor_path.read_text(encoding="utf-8"))
    source_audit = json.loads(source_audit_path.read_text(encoding="utf-8"))
    _validate_bound_inputs(
        predecessor_path,
        source_audit_path,
        predecessor,
        source_audit,
        frozen,
    )
    expected_dev_snapshot, _ = _dev_snapshot_document(
        source_snapshot_root=source_snapshot_root,
        predecessor=predecessor,
        contract=frozen,
    )
    dev_snapshot_path = dev_source_root / "snapshot-manifest.json"
    dev_snapshot = json.loads(dev_snapshot_path.read_text(encoding="utf-8"))
    validate_dev_source_snapshot_document(dev_snapshot)
    if dev_snapshot != expected_dev_snapshot:
        raise DecompositionTestRemediationError(
            "Separated dev-source snapshot does not reproduce"
        )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    validate_remediated_selection_document(selection, contract=frozen)
    snapshot_path = test_source_root / "snapshot-manifest.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    validate_test_source_snapshot_document(snapshot)
    binding = selection["test_source_snapshot"]
    dev_binding = selection["dev_source_snapshot"]
    if dev_binding["root"] != frozen["storage"]["dev_source_root"] or dev_binding[
        "snapshot_id"
    ] != dev_snapshot["snapshot_id"] or dev_binding["snapshot_sha256"] != (
        dev_snapshot["snapshot_sha256"]
    ):
        raise DecompositionTestRemediationError(
            "Selection/dev-source snapshot binding mismatch"
        )
    if binding["root"] != frozen["storage"]["test_source_root"] or binding[
        "snapshot_id"
    ] != snapshot["snapshot_id"] or binding["snapshot_sha256"] != snapshot[
        "snapshot_sha256"
    ]:
        raise DecompositionTestRemediationError(
            "Selection/test-source snapshot binding mismatch"
        )
    for record in snapshot["records"]:
        if record["status"] != "imported":
            continue
        for path_key, hash_key in (
            ("raw_path", "raw_sha256"),
            ("protocol_path", "protocol_file_sha256"),
        ):
            path = test_source_root / record[path_key]
            if not path.is_file() or _file_hash(path) != record[hash_key]:
                raise DecompositionTestRemediationError(
                    "Protected test-source file hash mismatch"
                )
    for record in dev_snapshot["records"]:
        for path_key, hash_key in (
            ("source_study_path", "source_study_file_sha256"),
            ("protocol_path", "protocol_file_sha256"),
        ):
            path = dev_source_root / record[path_key]
            if not path.is_file() or _file_hash(path) != record[hash_key]:
                raise DecompositionTestRemediationError(
                    "Separated dev-source file hash mismatch"
                )
    return selection
