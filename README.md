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

The supported public runtime is CPU-only CPython 3.11. Its direct and
transitive dependencies are hash-locked separately from GPU training and
legacy experiments:

```bash
uv venv --python 3.11
uv pip sync --python .venv/bin/python --require-hashes --strict requirements/public-py311.lock
uv pip install --python .venv/bin/python --no-deps --no-build-isolation -e .
uv run --no-sync python scripts/check_public_data.py
uv run --no-sync clinical-matcher-validate fixtures/synthetic/trial_matching.json
uv run --no-sync python -m unittest discover -s tests -v
uv run --no-sync clinical-matcher-smoke --fixture fixtures/synthetic/trial_matching.json
```

See [requirements/README.md](requirements/README.md) for the pinned uv version,
Windows paths, lock regeneration, and the GPU-environment separation policy.

The smoke test evaluates two independently authored fictional patients against
two fictional trials. It verifies criterion polarity, evidence links,
abstention on missing facts, aggregation, and deterministic ranking.

The frozen P1 `1.0.0` schema and aggregation semantics are documented in
[docs/SCHEMA.md](docs/SCHEMA.md). Compound criteria use a restricted
`ALL/ANY/NOT/ATOM` expression tree with typed values, explicit units, optional
time windows, and three-valued logic. Eligibility score, evidence coverage, and
abstention are reported separately; atomic coverage and data-quality issues
keep unresolved OR branches visible. Schema changes are recorded in
[docs/SCHEMA_MIGRATIONS.md](docs/SCHEMA_MIGRATIONS.md).

The resume-first Apixaban benchmark has a separate frozen note-grounded fact
contract in [docs/APIXABAN_BENCHMARK.md](docs/APIXABAN_BENCHMARK.md). Its 23
question definitions distinguish fact truth from eligibility, preserve
`not_specified` as `unknown`, and do not invent units or evidence gold that the
official release does not provide. Its P2.1 deterministic extraction baseline
adds versioned lexical, negation, temporal-context, and numeric min/max rules,
evidence-linked prediction traces, explicit abstention, and a validation-only
restricted report before any local-model or retrieval experiment. P2.2 adds a
loopback-only structured-output baseline using a digest-pinned local Llama 3.1
8B Q4_K_M model, with versioned prompt/input policy, strict evidence IDs,
measured invalid-output abstention, and latency/token/memory reporting.

The executable P1 evaluation protocol is documented in
[docs/EVALUATION.md](docs/EVALUATION.md). It provides lineage-tracked
patient/trial holdouts, exact and semantic leakage assertions, separate
retrieval/decision/ranking metrics, patient-cluster bootstrap intervals,
coverage–risk curves, error attribution, and paired JSON/Markdown run reports.
Generated manifests and reports belong under ignored `artifacts/`.

Public ClinicalTrials.gov v2 ingestion and the restricted local patient adapter
boundary are documented in
[docs/DATA_INGESTION.md](docs/DATA_INGESTION.md). P2.1 adds cursor-paginated
batch selection, immutable trial snapshot manifests, parser coverage reports,
offline-only snapshot loading, and an explicit patient-trial gold-readiness
gate. P2.2 adds pilot-derived annotation-capacity planning and removes
recent-update/first-N sampling: all registry hits are explicitly filtered, then
sampled by a capacity-plan-derived NCT hash with a complete public selection
audit. Candidate snapshots remain ignored runtime artifacts during
development; an actual public benchmark snapshot is frozen only after its
capacity, selection policy, attribution, update notice, statistical
limitations, and gold governance have been reviewed. Normalized patient files,
semantic pair details, and patient-level reports are never committed.
The executable two-annotator timing-pilot workflow and local-only disclosure
boundary are documented in
[docs/ANNOTATION_PROTOCOL.md](docs/ANNOTATION_PROTOCOL.md). Only a validated,
hash-bound aggregate from completed adjudication can authorize a
capacity-bound snapshot; manually entered pilot estimates remain provisional.
The end-to-end Chinese operating guide, including the frozen patient-sampling
algorithm and the division between code and human work, is in
[docs/PILOT_EXECUTION_GUIDE_ZH.md](docs/PILOT_EXECUTION_GUIDE_ZH.md).
The official MIMIC-IV-Ext Apixaban `1.0.0` CSV can now be verified and converted
locally into an evidence-chunked staging corpus with keyed pseudonyms and a
separate owner-only raw-ID map. The released extension does not expose usable
index dates, so the adapter refuses to call that staging output a runtime
patient source until authorized MIMIC note metadata supplies them.

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

The repository does not distribute Llama model weights. The optional P2.2
baseline is Built with Llama and uses Llama 3.1 under the separate
[Llama 3.1 Community License](https://www.llama.com/llama3_1/license/), not
Apache-2.0 or an OSI open-source license. Llama 3.1 is licensed under the Llama
3.1 Community License, Copyright © Meta Platforms, Inc. All Rights Reserved.
