import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..fixture import parse_patients
from ..splits import canonical_sha256, current_git_commit
from ..validation import validate_document


PATIENT_SOURCE_VERSION = "1.0.0"
PATIENT_SOURCE_SCHEMA_RESOURCE = "schemas/patient-source-1.0.0.schema.json"
REGENERATION_MANIFEST_VERSION = "1.0.0"
REGENERATION_SCHEMA_RESOURCE = (
    "schemas/patient-regeneration-manifest-1.0.0.schema.json"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def validate_patient_source(document: Dict[str, Any]) -> None:
    validate_document(document, PATIENT_SOURCE_SCHEMA_RESOURCE)
    patients = parse_patients(document["patients"])
    patient_ids = [patient.patient_id for patient in patients]
    if len(patient_ids) != len(set(patient_ids)):
        raise ValueError("Patient source IDs must be unique")


def regenerate_normalized_patient_source(
    input_path: Path,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    assert_restricted_local_path(input_path)
    source: Dict[str, Any] = json.loads(
        input_path.read_text(encoding="utf-8")
    )
    validate_patient_source(source)
    normalized_text = (
        json.dumps(source, indent=2, sort_keys=True) + "\n"
    )
    normalized_bytes = normalized_text.encode("utf-8")
    normalized_sha256 = hashlib.sha256(normalized_bytes).hexdigest()
    manifest: Dict[str, Any] = {
        "manifest_version": REGENERATION_MANIFEST_VERSION,
        "manifest_sha256": "pending",
        "source_dataset_id": source["source"]["dataset_id"],
        "source_dataset_version": source["source"]["dataset_version"],
        "access_policy": source["source"]["access_policy"],
        "terms_url": source["source"]["terms_url"],
        "adapter": {
            "name": "normalized-json",
            "version": "1.0.0",
        },
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "input_raw_sha256": _raw_sha256(input_path),
        "normalized_output_sha256": normalized_sha256,
        "patient_count": len(source["patients"]),
        "modifications": [
            "Validated typed patient facts and evidence references.",
            "Canonicalized JSON key order and indentation.",
            "Did not change clinical values, identifiers, or evidence text.",
        ],
        "disclosure_note": (
            "The normalized patient output remains restricted and local. "
            "This aggregate manifest contains no row-level IDs or text, but "
            "export still requires the applicable data-governance review."
        ),
    }
    manifest["manifest_sha256"] = _manifest_hash(manifest)
    validate_document(manifest, REGENERATION_SCHEMA_RESOURCE)
    return source, manifest


def assert_restricted_local_path(path: Path) -> None:
    resolved = path.resolve()
    repository = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if repository.returncode != 0:
        return
    root = Path(repository.stdout.strip()).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", str(resolved)],
        check=False,
    )
    if ignored.returncode != 0:
        raise ValueError(
            "Restricted input/output inside the repository must be covered by "
            ".gitignore (use artifacts/ or private_data/)"
        )


def write_regenerated_patient_source(
    source: Dict[str, Any],
    manifest: Dict[str, Any],
    output_path: Path,
    overwrite: bool = False,
) -> Tuple[Path, Path]:
    assert_restricted_local_path(output_path)
    manifest_path = output_path.with_name(
        f"{output_path.stem}.regeneration-manifest.json"
    )
    existing = [path for path in (output_path, manifest_path) if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Refusing to overwrite existing restricted output: "
            + ", ".join(str(path) for path in existing)
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(source, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    actual_sha256 = _raw_sha256(output_path)
    if actual_sha256 != manifest["normalized_output_sha256"]:
        raise RuntimeError("Normalized patient output hash mismatch")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path, manifest_path
