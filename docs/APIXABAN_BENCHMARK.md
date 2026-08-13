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

## Data-quality reporting and disclosure control

P1.3 produces two local reports from the validated benchmark:

- a restricted report with exact per-question counts and numeric ranges;
- an aggregate public-release candidate with small-cell and complementary
  suppression and no numeric extrema.

The restricted report measures patient-grid completeness, missing and duplicate
patient-question pairs, boolean/numeric totals, fact/source-label counts,
unknown rates, numeric minima/maxima, and unresolved source anomalies. No value
is removed. Because catalog `1.0.0` has no canonical units and no reviewed
unit-aware plausibility rules, the report records plausibility as not assessed
instead of inventing clinical cutoffs.

Generate a pending-review candidate with an explicit proposed threshold:

```bash
clinical-matcher-apixaban-quality build \
  --benchmark /restricted/path/apixaban-fact-benchmark.json \
  --benchmark-manifest /restricted/path/apixaban-fact-benchmark.manifest.json \
  --restricted-output /restricted/path/apixaban-quality.restricted.json \
  --public-output /restricted/path/apixaban-quality.public-candidate.json \
  --minimum-cell-size 10 \
  --acknowledge-restricted-data-local-only
```

The value `10` above is an example candidate, not a claim of institutional
approval. Without an approval reference, the generated public projection says
`governance_status = pending_review` and `release_authorized = false`.

Only after the applicable data-governance authority approves the exact
threshold may the command add both:

```text
--governance-approval-reference NON_SENSITIVE_REFERENCE
--acknowledge-governance-approved-threshold
```

Suppression rules are deliberately conservative:

- positive counts below the threshold are hidden; zero remains disclosable;
- if one cell in an additive group is hidden, at least one other positive cell
  is hidden to prevent subtraction attacks;
- an unknown rate is hidden whenever its source count is hidden;
- exact numeric minima and maxima are always withheld from the public
  projection because each is an individual extreme;
- public output omits patient/assessment IDs, benchmark fingerprints, the Git
  commit, note text, and evidence.

Verify both local files with:

```bash
clinical-matcher-apixaban-quality verify \
  --restricted-report /restricted/path/apixaban-quality.restricted.json \
  --public-report /restricted/path/apixaban-quality.public-candidate.json \
  --acknowledge-restricted-data-local-only
```

## Patient-grouped split candidates and freezing

P1.4 uses a restricted three-way manifest. It never splits the 2,300 answer
rows independently. Patients that share an admission or exact note-content
hash are assigned as one group, and the deterministic greedy objective balances
both question-level fact status and released source status.

Generate a candidate only after explicitly recording all three fractions and a
seed. For example, the following proposes 70/15/15 with seed 17; neither value
is final merely because it appears here:

```bash
clinical-matcher-apixaban-split candidate \
  --benchmark /restricted/path/apixaban-fact-benchmark.json \
  --benchmark-manifest /restricted/path/apixaban-fact-benchmark.manifest.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --import-manifest /restricted/path/apixaban-staging-corpus.import-manifest.json \
  --id-map /restricted/path/apixaban-staging-corpus.id-map.json \
  --quality-report /restricted/path/apixaban-quality.restricted.json \
  --train-fraction 0.70 \
  --validation-fraction 0.15 \
  --test-fraction 0.15 \
  --seed 17 \
  --semantic-similarity-threshold 0.95 \
  --output /restricted/path/apixaban-split.candidate.json \
  --acknowledge-restricted-data-local-only
```

The candidate contains pseudonymous membership and note-content fingerprints,
so it remains local. It records exact split sizes, per-question distributions,
prevalence deviations, observed and mathematically unavoidable zero-support
cells, admission isolation, exact-content isolation, source hashes, algorithm
version, seed, and generating commit. Algorithm `1.1.0` prioritizes attainable
rare-label coverage and then performs deterministic equal-size group swaps; it
does not search multiple seeds and select the most favorable result.

The candidate is not frozen until an authorized local embedding scan evaluates
all cross-split pairs, or an ANN scan reports measured candidate recall. The
detailed pair file remains local. Install the optional local scanner with
`pip install -e '.[semantic-scan]'`, then run the fixed-revision PubMedBERT
scan in the authorized environment:

```bash
clinical-matcher-apixaban-split scan-semantic \
  --manifest /restricted/path/apixaban-split.candidate.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --semantic-pairs-output /restricted/path/apixaban-split.semantic-pairs.json \
  --summary-output /restricted/path/apixaban-split.semantic-summary.json \
  --batch-size 16 \
  --acknowledge-restricted-data-local-only
```

The default model is `NeuML/pubmedbert-base-embeddings` at immutable revision
`b79526d6ef3645e0df4530322e266f24c829f5ef` (Apache-2.0). Each evidence chunk
is encoded locally and L2-normalized; normalized chunk vectors are averaged
and normalized again to form one patient vector. The encoder input is capped
at 512 tokens per evidence chunk. The command verifies the
exact staging-corpus hash, patient membership, and per-patient content hashes
before encoding. It evaluates only the 2,325 cross-split patient pairs and
writes neither note text nor embeddings. Pair IDs and similarities remain
restricted; the summary is text-free but still needs governance review before
export. A similarity hit exits with status 2 and blocks freezing; it does not
prove that two patients are clinically identical and should be reviewed
locally. The model was trained primarily on biomedical literature rather than
MIMIC notes, and the 0.95 threshold is a conservative predeclared screening
rule, not a clinically calibrated identity threshold; both limitations remain
part of the audit interpretation.

If the scan finds cross-split pairs, do not raise the threshold or search for a
favorable seed. Regenerate a candidate that binds the failed scan and treats
the retained semantic pairs as grouping edges:

```bash
clinical-matcher-apixaban-split candidate \
  --benchmark /restricted/path/apixaban-fact-benchmark.json \
  --benchmark-manifest /restricted/path/apixaban-fact-benchmark.manifest.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --import-manifest /restricted/path/apixaban-staging-corpus.import-manifest.json \
  --id-map /restricted/path/apixaban-staging-corpus.id-map.json \
  --quality-report /restricted/path/apixaban-quality.restricted.json \
  --semantic-pairs /restricted/path/apixaban-split.semantic-pairs.json \
  --semantic-summary /restricted/path/apixaban-split.semantic-summary.json \
  --semantic-source-candidate /restricted/path/apixaban-split.candidate.json \
  --train-fraction 0.70 --validation-fraction 0.15 --test-fraction 0.15 \
  --seed 17 --semantic-similarity-threshold 0.95 \
  --output /restricted/path/apixaban-split.regrouped-candidate.json \
  --acknowledge-restricted-data-local-only
```

The regrouped manifest records hashes for the source candidate, failed scan
summary, and detailed pair payload. It must be scanned again: a pair that was
within one split in the first candidate may cross a boundary after regrouping.
Repeat only until a candidate passes; every iteration remains an auditable
grouping correction, not seed or threshold selection.
For a second or later correction, repeat all three flags
`--semantic-pairs`, `--semantic-summary`, and
`--semantic-source-candidate` once per scan, in chronological order. The
builder rejects a chain that omits an earlier scan or does not inherit its
grouping provenance; all unique edges are accumulated.

To audit a pair file produced by another authorized implementation, use:

```bash
clinical-matcher-apixaban-split audit-semantic \
  --manifest /restricted/path/apixaban-split.candidate.json \
  --semantic-pairs /restricted/path/semantic-pairs.json \
  --embedding-model-id MODEL_ID \
  --embedding-model-revision IMMUTABLE_REVISION \
  --pooling mean \
  --vectors-normalized \
  --search-method exhaustive_cosine \
  --candidate-pairs-evaluated 2325 \
  --output /restricted/path/apixaban-split.semantic-summary.json \
  --acknowledge-restricted-data-local-only
```

The value 2,325 is correct only for a 70/15/15 split of 100 patients
(`70×15 + 70×15 + 15×15`). The audit rejects an exhaustive claim when the
recorded count does not equal every possible cross-split pair.

After reviewing the proposed proportions, balance report, seed choice, and a
passing semantic scan, freeze the unchanged membership with a non-sensitive
decision reference:

```bash
clinical-matcher-apixaban-split freeze \
  --candidate /restricted/path/apixaban-split.candidate.json \
  --semantic-summary /restricted/path/apixaban-split.semantic-summary.json \
  --decision-reference NON_SENSITIVE_REVIEW_REFERENCE \
  --output /restricted/path/apixaban-split.frozen.json \
  --acknowledge-restricted-data-local-only
```

Once frozen, the test membership must not guide prompt, retriever, threshold,
or model selection. Any later split change requires a new version and an
explicit reason; it cannot silently replace the locked test set.
