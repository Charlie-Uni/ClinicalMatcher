import copy
import hashlib
import json
import unittest

from clinical_matcher.decomposition_test_remediation import (
    DecompositionTestRemediationError,
    _selection_document,
    _snapshot_document,
    collect_replacement_sources,
    load_remediation_contract,
    ordered_unfetched_remainder,
    validate_remediation_contract,
)


VERSION = {
    "apiVersion": "2.0.5",
    "dataTimestamp": "2026-09-01T00:00:00Z",
}


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def criterion_text(tier, nct_id, index):
    suffix = f" {nct_id} marker {index}"
    if tier == "low":
        return f"Adult participant{suffix}"
    if tier == "medium":
        return f"Age at least 18 and clinically stable{suffix}"
    return (
        "No prior treatment within 30 days and either documented disease "
        f"or specialist confirmation{suffix} " + "context " * 20
    )


def synthetic_study(nct_id):
    lines = []
    index = 0
    for kind in ("Inclusion", "Exclusion"):
        lines.append(f"{kind} Criteria:")
        for tier in ("low", "medium", "high"):
            for _ in range(2):
                lines.append(f"- {criterion_text(tier, nct_id, index)}")
                index += 1
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": f"Synthetic trial {nct_id}",
            },
            "statusModule": {
                "lastUpdatePostDateStruct": {"date": "2026-08-01"}
            },
            "eligibilityModule": {
                "eligibilityCriteria": "\n".join(lines),
                "sex": "ALL",
                "minimumAge": "18 Years",
                "maximumAge": "90 Years",
                "healthyVolunteers": False,
                "stdAges": ["ADULT", "OLDER_ADULT"],
            },
        },
        "derivedSection": {
            "miscInfoModule": {"versionHolder": "2026-08-01"}
        },
    }


def predecessor():
    records = []
    for split in ("dev", "test"):
        for index in range(40):
            text = f"Frozen {split} criterion {index}"
            records.append(
                {
                    "selected": True,
                    "assigned_split": split,
                    "nct_id": f"NCT{80000000 + index:08d}",
                    "criterion_id": f"{split}-{index}",
                    "criterion_type": (
                        "inclusion" if index < 20 else "exclusion"
                    ),
                    "source_record_version": "v1",
                    "protocol_sha256": "1" * 64,
                    "eligibility_sha256": "2" * 64,
                    "source_text": text,
                    "normalized_text_sha256": hashlib.sha256(
                        text.casefold().encode("utf-8")
                    ).hexdigest(),
                    "source_span": {"start": 0, "end": len(text)},
                    "complexity": {
                        "unicode_codepoints": len(text),
                        "length_points": 0,
                        "numeric_or_comparator": False,
                        "connector": False,
                        "negation": False,
                        "temporal": False,
                        "score": 0,
                        "tier": "low",
                    },
                }
            )
    return {
        "selection_manifest_id": "decomposition-selection-befdca243400ea10",
        "selection_manifest_sha256": (
            "befdca243400ea10e7acda1f8fae6351917e4e4aca067b8c839b01a02c1963c3"
        ),
        "records": records,
    }


def source_audit(studies):
    records = []
    for index in range(506):
        nct_id = f"NCT{10000000 + index:08d}"
        study = studies.get(nct_id)
        records.append(
            {
                "nct_id": nct_id,
                "filter_passed": True,
                "selected": False,
                "sampling_hash": f"{index:064x}",
                "source_study_sha256": (
                    canonical_hash(study) if study is not None else "f" * 64
                ),
            }
        )
    return {
        "selection_audit_id": "decomposition-source-selection-63afa142c97ec592",
        "selection_audit_sha256": "a" * 64,
        "flow": {
            "registry_reported_total_count": 833,
            "filter_passed_count": 546,
            "selected_count": 40,
            "eligible_not_sampled_count": 506,
        },
        "records": records,
    }


class DecompositionTestRemediationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_remediation_contract()

    def test_contract_is_self_authenticating(self):
        validate_remediation_contract(self.contract)
        tampered = copy.deepcopy(self.contract)
        tampered["contract_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            DecompositionTestRemediationError, "contract hash mismatch"
        ):
            validate_remediation_contract(tampered)

    def test_remainder_uses_only_unselected_filter_passed_frozen_order(self):
        studies = {}
        audit = source_audit(studies)
        ordered = ordered_unfetched_remainder(audit, self.contract)
        self.assertEqual(506, len(ordered))
        self.assertEqual("NCT10000000", ordered[0]["nct_id"])
        self.assertEqual("NCT10000505", ordered[-1]["nct_id"])

    def test_headless_prefix_selection_meets_all_frozen_quotas(self):
        studies = {
            f"NCT{10000000 + index:08d}": synthetic_study(
                f"NCT{10000000 + index:08d}"
            )
            for index in range(12)
        }
        audit = source_audit(studies)

        def fetcher(nct_id):
            if nct_id not in studies:
                raise OSError("synthetic missing")
            return studies[nct_id], VERSION

        imported, outcomes, selected = collect_replacement_sources(
            predecessor=predecessor(),
            source_audit=audit,
            fetcher=fetcher,
            contract=self.contract,
            builder_code_commit="a" * 40,
        )
        self.assertEqual(40, len(selected))
        self.assertGreaterEqual(len({item["nct_id"] for item in selected}), 5)
        for stratum, expected in self.contract["selection"]["quota"].items():
            kind, tier = stratum.split("-", 1)
            self.assertEqual(
                expected,
                sum(
                    item["criterion_type"] == kind
                    and item["complexity"]["tier"] == tier
                    for item in selected
                ),
            )
        self.assertEqual(len(imported), len(outcomes))
        self.assertFalse(
            any(
                key in outcome
                for outcome in outcomes
                for key in ("source_text", "normalized_text", "eligibility_text")
            )
        )
        snapshot = _snapshot_document(
            imported=imported,
            outcomes=outcomes,
            contract=self.contract,
            created_at="2026-09-01T00:00:00Z",
            builder_code_commit="a" * 40,
        )
        audit["selection_audit_sha256"] = (
            "63afa142c97ec5920353068e0d73a82b646ac1d774bda918f9657c00eeedefb7"
        )
        document = _selection_document(
            predecessor=predecessor(),
            source_audit=audit,
            dev_snapshot={
                "snapshot_id": "decomposition-dev-source-" + "1" * 16,
                "snapshot_sha256": "1" * 64,
            },
            snapshot=snapshot,
            selected=selected,
            contract=self.contract,
            builder_code_commit="a" * 40,
        )
        self.assertEqual(40, document["counts"]["replacement_test_count"])
        self.assertFalse(
            any("source_text" in item for item in document["test_records"])
        )

    def test_source_hash_mismatch_is_skipped_without_persisting_text(self):
        studies = {
            "NCT10000000": synthetic_study("NCT10000000")
        }
        audit = source_audit(studies)
        audit["records"][0]["source_study_sha256"] = "0" * 64

        def fetcher(nct_id):
            if nct_id == "NCT10000000":
                return studies[nct_id], VERSION
            raise OSError("synthetic missing")

        with self.assertRaisesRegex(
            DecompositionTestRemediationError, "cannot satisfy"
        ):
            collect_replacement_sources(
                predecessor=predecessor(),
                source_audit=audit,
                fetcher=fetcher,
                contract=self.contract,
                builder_code_commit="a" * 40,
            )

    def test_quota_shortage_fails_closed(self):
        audit = source_audit({})
        with self.assertRaisesRegex(
            DecompositionTestRemediationError, "no selection artifact"
        ):
            collect_replacement_sources(
                predecessor=predecessor(),
                source_audit=audit,
                fetcher=lambda _: (_ for _ in ()).throw(OSError("offline")),
                contract=self.contract,
                builder_code_commit="a" * 40,
            )


if __name__ == "__main__":
    unittest.main()
