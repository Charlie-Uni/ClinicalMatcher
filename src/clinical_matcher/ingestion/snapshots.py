import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..splits import current_git_commit
from ..validation import DocumentValidationError, validate_document
from .trials import (
    CLINICALTRIALS_TERMS_URL,
    NCT_PATTERN,
    ClinicalTrialsClient,
    TrialImportError,
    normalize_study,
)


SNAPSHOT_VERSION = "1.0.0"
COVERAGE_VERSION = "1.0.0"
SNAPSHOT_SCHEMA_RESOURCE = "schemas/trial-snapshot-1.0.0.schema.json"
COVERAGE_SCHEMA_RESOURCE = "schemas/trial-import-coverage-1.0.0.schema.json"


class SnapshotError(ValueError):
    """Raised when an immutable trial snapshot cannot be built or verified."""


@dataclass(frozen=True)
class TrialSelection:
    disease_domain: str
    rationale: str
    query_parameters: Mapping[str, str]
    max_studies: Optional[int] = None

    def normalized(self) -> Dict[str, Any]:
        if not self.disease_domain.strip():
            raise SnapshotError("disease_domain must not be empty")
        if not self.rationale.strip():
            raise SnapshotError("selection rationale must not be empty")
        parameters = {
            str(key): str(value)
            for key, value in sorted(self.query_parameters.items())
        }
        if "pageToken" in parameters:
            raise SnapshotError(
                "pageToken is transient and cannot be frozen as selection logic"
            )
        if not any(key.startswith("query.") for key in parameters):
            raise SnapshotError("selection requires at least one query.* parameter")
        if not parameters.get("sort"):
            raise SnapshotError("selection must define an explicit sort order")
        parameters.setdefault("format", "json")
        parameters.setdefault("markupFormat", "markdown")
        parameters.setdefault("countTotal", "true")
        parameters.setdefault("pageSize", "100")
        if parameters["format"] != "json":
            raise SnapshotError("snapshot selection requires format=json")
        if parameters["markupFormat"] != "markdown":
            raise SnapshotError(
                "snapshot selection requires markupFormat=markdown"
            )
        if parameters["countTotal"].lower() != "true":
            raise SnapshotError("snapshot selection requires countTotal=true")
        try:
            page_size = int(parameters["pageSize"])
        except ValueError as error:
            raise SnapshotError("selection pageSize must be an integer") from error
        if not 1 <= page_size <= 1000:
            raise SnapshotError("selection pageSize must be between 1 and 1000")
        if self.max_studies is not None and self.max_studies < 1:
            raise SnapshotError("max_studies must be at least 1")
        return {
            "disease_domain": self.disease_domain.strip(),
            "rationale": self.rationale.strip(),
            "query_parameters": parameters,
            "max_studies": self.max_studies,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _study_nct_id(study: Dict[str, Any]) -> Optional[str]:
    value = (
        study.get("protocolSection", {})
        .get("identificationModule", {})
        .get("nctId")
    )
    if isinstance(value, str) and NCT_PATTERN.fullmatch(value):
        return value
    return None


def _snapshot_fingerprint_payload(manifest: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "snapshot_version": manifest["snapshot_version"],
        "registry": manifest["registry"],
        "api_version": manifest["api_version"],
        "api_data_timestamp": manifest["api_data_timestamp"],
        "builder_code_commit": manifest["builder_code_commit"],
        "selection": manifest["selection"],
        "search": manifest["search"],
        "candidate_keys": manifest["candidate_keys"],
        "records": manifest["records"],
    }


def _coverage_document(
    snapshot_id: str,
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    total = len(records)
    counts = {
        status: sum(record["status"] == status for record in records)
        for status in ("imported", "skipped", "failed")
    }
    reason_counts: Dict[str, int] = {}
    criteria_counts: List[int] = []
    outcomes = []
    for record in records:
        reason = record.get("reason_code")
        if reason:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if record["status"] == "imported":
            criteria_counts.append(record["criterion_count"])
        outcome = {
            "candidate_key": record["candidate_key"],
            "nct_id": record["nct_id"],
            "status": record["status"],
        }
        if reason:
            outcome["reason_code"] = reason
            outcome["reason"] = record["reason"]
        outcomes.append(outcome)
    document = {
        "coverage_version": COVERAGE_VERSION,
        "snapshot_id": snapshot_id,
        "total_candidates": total,
        "imported_count": counts["imported"],
        "skipped_count": counts["skipped"],
        "failed_count": counts["failed"],
        "parse_success_rate": counts["imported"] / total if total else 0.0,
        "skipped_rate": counts["skipped"] / total if total else 0.0,
        "failed_rate": counts["failed"] / total if total else 0.0,
        "reason_counts": dict(sorted(reason_counts.items())),
        "criteria_count": {
            "total": sum(criteria_counts),
            "minimum": min(criteria_counts) if criteria_counts else 0,
            "maximum": max(criteria_counts) if criteria_counts else 0,
        },
        "outcomes": outcomes,
    }
    validate_document(document, COVERAGE_SCHEMA_RESOURCE)
    return document


def build_trial_snapshot(
    studies: Sequence[Dict[str, Any]],
    version_payload: Dict[str, Any],
    selection: TrialSelection,
    output_dir: Path,
    search_metadata: Optional[Mapping[str, Any]] = None,
    created_at: Optional[str] = None,
    builder_code_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an immutable, self-verifying public trial snapshot.

    Both successfully parsed protocols and rejected source studies are retained.
    This allows future parser changes to be audited without calling the live API.
    """
    if output_dir.exists():
        raise SnapshotError(f"Snapshot destination already exists: {output_dir}")
    if not studies:
        raise SnapshotError("Cannot build an empty trial snapshot")
    api_version = version_payload.get("apiVersion")
    api_data_timestamp = version_payload.get("dataTimestamp")
    if not isinstance(api_version, str) or not api_version:
        raise SnapshotError("API version payload is missing apiVersion")
    if not isinstance(api_data_timestamp, str) or not api_data_timestamp:
        raise SnapshotError("API version payload is missing dataTimestamp")

    normalized_selection = selection.normalized()
    commit = builder_code_commit or current_git_commit()
    timestamp = created_at or _now()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.",
            dir=str(output_dir.parent),
        )
    )
    records: List[Dict[str, Any]] = []
    candidate_keys: List[str] = []
    seen_nct_ids = set()
    try:
        for index, study in enumerate(studies):
            if not isinstance(study, dict):
                raise SnapshotError(f"Candidate {index} is not a JSON object")
            nct_id = _study_nct_id(study)
            candidate_key = nct_id or f"candidate-{index:05d}"
            if candidate_key in candidate_keys:
                raise SnapshotError(f"Duplicate candidate: {candidate_key}")
            if nct_id in seen_nct_ids:
                raise SnapshotError(f"Duplicate NCT ID: {nct_id}")
            if nct_id:
                seen_nct_ids.add(nct_id)
            candidate_keys.append(candidate_key)

            source_bytes = _canonical_bytes(study)
            source_relative = f"source-studies/{candidate_key}.json"
            _write_json(staging / source_relative, study)
            base_record: Dict[str, Any] = {
                "candidate_key": candidate_key,
                "candidate_index": index,
                "nct_id": nct_id,
                "source_study_path": source_relative,
                "source_study_sha256": _sha256(source_bytes),
            }
            try:
                protocol = normalize_study(
                    study,
                    version_payload,
                    retrieved_at=timestamp,
                    importer_code_commit=commit,
                )
                protocol_relative = f"protocols/{protocol['nct_id']}.json"
                _write_json(staging / protocol_relative, protocol)
                protocol_sha256 = _sha256(_canonical_bytes(protocol))
                records.append(
                    {
                        **base_record,
                        "status": "imported",
                        "protocol_path": protocol_relative,
                        "protocol_sha256": protocol_sha256,
                        "source_record_version": protocol[
                            "source_record_version"
                        ],
                        "registry_snapshot_date": protocol[
                            "registry_snapshot_date"
                        ],
                        "last_update_posted": protocol["last_update_posted"],
                        "eligibility_sha256": protocol["eligibility_sha256"],
                        "criterion_count": len(protocol["criteria"]),
                        "criterion_ids": [
                            item["criterion_id"]
                            for item in protocol["criteria"]
                        ],
                    }
                )
            except TrialImportError as error:
                records.append(
                    {
                        **base_record,
                        "status": "skipped",
                        "reason_code": error.code,
                        "reason": str(error),
                    }
                )
            except DocumentValidationError as error:
                records.append(
                    {
                        **base_record,
                        "status": "failed",
                        "reason_code": "normalized_protocol_schema_error",
                        "reason": str(error),
                    }
                )

        search = {
            "reported_total_count": None,
            "pages_fetched": 1,
            "selection_truncated": False,
        }
        if search_metadata:
            search.update(search_metadata)
        manifest: Dict[str, Any] = {
            "snapshot_version": SNAPSHOT_VERSION,
            "registry": "ClinicalTrials.gov",
            "attribution": (
                "ClinicalTrials.gov, U.S. National Library of Medicine"
            ),
            "terms_url": CLINICALTRIALS_TERMS_URL,
            "api_version": api_version,
            "api_data_timestamp": api_data_timestamp,
            "created_at": timestamp,
            "builder_code_commit": commit,
            "selection": normalized_selection,
            "search": search,
            "candidate_keys": candidate_keys,
            "records": records,
        }
        content_sha256 = _sha256(
            _canonical_bytes(_snapshot_fingerprint_payload(manifest))
        )
        snapshot_id = f"ctg-{content_sha256[:16]}"
        manifest["snapshot_id"] = snapshot_id
        manifest["snapshot_content_sha256"] = content_sha256

        coverage = _coverage_document(snapshot_id, records)
        _write_json(staging / "coverage-report.json", coverage)
        manifest["coverage_report_path"] = "coverage-report.json"
        manifest["coverage_report_sha256"] = _sha256(
            _canonical_bytes(coverage)
        )
        validate_document(manifest, SNAPSHOT_SCHEMA_RESOURCE)
        _write_json(staging / "snapshot-manifest.json", manifest)
        staging.rename(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def build_live_trial_snapshot(
    client: ClinicalTrialsClient,
    selection: TrialSelection,
    output_dir: Path,
) -> Dict[str, Any]:
    normalized = selection.normalized()
    studies, version, search_metadata = client.search(
        normalized["query_parameters"],
        max_studies=selection.max_studies,
    )
    return build_trial_snapshot(
        studies=studies,
        version_payload=version,
        selection=selection,
        output_dir=output_dir,
        search_metadata=search_metadata,
    )


def validate_trial_snapshot(snapshot_dir: Path) -> Dict[str, Any]:
    """Verify schemas, hashes, IDs, record versions, and local file closure."""
    manifest_path = snapshot_dir / "snapshot-manifest.json"
    if not manifest_path.is_file():
        raise SnapshotError(f"Missing snapshot manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_document(manifest, SNAPSHOT_SCHEMA_RESOURCE)
    if manifest["candidate_keys"] != [
        record["candidate_key"] for record in manifest["records"]
    ]:
        raise SnapshotError("Candidate keys and record order do not match")
    if [record["candidate_index"] for record in manifest["records"]] != list(
        range(len(manifest["records"]))
    ):
        raise SnapshotError("Candidate indexes are not contiguous")

    expected_content_hash = _sha256(
        _canonical_bytes(_snapshot_fingerprint_payload(manifest))
    )
    if expected_content_hash != manifest["snapshot_content_sha256"]:
        raise SnapshotError("Snapshot content fingerprint does not match manifest")
    if manifest["snapshot_id"] != f"ctg-{expected_content_hash[:16]}":
        raise SnapshotError("Snapshot ID does not match its content fingerprint")

    coverage_path = _contained_snapshot_path(
        snapshot_dir,
        manifest["coverage_report_path"],
    )
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    validate_document(coverage, COVERAGE_SCHEMA_RESOURCE)
    if _sha256(_canonical_bytes(coverage)) != manifest["coverage_report_sha256"]:
        raise SnapshotError("Coverage report hash does not match manifest")
    if coverage["snapshot_id"] != manifest["snapshot_id"]:
        raise SnapshotError("Coverage report references another snapshot")
    expected_coverage = _coverage_document(
        manifest["snapshot_id"],
        manifest["records"],
    )
    if coverage != expected_coverage:
        raise SnapshotError("Coverage report does not match snapshot records")

    for record in manifest["records"]:
        source_path = _contained_snapshot_path(
            snapshot_dir,
            record["source_study_path"],
        )
        source = json.loads(source_path.read_text(encoding="utf-8"))
        if _sha256(_canonical_bytes(source)) != record["source_study_sha256"]:
            raise SnapshotError(
                f"Source study hash mismatch for {record['candidate_key']}"
            )
        if record["status"] != "imported":
            continue
        protocol_path = _contained_snapshot_path(
            snapshot_dir,
            record["protocol_path"],
        )
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        validate_document(protocol, "schemas/trial-protocol-1.0.0.schema.json")
        if _sha256(_canonical_bytes(protocol)) != record["protocol_sha256"]:
            raise SnapshotError(f"Protocol hash mismatch for {record['nct_id']}")
        if protocol["nct_id"] != record["nct_id"]:
            raise SnapshotError(f"Protocol NCT ID mismatch for {record['nct_id']}")
        if protocol["source_record_version"] != record["source_record_version"]:
            raise SnapshotError(
                f"Protocol record version mismatch for {record['nct_id']}"
            )
        criterion_ids = [item["criterion_id"] for item in protocol["criteria"]]
        if criterion_ids != record["criterion_ids"]:
            raise SnapshotError(
                f"Criterion IDs changed for {record['nct_id']}"
            )
    return manifest


def _contained_snapshot_path(snapshot_dir: Path, relative_path: str) -> Path:
    path = (snapshot_dir / relative_path).resolve()
    root = snapshot_dir.resolve()
    if path == root or root not in path.parents:
        raise SnapshotError(f"Snapshot path escapes its root: {relative_path}")
    if not path.is_file():
        raise SnapshotError(f"Snapshot file is missing: {relative_path}")
    return path


def load_snapshot_protocols(snapshot_dir: Path) -> Tuple[Dict[str, Any], ...]:
    """Load evaluation inputs from a verified snapshot, never from a live API."""
    manifest = validate_trial_snapshot(snapshot_dir)
    protocols = []
    for record in manifest["records"]:
        if record["status"] == "imported":
            protocols.append(
                json.loads(
                    _contained_snapshot_path(
                        snapshot_dir,
                        record["protocol_path"],
                    ).read_text(encoding="utf-8")
                )
            )
    return tuple(protocols)
