"""P8 1.1.0 one-shot mechanical partitioning. No scoring or label validation.

Full-source access exists only here, behind a separate durable consumption
marker. Holdout output is born in the guarded vault and is never read back.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from importlib.resources import files

from .apixaban_calibration import validate_apixaban_calibration_reservation
from .apixaban_split import validate_apixaban_split_manifest
from .apixaban_contract import load_question_catalog
from .validation import validate_document
from .p8_safety import (P8Error, EventLedger, _no_symlinks, _read_private_bytes,
                        _strict_json, canonical_bytes, check_pin, check_seal,
                        holdout_state_root, make_pin, now, private_directory,
                        read_metadata, seal, write_private)

VERSION = "1.1.0"
SOURCE_NAMES = {"benchmark": "apixaban-fact-benchmark.json",
                "staging": "apixaban-staging-corpus.json"}
POLICY = {"version": VERSION, "export_lifetime_limit": 1,
          "partitions": ["train_fit", "validation", "secondary_holdout"],
          "test_output": False, "clinical_label_checks": False,
          "metrics": False, "model_calls": False, "content_display": False,
          "holdout_readback": False, "overwrite": False, "retry": False,
          "preserve_source_order": True,
          "exposure_definition": "read_for_evaluation_not_mechanical_creation",
          "independence_claim": "audited_inventory_and_owner_attestation"}


def export_policy() -> dict:
    document = _strict_json(files("clinical_matcher").joinpath(
        "resources/p8-mechanical-export-1.1.0.json").read_bytes())
    check_seal(document)
    if document != seal({"p8_export_policy_version": VERSION, "policy": POLICY}):
        raise P8Error("Mechanical export policy mismatch")
    return document


def _populations(split: dict, reservation: dict) -> dict:
    validate_apixaban_split_manifest(split)
    validate_apixaban_calibration_reservation(reservation, split)
    return {"train_fit": reservation["partitions"]["train_fit"]["patient_ids"],
            "validation": split["splits"]["validation"]["patient_ids"],
            "secondary_holdout": reservation["partitions"]["calibration_only"]["patient_ids"]}


def implementation_pins() -> list:
    root = Path(__file__).parent
    return [{"path": str(p.relative_to(root)), "file_pin": make_pin(p.read_bytes(), "file")}
            for p in sorted(root.rglob("*")) if p.suffix in {".py", ".json"}]


def freeze_export_contract(split: dict, reservation: dict, source_paths: dict,
                           *, owner_decision_record_id: str, synthetic: bool = False) -> dict:
    populations = _populations(split, reservation)
    if not synthetic and [len(populations[k]) for k in POLICY["partitions"]] != [55, 15, 15]:
        raise P8Error("Unexpected real export population")
    if not synthetic and len(split["splits"]["test"]["patient_ids"]) != 15:
        raise P8Error("Unexpected test population")
    if not owner_decision_record_id or set(source_paths) != set(SOURCE_NAMES):
        raise P8Error("Explicit export decision and exactly two sources required")
    sources = {}
    for role, name in SOURCE_NAMES.items():
        path = Path(source_paths[role])
        _no_symlinks(path)
        if path.name != name or any(part in {"keys", "p7-locked-test-single-batch-v1"} for part in path.parts):
            raise P8Error("Source outside the mechanical export allowlist")
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid() or info.st_nlink != 1):
            raise P8Error("Export source must be owner-only without links")
        field = "benchmark_sha256" if role == "benchmark" else "staging_corpus_sha256"
        # Original split stores byte hashes for these two fields. No source read.
        pin = make_pin(b"", "file")
        pin["value"] = split["dataset"][field]
        sources[role] = {"path": str(path), "file_pin": pin}
    result = seal({"p8_export_contract_version": VERSION, "synthetic": synthetic,
                 "policy_pin": make_pin(export_policy(), "self", self_field="self_sha256"),
                 "source_split_pin": make_pin(split, "self", self_field="manifest_sha256"),
                 "reservation_pin": make_pin(reservation, "self", self_field="manifest_sha256"),
                 "owner_decision_record_id": owner_decision_record_id,
                 "sources": sources, "implementation": implementation_pins()})
    validate_document(result, "schemas/p8-export-contract-1.1.0.schema.json")
    return result


def _partition_rows(staging: dict, benchmark: dict, split: dict, populations: dict) -> dict:
    """Membership/key reconciliation only; never branch on a gold value/status."""
    test_ids = set(split["splits"]["test"]["patient_ids"])
    membership = {pid: part for part, ids in populations.items() for pid in ids}
    expected = set(membership) | test_ids
    patient_rows = staging["patients"]
    ids = [p["patient_id"] for p in patient_rows]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise P8Error("Staging membership reconciliation failed")
    if set(benchmark["patient_ids"]) != expected or len(benchmark["patient_ids"]) != len(expected):
        raise P8Error("Benchmark membership reconciliation failed")
    questions = {q["question_id"] for q in load_question_catalog()["questions"]}
    keys = [(r["patient_id"], r["question_id"]) for r in benchmark["assessments"]]
    if len(keys) != len(set(keys)) or set(keys) != {(pid, qid) for pid in expected for qid in questions}:
        raise P8Error("Patient-question key reconciliation failed")
    output = {part: {"evidence": [], "gold": []} for part in populations}
    for patient in patient_rows:
        part = membership.get(patient["patient_id"])
        if part is not None:
            output[part]["evidence"].append({"patient_id": patient["patient_id"],
                                             "evidence": patient["evidence"]})
    for row in benchmark["assessments"]:
        part = membership.get(row["patient_id"])
        if part is not None:
            output[part]["gold"].append(row)
    return output


def validate_export_receipt(receipt: dict, split: dict, reservation: dict) -> None:
    validate_document(receipt, "schemas/p8-export-receipt-1.1.0.schema.json")
    check_seal(receipt)
    if (receipt.get("p8_export_receipt_version") != VERSION or receipt.get("status") != "completed"
            or receipt.get("test_outputs") != 0 or receipt.get("holdout_readback") is not False):
        raise P8Error("Incomplete mechanical export receipt")
    check_pin(receipt["source_split_pin"], split, kind="self")
    check_pin(receipt["reservation_pin"], reservation, kind="self")
    for role, field in (("benchmark", "benchmark_sha256"), ("staging", "staging_corpus_sha256")):
        expected = make_pin(b"", "file")
        expected["value"] = split["dataset"][field]
        if receipt["source_file_pins"][role] != expected:
            raise P8Error("Export receipt source hash semantics mismatch")
    if receipt["policy_pin"] != make_pin(export_policy(), "self", self_field="self_sha256"):
        raise P8Error("Export receipt policy mismatch")
    populations = _populations(split, reservation)
    if receipt["counts"] != {p: len(ids) for p, ids in populations.items()}:
        raise P8Error("Export receipt count mismatch")
    if receipt["discarded_test_patient_count"] != len(split["splits"]["test"]["patient_ids"]):
        raise P8Error("Export test count mismatch")
    artifacts = receipt["artifacts"]
    if len(artifacts) != 6 or {(a["partition"], a["kind"]) for a in artifacts} != {
            (p, k) for p in populations for k in ("evidence", "gold")}:
        raise P8Error("Unexpected export output set")
    for a in artifacts:
        if a["scope"] != "preisolated_partition_only":
            raise P8Error("Export output scope mismatch")
        if a["partition"] == "secondary_holdout":
            if Path(a["path"]).parent != holdout_state_root() / "sealed-partitions":
                raise P8Error("Holdout output is outside the guarded vault")


def export_once(contract: dict, split: dict, reservation: dict, *, output_root: Path,
                synthetic: bool = False) -> dict:
    """Consume first, read each source once, copy without evaluation, never reopen outputs."""
    check_seal(contract)
    expected = freeze_export_contract(split, reservation,
        {k: v["path"] for k, v in contract["sources"].items()},
        owner_decision_record_id=contract["owner_decision_record_id"], synthetic=synthetic)
    if contract != expected:
        raise P8Error("Frozen export contract changed")
    output_root = output_root.absolute()
    vault = holdout_state_root() / "sealed-partitions"
    for path in (output_root, vault):
        _no_symlinks(path)
        if path.exists():
            raise P8Error("Export output already exists")
    contract_pin = make_pin(contract, "self", self_field="self_sha256")
    marker = seal({"p8_export_consumption_version": VERSION, "status": "consumed_before_source_read",
                   "timestamp": now(), "contract_pin": contract_pin,
                   "reservation_pin": contract["reservation_pin"]})
    # This fixed path is independent of every caller-selected output directory.
    write_private(marker, holdout_state_root() / "mechanical-export-consumed.json")
    private_directory(output_root)
    ledger = EventLedger(output_root / "events")
    ledger.append(event="attempt_started", attempt_id="mechanical-export-once",
                  config_pin=contract_pin, details={"stage": "mechanical_export", "metrics": False})
    operation = "read_source"
    try:
        documents = {}
        for role, source in contract["sources"].items():
            payload = _read_private_bytes(Path(source["path"]))
            check_pin(source["file_pin"], payload, kind="file")
            documents[role] = _strict_json(payload)
            del payload
        operation = "reconcile_membership"
        populations = _populations(split, reservation)
        rows = _partition_rows(documents["staging"], documents["benchmark"], split, populations)
        del documents
        operation = "write_partition_artifacts"
        private_directory(vault)
        artifacts, output_self_pins = [], []
        for partition in POLICY["partitions"]:
            for kind in ("evidence", "gold"):
                document = seal({"p8_partition_version": "1.0.0", "partition": partition,
                                 "kind": kind, "source_split_pin": contract["source_split_pin"],
                                 "reservation_pin": contract["reservation_pin"],
                                 "rows": rows[partition][kind]})
                root = vault if partition == "secondary_holdout" else output_root
                path = root / f"{partition}.{kind}.json"
                # Hash exactly the buffer write_private emits. NO file readback.
                file_pin = make_pin(canonical_bytes(document) + b"\n", "file")
                write_private(document, path)
                artifact = {"artifact_id": f"{partition}.{kind}", "partition": partition,
                            "kind": kind, "scope": "preisolated_partition_only", "path": str(path),
                            "file_pin": file_pin, "content_pin": make_pin(document, "content")}
                artifacts.append(artifact)
                output_self_pins.append({"artifact_id": artifact["artifact_id"],
                                         "self_pin": make_pin(document, "self", self_field="self_sha256")})
                del document
        receipt = seal({"p8_export_receipt_version": VERSION, "status": "completed",
                        "contract_pin": contract_pin, "policy_pin": contract["policy_pin"],
                        "source_split_pin": contract["source_split_pin"],
                        "reservation_pin": contract["reservation_pin"],
                        "source_file_pins": {k: v["file_pin"] for k, v in contract["sources"].items()},
                        "counts": {p: len(ids) for p, ids in populations.items()},
                        "discarded_test_patient_count": len(split["splits"]["test"]["patient_ids"]),
                        "test_outputs": 0, "holdout_readback": False, "artifacts": artifacts,
                        "output_self_pins": output_self_pins})
        validate_export_receipt(receipt, split, reservation)
        write_private(receipt, output_root / "export-receipt.json")
        ledger.append(event="completed", attempt_id="mechanical-export-once", config_pin=contract_pin,
                      details={"receipt_pin": make_pin(receipt, "self", self_field="self_sha256")})
        return receipt
    except Exception as error:
        ledger.append(event="failed", attempt_id="mechanical-export-once", config_pin=contract_pin,
                      details={"operation": operation, "failure_type": type(error).__name__, "retry": False})
        raise P8Error("Mechanical export failed terminally; private records retained") from None
