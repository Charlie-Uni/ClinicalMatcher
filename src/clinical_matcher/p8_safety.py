"""P8 local-only I/O, typed hash pins, and fail-closed exposure accounting.

No function in this module imports a full benchmark/staging loader. A read
requires a predeclared partition-only artifact; selecting a split after a read
is deliberately not an available operation. Holdout execution is not exposed
by the P8 development CLI.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .apixaban_calibration import validate_apixaban_calibration_reservation
from .apixaban_contract import load_question_catalog
from .ingestion.patients import assert_restricted_local_path
from .validation import validate_document


class P8Error(ValueError):
    """A P8 precondition failed; messages must not contain patient content."""


SELF_SEMANTICS = {
    "storage": "sha256_canonical_json_without_top_level_self_sha256",
    "consumption": "remove_self_sha256_then_recompute_canonical_json",
    "canonicalization": "utf8_ensure_ascii_false_sort_keys_true_compact_no_nan",
}
PIN_SEMANTICS = {
    "file": ("sha256_exact_file_bytes", "rehash_exact_file_bytes"),
    "content": ("sha256_canonical_json", "rehash_canonical_json"),
    "self": ("sha256_canonical_json_without_named_field", "remove_named_field_then_rehash"),
}
PARTITIONS = ("train_fit", "validation", "secondary_holdout")
REVIEW_USES = ("development_inspection", "fitting", "selection", "examples",
               "subsequent_annotation", "calibration", "evaluation")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def seal(document: Mapping[str, Any]) -> dict:
    result = copy.deepcopy(dict(document))
    result.pop("self_sha256", None)
    result["hash_semantics"] = dict(SELF_SEMANTICS)
    result["self_sha256"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    return result


def check_seal(document: Mapping[str, Any]) -> None:
    if document.get("hash_semantics") != SELF_SEMANTICS:
        raise P8Error("Self-hash storage/consumption semantics mismatch")
    if document.get("self_sha256") != seal(document)["self_sha256"]:
        raise P8Error("Document self-hash mismatch")


def make_pin(value: Any, kind: str, *, self_field: str | None = None) -> dict:
    if kind not in PIN_SEMANTICS:
        raise P8Error("Unsupported hash semantics")
    if kind == "self":
        if not self_field or not isinstance(value, dict) or self_field not in value:
            raise P8Error("Self-hash pin requires an existing named field")
        unsigned = dict(value)
        recorded = unsigned.pop(self_field)
        digest = hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
        if digest != recorded:
            raise P8Error("Source document self-hash mismatch")
    elif kind == "file":
        if not isinstance(value, bytes) or self_field is not None:
            raise P8Error("File pin requires exact bytes")
        digest = hashlib.sha256(value).hexdigest()
    else:
        if self_field is not None:
            raise P8Error("Content pin cannot omit a self field")
        digest = hashlib.sha256(canonical_bytes(value)).hexdigest()
    storage, consumption = PIN_SEMANTICS[kind]
    return {"kind": kind, "value": digest, "self_field": self_field,
            "storage": storage, "consumption": consumption}


def check_pin(pin: Mapping[str, Any], value: Any, *, kind: str) -> None:
    if pin.get("kind") != kind:
        raise P8Error("Hash kind does not match consumer")
    if dict(pin) != make_pin(value, kind, self_field=pin.get("self_field")):
        raise P8Error("Hash pin or storage/consumption semantics mismatch")


def _strict_json(payload: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise P8Error("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise P8Error("Non-finite JSON value")

    try:
        result = json.loads(payload, object_pairs_hook=pairs,
                            parse_constant=invalid_constant)
    except (ValueError, UnicodeError):
        raise P8Error("Invalid restricted JSON") from None
    if not isinstance(result, dict):
        raise P8Error("Restricted JSON must be an object")
    return result


def _no_symlinks(path: Path) -> None:
    # absolute(), unlike resolve(), preserves components for the symlink check.
    if not path.is_absolute() or ".." in path.parts:
        raise P8Error("Private path must be absolute without parent traversal")
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise P8Error("Symbolic links are not allowed in private paths")


def private_directory(path: Path) -> Path:
    path = path.absolute()
    _no_symlinks(path)
    assert_restricted_local_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        raise P8Error("Private directory must have mode 0700")
    if info.st_uid != os.getuid():
        raise P8Error("Private directory must belong to the current owner")
    return path


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(document: Any, path: Path) -> Path:
    """Exclusive durable write; a failed/partial file is retained, never erased."""
    path = path.absolute()
    _no_symlinks(path)
    assert_restricted_local_path(path)
    payload = canonical_bytes(document) + b"\n"
    private_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_directory(path.parent)
    return path


def _read_private_bytes(path: Path) -> bytes:
    """Caller must check authorization and artifact identity BEFORE this call."""
    _no_symlinks(path)
    assert_restricted_local_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1):
            raise P8Error("Input must be an owner-only regular file without hard links")
        return stream.read()


def read_metadata(path: Path) -> dict:
    """Only explicitly supplied metadata paths, never recursive discovery."""
    return _strict_json(_read_private_bytes(path.absolute()))


def build_access_manifest(split: dict, reservation: dict, *, review: dict,
                          artifacts: list[dict], synthetic: bool = False,
                          export_receipt: dict | None = None) -> dict:
    """Bind pre-isolated artifacts using membership metadata only. No file reads."""
    validate_apixaban_calibration_reservation(reservation, split)
    manifest = seal({
        "p8_access_version": "1.1.0" if export_receipt else "1.0.0", "synthetic": synthetic,
        "catalog_pin": make_pin(load_question_catalog(), "self", self_field="catalog_sha256"),
        "source_split_pin": make_pin(split, "self", self_field="manifest_sha256"),
        "reservation_pin": make_pin(reservation, "self", self_field="manifest_sha256"),
        "source_metadata": copy.deepcopy({"split": split, "reservation": reservation}),
        "populations": {
            "train_fit": reservation["partitions"]["train_fit"]["patient_ids"],
            "validation": split["splits"]["validation"]["patient_ids"],
            "secondary_holdout": reservation["partitions"]["calibration_only"]["patient_ids"],
        },
        "independence_review": review, "artifacts": artifacts,
        "holdout_authorized": False,
        "locked_test_permanently_closed": True,
        "holdout_lifetime_exposures": 1,
        "replacement_holdout_forbidden": True,
    })
    if export_receipt is not None:
        manifest["mechanical_export_record"] = copy.deepcopy(export_receipt)
        manifest["mechanical_export_pin"] = make_pin(export_receipt, "self", self_field="self_sha256")
        manifest = seal(manifest)
    validate_access_manifest(manifest, synthetic=synthetic)
    return manifest


def validate_access_manifest(document: dict, *, synthetic: bool = False) -> None:
    version = document.get("p8_access_version")
    if version not in {"1.0.0", "1.1.0"}:
        raise P8Error("Unknown P8 access version")
    validate_document(document, f"schemas/p8-data-access-{version}.schema.json")
    check_seal(document)
    if document["synthetic"] != synthetic:
        raise P8Error("Synthetic access manifest is not a real-data authorization")
    check_pin(document["catalog_pin"], load_question_catalog(), kind="self")
    sources = document["source_metadata"]
    validate_apixaban_calibration_reservation(sources["reservation"], sources["split"])
    check_pin(document["source_split_pin"], sources["split"], kind="self")
    check_pin(document["reservation_pin"], sources["reservation"], kind="self")
    populations = document["populations"]
    expected_populations = {
        "train_fit": sources["reservation"]["partitions"]["train_fit"]["patient_ids"],
        "validation": sources["split"]["splits"]["validation"]["patient_ids"],
        "secondary_holdout": sources["reservation"]["partitions"]["calibration_only"]["patient_ids"],
    }
    if populations != expected_populations:
        raise P8Error("Population membership differs from original source metadata")
    all_ids = [pid for ids in populations.values() for pid in ids]
    if len(set(all_ids)) != len(all_ids):
        raise P8Error("Population overlap")
    if any(ids != sorted(set(ids)) for ids in populations.values()):
        raise P8Error("Membership must be sorted and unique")
    if not synthetic and [len(populations[key]) for key in PARTITIONS] != [55, 15, 15]:
        raise P8Error("P8 requires the original 55/15/15 populations")
    review = document["independence_review"]
    if version == "1.1.0":
        from .p8_export import validate_export_receipt
        receipt = document["mechanical_export_record"]
        check_pin(document["mechanical_export_pin"], receipt, kind="self")
        validate_export_receipt(receipt, sources["split"], sources["reservation"])
        exported = [a for a in document["artifacts"] if a["kind"] != "raw_predictions"]
        if exported != receipt["artifacts"]:
            raise P8Error("Partition registry differs from the mechanical export")
    if review["status"] == "verified":
        if (set(review["uses_checked"]) != set(REVIEW_USES)
                or not review["evidence_pins"] or not review["review_record_id"]
                or review["unresolved_items"] or review["development_use_found"]):
            raise P8Error("Independence review cannot be marked verified")
    keys, paths = set(), set()
    for artifact in document["artifacts"]:
        if artifact["artifact_id"] in keys or artifact["path"] in paths:
            raise P8Error("Repeated registered artifact identity")
        keys.add(artifact["artifact_id"])
        paths.add(artifact["path"])
        path = Path(artifact["path"])
        if not path.is_absolute() or ".." in path.parts:
            raise P8Error("Artifact path must be absolute and canonical")
        if artifact["scope"] != "preisolated_partition_only":
            raise P8Error("Full-corpus input is forbidden")
        if artifact["kind"] == "raw_predictions" and artifact["partition"] != "validation":
            raise P8Error("Only frozen validation predictions may be registered")


def require_development_ready(manifest: dict, *, synthetic: bool = False) -> None:
    validate_access_manifest(manifest, synthetic=synthetic)
    if not synthetic and manifest["p8_access_version"] != "1.1.0":
        raise P8Error("Real P8 development requires the amended export-bound access gate")
    if manifest["independence_review"]["status"] != "verified":
        raise P8Error("P8.1 independence review is incomplete; real development is closed")


def read_development_artifact(manifest: dict, artifact_id: str, *, purpose: str,
                              synthetic: bool = False) -> dict:
    """Registry-key API: authorization and partition checks precede all I/O."""
    require_development_ready(manifest, synthetic=synthetic)
    allowed = {"evaluation": {"validation"}, "inference": {"validation"},
               "examples": {"train_fit"}}
    if purpose not in allowed:
        raise P8Error("Unknown development purpose")
    matches = [a for a in manifest["artifacts"] if a["artifact_id"] == artifact_id]
    if len(matches) != 1:
        raise P8Error("Unregistered input; refusing to open a path")
    artifact = matches[0]
    if artifact["partition"] not in allowed[purpose]:
        raise P8Error("Partition is not authorized for this development purpose")
    if purpose == "inference" and artifact["kind"] != "evidence":
        raise P8Error("Inference cannot read labels or predictions")
    if purpose == "examples" and artifact["kind"] not in {"evidence", "gold", "examples"}:
        raise P8Error("Unsupported example source")
    payload = _read_private_bytes(Path(artifact["path"]))
    check_pin(artifact["file_pin"], payload, kind="file")
    result = _strict_json(payload)
    check_pin(artifact["content_pin"], result, kind="content")
    # Population/content checks are additional integrity checks, NOT the I/O gate.
    if artifact["kind"] == "raw_predictions":
        if result.get("split_name") != "validation":
            raise P8Error("Registered raw prediction has the wrong split")
        if result.get("benchmark_sha256") != manifest["source_metadata"]["split"]["dataset"]["benchmark_sha256"]:
            raise P8Error("Raw prediction benchmark differs from the frozen source")
        rows = result.get("predictions", [])
        observed = {row.get("patient_id") for row in rows}
    else:
        validate_document(result, "schemas/p8-partition-artifact-1.0.0.schema.json")
        check_seal(result)
        if result["partition"] != artifact["partition"] or result["kind"] != artifact["kind"]:
            raise P8Error("Partition artifact identity mismatch")
        if result["reservation_pin"] != manifest["reservation_pin"]:
            raise P8Error("Partition artifact reservation mismatch")
        if result["source_split_pin"] != manifest["source_split_pin"]:
            raise P8Error("Partition artifact source split mismatch")
        observed = {row.get("patient_id") for row in result["rows"]}
    if observed != set(manifest["populations"][artifact["partition"]]):
        raise P8Error("Artifact does not match its exact registered population")
    return result


class EventLedger:
    """Append-only, serialized event files; failed writes remain fail-closed."""

    def __init__(self, root: Path):
        self.root = private_directory(root.absolute())

    def append(self, *, event: str, attempt_id: str, config_pin: dict,
               details: dict) -> dict:
        lock_path = self.root / ".append.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            events = self.read()
            if event == "attempt_started" and any(item["attempt_id"] == attempt_id for item in events):
                raise P8Error("Attempt identity was already used; preserve the previous attempt")
            result = seal({"p8_event_version": "1.0.0", "sequence": len(events),
                           "previous_event_pin": make_pin(events[-1], "self", self_field="self_sha256") if events else None,
                           "event": event, "attempt_id": attempt_id,
                           "config_pin": config_pin, "timestamp": now(), "details": details})
            validate_document(result, "schemas/p8-event-1.0.0.schema.json")
            write_private(result, self.root / f"{len(events):06d}.json")
            return result

    def read(self) -> list[dict]:
        if any(p.name != ".append.lock" and not re.fullmatch(r"[0-9]{6}\.json", p.name)
               for p in self.root.iterdir()):
            raise P8Error("Event directory contains unrecognized content")
        paths = sorted(self.root.glob("*.json"))
        if any(p.name != f"{i:06d}.json" for i, p in enumerate(paths)):
            raise P8Error("Event sequence has a gap or unknown file")
        events = []
        for i, path in enumerate(paths):
            result = read_metadata(path)
            validate_document(result, "schemas/p8-event-1.0.0.schema.json")
            check_seal(result)
            expected_parent = make_pin(events[-1], "self", self_field="self_sha256") if events else None
            if result.get("sequence") != i or result.get("previous_event_pin") != expected_parent:
                raise P8Error("Event chain mismatch")
            events.append(result)
        return events


def holdout_state_root() -> Path:
    # Deliberately no CLI/path override: shared across branches, worktrees,
    # clones and output directories for this user's ClinicalMatcher project.
    return Path.home() / ".clinicalmatcher-p8-lifetime-state"


def consume_holdout_once(*, authorization: dict, access_manifest: dict,
                         final_plan: dict, synthetic: bool = False) -> dict:
    """Reserve the lifetime exposure BEFORE any protected content is opened.

    This is only a guard primitive, not a holdout runner or an authorization
    editor. Synthetic tests patch holdout_state_root; production has no override.
    """
    require_development_ready(access_manifest, synthetic=synthetic)
    check_seal(final_plan)
    if not authorization.get("owner_explicitly_authorized", False):
        raise P8Error("Holdout execution is not authorized")
    if not authorization.get("owner_decision_record_id"):
        raise P8Error("Holdout owner decision record is missing")
    check_pin(authorization["final_plan_pin"], final_plan, kind="self")
    check_pin(final_plan["access_manifest_pin"], access_manifest, kind="self")
    if final_plan.get("stage") != "final_frozen" or not final_plan.get("arms"):
        raise P8Error("Final holdout arm set is not frozen")
    if len(set(final_plan["arms"])) != len(final_plan["arms"]):
        raise P8Error("Final holdout arm set repeats an arm")
    event = seal({"p8_lifetime_exposure_version": "1.0.0",
                  "status": "consumed_before_content_access", "timestamp": now(),
                  "final_plan_pin": make_pin(final_plan, "self", self_field="self_sha256"),
                  "reservation_pin": access_manifest["reservation_pin"],
                  "owner_decision_record_id": authorization["owner_decision_record_id"]})
    # O_EXCL is the serialization point. Even a partial event permanently burns
    # the slot; there is no delete/reset/retry function.
    try:
        write_private(event, holdout_state_root() / "secondary-holdout-consumed.json")
    except FileExistsError:
        raise P8Error("Project lifetime holdout exposure already consumed") from None
    return event
