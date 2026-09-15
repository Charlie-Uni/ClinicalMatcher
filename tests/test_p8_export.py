import copy
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from clinical_matcher.apixaban_contract import load_question_catalog
from clinical_matcher.p8_export import export_once, freeze_export_contract
from clinical_matcher.p8_safety import (P8Error, REVIEW_USES, build_access_manifest,
    canonical_bytes, make_pin, read_development_artifact, read_metadata, seal)
from clinical_matcher.p8_cli import main
from tests.test_apixaban_calibration import frozen_split, build_reservation, self_hash


class P8MechanicalExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.state = self.root / 'fixed-project-state'
        for module in ['clinical_matcher.p8_export', 'clinical_matcher.p8_safety']:
            patcher = patch(module + '.holdout_state_root', return_value=self.state)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.split = frozen_split()
        ids = [pid for part in self.split['splits'].values() for pid in part['patient_ids']]
        self.test_ids = set(self.split['splits']['test']['patient_ids'])
        self.staging = {'patients': [
            {'patient_id': pid, 'legacy_questions': ['MUST_NOT_ENTER_EVIDENCE'],
             'evidence': [{'evidence_id': 'evidence-' + pid.removeprefix('patient-') + '-000',
                           'text': 'FORBIDDEN_TEST_SENTINEL' if pid in self.test_ids else 'SYNTHETIC_ONLY'}]}
            for pid in ids]}
        self.benchmark = {'patient_ids': ids, 'assessments': [
            {'patient_id': pid, 'question_id': q['question_id'], 'value': 'UNINTERPRETED_SOURCE_VALUE'}
            for pid in ids for q in load_question_catalog()['questions']]}
        self.paths = {'staging': self.root / 'apixaban-staging-corpus.json',
                      'benchmark': self.root / 'apixaban-fact-benchmark.json'}
        self.refresh_sources()

    def refresh_sources(self):
        for role, document in [('staging', self.staging), ('benchmark', self.benchmark)]:
            payload = (json.dumps(document, indent=2) + '\n').encode()
            self.paths[role].write_bytes(payload)
            self.paths[role].chmod(0o600)
            field = 'staging_corpus_sha256' if role == 'staging' else 'benchmark_sha256'
            self.split['dataset'][field] = make_pin(payload, 'file')['value']
        self.split['manifest_sha256'] = self_hash(self.split)
        self.reservation = build_reservation(self.split)
        self.contract = freeze_export_contract(self.split, self.reservation, self.paths,
            owner_decision_record_id='synthetic-owner-decision', synthetic=True)

    def run_export(self, name='output'):
        return export_once(self.contract, self.split, self.reservation,
                           output_root=self.root / name, synthetic=True)

    def new_access(self, receipt):
        review = {'status': 'verified', 'review_record_id': 'synthetic-audit',
            'uses_checked': list(REVIEW_USES), 'evidence_pins': [make_pin({'audit': 'synthetic'}, 'content')],
            'unresolved_items': [], 'development_use_found': False,
            'claim_scope': 'audited_inventory_and_owner_attestation',
            'owner_attestation': {'record_id': 'synthetic-owner', 'no_unrecorded_manual_inspection': True,
                                 'no_real_sft_export': True, 'no_real_silver_generation': True}}
        return build_access_manifest(self.split, self.reservation, review=review,
            artifacts=receipt['artifacts'], export_receipt=receipt, synthetic=True)

    def test_mechanical_export_no_display_test_output_or_readback(self):
        from clinical_matcher.p8_export import _read_private_bytes
        reads = []
        def tracked(path):
            self.assertTrue((self.state / 'mechanical-export-consumed.json').exists())
            self.assertIn(path, self.paths.values())
            reads.append(path)
            return _read_private_bytes(path)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('clinical_matcher.p8_export._read_private_bytes', side_effect=tracked), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            receipt = self.run_export()
        self.assertEqual('', stdout.getvalue() + stderr.getvalue())
        self.assertCountEqual(self.paths.values(), reads)
        self.assertEqual(6, len(receipt['artifacts']))
        self.assertFalse((self.state / 'secondary-holdout-consumed.json').exists())
        for a in receipt['artifacts']:
            path = Path(a['path'])
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
            # Synthetic test inspection only; production exporter never reads back.
            payload = path.read_bytes()
            self.assertEqual(a['file_pin'], make_pin(payload, 'file'))
            doc = json.loads(payload)
            self.assertEqual(a['content_pin'], make_pin(doc, 'content'))
            self.assertNotEqual(doc['self_sha256'], a['file_pin']['value'])
            self.assertNotIn('FORBIDDEN_TEST_SENTINEL', payload.decode())
            self.assertNotIn('MUST_NOT_ENTER_EVIDENCE', payload.decode())
            self.assertTrue(self.test_ids.isdisjoint(r['patient_id'] for r in doc['rows']))
            if a['kind'] == 'gold':
                self.assertTrue(all(r['value'] == 'UNINTERPRETED_SOURCE_VALUE' for r in doc['rows']))
        for path in (self.root / 'output').rglob('*.json'):
            content = path.read_text()
            for pid in self.test_ids:
                self.assertNotIn(pid, content)

    def test_holdout_guard_before_io_and_exact_registry_binding(self):
        receipt = self.run_export()
        manifest = self.new_access(receipt)
        with patch('clinical_matcher.p8_safety._read_private_bytes', side_effect=AssertionError) as reader:
            with self.assertRaises(P8Error):
                read_development_artifact(manifest, 'secondary_holdout.gold', purpose='evaluation', synthetic=True)
            reader.assert_not_called()
        result = read_development_artifact(manifest, 'validation.gold', purpose='evaluation', synthetic=True)
        self.assertEqual('validation', result['partition'])
        bad = copy.deepcopy(manifest)
        bad['artifacts'][0]['path'] = str(self.paths['benchmark'])
        from clinical_matcher.p8_safety import validate_access_manifest
        with self.assertRaises(P8Error):
            validate_access_manifest(seal(bad), synthetic=True)

    def test_lifetime_reuse_refused_before_source_read_in_new_output_directory(self):
        self.run_export()
        with patch('clinical_matcher.p8_export._read_private_bytes', side_effect=AssertionError) as reader:
            with self.assertRaises((P8Error, FileExistsError)):
                self.run_export('another-output')
            reader.assert_not_called()

    def test_wrong_source_bytes_consume_export_and_do_not_print_exception_values(self):
        self.paths['benchmark'].write_bytes(b'SYNTHETIC_SECRET_ERROR')
        with self.assertRaises(P8Error):
            self.run_export()
        self.assertTrue((self.state / 'mechanical-export-consumed.json').exists())
        log = ''.join(p.read_text() for p in (self.root / 'output' / 'events').glob('*.json'))
        self.assertNotIn('SYNTHETIC_SECRET_ERROR', log)
        self.assertIn('failed', log)
        with self.assertRaises((P8Error, FileExistsError)):
            self.run_export('new-output')

    def test_missing_gold_key_fails_reconciliation_without_exporting_rows(self):
        self.benchmark['assessments'].pop()
        self.refresh_sources()
        with self.assertRaises(P8Error):
            self.run_export()
        self.assertFalse(self.state.joinpath('sealed-partitions').exists())

    def test_duplicate_or_missing_patient_rejected(self):
        self.staging['patients'].append(self.staging['patients'][0])
        self.refresh_sources()
        with self.assertRaises(P8Error):
            self.run_export()

    def test_two_concurrent_exports_only_one_source_reader_wins(self):
        def attempt(name):
            try:
                self.run_export(name)
                return True
            except (P8Error, FileExistsError):
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['one', 'two']))
        self.assertEqual(1, sum(results))

    def test_existing_output_and_symlink_rejected_before_consumption(self):
        (self.root / 'output').mkdir()
        with self.assertRaises(P8Error):
            self.run_export()
        self.assertFalse(self.state.exists())
        (self.root / 'alias').symlink_to(self.root / 'output', target_is_directory=True)
        with self.assertRaises(P8Error):
            self.run_export('alias')
        self.assertFalse(self.state.exists())

    def test_source_allowlist_and_hash_kind_rejected_before_source_read(self):
        with self.assertRaises(P8Error):
            freeze_export_contract(self.split, self.reservation, dict(self.paths, id_map=self.root/'id-map.json'),
                owner_decision_record_id='synthetic', synthetic=True)
        changed = copy.deepcopy(self.contract)
        changed['sources']['benchmark']['file_pin'] = make_pin({'synthetic': True}, 'content')
        with patch('clinical_matcher.p8_export._read_private_bytes', side_effect=AssertionError) as reader:
            with self.assertRaises(P8Error):
                export_once(seal(changed), self.split, self.reservation, output_root=self.root/'output', synthetic=True)
            reader.assert_not_called()

    def test_cli_failure_never_displays_input_or_exception(self):
        stdout = io.StringIO()
        with patch('clinical_matcher.p8_cli.read_metadata', side_effect=ValueError('SECRET_PATIENT_ROW')), redirect_stdout(stdout):
            code = main(['export-once', '--frozen-split-metadata', 'synthetic-split',
                '--reservation-metadata', 'synthetic-reservation', '--export-contract', 'synthetic-contract',
                '--output-root', 'synthetic-output', '--acknowledge-restricted-data-local-only'])
        self.assertEqual(2, code)
        self.assertNotIn('SECRET_PATIENT_ROW', stdout.getvalue())

    def test_partial_marker_is_terminal_and_never_overwritten(self):
        self.state.mkdir(mode=0o700)
        marker = self.state / 'mechanical-export-consumed.json'
        marker.write_bytes(b'partial')
        marker.chmod(0o600)
        with patch('clinical_matcher.p8_export._read_private_bytes', side_effect=AssertionError) as reader:
            with self.assertRaises(FileExistsError):
                self.run_export()
            reader.assert_not_called()
        self.assertEqual(b'partial', marker.read_bytes())

    def test_partition_bytes_are_deterministic_across_synthetic_vaults(self):
        first = self.run_export('first')
        other_state = self.root / 'second-synthetic-state'
        with patch('clinical_matcher.p8_export.holdout_state_root', return_value=other_state):
            second = self.run_export('second')
        self.assertEqual([a['file_pin'] for a in first['artifacts']],
                         [a['file_pin'] for a in second['artifacts']])

    def test_output_write_failure_leaves_terminal_marker_and_private_failure_log(self):
        from clinical_matcher.p8_export import write_private
        def fail_partition(document, path):
            if path.name.endswith('.gold.json'):
                raise OSError('SYNTHETIC_PRIVATE_VALUE')
            return write_private(document, path)
        with patch('clinical_matcher.p8_export.write_private', side_effect=fail_partition), self.assertRaises(P8Error):
            self.run_export()
        self.assertTrue((self.state / 'mechanical-export-consumed.json').exists())
        logs = ''.join(p.read_text() for p in (self.root/'output'/'events').glob('*.json'))
        self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', logs)
        self.assertIn('failed', logs)
