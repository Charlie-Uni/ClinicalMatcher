import copy
import json
import io
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from clinical_matcher.p8_safety import (
    EventLedger, P8Error, REVIEW_USES, build_access_manifest, check_pin,
    consume_holdout_once, make_pin, read_development_artifact, seal,
    validate_access_manifest, write_private,
)
from clinical_matcher.p8_cli import main, build_parser
from tests.test_apixaban_calibration import frozen_split, build_reservation


def access_fixture(root, verified=True):
    split = frozen_split()
    reservation = build_reservation(split)
    review = {
        "status": "verified" if verified else "unverified",
        "review_record_id": "synthetic-only-reviewed-metadata" if verified else None,
        "uses_checked": list(REVIEW_USES) if verified else [],
        "evidence_pins": [make_pin({"synthetic": True}, "content")] if verified else [],
        "unresolved_items": [] if verified else ["history_not_verified"],
        "development_use_found": False,
    }
    result = build_access_manifest(split, reservation, review=review,
                                   artifacts=[], synthetic=True)
    for partition, patient_ids in result["populations"].items():
        document = seal({"p8_partition_version": "1.0.0", "partition": partition,
                         "kind": "evidence", "source_split_pin": result["source_split_pin"],
                         "reservation_pin": result["reservation_pin"],
                         "rows": [{"patient_id": pid, "evidence": []} for pid in patient_ids]})
        payload = (json.dumps(document, indent=2) + "\n").encode()
        path = root / f"{partition}.json"
        path.write_bytes(payload)
        path.chmod(0o600)
        result["artifacts"].append({
            "artifact_id": partition, "partition": partition, "kind": "evidence",
            "scope": "preisolated_partition_only", "path": str(path),
            "file_pin": make_pin(payload, "file"), "content_pin": make_pin(document, "content"),
        })
    return seal(result)


class P8SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.manifest = access_fixture(self.root)

    def test_distinct_hashes_roundtrip_and_swapped_consumer_rejected(self):
        document = seal({"example": "not a patient"})
        payload = (json.dumps(document, indent=2) + "\n").encode()
        file_pin = make_pin(payload, "file")
        self_pin = make_pin(document, "self", self_field="self_sha256")
        self.assertNotEqual(file_pin["value"], self_pin["value"])
        check_pin(file_pin, payload, kind="file")
        check_pin(self_pin, document, kind="self")
        with self.assertRaises(P8Error):
            check_pin(self_pin, payload, kind="file")
        corrupt = dict(file_pin, value=self_pin["value"])
        with self.assertRaises(P8Error):
            check_pin(corrupt, payload, kind="file")

    def test_only_registered_partition_opened_and_no_global_loader(self):
        with patch("clinical_matcher.apixaban_evaluation.evaluate_apixaban_predictions", side_effect=AssertionError):
            result = read_development_artifact(self.manifest, "validation", purpose="inference", synthetic=True)
        self.assertEqual("validation", result["partition"])

    def test_holdout_train_for_eval_unknown_id_and_unreviewed_reject_before_io(self):
        cases = [("secondary_holdout", "inference"), ("train_fit", "evaluation"),
                 ("/arbitrary/full-benchmark.json", "evaluation")]
        with patch("clinical_matcher.p8_safety._read_private_bytes", side_effect=AssertionError) as read:
            for key, purpose in cases:
                with self.subTest(key=key), self.assertRaises(P8Error):
                    read_development_artifact(self.manifest, key, purpose=purpose, synthetic=True)
            pending = copy.deepcopy(self.manifest)
            pending["independence_review"]["status"] = "unverified"
            with self.assertRaises(P8Error):
                read_development_artifact(seal(pending), "validation", purpose="evaluation", synthetic=True)
            read.assert_not_called()

    def test_synthetic_manifest_cannot_authorize_real_data(self):
        with self.assertRaises(P8Error):
            validate_access_manifest(self.manifest)

    def test_development_cli_has_no_holdout_or_full_benchmark_route(self):
        with self.assertRaises(SystemExit), patch("sys.stderr", new=io.StringIO()):
            build_parser().parse_args(["holdout", "--benchmark", "must-not-open"])
        with self.assertRaises(SystemExit), patch("sys.stderr", new=io.StringIO()):
            build_parser().parse_args(["run-e0", "--split", "test"])

    def test_cli_rejects_synthetic_manifest_without_content_or_exception_leak(self):
        path = write_private(self.manifest, self.root / "manifest.json")
        capture = io.StringIO()
        with patch("sys.stdout", capture), patch("clinical_matcher.p8_safety.read_development_artifact", side_effect=AssertionError):
            code = main(["check-access", "--access-manifest", str(path), "--acknowledge-restricted-data-local-only"])
        self.assertEqual(2, code)
        self.assertNotIn(self.manifest["populations"]["validation"][0], capture.getvalue())

    def test_review_cannot_hide_missing_uses_evidence_or_development(self):
        for field, value in [("uses_checked", []), ("evidence_pins", []),
                             ("unresolved_items", ["not_known"]), ("development_use_found", True)]:
            changed = copy.deepcopy(self.manifest)
            changed["independence_review"][field] = value
            with self.subTest(field=field), self.assertRaises(P8Error):
                validate_access_manifest(seal(changed), synthetic=True)

    def test_membership_overlap_and_full_corpus_registration_rejected(self):
        changed = copy.deepcopy(self.manifest)
        changed["populations"]["validation"] = changed["populations"]["secondary_holdout"]
        with self.assertRaises(P8Error):
            validate_access_manifest(seal(changed), synthetic=True)
        changed = copy.deepcopy(self.manifest)
        changed["artifacts"][0]["scope"] = "read_full_then_filter"
        with self.assertRaises(ValueError):
            validate_access_manifest(seal(changed), synthetic=True)

    def test_resealed_disjoint_population_swap_fails_before_content_read(self):
        changed = copy.deepcopy(self.manifest)
        validation = changed["populations"]["validation"]
        holdout = changed["populations"]["secondary_holdout"]
        validation[0], holdout[0] = holdout[0], validation[0]
        validation.sort()
        holdout.sort()
        with patch("clinical_matcher.p8_safety._read_private_bytes") as read, self.assertRaises(P8Error):
            read_development_artifact(seal(changed), "validation", purpose="evaluation", synthetic=True)
        read.assert_not_called()

    def test_input_tamper_and_public_permissions_rejected(self):
        path = Path(self.manifest["artifacts"][1]["path"])
        path.write_text('{}\n')
        with self.assertRaises(P8Error):
            read_development_artifact(self.manifest, "validation", purpose="evaluation", synthetic=True)
        path.chmod(0o644)
        with self.assertRaises(P8Error):
            read_development_artifact(self.manifest, "validation", purpose="evaluation", synthetic=True)

    def test_symlink_and_hardlink_rejected(self):
        path = Path(self.manifest["artifacts"][1]["path"])
        other = path.with_name("other.json")
        os.link(path, other)
        with self.assertRaises(P8Error):
            read_development_artifact(self.manifest, "validation", purpose="evaluation", synthetic=True)
        other.unlink()
        path.rename(other)
        path.symlink_to(other)
        with self.assertRaises(P8Error):
            read_development_artifact(self.manifest, "validation", purpose="evaluation", synthetic=True)

    def test_owner_only_exclusive_output_and_durable_chain(self):
        path = write_private({"synthetic": True}, self.root / "outputs" / "one.json")
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
        with self.assertRaises(FileExistsError):
            write_private({}, path)
        ledger = EventLedger(self.root / "events")
        for event in ["attempt_started", "infrastructure_failed", "retry_started", "completed"]:
            ledger.append(event=event, attempt_id="synthetic", config_pin=make_pin({}, "content"), details={})
        self.assertEqual(4, len(ledger.read()))
        with self.assertRaisesRegex(P8Error, "already used"):
            ledger.append(event="attempt_started", attempt_id="synthetic",
                          config_pin=make_pin({}, "content"), details={})
        events = sorted(ledger.root.glob("*.json"))
        events[1].unlink()
        with self.assertRaises(P8Error):
            ledger.read()

    def final_plan(self):
        plan = seal({"stage": "final_frozen", "arms": ["historical_v1", "winner"],
                     "access_manifest_pin": make_pin(self.manifest, "self", self_field="self_sha256")})
        authorization = {"owner_explicitly_authorized": True,
                         "owner_decision_record_id": "synthetic-only-owner-decision",
                         "final_plan_pin": make_pin(plan, "self", self_field="self_sha256")}
        return plan, authorization

    def test_unauthorized_exposure_neither_reads_content_nor_consumes(self):
        plan, authorization = self.final_plan()
        authorization["owner_explicitly_authorized"] = False
        with patch("clinical_matcher.p8_safety.holdout_state_root") as root, self.assertRaises(P8Error):
            consume_holdout_once(authorization=authorization, access_manifest=self.manifest,
                                  final_plan=plan, synthetic=True)
        root.assert_not_called()

    def test_exposure_concurrency_and_new_plan_cannot_reset_lifetime(self):
        plan, authorization = self.final_plan()
        def attempt(_):
            try:
                consume_holdout_once(authorization=authorization, access_manifest=self.manifest,
                                      final_plan=plan, synthetic=True)
                return True
            except P8Error:
                return False
        with patch("clinical_matcher.p8_safety.holdout_state_root", return_value=self.root / "state"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(1, sum(pool.map(attempt, range(2))))
            changed = seal(dict(plan, arms=["renamed_winner"]))
            authorization["final_plan_pin"] = make_pin(changed, "self", self_field="self_sha256")
            with self.assertRaisesRegex(P8Error, "already consumed"):
                consume_holdout_once(authorization=authorization, access_manifest=self.manifest,
                                      final_plan=changed, synthetic=True)

    def test_partial_exposure_file_is_not_repaired_or_retried(self):
        plan, authorization = self.final_plan()
        state = self.root / "state"
        state.mkdir(mode=0o700)
        (state / "secondary-holdout-consumed.json").write_bytes(b"{")
        with patch("clinical_matcher.p8_safety.holdout_state_root", return_value=state), self.assertRaises(P8Error):
            consume_holdout_once(authorization=authorization, access_manifest=self.manifest,
                                  final_plan=plan, synthetic=True)
