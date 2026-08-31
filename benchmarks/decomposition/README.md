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

## Criterion selection

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

No decomposition annotation or gold tree is included yet.
