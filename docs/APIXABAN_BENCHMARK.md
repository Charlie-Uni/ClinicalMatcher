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

## Mixed typed-fact evaluation

P1.5 evaluates the 15 boolean and 8 numeric questions as different tasks. A
prediction set must conform to a supported version of the
`apixaban-prediction-set` schema and bind itself to the exact
benchmark hash, frozen split hash, split name, model ID, prompt version,
generation commit, and generation time. Missing patient-question predictions
are allowed so that incomplete systems can be measured, but are always scored
as missing rather than silently converted to `unknown`.

Run train or validation evaluation locally with:

```bash
clinical-matcher-evaluate-apixaban \
  --benchmark /restricted/path/apixaban-fact-benchmark.json \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --predictions /restricted/path/validation-predictions.json \
  --split validation \
  --bootstrap-samples 1000 \
  --output-dir /restricted/path/validation-report \
  --acknowledge-restricted-data-local-only
```

Locked-test evaluation additionally requires
`--acknowledge-locked-test-evaluation`. That flag does not authorize tuning on
test results; it makes the deliberate final-evaluation action explicit.

The report separates:

- boolean `present / absent / unknown` accuracy, macro/micro-F1, unknown-F1,
  and confusion matrix;
- numeric `present / unknown` status classification;
- numeric exact match, valid-pair MAE, value coverage, invalid-unit count, and
  tolerance accuracy using every gold-present value as the primary denominator;
- per-question results, macro-by-question results, and patient-cluster
  bootstrap intervals.

Catalog 1.0.0 defines no canonical numeric units. The built-in reviewed policy
therefore uses zero absolute tolerance for all eight numeric questions. This is
an exact source-value extraction metric, not a clinical-equivalence claim.
Non-zero tolerances are rejected until a future reviewed canonical-unit
contract exists. Reports are written owner-only, refuse overwrite, contain no
note text, and remain restricted because small aggregate cells may still be
disclosive.

## Deterministic extraction baseline

P2.1 provides a deliberately simple, non-trained reference before local-model
or retrieval experiments. Rule set `1.0.0` is stored in
`apixaban-deterministic-rules-1.0.0.json`. It covers all 23 questions with
reviewed lexical aliases and records that its design is limited to frozen
question semantics plus train/validation development; locked test answers are
forbidden input.

Generate validation predictions locally with:

```bash
clinical-matcher-apixaban-deterministic \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --split validation \
  --output /restricted/path/deterministic-v1.validation.predictions.json \
  --acknowledge-restricted-data-local-only
```

The evidence-linked prediction schema is `1.1.0`. Each row contains stable
evidence IDs and rule IDs, while the top level records the rule-set hash.
Boolean positive/negative conflicts abstain. A mention that lacks a
question-required temporal context abstains. A missing lexical fact abstains,
except for `med_decisions`, whose frozen source question explicitly says to
answer no unless inability is evidenced. Numeric rules extract finite source
numbers, apply the question's min/max aggregation, and implement the explicit
LVEF instruction to report 55 when the minimum is at least 55. They do not
invent units, convert measurements, or apply learned plausibility thresholds.

`--split test` requires the additional
`--acknowledge-locked-test-prediction` flag. Final test evaluation separately
requires the evaluator's locked-test acknowledgement. Neither flag permits
test-guided rule, prompt, retriever, threshold, or model selection.

The first real run is intentionally a validation-only engineering baseline.
Its owner-only aggregate report confirms that the complete restricted pipeline
runs, but it is exploratory and is not a clinical-performance or publication
claim. Validation predictions and reports remain outside Git.

## Pinned local Llama structured baseline

P2.2 freezes the previously used Llama path as a reproducible local baseline,
not as a clinical model. The machine-readable contract
`apixaban-llama-structured-contract-1.0.0.json` pins:

- Llama 3.1 8B Instruct Q4_K_M and the complete Ollama manifest/model-blob
  digests;
- Ollama `0.32.6` on `127.0.0.1:11434`, with proxies and cloud fallback
  disabled by the client;
- the Llama 3.1 Community License and Acceptable Use Policy, explicitly
  recording that Llama is open-weight but not OSI open source;
- `temperature=0`, `seed=17`, 16,384 context tokens, and 4,096 maximum output
  tokens;
- prompt `apixaban-23-facts-structured-1.0.0` and the deterministic
  `ordered-complete-evidence-prefix-v1` input policy;
- the development hardware profile and research-only use boundary.

The P2.2 short-context policy retains the longest ordered prefix of complete
evidence chunks within 8,000 characters. It never cuts a chunk and never uses
labels to select text. This deliberately creates a measurable truncation
baseline; P2.3 will run the same model, question contract, and decoding policy
with full notes to isolate the effect of long context.

Start Ollama locally and run validation with:

```bash
clinical-matcher-apixaban-structured-llm \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --split validation \
  --output-dir /restricted/path/llama31-structured-v1-validation \
  --acknowledge-restricted-data-local-only
```

The command refuses to run if the Ollama version or local model digest differs
from the contract. The model receives all 23 public question definitions and
the selected evidence, but no gold answers. The note is marked as untrusted
quoted data in the system prompt. A dynamic JSON Schema restricts question
IDs, types, values, units, and evidence IDs to the current request. Every
question must appear exactly once.

Prediction-set `1.2.0` stores the inference-contract hash and validated typed
predictions. If a request returns invalid JSON, violates the schema, duplicates
a question, or omits one, the whole request becomes 23 explicit `unknown`
predictions. There is no regex repair, manual correction, or hidden retry.

The separate structured run report records schema-valid/invalid requests,
input retention and truncation, latency mean/p50/p95, prompt/output token
counts, output throughput, actual model memory, hardware, model digest, prompt
version, configuration hash, and canonical prediction-set content hash. The
usual P1.5
evaluator then measures fact accuracy using the same frozen validation split as
P2.1. Both reports remain owner-only and restricted. `test` additionally
requires `--acknowledge-locked-test-inference`; final test evaluation is still
deferred until model selection is closed.

The completed validation run established an honest engineering baseline: its
structured format was reliable and aggregate task metrics improved over the
lexical baseline on several dimensions, but inference was slow on the M3
MacBook Air, most notes were truncated by design, and unknown recognition
remained weak. These findings motivate the matched P2.3 long-context comparison
without supporting any clinical-use claim.

## Matched full-note long-context baseline

P2.3 changes only the note-input policy and configured context window. The
contract `apixaban-llama-long-context-contract-1.0.0.json` is checked against
the P2.2 contract at load time: model and manifest digest, runtime, license,
prompt, 23-question catalog, seed, temperature, output-token limit, invalid
output handling, hardware declaration, and development-only split boundary
must match. The context window is 32,768 tokens and
`all-complete-evidence-v1` passes every ordered evidence chunk without using
labels or applying a character cap.

Run the matched validation comparison locally with:

```bash
clinical-matcher-apixaban-long-context \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --split validation \
  --output-dir /restricted/path/llama31-long-context-v1-validation \
  --acknowledge-restricted-data-local-only
```

The aggregate report records source-text retention, patients truncated by the
application, note characters exposed to the local model as a privacy-exposure
proxy, maximum observed prompt tokens, and requests whose prompt-token count
reached the declared context limit. The last signal is conservative: Ollama's
response does not expose a stronger per-request truncation flag. Full-note
input may recover omitted evidence, but it also increases sensitive-text
exposure, latency, and distraction; P2.3 is retained only if validation
evidence supports it. Patient text and row-level predictions remain restricted
and outside Git, and the locked test remains untouched.

The frozen validation run completed for all 15 patients with every application
evidence chunk retained, no observed prompt reaching the 32,768-token limit,
and every response passing the structured schema without repair. Results were
mixed under the unchanged P1.5 evaluator: overall typed exact match and boolean
classification improved relative to P2.2, while numeric status quality, value
coverage, and tolerance accuracy declined. Patient-bootstrap intervals from
only 15 clusters were wide and overlapping, so this is not evidence that full
notes are generally superior. Full-note input also increased prompt volume,
sensitive-text exposure, and model memory. The lower latency observed in this
single serial run is reported descriptively and is not treated as a stable
speed advantage. The long-context configuration is now a frozen comparator;
model selection will proceed to evidence retrieval without further P2.3
tuning or test access.

## Frozen evidence-index input contract

P3.1 preserves the evidence chunks created by the reviewed staging adapter; it
does not re-split or normalize note text. The public contract
`apixaban-evidence-chunk-contract-1.0.0.json` permits only patient/source IDs,
evidence IDs, exact half-open character spans, and evidence text as index
inputs. Legacy answers, benchmark assessments, prediction outputs, and test
labels are explicitly forbidden. Queries are also excluded at this stage, so
chunk/index construction cannot adapt to a question or answer.

For every selected split, the builder asserts that:

- evidence IDs are globally unique and follow the patient-HMAC/ordinal rule;
- each evidence source belongs to exactly one patient;
- spans start at zero, are contiguous and non-overlapping, and reconstruct the
  exact unnormalized chunk text;
- chunks respect the maximum declared by the source adapter;
- retrieval scope is `within_patient_only`;
- unavailable section metadata remains explicitly unavailable rather than
  being guessed.

The restricted manifest binds the frozen split, staging-corpus hash, public
contract hash, patient membership, ordered evidence IDs, complete index-input
projection, code commit, and deterministic `index_id`. Changing evidence text,
membership, ordering semantics, or the chunk contract therefore creates a new
index version. The manifest contains no note text but remains local because its
hashes and counts derive from restricted data.

Build and independently reproduce a validation manifest with:

```bash
clinical-matcher-apixaban-evidence-index build \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --split validation \
  --output /restricted/path/evidence-index.validation.manifest.json \
  --acknowledge-restricted-data-local-only

clinical-matcher-apixaban-evidence-index verify \
  --manifest /restricted/path/evidence-index.validation.manifest.json \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --acknowledge-restricted-data-local-only
```

Building a locked-test manifest additionally requires
`--acknowledge-locked-test-indexing`; it remains deferred during model
development. P3.1 does not build embeddings, claim evidence relevance, or
measure downstream effectiveness. Those belong to P3.2–P3.5.

The first frozen validation manifest covers 15 patient-local sources and 107
existing evidence chunks. Independent verification reproduced the same index
identity and confirmed stable IDs, globally unique evidence documents,
contiguous exact spans, source/patient isolation, the declared 2,000-character
maximum, and no text normalization. The manifest remains owner-only and local;
its restricted hashes and deterministic index ID are intentionally not copied
into this public document. No test manifest was built.

## Frozen patient-local BM25 baseline

P3.2 adds a dependency-free BM25 implementation behind the common
`EvidenceRetriever` protocol. Each question is searched only against evidence
belonging to the same patient. The versioned public contract freezes:

- the unchanged public `source_question` as the complete query, with no answer
  text, fact-field name, manual expansion, or test label;
- Unicode case-folding and the declared token regular expression, with no
  stemming, stopword removal, or additional text normalization;
- positive-IDF BM25 with `k1=1.2`, `b=0.75`, and document frequencies computed
  within one patient;
- at most three positive-score chunks, tied by source start then evidence ID.

Top three was predeclared as a bounded exposure policy of approximately 6,000
source characters per question under the frozen 2,000-character chunk limit;
it was not selected against validation labels. The owner-only run artifact
binds the split, evidence-index manifest, question catalog, BM25 contract,
prediction-set content, configuration, and commit. It records deterministic
serialized-index size, build/retrieval timing, candidate comparisons, and
selected-versus-full-note character exposure. Timings are descriptive local
measurements rather than machine-independent benchmarks.

Run validation retrieval and its separately identifiable downstream diagnostic
with:

```bash
clinical-matcher-apixaban-bm25 \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --evidence-index-manifest /restricted/path/evidence-index.validation.manifest.json \
  --split validation \
  --output-dir /restricted/path/bm25-v1-validation \
  --acknowledge-restricted-data-local-only
```

`retrieval.json` contains no note text or question text, but patient IDs,
rankings, scores, and restricted-data-derived hashes still make it a local-only
artifact. `predictions.json` applies the already frozen deterministic fact
extractor to only the selected chunks and uses prediction-set schema `1.2.0`.
Its answer metrics can show downstream information retention, but they do not
establish retrieval relevance.

The official release has no independent human-authored evidence-ID gold.
Consequently P3.2 does **not** report Evidence Recall@k, MRR, or nDCG on real
patients. Ranking correctness is tested on controlled synthetic examples; the
real validation run reports resource/exposure statistics and downstream answer
metrics only. Calling lexical answer occurrence or rule-generated links
clinical relevance would be circular and is explicitly outside this baseline.
Locked-test retrieval and evaluation remain deferred.

The frozen validation run from implementation commit `ed8d700` completed all
345 patient-question queries over 15 patients and 107 chunks. Each query had a
positive lexical match and selected three chunks. The deterministic serialized
index proxy was 209,197 bytes; local index construction took 9.59 ms and mean
retrieval took 0.035 ms/query. Selected chunks represented 43.4% of the
per-question full-note character exposure, a 56.6% reduction.

The unchanged downstream evaluator reported typed exact match 0.3188 (95%
patient-bootstrap CI 0.2638–0.3797), compared with 0.3275 (0.2696–0.3913) for
the full-evidence deterministic comparator. Boolean macro-F1 was essentially
unchanged, while numeric value coverage fell from 0.5696 to 0.3671 and numeric
status accuracy fell from 0.7000 to 0.5667. These small-sample intervals overlap,
and the answer diagnostic cannot identify evidence relevance. BM25 is therefore
retained as the cheap lexical comparator for P3.3/P3.4, not selected as a
superior evidence method. Patient-level run and evaluation artifacts remain
owner-only outside Git, and no locked-test artifact was created.

## Frozen patient-local MedCPT dense baseline

P3.3 evaluates exactly one biomedical dense retriever rather than collecting
multiple encoders. The public-domain NCBI MedCPT dual encoder is pinned to
immutable Hugging Face revisions:

- `ncbi/MedCPT-Query-Encoder` at
  `d83a36cc6b8e3a5c5e9d9d6ba156808c1643dcbc`;
- `ncbi/MedCPT-Article-Encoder` at
  `d05a736da4bb84ee4057b7f7999485be6ed85465`.

The pinned model cards specify `[CLS]` last-hidden-state representations and a
768-dimensional shared query/article space. The implementation uses the public
source question unchanged, capped at 64 tokens. Each exact evidence chunk is
encoded as an empty title paired with the unnormalized chunk text, capped at
512 tokens. Vectors are unnormalized float32 values and are ranked by exact dot
product only within the same patient. Top three and tie-breaking are identical
to P3.2, so BM25 and dense exposure budgets are directly comparable. The empty
title is an explicit adaptation: MedCPT was trained on PubMed query/article
logs, not clinical notes, and its Article Encoder normally receives title plus
abstract. This domain and input-format mismatch is a limitation, not hidden
preprocessing.

The runtime freezes Torch 2.2.2, Transformers 4.43.0, CPU inference,
deterministic algorithms, batch size eight, `trust_remote_code=False`, and
`local_files_only=True`. Download public weights separately before entering the
authorized environment; an inference run cannot contact Hugging Face or send
patient text to an external service. Install the optional local runtime with:

```bash
pip install -e '.[dense]'
```

Then build and run the validation index with:

```bash
clinical-matcher-apixaban-dense \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --evidence-index-manifest /restricted/path/evidence-index.validation.manifest.json \
  --split validation \
  --output-dir /restricted/path/medcpt-dense-v1-validation \
  --acknowledge-restricted-data-local-only
```

The output directory contains owner-only `vectors.f32`,
`index-manifest.json`, `retrieval.json`, and `predictions.json`. The manifest
binds the fixed model revisions, P3.1 evidence index, ordered evidence IDs,
vector count/dimension/dtype/byte count, vector-file SHA-256, and a deterministic
index ID. The run validator checks the full patient-question grid, exact patient
isolation, score ordering, aggregate reconciliation, and that every downstream
evidence citation belongs to its retrieved top three. All four artifacts remain
outside Git. As in P3.2, downstream answer metrics are diagnostic because the
release has no independent evidence-ID relevance gold.

Model provenance: [MedCPT Query Encoder](https://huggingface.co/ncbi/MedCPT-Query-Encoder),
[MedCPT Article Encoder](https://huggingface.co/ncbi/MedCPT-Article-Encoder), and
the [MedCPT paper](https://doi.org/10.1093/bioinformatics/btad651). The pinned
model license files identify the release as a freely available United States
Government Work and disclaim clinical decision use without professional review.
