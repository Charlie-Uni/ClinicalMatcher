# Apixaban three-class source audit

Status: source audited; legacy labels not accepted as independent human gold

Audit date: 2026-08-30

## Decision

The official MIMIC-IV-Ext Apixaban `1.0.0` release is authoritative for the
2,300 human-reviewed note-question answers. It does not contain a patient-level
`ideal / semi-ideal / non-ideal` annotation.

The three-class labels used by the legacy VRI experiments are a deterministic
projection of two overlapping rule-screening flags supplied with the mentor
materials. The owner subsequently confirmed that the mentor designated the new
criteria and supplied result as project ground truth. ClinicalMatcher therefore
records the precise role as
`mentor_designated_rule_derived_project_ground_truth`; the shorter public term
remains **legacy rule-derived reference**. Neither name permits describing the
artifact as an official PhysioNet label, independently annotated eligibility
gold, or clinical ground truth.

This distinction changes the interpretation of a future single-trial result:
agreement with the legacy labels measures reproduction of a rule-based
reference, not clinical eligibility accuracy.

## Official release provenance

The credentialed PhysioNet release is:

- dataset: `MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions`;
- version: `1.0.0`;
- DOI: `10.13026/4p6q-vb04`;
- local source SHA-256:
  `8e8083b0b5e3d038ad912a812be1bb8a53f8a59bc37a4c29d8a420cb4296e267`;
- logical contents: 100 notes, 23 questions per note, and 2,300 rows;
- columns: `text`, `note_id`, `hadm_id`, `criterion`, `question_type`,
  `question`, `answer`, and `not_specified`.

The local CSV hash matches its adjacent release `SHA256SUMS.txt`. Every
`note_id + criterion` key is unique and every note has exactly 23 rows. The
PhysioNet description states that a human reviewer validated and corrected the
2,300 question-answer pairs. The associated article and author repository also
describe the Apixaban task as note-level information extraction over these 23
questions; none defines or releases final three-class patient eligibility.

Primary sources:

- [PhysioNet release](https://physionet.org/content/mimic-iv-ext-apixaban-trial/1.0.0/)
- [npj Digital Medicine article](https://www.nature.com/articles/s41746-025-01681-4)
- [author code repository](https://github.com/bbj-lab/clinical-synthetic-data-distil)

## Local artifact lineage

All files in this section are restricted MIMIC derivatives and remain outside
Git. Hashes and non-row-level structural findings are recorded so the audit can
be repeated without publishing their contents.

| Artifact | SHA-256 | Audited role and finding |
|---|---|---|
| mentor `annotated_apixaban_combined.xlsx` | `5d67d13d8da0434fd13a0002f9d65372fd6e91fdb2c1db071f62f0b92c011e94` | 2,300 rows, 100 note/admission keys, 23 questions, and the same eight-column schema as the release; it contains no classification field. A nonzero subset of note text differs from the pinned CSV and numeric answers are stored with Excel numeric types, so it is not the authoritative source. |
| `screening_results.json` | `f358d18feb47997d87d27b104b0c3490d08bba913e64b33b17b75ab2c65c59d3` | Mentor-designated project reference: five-rule screening output plus overlapping semi-ideal and ideal patient-number sets. The companion README says the result was produced automatically by a preprocessing notebook and screener module. |
| `ideal_candidates.csv` | `ff3871060b9e0ec97952d4b5bff998cb9504e7d8e3fd461edc2c976d199d70ea` | 100 rows with `patient_id`, `note_id`, `hadm_id`, `semi_ideal_candidate`, and `ideal_candidate`. Its patient keys align exactly with the official 100-note cohort and its flags align exactly with the screening JSON. |
| `apixaban_with_mteb_small.csv` | `93a09907ecc82f08903458d37177acbddfa4a1a3a76306beb0787b2c23ecfb38` | 100 rows; official note text and the two classification flags align exactly with `ideal_candidates.csv`. `embedding_index` is metadata, not an embedding. |

The two screening flags are not mutually exclusive: every ideal record is also
semi-ideal under the supplied rule definition. Legacy code makes the final
classes exclusive by applying this precedence:

1. `ideal_candidate == 1` -> `ideal`;
2. otherwise `semi_ideal_candidate == 1` -> `semi-ideal`;
3. otherwise -> `non-ideal`.

This final label is therefore code-derived even if the input flags are treated
as fixed mentor-provided references.

## Reproducibility gap

The mentor README names `notebooks/preprocess.ipynb` and
`data_processing/screener.py` as the generator. Neither file is present in the
audited VRI/VRI1 materials or in ClinicalMatcher. The available legacy
`apixaban_processing.py` can join the resulting flags and contains a similar
five-rule implementation, but it does not establish the exact code, revision,
inputs, or review process that produced the frozen screening artifacts.

Consequently, ClinicalMatcher can currently verify cohort alignment, flag
integrity, and final-class projection, but cannot reproduce the flags from the
official CSV under a hash-bound generator. Calling the flags gold would hide
this provenance gap and their automatic origin.

## Publication boundary

- The official CSV, workbook, screening JSON, row-level flags, note text, and
  raw or joinable identifiers remain restricted and must not enter Git.
- A public result may identify the official dataset/version, record the
  artifact hashes above, and report disclosure-reviewed aggregates.
- Exact rare-class counts remain out of the public audit while the P1.3
  disclosure threshold lacks an approved governance reference.
- Any report using the three-class reference must say `legacy rule-derived
  reference` or `silver label`, publish its precedence rule, and avoid a claim
  of human-adjudicated eligibility accuracy.

## Required evidence to upgrade the labels

The legacy labels can be upgraded only after all applicable evidence is
available and reviewed:

1. the exact preprocessing notebook and screener source, with a version or
   content hash and a clean regeneration match;
2. the origin and version of the five screening criteria and thresholds;
3. a statement from the mentor describing whether clinicians independently
   reviewed patient-level final classifications, by whom, and under what
   annotation/adjudication protocol; and
4. the permitted publication scope for row-level labels and aggregates under
   the restricted-data agreement.

Absent independent patient-level review, a reproducible generator would
improve provenance but would still produce silver labels rather than human
eligibility gold.

## Consequence for the next task

The next mainline task may encode an owner-reviewed Apixaban expression tree,
bind released facts to its atoms, and run it over the frozen validation split.
It must freeze the tree and mapping before inspecting validation agreement.
The output must report criterion traces, abstention/unknown handling, and
agreement with the legacy rule-derived reference. It must not use that
agreement as evidence of clinical correctness, and it must not expose the
locked test split.
