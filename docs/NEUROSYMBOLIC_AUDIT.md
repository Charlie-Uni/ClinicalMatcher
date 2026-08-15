# Neuro-symbolic real-output audit boundary

The P4.2 readiness command audits restricted Apixaban prediction sets without
emitting patient IDs, evidence IDs, text, or row-level decisions:

```bash
clinical-matcher-apixaban-neurosymbolic-audit \
  --predictions /restricted/runs/model/predictions.json \
  --staging-corpus /restricted/apixaban-staging-corpus.json \
  --frozen-split /restricted/apixaban-split.frozen.json \
  --output /restricted/reports/model-neurosymbolic-readiness.json \
  --acknowledge-restricted-data
```

The command verifies owner-only inputs, frozen-split lineage, exact validation
membership, complete patient-question coverage, numeric output shape, the
catalog unit contract, patient-local evidence references, and known-fact
evidence missingness. Output is owner-only and overwrite is refused.

The source benchmark cannot currently support a full eligibility audit:

- every staging `index_date` is explicitly unavailable and predictions contain
  no observation date;
- predictions contain no claim-level negation trace;
- every catalog question is a fact-only target with no direct eligibility
  mapping;
- models emit fact assessments, not criterion-level eligibility decisions.

Consequently time, negation, criterion polarity, conflict rate, and before/after
error analysis are recorded as `not_evaluable`. A null conflict rate does not
mean zero conflicts. Completing P4.2 requires a reviewed real criterion binding
and corresponding temporal/model-decision fields; these must not be inferred
from the Apixaban fact labels.

## Validation readiness results

Implementation commit `9fca66b505567670183e2b6f1a42d2283652ea08` was run
against both frozen Llama validation prediction sets (15 patients × 23
questions = 345 rows per run). The owner-only reports remain outside Git.

| Validation output | Numeric type failures | Unit-contract failures | Evidence-link failures | Known facts without evidence | Review required |
| --- | ---: | ---: | ---: | ---: | ---: |
| Structured prefix | 0 | 0 | 0 | 1 | 1 |
| Matched long context | 0 | 0 | 0 | 3 | 3 |

The structured report SHA-256 is
`0d0d166ea57e7e1aa02519df817d0f817a77d3515a631a9a5d8c1f6044b0a208`;
the long-context report SHA-256 is
`5431c3b2bca67a17b247898d7028ef83c3cb8fe9a676a205cd9329b0d966d40d`.
Both files were written with mode `0600`.

Every known fact without evidence was an `absent` result for the frozen
`med_decisions` question. The structured-output contract intentionally permits
that historical source-defined default without evidence. P4.1's strict adapter
does not permit it to enter the typed verifier, so the readiness audit correctly
marks these rows for review. P4.3 must decide explicitly whether to preserve a
dedicated default reason or abstain; this result does not silently choose either
policy.

All 345 rows in each run remain unevaluable for time, claim-level negation,
criterion polarity, and model–verifier conflict. Conflict rate is therefore
`null`, not zero, and P4.2 remains incomplete.
