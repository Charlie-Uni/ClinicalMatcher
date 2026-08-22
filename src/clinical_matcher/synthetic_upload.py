"""Exact-manifest guard for synthetic-only upload bundles."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

from .public_safety import scan_public_file
from .splits import canonical_sha256, current_git_commit
from .validation import validate_document


SYNTHETIC_UPLOAD_VERSION = "1.0.0"
SYNTHETIC_UPLOAD_SCHEMA = "schemas/synthetic-upload-manifest-1.0.0.schema.json"
MANIFEST_NAME = "synthetic-upload-manifest.json"
ALLOWED_PAYLOAD_SUFFIXES = {
    ".json",
    ".jsonl",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


class SyntheticUploadError(ValueError):
    """Raised when a proposed synthetic upload bundle is unsafe or changed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def _validate_relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.startswith(".") for part in path.parts)
        or "\\" in value
    ):
        raise SyntheticUploadError(
            f"Upload manifest path is not normalized and safe: {value!r}"
        )
    if path.name == MANIFEST_NAME:
        raise SyntheticUploadError(
            "A synthetic upload manifest cannot be nested or listed as payload"
        )


def _payload_files(bundle_dir: Path) -> list[tuple[str, Path]]:
    if not bundle_dir.is_dir():
        raise SyntheticUploadError(f"Bundle directory does not exist: {bundle_dir}")
    payload: list[tuple[str, Path]] = []
    for path in sorted(bundle_dir.rglob("*")):
        relative = path.relative_to(bundle_dir).as_posix()
        if path.is_symlink():
            raise SyntheticUploadError(
                f"Synthetic upload bundle contains a symbolic link: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise SyntheticUploadError(
                f"Synthetic upload bundle contains a non-regular file: {relative}"
            )
        if relative == MANIFEST_NAME:
            continue
        _validate_relative_path(relative)
        payload.append((relative, path))
    return payload


def _assert_files_are_upload_safe(files: list[tuple[str, Path]]) -> None:
    problems = []
    for relative, path in files:
        if path.suffix.lower() not in ALLOWED_PAYLOAD_SUFFIXES:
            problems.append(
                f"{relative}: file type is not allowed in a synthetic upload"
            )
        problems.extend(
            scan_public_file(
                path,
                logical_path=relative,
                allow_synthetic_jsonl=True,
                require_synthetic_marker=True,
            )
        )
    if problems:
        raise SyntheticUploadError(
            "Synthetic upload safety check failed:\n- " + "\n- ".join(problems)
        )


def build_synthetic_upload_manifest(
    bundle_dir: Path,
    *,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: str,
) -> Dict[str, Any]:
    if not generation_command.strip():
        raise SyntheticUploadError("Generation command must be recorded")
    if (bundle_dir / MANIFEST_NAME).exists():
        raise SyntheticUploadError(
            f"Refusing to replace existing {MANIFEST_NAME}"
        )
    files = _payload_files(bundle_dir)
    if not files:
        raise SyntheticUploadError("Synthetic upload bundle must not be empty")
    _assert_files_are_upload_safe(files)

    document: Dict[str, Any] = {
        "synthetic_upload_manifest_version": SYNTHETIC_UPLOAD_VERSION,
        "manifest_sha256": "pending",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command,
        "attestation": "independently_authored_synthetic_only",
        "constraints": {
            "exact_file_set_required": True,
            "restricted_or_clinical_data_allowed": False,
            "symbolic_links_allowed": False,
        },
        "files": [
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
            for relative, path in files
        ],
    }
    document["manifest_sha256"] = _manifest_hash(document)
    validate_synthetic_upload_manifest(document)
    return document


def validate_synthetic_upload_manifest(
    document: Dict[str, Any], bundle_dir: Optional[Path] = None
) -> None:
    validate_document(document, SYNTHETIC_UPLOAD_SCHEMA)
    if document["manifest_sha256"] != _manifest_hash(document):
        raise SyntheticUploadError("Synthetic upload manifest hash mismatch")

    listed_paths = [item["path"] for item in document["files"]]
    for path in listed_paths:
        _validate_relative_path(path)
    if listed_paths != sorted(listed_paths):
        raise SyntheticUploadError("Synthetic upload paths must be sorted")
    if len(listed_paths) != len(set(listed_paths)):
        raise SyntheticUploadError("Synthetic upload paths must be unique")

    if bundle_dir is None:
        return
    manifest_path = bundle_dir / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SyntheticUploadError(
            f"Bundle must contain a regular {MANIFEST_NAME}"
        )
    actual_files = _payload_files(bundle_dir)
    actual_paths = [relative for relative, _ in actual_files]
    if actual_paths != listed_paths:
        raise SyntheticUploadError(
            "Synthetic upload file set differs from the frozen manifest"
        )
    _assert_files_are_upload_safe(actual_files)
    by_path = {relative: path for relative, path in actual_files}
    for item in document["files"]:
        path = by_path[item["path"]]
        if path.stat().st_size != item["size_bytes"]:
            raise SyntheticUploadError(
                f"Synthetic upload size mismatch: {item['path']}"
            )
        if _file_sha256(path) != item["sha256"]:
            raise SyntheticUploadError(
                f"Synthetic upload SHA-256 mismatch: {item['path']}"
            )


def write_synthetic_upload_manifest(
    document: Dict[str, Any], bundle_dir: Path
) -> Path:
    validate_synthetic_upload_manifest(document)
    output = bundle_dir / MANIFEST_NAME
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(output, 0o644)
    return output


def load_and_verify_synthetic_upload_bundle(bundle_dir: Path) -> Dict[str, Any]:
    manifest_path = bundle_dir / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SyntheticUploadError(
            f"Bundle must contain a regular {MANIFEST_NAME}"
        )
    document: Dict[str, Any] = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )
    validate_synthetic_upload_manifest(document, bundle_dir)
    return document
