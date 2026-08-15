# Evidence evaluation boundary

Status: frozen and measured for P3.5

Scope: MIMIC-IV-Ext Apixaban release `1.0.0` and the P3.1–P3.4
patient-local retrieval experiments.

## Official-release audit

The locally retained official release was checked against its supplied
`SHA256SUMS.txt`; the license, README, data dictionary, and CSV all matched.
The CSV SHA-256 is
`8e8083b0b5e3d038ad912a812be1bb8a53f8a59bc37a4c29d8a420cb4296e267`.
This fingerprint is safe to publish; the CSV itself remains restricted.

The README says that a human reviewer validated and corrected 2,300
question–answer pairs for 100 notes and 23 questions. The actual CSV contains
exactly 2,300 rows, 100 distinct notes/admissions, and these eight columns:

```text
text, note_id, hadm_id, criterion, question_type, question, answer,
not_specified
```

There is no evidence ID, supporting sentence, source span, rationale, or
evidence-relevance field. The `text` column is the complete source note, not an
annotation that identifies which part supports an answer. `not_specified` is an
answer-missingness flag, not an evidence label. The evidence IDs in the local
staging corpus are deterministic chunk identifiers created by this project;
they were not supplied or adjudicated by the dataset authors.

Consequently, the official release provides human-reviewed answer gold but
zero independently annotated evidence links: evidence-gold coverage is
`0/2300` question–answer pairs. All 2,300 rows are excluded from real-patient
Evidence Recall@k, MRR, and nDCG evaluation.

## Permitted signals and their meaning

| Signal | Gold source | Current coverage | Exclusions | Tier and allowed claim |
| --- | --- | ---: | --- | --- |
| Real-patient Evidence Recall@k, MRR, nDCG | None in official release | 0/2,300 | All official rows | Unavailable; do not compute or report |
| Controlled retrieval-order tests | Independently authored synthetic fixtures | Fixture cases only | All real patients | Synthetic mechanics test only; no clinical-performance claim |
| Exact numeric-token occurrence@1/@3 | Official numeric answer, gated by exact occurrence in full context | Validation: 75/120 numeric rows | 41 unknown, 2 ambiguous LVEF=55, 2 full-context non-occurrences; all 225 boolean rows outside scope | Weak diagnostic only; never call it relevance gold |
| Downstream fact-answer metrics | Human-reviewed official `answer` and `not_specified` fields | Validation: 345/2,300 rows (15 patients × 23 questions) | Train and locked test during development | Diagnostic information-retention signal, not retrieval relevance |

The existing BM25, MedCPT, and RRF run schemas therefore fix
`independent_evidence_gold_available=false` and
`retrieval_relevance_metrics_reported=false`. Their typed exact match,
boolean, unknown, and numeric results are downstream diagnostics. They cannot
support a claim that the selected chunks are clinically relevant or complete.

## Frozen weak diagnostic

Contract `apixaban-numeric-answer-occurrence-v1` defines one validation-only
diagnostic. A row enters its denominator only when the official question is
numeric, the released fact status is present, and the released number appears
as an independent decimal token somewhere in the complete note. Boolean and
unknown rows are excluded. LVEF answers equal to 55 are also excluded because
the source protocol maps any minimum at or above 55 to 55, so the released
value may not be a literal observation.

For each included row, occurrence@1 and occurrence@3 ask only whether the same
decimal value appears within any one of the selected chunks. Commas are removed
from thousands-grouped numbers and values are compared with exact decimal
equality. Scientific notation and numbers embedded in alphanumeric identifiers
are not matched. The diagnostic compares the already frozen BM25, MedCPT, and
RRF runs and emits aggregate counts only. No matching parameter is selected
from validation results, and locked test is unavailable to the runner.

Run the frozen diagnostic only after all three validation retrieval artifacts
exist:

```bash
clinical-matcher-apixaban-numeric-occurrence \
  --benchmark /restricted/path/apixaban-fact-benchmark.json \
  --frozen-split /restricted/path/apixaban-split.frozen.json \
  --staging-corpus /restricted/path/apixaban-staging-corpus.json \
  --bm25-run /restricted/path/bm25-validation/retrieval.json \
  --dense-run /restricted/path/dense-validation/retrieval.json \
  --rrf-run /restricted/path/rrf-validation/retrieval.json \
  --output /restricted/path/numeric-occurrence-validation.json \
  --acknowledge-restricted-data-local-only
```

The writer refuses overwrite and creates the aggregate report with owner-only
permissions. The report remains restricted even though it contains no row-level
identifiers or text.

At implementation commit `c5c0852`, the restricted validation run reconciled
all 345 rows. Of 120 numeric rows, 75 were evaluable after excluding 41 unknown
answers, two ambiguous LVEF=55 protocol values, and two values without an exact
token anywhere in full context. BM25 retained the token in 21/75 top-one and
45/75 top-three selections (0.280 and 0.600). MedCPT retained it in 51/75 and
73/75 (0.680 and 0.973). RRF retained it in 36/75 and 70/75 (0.480 and 0.933).
The aggregate report remains owner-only outside Git, and locked test was not
read.

This weak result is consistent with the downstream numeric-coverage pattern:
MedCPT preserves literal numeric values more often than BM25, while RRF does
not improve over MedCPT. It is not a relevance result. A matching number may be
unrelated to the question, and exact-token gating excludes nonliteral answers;
therefore the rates cannot be compared to evidence Recall@k or used to claim
that all necessary clinical evidence was retrieved.

## Mandatory reporting fields

Any future evidence-retrieval metric must state:

1. who created the gold and whether annotation was independent of the system;
2. the exact gold artifact/snapshot and content hash;
3. the evaluated numerator and denominator;
4. excluded cases and reasons;
5. whether the signal is primary, weak/silver, or diagnostic only;
6. split name and proof that no test labels influenced model or threshold
   selection.

If any item is missing, the number must not be presented as evidence retrieval
performance.

## Prohibited circular evaluations

- Do not score a retriever against evidence IDs produced by that retriever,
  the deterministic extractor, field-name matching, or answer-string search.
- Do not treat a chunk as relevant merely because it contains the gold answer
  string. This is particularly invalid for negative and unknown answers.
- Do not infer human evidence annotation from the README phrase “supporting
  information”; the released table contains no structured supporting link.
- Do not use downstream answer accuracy as a renamed retrieval metric.

## What would unlock primary evidence metrics

Primary real-patient Evidence Recall@k, MRR, and nDCG require a new restricted
gold artifact created without access to model rankings. At minimum it must bind
each patient–question pair to reviewed evidence IDs or immutable source spans,
record two independent annotations plus adjudication, define whether one or all
supporting spans are required, freeze coverage/exclusions, and remain isolated
from training and retrieval tuning. Until then, P3 retrieval conclusions stay
limited to reproducibility, resource/exposure measurements, synthetic ranking
mechanics, and downstream answer diagnostics.
