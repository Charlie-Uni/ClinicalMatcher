# Observable error attribution

## Scope

`clinical-matcher-apixaban-error-attribution` produces an owner-only,
aggregate diagnostic report for one frozen Apixaban prediction set. It does
not emit patient IDs, question IDs, evidence IDs, note text, or representative
cases.

The report uses one fixed precedence so each row contributes to at most one
error category:

1. `unsupported_answering`: a known fact has no citation or cites evidence
   outside that patient's frozen evidence inventory;
2. `unit_contract_error`: the output unit differs from the frozen source
   contract;
3. `abstention_on_gold_known`: the output is unknown while the released fact
   label is known;
4. `numeric_value_error`: numeric status is present on both sides but values
   differ under the frozen exact-source policy;
5. `fact_status_error_with_patient_local_citation`: fact status differs and
   the known prediction has a patient-local citation;
6. `other_typed_error`: a remaining typed mismatch.

The error universe is the union of typed gold mismatches and known-answer
evidence/unit contract violations. Consequently, a prediction that happens to
match the fact label but answers without usable evidence remains an attributed
safety error. Category totals plus rows with no attributed error must equal
the complete split grid.

## Causal boundary

The official benchmark declares `gold_evidence_status` as
`not_available_in_source`. A patient-local citation is therefore not an
independently adjudicated relevant citation. The following requested
dimensions remain explicit `not_evaluable` values rather than zero counts:

- retrieval failure;
- reasoning failure with usable evidence;
- time error;
- negation error;
- false abstention.

`abstention_on_gold_known` is an observable proxy only. A known source label
does not prove that the evidence required by the model was present, retrieved,
or usable. Numeric differences and units are evaluated only against the
source-exact contract; they are not clinical-equivalence judgments.

## Restricted execution

Run only inside the authorized local environment. Every input and the output
must be owner-only. Locked-test use requires a second explicit acknowledgement
and remains prohibited until model selection closes.

```bash
clinical-matcher-apixaban-error-attribution \
  --predictions /restricted/predictions.json \
  --benchmark /restricted/apixaban-benchmark.json \
  --staging-corpus /restricted/apixaban-staging-corpus.json \
  --frozen-split /restricted/apixaban-split-frozen.json \
  --output /restricted/error-attribution.json \
  --acknowledge-restricted-data
```

The command validates the complete patient-question grid and exact benchmark,
staging-corpus, split-manifest, catalog, and prediction lineage before writing
with mode `0600`. It refuses to overwrite an existing output.

## Completion boundary

The aggregate implementation is not P4.5 completion by itself. Representative
errors must still be reviewed inside the authorized environment, and the
requested causal categories require adjudicated evidence relevance plus
temporal and negation trace fields. No restricted example may be copied into
the public repository as proof of review.
