# ClinicalMatcher

ClinicalMatcher is a research project for explainable patient–clinical-trial
matching. The target system ranks multiple trials for a patient by decomposing
eligibility criteria, retrieving criterion-level evidence, producing calibrated
structured decisions, and aggregating them into auditable trial scores.

This repository contains no MIMIC records, row-level derivatives, annotations,
embeddings, indexes, or patient-level experiment outputs. Authorized users must
obtain restricted datasets from their official provider and regenerate local
artifacts outside Git.

This is research software, not a medical device. It must not be used for
diagnosis, treatment, autonomous enrollment, or automatic patient exclusion.

## Target pipeline

```text
patient record + candidate trials
  -> atomic inclusion/exclusion criteria
  -> query normalization and expansion
  -> BM25 + dense retrieval
  -> cross-encoder reranking and optional IB denoising
  -> criterion-level decision with evidence
  -> neuro-symbolic validation and calibrated abstention
  -> trial-level aggregation and ranked recommendations
```

## Current status

The code under `legacy/apixaban/` is an early single-trial prototype. It
contains preprocessing, dense-retrieval, frozen-Ollama, criterion-parsing,
MI/IB, evaluation, and Streamlit scripts, but it is not a reproducible
end-to-end system:

- it expects restricted or generated local files that are intentionally absent;
- its committed historical evaluation used only ten cases;
- its old retrieval evaluation defined relevance by label similarity rather
  than criterion evidence;
- `rag_mi_infer.py` remains a placeholder;
- it has no multi-trial ranking, fine-tuned model, calibrated abstention, or
  leakage-controlled evaluation.

The implementation backlog and acceptance criteria are in [TASKS.md](TASKS.md).
The evidence-based scope audit is in
[docs/PROJECT_AUDIT.md](docs/PROJECT_AUDIT.md).

## Quick start

The baseline uses only the Python standard library:

```bash
python -m pip install -e .
python scripts/check_public_data.py
python -m unittest discover -s tests -v
clinical-matcher-smoke --fixture fixtures/synthetic/trial_matching.json
```

The smoke test evaluates two independently authored fictional patients against
two fictional trials. It verifies criterion polarity, evidence links,
abstention on missing facts, aggregation, and deterministic ranking.

The proposed P1 schema and aggregation semantics are documented in
[docs/SCHEMA.md](docs/SCHEMA.md). Compound criteria use a restricted
`ALL/ANY/NOT/ATOM` expression tree with typed values, explicit units, optional
time windows, and three-valued logic. Eligibility score, evidence coverage, and
abstention are reported separately.

## Data access and reproducibility

Do not add restricted patient data to this repository. Public development and
CI must use an independently authored synthetic fixture. Restricted evaluation
must run only in an approved local environment.

The reproducibility goal is:

1. a clean clone runs unit tests and a synthetic end-to-end smoke test;
2. an authorized user can regenerate ignored local artifacts from official
   source data;
3. every run records its config, seed, split manifest, code commit, dataset
   fingerprint, model IDs, and index fingerprint.

## External references

Pinned external repositories and integration boundaries are recorded in
[docs/REFERENCES.md](docs/REFERENCES.md). They are references, not vendored
source trees.

## License

Apache License 2.0. See [LICENSE](LICENSE).
