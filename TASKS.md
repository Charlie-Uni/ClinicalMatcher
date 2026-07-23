# ClinicalMatcher implementation tasks

“Done” means code, tests, documentation, and a reproducible command exist.

## P0 — Compliance-safe reproducibility

- [x] Add independently authored synthetic patients, trials, criteria, evidence
  spans, and expected rankings.
- [x] Define typed Patient, Trial, Criterion, Evidence, CriterionDecision, and
  TrialMatch models.
- [x] Add a synthetic end-to-end CLI and CPU-only tests.
- [ ] Add an authorized-user local regeneration command; never embed download
  credentials or distribute restricted outputs.
- [x] Add CI checks for clinical text identifiers, archives,
  row-level tables, embeddings, and indexes.
- [ ] Lock dependencies and add CI.

Acceptance: a clean clone runs tests and the synthetic smoke test without
restricted data, Ollama, or a GPU.

## P1 — Task schema and evaluation

- [x] Draft the prediction unit as patient × trial × criterion.
- [x] Preserve inclusion/exclusion polarity and eligible/ineligible/unknown.
- [x] Add typed values, explicit units, index-date time windows, and a
  restricted compound-condition expression tree.
- [x] Review and freeze schema `1.0.0` in `docs/SCHEMA.md`.
- [x] Define adjudication, decomposition provenance, and trial relevance
  guidance.
- [x] Add a versioned JSON Schema, strict validator CLI, semantic link checks,
  and valid/invalid schema tests.
- [x] Add independently authored synthetic criterion-evidence and trial gold.
- [ ] Build a clinically adjudicated criterion-evidence relevance set.
- [ ] Split by patient/trial and add exact/semantic near-duplicate checks.
- [ ] Implement evidence Recall@k/MRR, criterion F1, ranking nDCG/MRR/Recall@k,
  calibration, abstention coverage/risk, bootstrap confidence intervals, and
  latency.

Acceptance: every result has a split manifest, seed, config, code commit,
dataset fingerprint, model IDs, and index fingerprint.

## P2 — Multi-trial data

- [ ] Verify access, license, prediction units, and labels before selecting a
  proposed extended MIMIC dataset.
- [ ] Normalize public trial inclusion/exclusion sections into stable IDs.
- [ ] Validate patient–criterion labels and evidence spans.
- [ ] Keep Apixaban as a regression case study, not the primary benchmark.

Acceptance: each patient has multiple candidate trials with adjudicated
criterion evidence and ranking relevance.

## P3 — Query optimization

- [ ] Implement schema-validated atomic criterion decomposition.
- [ ] Add deterministic medical normalization.
- [ ] Add multi-query expansion and reciprocal-rank fusion.
- [ ] Ablate original query, decomposition, expansion, and both.

Acceptance: evidence Recall@k improves without test-label access.

## P4 — Retrieval and differentiated reasoning

- [ ] Implement BM25 and one validated dense retriever behind one interface.
- [ ] Add fusion and a clinical cross-encoder reranker.
- [ ] Port only required IB logic with a defined objective, insertion point,
  leakage boundary, and cheaper-filter ablations.
- [ ] Add neuro-symbolic checks for numeric, temporal, negation,
  missing-evidence, and criterion-polarity errors.
- [ ] Compare RAG with a matched long-context baseline on quality,
  faithfulness, latency, memory/cost, updateability, and privacy exposure.

Acceptance: improvements hold for evidence retrieval and downstream criterion
decisions with confidence intervals and resource measurements.

## P5 — Model adaptation

- [ ] Establish rules and frozen-model structured-output baselines.
- [ ] Build SFT data from training folds only.
- [ ] Export the project schema to the pinned MedicalGPT SFT format and train in
  a separate environment.
- [ ] LoRA-SFT an available model sized for the actual GPU budget.
- [ ] Attempt GRPO only after stable SFT, non-gameable rewards, and held-out
  evidence of benefit.

Acceptance: the adapted model beats a strong frozen baseline and always
preserves evidence IDs and unknown decisions.

## P6 — Aggregation and demo

- [ ] Implement documented hard exclusions, unknown handling, calibration, and
  trial scoring.
- [ ] Calibrate criterion confidence and implement review-required abstention.
- [ ] Return ranked trials with criterion decisions, evidence, uncertainty,
  verifier conflicts, and an audit trace.
- [ ] Build a multi-trial UI with explicit research-only warnings.

Acceptance: synthetic and held-out benchmarks produce deterministic,
explainable rankings without presenting output as medical advice.

## Optional — LightRAG

- [ ] Evaluate only after the conventional hybrid baseline is complete.
- [ ] Index public trial criteria/protocols, not raw patient records.
- [ ] Compare evidence retrieval, ranking, latency, memory, and build cost.
- [ ] Retain only if it provides a reproducible multi-hop benefit.
