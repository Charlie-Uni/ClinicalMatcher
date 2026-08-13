# Apixaban note-grounded benchmark contract

Status: frozen mainline baseline

Question catalog version: `1.0.0`

Fact assessment version: `1.0.0`

## What this benchmark measures

The official MIMIC-IV-Ext Apixaban `1.0.0` release contains 100 clinical notes
and 23 human-reviewed answers per note. ClinicalMatcher uses these 2,300
answers as a benchmark for extracting facts from the released note.

It does not treat them as gold for:

- the patient's complete longitudinal EHR;
- complete eligibility for the original Apixaban trial;
- eligibility for a new trial;
- ranking multiple trials;
- criterion-level evidence relevance.

The public question catalog contains question definitions only. It contains no
patient text, row-level answer, identifier, pseudonym, embedding, or model
output. The restricted source CSV and generated benchmark records remain local.

## Label semantics

Boolean source answers are normalized as follows:

| Released label | Fact status | Typed value | Meaning |
|---|---|---:|---|
| `Yes` | `present` | `true` | The note supports the question-defined fact. |
| `No` | `absent` | `false` | The note received `No` under the question's annotation rule. |
| `not_specified` | `unknown` | `null` | The released source marks the answer unavailable. |
| unresolved empty source answer | `unknown` | `null` | A source anomaly is preserved, not guessed. |

`absent` is note-grounded. It must not be reported as proof that a diagnosis or
event is absent from the patient's complete clinical record.

Numeric source answers are normalized as:

| Released label | Fact status | Typed value |
|---|---|---:|
| finite number | `present` | that number |
| `not_specified` | `unknown` | `null` |
| unresolved empty source answer | `unknown` | `null` |

Numeric questions do not have an `absent` state. The source questions do not
define canonical measurement units, so version `1.0.0` records `unit = null`.
Later evidence processing may preserve units from text or structured EHR, but
those units cannot be retroactively attributed to the released gold labels.

## Frozen question mapping

The exact source wording, stable question ID, and source hash are frozen in
`src/clinical_matcher/resources/apixaban-question-catalog-1.0.0.json`.
Typographical details in source wording are preserved because the stable ID is
bound to the verbatim definition.

| Source label | Normalized fact field | Type | Aggregation |
|---|---|---|---|
| `afib` | `atrial_fibrillation` | boolean | question-defined |
| `chads2` | `chads2_score` | numeric | maximum |
| `prior_stroke` | `prior_stroke_or_tia` | boolean | question-defined |
| `arterial_hypertension` | `treated_arterial_hypertension` | boolean | question-defined |
| `t2d` | `diabetes_mellitus` | boolean | question-defined |
| `blood_glucose` | `blood_glucose` | numeric | maximum |
| `heart_failure` | `heart_failure` | boolean | question-defined |
| `lvef` | `left_ventricular_ejection_fraction` | numeric | minimum |
| `surgical_valvular_disease` | `valvular_disease_requiring_surgery` | boolean | question-defined |
| `afib_ablation` | `afib_ablation` | boolean | question-defined |
| `bleeding` | `serious_bleeding_within_6_months` | boolean | question-defined |
| `peptic_ulcer_disease` | `peptic_ulcer_disease` | boolean | question-defined |
| `PLT` | `platelet_count` | numeric | minimum |
| `HGB` | `hemoglobin` | numeric | minimum |
| `recent_stroke` | `stroke_during_admission_or_within_last_month` | boolean | question-defined |
| `hemorrhagic` | `hemorrhagic_tendency_or_blood_dyscrasia` | boolean | question-defined |
| `CREAT` | `serum_creatinine` | numeric | maximum |
| `AST` | `aspartate_aminotransferase` | numeric | maximum |
| `BILI` | `total_bilirubin` | numeric | maximum |
| `bipolar` | `bipolar_disorder` | boolean | question-defined |
| `schizophrenia` | `schizophrenia_or_schizoaffective_disorder` | boolean | question-defined |
| `mdd` | `major_depressive_disorder` | boolean | question-defined |
| `med_decisions` | `unable_to_make_medical_decisions` | boolean | question-defined |

The boolean `question_defined` aggregation means that the full released
question, including its temporal or diagnostic wording, defines the target. It
does not authorize a generic keyword-presence rule.

## Eligibility boundary

Fact extraction and eligibility are different stages. For example:

```text
question-defined fact: serious bleeding within six months = present
trial criterion: excludes serious bleeding within six months
criterion result: ineligible
```

The fact is true while the eligibility result is negative. The fact-assessment
schema therefore contains no `eligible` field. Eligibility can be produced only
after a separate, versioned inclusion/exclusion criterion is supplied.

## Evidence boundary

The release provides human-reviewed answers but not an independently
adjudicated evidence-ID set for every answer. Released labels are represented
with:

```json
{
  "evidence_status": "not_available_in_source",
  "evidence_ids": []
}
```

Model or rule predictions may use `provided` only when their evidence IDs
exist in the corresponding patient document. Automatically generated evidence
links cannot be used as their own retrieval gold.

## Validation

Validate the bundled catalog:

```bash
clinical-matcher-validate-apixaban-contract
```

Validate a fact-assessment document as well:

```bash
clinical-matcher-validate-apixaban-contract artifacts/assessment.json
```

Validation checks:

- exactly 23 questions: 15 boolean and 8 numeric;
- unique question IDs, source labels, and normalized fact fields;
- question IDs bound to verbatim source definitions;
- catalog content hash;
- boolean and numeric status/value consistency;
- explicit abstention for unknown results;
- evidence-status/evidence-ID consistency;
- no invented unit in the note-only benchmark contract;
- no direct fact-to-eligibility mapping.

## Restricted benchmark materialization

P1.2 converts the verified staging corpus into two new local files:

- `apixaban-fact-benchmark.json`: 100 pseudonymous patient keys and 2,300
  frozen fact assessments;
- `apixaban-fact-benchmark.manifest.json`: aggregate counts, provenance hashes,
  the generating commit, and the benchmark content hash.

The benchmark deliberately does not duplicate note text, evidence chunks, raw
identifiers, source row numbers, or the raw-ID crosswalk. Runtime code joins it
to the separately retained staging corpus by `patient_id`. The benchmark still
contains restricted patient-level derivatives and must never enter Git.

Build it only in the authorized local environment:

```bash
clinical-matcher-apixaban-benchmark build \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --import-manifest /restricted/path/apixaban-staging-corpus.import-manifest.json \
  --output /restricted/path/apixaban-fact-benchmark.json \
  --acknowledge-restricted-data-local-only
```

Verify an existing output without reading or printing note text:

```bash
clinical-matcher-apixaban-benchmark verify \
  --benchmark /restricted/path/apixaban-fact-benchmark.json \
  --acknowledge-restricted-data-local-only
```

Build and verify enforce:

- the pinned official source CSV hash;
- the staging corpus file hash and self-authenticating import manifest;
- the frozen 23-question catalog hash and verbatim question definitions;
- exactly 100 patients, 23 questions, and 2,300 unique patient-question pairs;
- exactly 2,033 answered, 265 not-specified, and two unresolved anomaly rows;
- deterministic patient, assessment, and content ordering;
- explicit unknown/abstention records rather than anomaly repair;
- owner-only output permissions and refusal to overwrite existing files.

`generated_at` belongs only to the aggregate manifest. The benchmark document
itself has no timestamp or commit-dependent field, so identical verified input
produces the same benchmark SHA-256 across repeat runs.
