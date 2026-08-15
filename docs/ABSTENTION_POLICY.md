# Deterministic abstention policy

Policy `1.0.0` creates a separate restricted prediction set. It never edits the
source model output. The projection is bound to the source prediction hash,
frozen split, staging corpus, question catalog, code commit, and policy hash.

Reasons are mutually exclusive under this fixed precedence:

1. `invalid_schema`: the local model runner already converted an invalid raw
   structured response to an explicit unknown row;
2. `unusable_evidence`: a cited evidence ID is outside the patient-local
   evidence inventory;
3. `missing_evidence`: a known fact cites no evidence;
4. `incompatible_unit`: the declared unit differs from the frozen catalog;
5. `verifier_conflict`: an evaluated criterion-level conflict was supplied;
6. `missing_fact`: the source prediction was already unknown.

Unknown has no assigned probability and is not folded into an eligibility
score. Existing unknown rows receive a normalized reason and trace, but this is
reported separately from a known-to-unknown decision change. When verifier
conflicts cannot be evaluated, the report records `not_evaluable`; it does not
assume an empty conflict set.

The restricted validation command is:

```bash
clinical-matcher-apixaban-abstention \
  --predictions /restricted/runs/model/predictions.json \
  --benchmark /restricted/apixaban-fact-benchmark.json \
  --staging-corpus /restricted/apixaban-staging-corpus.json \
  --frozen-split /restricted/apixaban-split.frozen.json \
  --projection-output /restricted/runs/model/abstained-predictions.json \
  --report-output /restricted/reports/model/abstention-report.json \
  --acknowledge-restricted-data
```

Both outputs are owner-only and overwrite is refused. Test use requires a
separate acknowledgement.

Because current models expose no probability, the report contains two
deterministic coverage–risk operating points: before policy and after policy.
Risk is typed exact-match error among answered note-grounded facts. These points
are not a calibrated curve or a clinical eligibility result.
