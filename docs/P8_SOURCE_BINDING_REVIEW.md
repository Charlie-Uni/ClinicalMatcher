# P8 source-binding review

Date: 2026-09-15. Status: `metadata_checks_passed_partition_export_blocked`.

This round follows the owner's supplied local-path inventory and the
[P8 execution specification](P8_INFERENCE_OPTIMIZATION_SPEC.md). The reviewed
implementation commit is `593980b176025b291ce6f94587da1359d17f0959`;
[hosted CI passed](https://github.com/Charlie-Uni/ClinicalMatcher/actions/runs/34908060442)
with 466 tests, including three conditional skips, in 576.238 seconds.

## Completed checks

- All nine explicitly supplied source/metadata/prediction paths exist, including
  the long-context raw `predictions.json`. They are regular non-symlink files
  with mode `0600`. Existence checks did not open prediction or clinical files.
- The original reservation and frozen split validate against their schemas,
  self-hashes, deterministic reservation rule and source-bound memberships.
  The original files were preserved.
- The two LLM run reports and six main evaluation reports declare validation
  and reference the supplied frozen split and benchmark identities.
  This verifies declared run scope; it is not an exhaustive history attestation.
- The historical SFT input plan contains exactly the train-fit membership and
  no reserved-patient membership. The length report validates and references
  the original reservation and split.
- Benchmark/import manifest self-hashes and metadata source links validate.
  No whole-source byte hash was recomputed: that would read protected content.
- The existing validation evidence index contains a manifest, not the evidence
  payload or an index of file byte ranges. Staging `source_span` values describe
  note-character positions, not byte positions in the serialized source file.

Detailed source pins, provenance and checks are retained in owner-only local
audit artifacts. No patient identifier, label, note, prediction or score is
included here. Keys and the original-ID mapping were excluded. No P7 sealed raw
file or locked-test run directory was opened.

## Why E0 cannot start under the current contract

Section 4.2 requires isolation before I/O and explicitly forbids loading a full
source then filtering. Section 4.3 includes protected-content reads performed
only to compute a file hash. The supplied evidence and gold sources are full,
unpartitioned JSON files; no pre-isolated payload or pre-existing source-bound
byte-range index is available in the supplied material.

The existing P8 CLI binds already isolated artifacts. It does not implement a
full-source exporter. A streaming parser would still read unselected content
while locating JSON records; a separate process would not remove that access.
Neither can be described as satisfying the present zero-read rule. A valid
source hash establishes identity but cannot locate a patient's file bytes.

Under the unchanged contract, isolation needs an existing validation-only
source or a trusted pre-existing range index that permits bounded reads of
only validation records. Constructing that index now by scanning the full JSON
has the same read-boundary problem. No such scan or export was performed.

If the owner instead intends a mechanical partition-export exception, that
would require an explicit protocol decision describing the source reads and
their treatment under both locked-test closure and secondary-holdout exposure.
The current instruction to export "according to the specification" is not
treated as silently overriding those restrictions. No exception is authorized
or implemented by this review.

## Historical independence remains a separate open gate

The inspected records support validation-only evaluation and train-fit-only
input planning. They do not prove absence of all development inspection,
examples, subsequent annotation, selection or unrecorded runs. Earlier whole-
cohort quality, split-stratification and semantic-grouping procedures also need
an explicit scope classification before the phrase "untouched by all prior
development" can be frozen. Their existence is not itself a finding of model
selection contamination, and filenames are not proof of non-use.

The independence status remains unverified; no affirmative attestation was
invented. The required scope/completeness and owner confirmation from section
4.1 remain outstanding.

## Round self-review

Metadata-only checks: **PASS**. Source-bound P8.1 freeze: **NOT COMPLETE**.
Real E0 gate: **CLOSED**. No prediction scoring, winner selection, inference,
v2 review or holdout exposure occurred. No runtime code changed, so the existing
code/CI validation remains applicable; documentation checks are run for this
record. Do not advance to the next experiment until these entry conditions
are resolved.
