# Evaluation protocol

Status: executable P1 baseline

Manifest version: `1.0.0`

Run report version: `1.0.0`

The evaluation framework is designed to separate retrieval quality, decision
quality, ranking quality, and selective prediction. The current synthetic
results test the contract and implementation; they are not estimates of
clinical performance.

## Reproducible split manifests

Every generated split manifest records:

- patient, trial, and criterion membership for train and test;
- the canonical SHA-256 of the complete source dataset;
- identifier-independent content hashes for duplicate detection;
- parent dataset hashes for lineage;
- the random seed, generation timestamp, command, and Git commit;
- the dimensions that must be isolated;
- the semantic similarity threshold;
- a hash of the manifest itself.

The generator refuses to record a Git commit while tracked changes are
present. The evaluator refuses a dataset whose content hash differs from the
manifest.

Patient and trial generalization are distinct protocols:

- `patient_holdout` isolates patients while sharing trials;
- `trial_holdout` isolates trials and all of their criteria while sharing
  patients;
- `joint_holdout` isolates patients, trials, and criteria together.

Do not claim trial generalization from a patient-only split. A clinical adapter
must add encounter/admission and note-group memberships as additional isolated
dimensions when those entities exist. The leakage checker is dimension-generic
so the same assertion covers those groups.

## Exact and semantic leakage checks

`clinical-matcher-check-split` fails when an isolated dimension has:

- the same ID in more than one split;
- identical identifier-independent content in more than one split;
- a semantic near-duplicate pair crossing splits at or above the manifest
  threshold;
- a semantic scan result that references an unknown ID.

The public package provides an exact cosine scanner for small embedding maps
and accepts candidate pairs from a scalable local ANN scan. Embedding creation
and large scans belong in the authorized local environment. Neither embeddings,
clinical text, nor real patient-level manifests are public artifacts.

The assertion is only as complete as its candidate scan. A semantic scan audit
therefore records the embedding model and revision, pooling, normalization,
threshold, expected cross-split pair count, evaluated candidate count, and ANN
candidate-recall estimate. An exhaustive claim fails unless every cross-split
pair was evaluated; an ANN claim fails without a measured recall estimate.
`clinical-matcher-audit-semantic-scan` emits an aggregate summary without row
IDs. The detailed pair file remains local.

## Metric layers

Retrieval metrics operate on ranked evidence IDs and independently adjudicated
gold evidence:

- Evidence Recall@k;
- evidence MRR;
- evidence nDCG@k.

Decision metrics operate on criterion labels:

- three-class confusion matrix;
- per-class precision, recall, and F1;
- macro-F1 over observed classes and a separately reported fixed
  three-class macro-F1;
- micro-F1 and accuracy.

Trial ranking metrics operate on adjudicated relevance grades:

- trial nDCG@k;
- trial MRR;
- trial Recall@k.

The run report attributes each decision error to one mutually exclusive class:

- evidence retrieval failure;
- decision error despite retrieved gold evidence;
- false abstention despite retrieved gold evidence.

This attribution is diagnostic, not causal proof. In particular, finding one
gold evidence item does not prove that all evidence needed for reasoning was
available.

## Coverage–risk and calibration boundary

Coverage–risk points sweep a selection score:

- coverage is the fraction of predictions answered;
- risk is the error rate among answered predictions;
- explicitly abstained predictions never count as answered.

The current baseline uses deterministic atomic coverage as the selection score.
It is not a calibrated confidence. When probabilistic models are introduced,
the same interface will accept calibrated confidence or abstention scores.
Calibration error, Brier score, threshold selection, and calibration-set
isolation remain future work.

## Confidence intervals

Bootstrap intervals resample whole patient clusters. All criteria and trials
belonging to a sampled patient are copied together, including repeated copies
when that patient is drawn more than once. Criterion rows are never treated as
independent samples.

Patient-level clustering is the default. A protocol may choose
patient–trial-level clustering only when that is the independent sampling unit
and the choice is recorded in the run configuration.

Small synthetic splits can have only one patient cluster, producing a
degenerate interval. Such intervals validate the implementation but carry no
inferential meaning and must not be presented as performance evidence.

## Run reports

Every run emits:

- `report.json` for aggregation and future W&B/MLflow ingestion;
- `report.md` for human review.

Both contain the dataset and split hashes, seed, code commit, model IDs, prompt
versions, index fingerprint, configuration, latency, metrics, confidence
intervals, coverage–risk points, and error attribution. The stable run ID is a
hash of the reproducibility specification; wall-clock time is not part of it.
The configuration names the timed code scope so latency values from different
pipelines are not compared under ambiguous boundaries.

Real-data manifests and reports contain row-level identifiers and therefore
must stay under the ignored local `artifacts/` directory.

## Commands

```bash
clinical-matcher-split \
  --fixture fixtures/synthetic/trial_matching.json \
  --strategy patient_holdout \
  --seed 17 \
  --test-fraction 0.5 \
  --output artifacts/splits/synthetic-patient.json

clinical-matcher-check-split \
  --manifest artifacts/splits/synthetic-patient.json

clinical-matcher-evaluate \
  --fixture fixtures/synthetic/trial_matching.json \
  --manifest artifacts/splits/synthetic-patient.json \
  --split test \
  --output-dir artifacts/runs/synthetic-patient-test
```
