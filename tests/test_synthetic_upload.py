import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_matcher.splits import canonical_sha256
from clinical_matcher.synthetic_upload import (
    MANIFEST_NAME,
    SyntheticUploadError,
    build_synthetic_upload_manifest,
    load_and_verify_synthetic_upload_bundle,
    validate_synthetic_upload_manifest,
    write_synthetic_upload_manifest,
)
from clinical_matcher.synthetic_upload_cli import main


def build_bundle(root: Path):
    (root / "data").mkdir()
    (root / "data" / "train.jsonl").write_text(
        '{"fixture_notice":"Independently authored synthetic row."}\n',
        encoding="utf-8",
    )
    (root / "config.json").write_text(
        '{"dataset":"synthetic"}\n', encoding="utf-8"
    )
    return build_synthetic_upload_manifest(
        root,
        generated_at="2026-08-22T03:00:00Z",
        code_commit="a" * 40,
        generation_command="synthetic upload test",
    )


def self_hash(document):
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


class SyntheticUploadTests(unittest.TestCase):
    def test_exact_bundle_builds_and_verifies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = build_bundle(root)
            self.assertEqual(
                ["config.json", "data/train.jsonl"],
                [item["path"] for item in document["files"]],
            )
            output = write_synthetic_upload_manifest(document, root)
            self.assertEqual(0o644, os.stat(output).st_mode & 0o777)
            self.assertEqual(
                document,
                load_and_verify_synthetic_upload_bundle(root),
            )

    def test_payload_tampering_and_extra_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = build_bundle(root)
            write_synthetic_upload_manifest(document, root)
            (root / "config.json").write_text(
                '{"dataset":"changed synthetic"}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(SyntheticUploadError, "size mismatch"):
                load_and_verify_synthetic_upload_bundle(root)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = build_bundle(root)
            write_synthetic_upload_manifest(document, root)
            (root / "extra.txt").write_text("synthetic extra\n", encoding="utf-8")
            with self.assertRaisesRegex(SyntheticUploadError, "file set differs"):
                load_and_verify_synthetic_upload_bundle(root)

    def test_manifest_hash_and_unsafe_paths_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            document = build_bundle(Path(directory))
        wrong_hash = copy.deepcopy(document)
        wrong_hash["generation_command"] = "changed"
        with self.assertRaisesRegex(SyntheticUploadError, "hash mismatch"):
            validate_synthetic_upload_manifest(wrong_hash)

        traversal = copy.deepcopy(document)
        traversal["files"][0]["path"] = "../outside.json"
        traversal["manifest_sha256"] = self_hash(traversal)
        with self.assertRaisesRegex(SyntheticUploadError, "not normalized"):
            validate_synthetic_upload_manifest(traversal)

        nested_manifest = copy.deepcopy(document)
        nested_manifest["files"][0]["path"] = (
            "sub/synthetic-upload-manifest.json"
        )
        nested_manifest["manifest_sha256"] = self_hash(nested_manifest)
        with self.assertRaisesRegex(SyntheticUploadError, "cannot be nested"):
            validate_synthetic_upload_manifest(nested_manifest)

    def test_restricted_artifacts_and_identifiers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "patient.csv").write_text("synthetic\n", encoding="utf-8")
            with self.assertRaisesRegex(
                SyntheticUploadError, "forbidden public artifact type"
            ):
                build_synthetic_upload_manifest(
                    root,
                    generated_at="2026-08-22T03:00:00Z",
                    code_commit="a" * 40,
                    generation_command="test",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "payload.json").write_text(
                '{"fixture_notice":"unmarked generated row"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SyntheticUploadError, "missing explicit synthetic marker"
            ):
                build_synthetic_upload_manifest(
                    root,
                    generated_at="2026-08-22T03:00:00Z",
                    code_commit="a" * 40,
                    generation_command="test",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "payload.bin").write_bytes(b"synthetic binary")
            with self.assertRaisesRegex(
                SyntheticUploadError, "file type is not allowed"
            ):
                build_synthetic_upload_manifest(
                    root,
                    generated_at="2026-08-22T03:00:00Z",
                    code_commit="a" * 40,
                    generation_command="test",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            synthetic_numeric_identifier = 100_000 + 23_456
            (root / "payload.jsonl").write_text(
                json.dumps(
                    {
                        "fixture_notice": "synthetic",
                        "hadm_id": synthetic_numeric_identifier,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                SyntheticUploadError, "possible numeric MIMIC identifier"
            ):
                build_synthetic_upload_manifest(
                    root,
                    generated_at="2026-08-22T03:00:00Z",
                    code_commit="a" * 40,
                    generation_command="test",
                )

    def test_symlinks_and_overwrite_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_text('{"synthetic":true}\n', encoding="utf-8")
            (root / "link.json").symlink_to(target)
            with self.assertRaisesRegex(SyntheticUploadError, "symbolic link"):
                build_synthetic_upload_manifest(
                    root,
                    generated_at="2026-08-22T03:00:00Z",
                    code_commit="a" * 40,
                    generation_command="test",
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = build_bundle(root)
            write_synthetic_upload_manifest(document, root)
            with self.assertRaises(FileExistsError):
                write_synthetic_upload_manifest(document, root)

    def test_cli_build_requires_explicit_attestation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "acknowledge"):
                main(["build", "--bundle-dir", directory])

    @patch(
        "clinical_matcher.synthetic_upload.current_git_commit",
        return_value="b" * 40,
    )
    def test_cli_build_and_verify_round_trip(self, _git_commit):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                '{"fixture_notice":"independently authored synthetic"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                0,
                main(
                    [
                        "build",
                        "--bundle-dir",
                        directory,
                        "--acknowledge-independently-authored-synthetic-only",
                    ]
                ),
            )
            document = load_and_verify_synthetic_upload_bundle(root)
            self.assertNotIn(
                directory,
                document["generation_command"],
            )
            self.assertEqual(
                0,
                main(["verify", "--bundle-dir", directory]),
            )


if __name__ == "__main__":
    unittest.main()
