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

## Frozen validation results

Implementation commit `5284dec77ba8feb51d853621f7f70c587ad2dd71` was
applied to the same two frozen 15-patient validation outputs. Projection and
report files remain owner-only outside Git.

| Output | Coverage before | Coverage after | Risk before | Risk after | Decision changes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Structured prefix | 0.744928 | 0.742029 | 0.357977 | 0.359375 | 1 |
| Matched long context | 0.794203 | 0.785507 | 0.321168 | 0.324723 | 3 |

For structured prefix, the projection/report file SHA-256 values are
`8fa9aa6ce9d379c71fc594981a8d20b1aec04b09e8ef8b0a955f28d7b518cc25`
and `ae8a5ace13e999353a713f1509d115b503c2b7fc177f2b03a4f977dfb6365a0d`.
For matched long context, they are
`ccdd1e417253ece9b9a78d0975dfd0a116716b8c09bd673772b3806295c36ef0`
and `8df83ea01ce0c800afbc85cab6156b42d2ba5e3c6810fc51ce63d21aeefe7de6`.
All four files have mode `0600`.

The policy converted the 1 and 3 evidence-free known facts identified by the
readiness audit to `missing_evidence`; it normalized the existing 88 and 71
unknown rows to `missing_fact`. No invalid-schema, unusable-evidence, or unit
failure was present. Verifier conflict input remained `not_evaluable`, not an
evaluated zero.

Typed exact-match error counts among answered facts did not fall (92 and 88).
The removed evidence-free predictions happened to agree with fact gold, so
coverage fell and selective risk rose slightly. This is an evidence-safety
baseline, not an accuracy or calibration improvement. The P4 phase-level claim
that abstention reduces risk is therefore not yet satisfied.
