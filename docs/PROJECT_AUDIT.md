# ClinicalMatcher scope audit

Audit date: 2026-07-23

## Confirmed objective

The target is an end-to-end patient-to-trial recommendation system with four
core capabilities:

1. local model adaptation;
2. eligibility-criteria query decomposition and expansion;
3. retrieval-augmented generation;
4. criterion decisions aggregated into ranked multi-trial recommendations.

The legacy Apixaban code is a single-trial case study, not the completed system.

## Legacy implementation evidence

| Capability | Status |
|---|---|
| Apixaban preprocessing | Script exists; restricted inputs are not distributed |
| Dense embeddings | E5, GatorTron, and Nemotron scripts exist |
| Vector retrieval | FAISS and Chroma builders/CLIs exist |
| Frozen local LLM | Ollama `llama3.1` evaluation/demo scripts exist |
| Criterion parsing | Five-domain prompts and parsers exist |
| MI / IB experiments | Partial exploratory scripts exist |
| Single-trial demo | Streamlit script exists |
| Combined pipeline | `rag_mi_infer.py` is a placeholder |
| Query decomposition/expansion | Missing |
| BM25+dense hybrid retrieval | Missing |
| Cross-encoder reranking | Missing |
| LoRA/SFT/GRPO | Missing |
| Multi-trial aggregation/ranking | Missing |

Historical result files were excluded from this public repository because they
contain or may reproduce patient-level restricted information. The earlier
ten-case measurements were exploratory: they were not cross-validated, lacked
confidence intervals, and may have allowed self-retrieval.

## Required task and label schema

The primary prediction unit is:

```text
patient_id × trial_id × criterion_id
```

Each record must preserve:

- inclusion or exclusion polarity;
- `eligible`, `ineligible`, or `unknown` decision;
- source document and evidence spans;
- annotation provenance and adjudication state;
- model confidence and calibration version;
- verifier conflicts and abstention reason.

A patient–trial result aggregates criterion records but never replaces them.

## Correct evaluation relevance

Retrieval relevance is criterion-evidence relevance, not equality of a final
patient label. Build a small adjudicated gold set of
patient–trial–criterion queries and relevant evidence spans/documents.

Report:

- evidence Recall@k and MRR;
- criterion macro/micro precision, recall, and F1;
- trial-ranking nDCG@k, MRR, and Recall@k;
- calibration and abstention coverage/risk;
- bootstrap confidence intervals, latency, and resource use.

Split by patient and, for generalization claims, by trial. Remove the query
patient and near duplicates from retrieval. Log the split manifest, seed, code
commit, config, dataset fingerprint, model IDs, and index fingerprint.

## Differentiated contributions

- **Neuro-symbolic verification:** check numeric thresholds, temporal relations,
  negation, missing evidence, and inclusion/exclusion polarity; surface
  disagreements instead of silently overriding them.
- **Designed IB denoising:** define the objective, insertion point, training
  boundary, calibration, and ablations against cheaper filters.
- **Calibrated abstention:** return `unknown/review required` when evidence is
  missing or confidence is insufficient.
- **RAG versus long context:** compare effectiveness, evidence faithfulness,
  latency, memory/cost, updateability, and privacy exposure.

## Safety and validity boundaries

- Synthetic or retrospective benchmarks do not establish clinical validity.
- Explanations are not evidence unless grounded to source spans.
- Missing information must not be silently converted into exclusion.
- Trial scoring requires documented hard-exclusion and unknown-handling rules.
- Raw data, row-level derivatives, annotations, embeddings, indexes, trained
  artifacts, and patient-level outputs remain outside public Git.

## Target structure

```text
configs/
docs/
src/clinical_matcher/
  data/
  criteria/
  retrieval/
  reranking/
  reasoning/
  verification/
  aggregation/
  evaluation/
  pipelines/
apps/
tests/
fixtures/synthetic/
artifacts/             # ignored
legacy/apixaban/
```
