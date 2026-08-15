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
