import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from clinical_matcher.public_demo import (
    EXPECTED_FIXTURE_NOTICE,
    PublicDemoError,
    load_and_build_public_demo,
    render_public_demo_markdown,
    validate_public_demo_report,
)
from clinical_matcher.public_demo_cli import main


FIXTURE = Path("fixtures/synthetic/trial_matching.json")


class PublicDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = load_and_build_public_demo(FIXTURE)

    def test_report_is_schema_valid_and_file_hash_is_unambiguous(self):
        validate_public_demo_report(self.report)
        expected = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        self.assertEqual(expected, self.report["fixture"]["fixture_file_sha256"])
        self.assertFalse(self.report["runtime"]["network_required"])
        self.assertFalse(self.report["runtime"]["model_required"])
        self.assertTrue(self.report["runtime"]["cpu_only"])

    def test_retrieval_is_patient_local_and_traceable(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        evidence_by_patient = {
            patient["patient_id"]: {
                item["evidence_id"] for item in patient["evidence"]
            }
            for patient in raw["patients"]
        }
        for patient in self.report["patients"]:
            allowed = evidence_by_patient[patient["patient_id"]]
            for trial in patient["ranked_trials"]:
                for criterion in trial["criteria"]:
                    for hit in criterion["retrieval"]["top_k"]:
                        self.assertIn(hit["evidence_id"], allowed)
                        self.assertIn(
                            "linked_to_deterministic_decision", hit
                        )
                    self.assertTrue(criterion["atomic_trace"])

    def test_safety_probes_abstain_for_missing_fact_and_unit_conflict(self):
        probes = {item["probe_id"]: item for item in self.report["safety_probes"]}
        self.assertEqual(
            {"missing_required_fact", "typed_unit_conflict"}, set(probes)
        )
        for probe in probes.values():
            self.assertEqual("unknown", probe["decision"])
            self.assertTrue(probe["abstained"])
            self.assertTrue(probe["abstention_reasons"])
        self.assertTrue(
            any(
                "unit mismatch" in issue
                for issue in probes["typed_unit_conflict"]["verifier_issues"]
            )
        )

    def test_report_is_deterministic(self):
        self.assertEqual(self.report, load_and_build_public_demo(FIXTURE))

    def test_markdown_keeps_warning_and_mechanism_boundaries_visible(self):
        rendered = render_public_demo_markdown(self.report)
        self.assertIn("SYNTHETIC RESEARCH DEMO ONLY", rendered)
        self.assertIn("patient-local BM25", rendered)
        self.assertIn("missing_required_fact", rendered)
        self.assertIn("typed_unit_conflict", rendered)
        self.assertIn("not clinical accuracy", rendered)

    def test_cli_json_output_is_machine_readable(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = main(["--fixture", str(FIXTURE), "--format", "json"])
        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr.getvalue())
        self.assertEqual(self.report, json.loads(stdout.getvalue()))

    def test_invalid_input_fails_without_echoing_input_or_partial_output(self):
        secret_marker = "do-not-echo-this-input"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(secret_marker, encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(["--fixture", str(path)])
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertNotIn(secret_marker, stderr.getvalue())
        self.assertIn("refused the input", stderr.getvalue())

    def test_non_synthetic_declaration_is_rejected(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["fixture_notice"] = "Unreviewed input."
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong-declaration.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(PublicDemoError, "declared synthetic"):
                load_and_build_public_demo(path)
        self.assertNotEqual(EXPECTED_FIXTURE_NOTICE, raw["fixture_notice"])

    def test_broken_evidence_link_fails_without_partial_output(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        raw["patients"][0]["facts"][0]["evidence_ids"] = ["missing-evidence"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken-links.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = main(["--fixture", str(path)])
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertNotIn("missing-evidence", stderr.getvalue())

    def test_probe_identity_is_part_of_report_semantics(self):
        tampered = json.loads(json.dumps(self.report))
        tampered["safety_probes"].reverse()
        with self.assertRaisesRegex(PublicDemoError, "probe set or order"):
            validate_public_demo_report(tampered)


if __name__ == "__main__":
    unittest.main()
