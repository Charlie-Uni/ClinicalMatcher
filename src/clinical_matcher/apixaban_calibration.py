import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .apixaban_split import (
    load_apixaban_split_manifest,
    validate_apixaban_split_manifest,
    write_private_json,
)
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


CALIBRATION_RESERVATION_VERSION = "1.0.0"
CALIBRATION_RESERVATION_SCHEMA = (
    "schemas/apixaban-calibration-reservation-1.0.0.schema.json"
)
ALGORITHM_NAME = "sha256_ranked_patient_reservation"
ALGORITHM_VERSION = "1.0.0"
SELECTION_SALT = "clinicalmatcher-p5-calibration-only-v1"


class ApixabanCalibrationError(ValueError):
    """Raised when a calibration-only reservation is invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def _patient_list_hash(patient_ids: list[str]) -> str:
    return canonical_sha256(patient_ids)


def patient_selection_digest(
    patient_id: str,
    *,
    staging_corpus_sha256: str,
    split_manifest_sha256: str,
) -> str:
    payload = "\0".join(
        (
            ALGORITHM_VERSION,
            SELECTION_SALT,
            staging_corpus_sha256,
            split_manifest_sha256,
            patient_id,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _expected_partitions(
    split_manifest: Dict[str, Any], calibration_patient_count: int
) -> Dict[str, list[str]]:
    train_ids = split_manifest["splits"]["train"]["patient_ids"]
    if not 1 <= calibration_patient_count < len(train_ids):
        raise ApixabanCalibrationError(
            "Calibration patient count must be at least 1 and leave at least "
            "one train-fit patient"
        )
    staging_hash = split_manifest["dataset"]["staging_corpus_sha256"]
    split_hash = split_manifest["manifest_sha256"]
    ranked = sorted(
        train_ids,
        key=lambda patient_id: (
            patient_selection_digest(
                patient_id,
                staging_corpus_sha256=staging_hash,
                split_manifest_sha256=split_hash,
            ),
            patient_id,
        ),
    )
    calibration = set(ranked[:calibration_patient_count])
    return {
        "train_fit": sorted(set(train_ids) - calibration),
        "calibration_only": sorted(calibration),
    }


def build_apixaban_calibration_reservation(
    split_manifest: Dict[str, Any],
    *,
    calibration_patient_count: int,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: str,
) -> Dict[str, Any]:
    validate_apixaban_split_manifest(split_manifest)
    if split_manifest["status"] != "frozen":
        raise ApixabanCalibrationError(
            "Calibration reservation requires a frozen split manifest"
        )
    if not split_manifest["freeze"]["test_locked"]:
        raise ApixabanCalibrationError(
            "Calibration reservation requires locked test membership"
        )
    if not generation_command.strip():
        raise ApixabanCalibrationError("Generation command must be recorded")

    memberships = _expected_partitions(
        split_manifest, calibration_patient_count
    )
    train_count = split_manifest["splits"]["train"]["patient_count"]
    document: Dict[str, Any] = {
        "apixaban_calibration_reservation_version": (
            CALIBRATION_RESERVATION_VERSION
        ),
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command,
        "source": {
            "split_manifest_sha256": split_manifest["manifest_sha256"],
            "benchmark_sha256": split_manifest["dataset"][
                "benchmark_sha256"
            ],
            "staging_corpus_sha256": split_manifest["dataset"][
                "staging_corpus_sha256"
            ],
            "source_train_patient_count": train_count,
        },
        "policy": {
            "algorithm": ALGORITHM_NAME,
            "algorithm_version": ALGORITHM_VERSION,
            "selection_salt": SELECTION_SALT,
            "selection_unit": "patient",
            "selection_input_partition": "train",
            "calibration_patient_count": calibration_patient_count,
            "selection_status": "predeclared_not_searched",
        },
        "partitions": {
            name: {
                "patient_count": len(patient_ids),
                "patient_ids": patient_ids,
                "patient_ids_sha256": _patient_list_hash(patient_ids),
            }
            for name, patient_ids in memberships.items()
        },
        "isolation": {
            "source_train_covered_once": True,
            "cross_partition_patient_overlap_count": 0,
            "validation_membership_used_for_selection": False,
            "test_membership_used_for_selection": False,
            "validation_or_test_labels_used": False,
        },
        "restrictions": [
            "Calibration-only patients must not contribute training rows.",
            "Calibration-only patients must not contribute silver labels.",
            "Calibration-only patients must not contribute early stopping or training-loss summaries.",
            "Locked-test membership and labels remain unavailable for model selection.",
        ],
        "disclosure_note": (
            "This owner-only manifest contains pseudonymous row-level patient "
            "membership and must remain outside Git and ordinary online services."
        ),
    }
    document["manifest_sha256"] = _manifest_hash(document)
    validate_apixaban_calibration_reservation(document, split_manifest)
    return document


def validate_apixaban_calibration_reservation(
    document: Dict[str, Any],
    split_manifest: Optional[Dict[str, Any]] = None,
) -> None:
    validate_document(document, CALIBRATION_RESERVATION_SCHEMA)
    if _manifest_hash(document) != document["manifest_sha256"]:
        raise ApixabanCalibrationError(
            "Calibration reservation manifest hash mismatch"
        )
    partitions = document["partitions"]
    for partition in partitions.values():
        if partition["patient_ids"] != sorted(partition["patient_ids"]):
            raise ApixabanCalibrationError(
                "Calibration reservation patient IDs must be sorted"
            )
        if partition["patient_count"] != len(partition["patient_ids"]):
            raise ApixabanCalibrationError(
                "Calibration reservation partition count is incorrect"
            )
        if partition["patient_ids_sha256"] != _patient_list_hash(
            partition["patient_ids"]
        ):
            raise ApixabanCalibrationError(
                "Calibration reservation patient-list hash mismatch"
            )
    train_fit = set(partitions["train_fit"]["patient_ids"])
    calibration = set(partitions["calibration_only"]["patient_ids"])
    overlap = train_fit & calibration
    if overlap:
        raise ApixabanCalibrationError(
            "Train-fit and calibration-only patients overlap"
        )
    if document["isolation"]["cross_partition_patient_overlap_count"] != len(
        overlap
    ):
        raise ApixabanCalibrationError(
            "Calibration reservation overlap count is incorrect"
        )
    if len(train_fit | calibration) != document["source"][
        "source_train_patient_count"
    ]:
        raise ApixabanCalibrationError(
            "Calibration reservation does not cover source train membership"
        )
    if partitions["calibration_only"]["patient_count"] != document[
        "policy"
    ]["calibration_patient_count"]:
        raise ApixabanCalibrationError(
            "Calibration-only count does not match the frozen policy"
        )

    if split_manifest is None:
        return
    validate_apixaban_split_manifest(split_manifest)
    if split_manifest["status"] != "frozen":
        raise ApixabanCalibrationError(
            "Calibration reservation source split is not frozen"
        )
    source = document["source"]
    if source["split_manifest_sha256"] != split_manifest["manifest_sha256"]:
        raise ApixabanCalibrationError(
            "Calibration reservation references a different split manifest"
        )
    for name in ("benchmark_sha256", "staging_corpus_sha256"):
        if source[name] != split_manifest["dataset"][name]:
            raise ApixabanCalibrationError(
                f"Calibration reservation source {name} mismatch"
            )
    expected = _expected_partitions(
        split_manifest,
        document["policy"]["calibration_patient_count"],
    )
    for name, patient_ids in expected.items():
        if partitions[name]["patient_ids"] != patient_ids:
            raise ApixabanCalibrationError(
                "Calibration reservation does not match deterministic selection"
            )


def build_apixaban_calibration_reservation_from_path(
    split_path: Path,
    **kwargs: Any,
) -> Dict[str, Any]:
    split_manifest = load_apixaban_split_manifest(split_path)
    return build_apixaban_calibration_reservation(split_manifest, **kwargs)


def write_apixaban_calibration_reservation(
    document: Dict[str, Any], output_path: Path
) -> Path:
    validate_apixaban_calibration_reservation(document)
    return write_private_json(document, output_path)
