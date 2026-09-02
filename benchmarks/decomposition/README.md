# AF criteria-decomposition benchmark sources

This directory contains public ClinicalTrials.gov source material and
deterministic selection artifacts for ClinicalMatcher's single-domain
atrial-fibrillation criteria-decomposition benchmark. It contains no patient
records, MIMIC data, or patient-derived labels.

## Source snapshot

- Registry: ClinicalTrials.gov, U.S. National Library of Medicine
- API: v2, reported API version `2.0.5`
- Query time: `2026-08-31T12:26:16.898901Z`
- API data timestamp: `2026-08-28T09:00:06`
- Terms: <https://clinicaltrials.gov/about-site/terms-conditions>
- Snapshot: `af_source_pool_2026-08-31` / `ctg-15b1e8aff71f895f`
- Snapshot content SHA-256:
  `15b1e8aff71f895ff1d7b2c9d06e8a16bb44fb350b6281116f2e800d046e5b7c`

The builder fetched all 833 registry hits returned by the frozen AF query.
Local, predeclared filters retained 546 interventional records, and the frozen
NCT-ID hash rule selected 40 trials. The conservative parser imported 37 and
reported three as `ambiguous_polarity` because their eligibility text lacked
polarity headings; they were not silently replaced. The selected source
studies are unmodified registry JSON. Normalized protocol files are project
derivatives that preserve the source text and exact spans.

ClinicalTrials.gov is a live registry. Current records, recruitment statuses,
and search totals may differ from this processed snapshot. Reproduction and
evaluation must use the frozen local snapshot, not a live API response.

## Criterion selection and locked-test remediation

`af_decomposition_selection_1.0.0.json` is a write-once manifest generated
from the verified snapshot. It records 501 parsed candidates, one exact
normalized-text duplicate exclusion, and exactly 80 selected criteria:

- dev: 40 criteria from 15 trials;
- locked test: 40 criteria from 16 disjoint trials;
- each split: 20 inclusion and 20 exclusion criteria;
- each split and polarity: 5 low-, 7 medium-, and 8 high-complexity criteria.

Selection manifest SHA-256:
`befdca243400ea10e7acda1f8fae6351917e4e4aca067b8c839b01a02c1963c3`.
These data measure only the frozen AF domain and must not be described as a
disease-independent decomposition benchmark.

The original locked-test membership was retired after a recorded public-source
timing violation. Protocol 1.2.0 preserves the original 40-item dev split and
uses a separate, headlessly constructed replacement test snapshot. The current
selection is `af_decomposition_selection_1.2.0.json`, ID
`decomposition-selection-b83592f4e26d0874`, SHA-256
`b83592f4e26d0874ef61ab74f06ebc432f4059251f9da05eb5184bfeb2ffe97e`.
Locked-test sources are stored separately and excluded from default repository
search; this README does not disclose their criterion text.

## Frozen dev annotation inputs

The dev-only concept catalog, owner-approved issue-resolution log, and
single-owner annotation package are bound to selection 1.2.0 and dev snapshot
`decomposition-dev-source-9d8d925255a65acd`. The catalog contains 85 shared
concepts and passes source-grounded alias validation. The issue log freezes all
eight pre-annotation ambiguity decisions. The package contains all 40 selected
dev criteria with `expression=null`; it is an annotation input, not gold and
not a model-generated artifact.

- `dev_concept_catalog_1.1.0.json`
  (`decomposition-catalog-dev-57dff640761ba61e`)
- `dev_annotation_issue_log_1.0.0.json`
  (`decomposition-issue-log-dev-44f675ac70a1335a`)
- `dev_single_annotator_package_1.0.0.json`
  - remains the immutable zero-expression source package; its intended
    independent route was withdrawn before execution on 2026-09-02

The active engineering workflow is documented in
`docs/DECOMPOSITION_LLM_ASSISTED_WORKFLOW.md`. Mutable and superseded work files
remain under ignored `artifacts/decomposition/`. The completed public dev
reference is `dev_llm_assisted_silver_1.0.0.json` (file SHA-256
`ca76ddec254f0d7489dd3f38fa1c42d93acf519f444de06760bc3f136c456a52`).
Its companion `dev_llm_assisted_silver_manifest_1.0.0.json` explicitly records
the 40/40 `accepted_unchanged`, zero-note review distribution, both model roles,
the eight-item information-asymmetry subgroup, the post-observation lock, and
all prohibited claims. It is always labelled
`llm_assisted_owner_reviewed_silver`, never gold.

## Initial-prompt local Llama dev result

`llama_dev_initial_prompt_1.0.0/` retains the one frozen P5D.5 run and its
P5D.6 disagreement analysis. The package contains the original self-hashed
predictions and comparison report, plus a self-hashed deterministic diagnostic
and human-readable summaries. All inputs are public ClinicalTrials.gov dev
criteria; no patient or MIMIC data is present.

The run compared pinned local Llama 3.1 8B with the Codex-drafted,
owner-accepted assisted silver under initial prompt v1.0.0, zero-shot, without
few-shot examples. It yielded zero exact atom matches and is retained as a
dev-only negative descriptive baseline for that configuration. It does not
measure independent-human-gold accuracy or the ceiling after prompt iteration.
The owner outcome remains disclosed as 40/40 accepted unchanged with zero
review notes. The test entry gate was not met; locked-test text was not
inspected or run.

The P5D.6 diagnostic has ID
`decomposition-disagreement-dev-d92d0e52b21598a9`, content SHA-256
`d92d0e52b21598a93442311fc7c7667e0c8d73240f9675528e69cc259e5f016a`,
and file SHA-256
`d2ad285afd6aa074026233068619e4eb9587f166f2dcc85fa9ad663922388504`.

## Cross-split lexical-overlap disclosure

`af_decomposition_overlap_1.0.0.json` exhaustively compares all 1,600 dev/test
pairs with the predeclared `unicode_word_set_jaccard/1.0.0` diagnostic. It has
no threshold and no effect on selection, annotation eligibility, or scoring.
The observed distribution was median `0.03125`, P95 `0.132352941176`, P99
`0.269230769231`, and maximum `1.0`. The maximum comes from three-token sets
with identical vocabulary; it does not establish identical text or semantic
equivalence. Conversely, lexical Jaccard can miss paraphrases.

Diagnostic report SHA-256:
`e8520469215ee1f996ee803eac8509bcbf5252d71b5b65f921029b1a97b91ffc`.

No independent decomposition annotation or gold tree is included. The
withdrawn manual route remains documented in
`docs/DECOMPOSITION_DEV_ANNOTATION_WORKFLOW.md` as historical process evidence.
