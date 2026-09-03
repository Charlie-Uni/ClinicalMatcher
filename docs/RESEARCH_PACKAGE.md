# ClinicalMatcher research package

Status: final public package; research-only, not clinically validated

This document is the public map from the project's research question to its
implemented code, development evidence, limitations, and reproduction path.
It deliberately separates completed work from measured negative results,
deferred components, and ideas that were never implemented.

## Research question and system boundary

ClinicalMatcher asks how a patient-to-trial matching system can keep the path
from source evidence to a final ranked trial list inspectable. The implemented
design separates five records that are often collapsed in an LLM-only system:

```text
patient-local evidence
  -> retrieved candidate evidence
  -> typed fact with evidence IDs
  -> typed criterion decision (eligible / ineligible / unknown)
  -> trial aggregation with score, coverage, abstention, and audit trace
```

The project is a retrospective research prototype. It is not a medical device,
does not establish clinical validity, and must not autonomously include or
exclude a patient from a trial.

## Architecture and implemented contributions

| Layer | Implemented and tested | Evidence status |
| --- | --- | --- |
| Data boundary | Restricted-data regeneration, keyed pseudonyms, patient-local artifacts, public-data scanner, immutable manifests, and patient-grouped split checks | Implemented; real row-level artifacts remain owner-only |
| Trial ingestion | ClinicalTrials.gov v2 import, immutable snapshots, deterministic selection, parser coverage, and source hashes/spans | Implemented on public trial records |
| Criteria representation | Typed `ATOM/ALL/ANY/NOT` trees with inclusion/exclusion polarity, units, time windows, provenance, and source spans | Implemented; public AF decomposition reference is assisted silver, not human gold |
| Retrieval | Patient-isolated BM25, pinned MedCPT dense retrieval, and fixed reciprocal-rank fusion | Implemented and measured on validation; no independent real evidence-relevance gold |
| Reasoning | Typed facts, Kleene three-valued logic, future-fact exclusion, hard/soft criteria, deterministic aggregation, and stable ranking | Implemented and covered by synthetic tests |
| Safety layer | Unit/type checks, evidence-ID validation, explicit `unknown`, deterministic abstention projection, and observable error attribution | Implemented; statistical calibration is not claimed |
| Evaluation | Patient-cluster bootstrap, mixed boolean/numeric metrics, coverage-risk interface, leakage assertions, lineage-bound JSON/Markdown reports | Implemented; final locked-test report was not produced |
| Public demonstration | Offline CPU-only fictional multi-trial trace with BM25, typed facts, decisions, abstention, and unit-conflict probes | Implemented in P7.3; mechanism evidence only |

The executable public demonstration is documented in
[PUBLIC_DEMO.md](PUBLIC_DEMO.md). The frozen schema and decision semantics are
in [SCHEMA.md](SCHEMA.md), and experiment-level scope is tracked in
[TASKS.md](../TASKS.md).

## Data card

### Public artifacts

- `fixtures/synthetic/trial_matching.json` contains independently authored
  fictional patients, evidence, trials, and adjudicated fictional judgments.
  It exists to test mechanics, not to estimate real-world performance.
- `benchmarks/decomposition/` contains frozen public ClinicalTrials.gov source
  snapshots and an atrial-fibrillation-only development benchmark. The domain
  restriction limits external validity.
- The 40-item decomposition development reference is
  `llm_assisted_owner_reviewed_silver`: Codex drafted it and the data owner
  accepted 40/40 entries unchanged with zero review notes. It is not independent
  human gold, clinical ground truth, or a GRPO semantic oracle. The unanimous
  no-note review distribution is an explicit rubber-stamp risk.

### Restricted source

The official MIMIC-IV-Ext Apixaban release contains 2,300 human-reviewed
question-answer rows from 100 notes and 23 questions. Its table contains full
note text and therefore is not distributed here. The project stores no MIMIC
record, row-level derivative, annotation, patient manifest, embedding, index,
model output, or trained adapter in public Git.

The official table supplies fact answers but no evidence ID, supporting
sentence, source span, rationale, or relevance judgment. Real evidence-gold
coverage is therefore `0/2,300`; Evidence Recall@k, MRR, and nDCG are not
reported for real patients. See
[EVIDENCE_EVALUATION_BOUNDARY.md](EVIDENCE_EVALUATION_BOUNDARY.md) for the
source audit and permitted claims.

The mentor-designated patient categories were traced to rule-derived legacy
outputs rather than an independent clinical annotation. They are described as
legacy reference or silver labels, never clinical gold. See
[APIXABAN_CLASSIFICATION_SOURCE_AUDIT.md](APIXABAN_CLASSIFICATION_SOURCE_AUDIT.md).

### Intended use and prohibited use

Permitted use is local research on provenance, evidence handling, typed
verification, abstention, and evaluation design. Prohibited uses include
clinical care, medical advice, autonomous enrollment, automatic exclusion,
public redistribution of restricted records, and presenting synthetic or
silver-label agreement as clinical accuracy.

## Model and component card

| Component | Exact role | What is not claimed |
| --- | --- | --- |
| Deterministic extractor `1.0.0` | Non-trained 23-question fact baseline with lexical, negation, numeric, and abstention rules | Not a clinical rule engine |
| Llama 3.1 8B Instruct Q4_K_M | Pinned local structured and long-context fact baselines; later an initial zero-shot decomposition comparison | Weights are not distributed; no fine-tuned adapter or clinical validation |
| NCBI MedCPT Query/Article encoders | One pinned dense-retrieval baseline using official CLS pooling and exact dot product | Downstream fact retention is not evidence relevance |
| BM25 + RRF | Dependency-light lexical baseline and one predeclared rank-fusion ablation | RRF did not earn a reranker; no cross-encoder result is claimed |
| MLX / MLX-LM | Reproducible local conversion and QLoRA feasibility investigation | No real SFT completed; no tuned checkpoint exists |
| MedicalGPT | Pinned training/format implementation reference | Not vendored, executed for real training, or part of the runtime |
| LightRAG | Pinned optional graph-retrieval reference | Not integrated or evaluated |

Llama use is governed by the separate Llama 3.1 Community License. MedCPT and
external repository attribution are recorded in [REFERENCES.md](REFERENCES.md).
The repository's own code is Apache-2.0.

## Development experiments and evidence

All real-data numbers below are validation diagnostics, not locked-test or
clinical-performance results. The evaluated validation subset contains 15
patients and 345 patient-question rows; patient-cluster intervals are wide.
No result should be generalized beyond its frozen task and data contract.

### Fact extraction and input policy

The matched Llama comparison used one pinned model and prompt. The structured
prefix run matched 187/345 typed answers; the all-complete-evidence
long-context run matched 211/345. This supported choosing long context for the
later validation-only propagation diagnostic, but does not isolate whether an
error came from attention, inference, or evidence use. The predeclared choice
and artifact lineage are in
[APIXABAN_SINGLE_TRIAL_EVALUATION_PROTOCOL.md](APIXABAN_SINGLE_TRIAL_EVALUATION_PROTOCOL.md).

### Retrieval ablation

| Validation arm | Typed exact match (95% patient-bootstrap CI) | Numeric value coverage | Retrieval time |
| --- | ---: | ---: | ---: |
| Patient-local BM25 | 0.3188 (0.2638–0.3797) | 0.3671 | 0.035 ms/query |
| MedCPT dense | 0.3159 (0.2638–0.3710) | 0.5443 | 0.312 ms/query |
| BM25 + MedCPT RRF | 0.3159 (0.2609–0.3740) | 0.5063 | 0.421 ms/query |

These are downstream answer diagnostics. RRF did not improve the available
signal and was retained as a negative ablation; under the predeclared rule, a
cross-encoder was not added. On the separately gated 75-row literal-numeric
subset, occurrence@3 was 0.600 for BM25, 0.973 for MedCPT, and 0.933 for RRF.
Literal occurrence is weak answer-token retention, not evidence relevance.
Full run specifications, resource measurements, exclusions, and artifact hashes
are in [APIXABAN_BENCHMARK.md](APIXABAN_BENCHMARK.md) and
[EVIDENCE_EVALUATION_BOUNDARY.md](EVIDENCE_EVALUATION_BOUNDARY.md).

### Deterministic safety projection

On the same development subset, the P4.3 projection removed unsupported known
answers by turning them into explicit abstentions. It did not reduce the total
error union: long-context errors remained 137 rows after projection, while
unsupported answers fell from 3 to 0 and the gold-known abstention proxy rose
from 46 to 49. This is a measured safety-policy trade-off, not a quality gain.
The complete observable attribution table and four report hashes are in
[ERROR_ATTRIBUTION.md](ERROR_ATTRIBUTION.md).

### Single-trial propagation diagnostic

The frozen intended evaluator separated disagreement with a mentor-designated
legacy reference from propagation of model fact errors. The small validation
population produced final-class concentration, so exact class cells and rates
remain suppressed under the unresolved disclosure threshold policy. The
result demonstrates the value of preserving `unknown` instead of silently
treating missing information as passing; it is not a clinical accuracy claim.
See [APIXABAN_SINGLE_TRIAL_VALIDATION_RESULT.md](APIXABAN_SINGLE_TRIAL_VALIDATION_RESULT.md).

### Criteria decomposition

With the initial frozen zero-shot prompt, Llama 3.1 8B produced 37/40
schema-valid and 26/40 semantic-valid development outputs but zero exact atom
matches with the Codex-drafted, owner-accepted assisted silver. Mean runtime was
140.333 seconds per item and P95 was 552.965 seconds on Apple M3. The largest
exclusive error groups were atom-count mismatch (15/40) and typed semantic
violations (11/40). This is a configuration-specific negative descriptive
baseline, not independent-gold accuracy or a claim about the model's ceiling.
The test-entry gate was not met. See the frozen
[comparison](../benchmarks/decomposition/llama_dev_initial_prompt_1.0.0/comparison-report.md)
and [disagreement analysis](../benchmarks/decomposition/llama_dev_initial_prompt_1.0.0/disagreement-analysis.md).

### Local SFT feasibility

The complete owner-only export, silver-audit, calibration-reservation, context,
and conversion machinery was implemented. Real SFT was then deferred for the
precise configuration family “Llama 3.1 8B QLoRA × {16,384, 8,192} × MLX ≤
0.32.1 × Apple M3 24 GB” after reproducible memory gates failed. The 16K
single-allocation failure was byte-matched to the materialized GQA attention
tensor; the 8K run later exhausted total device memory. This is an engineering
feasibility result, not a general claim that local training or SFT is
impossible. Restart conditions are in
[P5_TRAINING_DECISION.md](P5_TRAINING_DECISION.md).

## Final locked-test disposition

P7.1 froze and authorized one final locked-test batch. All three raw inference
arms completed, but the gold-backed phase then failed before any projection or
metric was created because a manifest canonical self-hash was consumed as a
file-byte hash. Under the predeclared strict exposure rule, opening the gold
file consumed the single test exposure. No rerun or second evaluation of the
preserved predictions is permitted.

Therefore this project reports **no locked-test performance number**. The
failure, consequences, and non-actions are documented in
[P7_LOCKED_TEST_TERMINAL_FAILURE.md](P7_LOCKED_TEST_TERMINAL_FAILURE.md). The
lesson is operational: every hash field must be reviewed as a producer/consumer
semantic pair—what was stored and what the reader assumes—not merely as a
well-formed 64-character value.

P7.4 originally required a successful P7.2. After the terminal no-rerun state,
the owner explicitly approved completing the public package with validation as
the only performance evidence and the failed locked test as a documented
limitation. This is a recorded exception to the entry condition, not a claim
that P7.2 passed.

## Reproduction

### Public CPU path

```bash
uv venv --python 3.11
uv pip sync --python .venv/bin/python --require-hashes --strict requirements/public-py311.lock
uv pip install --python .venv/bin/python --no-deps --no-build-isolation --reinstall .
uv run --no-sync python scripts/check_public_data.py
uv run --no-sync clinical-matcher-validate fixtures/synthetic/trial_matching.json
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync clinical-matcher-demo --fixture fixtures/synthetic/trial_matching.json --format markdown
```

This path is offline after the locked dependencies are available. It runs on
CPU, requires no model server, and emits only fictional data. JSON output is
available with `--format json`.

### Restricted path

Authorized users obtain the official dataset directly from its provider and
follow [DATA_INGESTION.md](DATA_INGESTION.md) and
[APIXABAN_BENCHMARK.md](APIXABAN_BENCHMARK.md). Generated corpora, split
membership, predictions, reports, and model artifacts stay in ignored,
owner-only locations. The commands validate dataset, split, model, prompt,
code, and artifact hashes before writing reports with restrictive permissions.
The terminal locked test must not be rerun.

## Reliability and security controls

- content-aware public-data scanning in CI;
- strict JSON Schema plus semantic validation;
- patient-isolated retrieval and evidence-reference checks;
- future-fact exclusion and explicit type/unit incompatibility;
- deterministic manifests, seeds, configuration hashes, and stable tie breaks;
- patient/trial split isolation plus exact and semantic leakage-audit interfaces;
- owner-only restricted outputs, overwrite refusal, and no online inference for
  clinical text; and
- observation locks that prevent labels or model disagreements from silently
  rewriting frozen contracts.

These controls reduce software and research-governance risk. They do not prove
HIPAA compliance, unit correctness, clinical safety, or deployment readiness.

## Known limitations and deferred work

- No final locked-test metric exists.
- The real validation sample is small and used for development decisions.
- Real evidence relevance has no independent gold annotation.
- Decomposition uses assisted silver and a single owner acceptance pass.
- The source release lacks usable index dates and claim-level temporal/negation
  traces for several intended verifier analyses.
- Abstention is deterministic, not probability-calibrated.
- No trained LoRA adapter, cross-encoder reranker, LightRAG path, GRPO run,
  multimodal model, production API, database, or deployment is claimed.
- Multi-trial clinical ranking remains synthetic until independent
  patient-trial gold and governance exist.

These items remain deferred because their prerequisites are absent or their
validation gate failed; they are not hidden components of the final system.
