# Observable error attribution

## Scope

`clinical-matcher-apixaban-error-attribution` produces an owner-only,
aggregate diagnostic report for one frozen Apixaban prediction set. It does
not emit patient IDs, question IDs, evidence IDs, note text, or representative
cases.

The report uses one fixed precedence so each row contributes to at most one
error category:

1. `unsupported_answering`: a citation-required known fact has no citation or
   cites evidence outside that patient's frozen evidence inventory;
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
safety error. Contract `1.1.0` mirrors the P4.3 exception for
`med_decisions=absent, value=false`; that source-defined result is not an
unsupported answer merely because its citation list is empty. No other known
result is exempt. Category totals plus rows with no attributed error must
equal the complete split grid.

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

## Validation result

Historical contract `1.0.0` was run from implementation commit `347375b` on
both frozen 15-patient validation outputs and their separate P4.3 projections.
Each report reconciled all 345 patient-question rows. The aggregate results
were:

| Output | Typed mismatches | Unsupported | Gold-known abstention proxy | Cited status | Numeric value | Attributed total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Structured source | 158 | 1 | 66 | 76 | 16 | 159 |
| Structured after P4.3 | 159 | 0 | 67 | 76 | 16 | 159 |
| Long-context source | 134 | 3 | 46 | 75 | 13 | 137 |
| Long-context after P4.3 | 137 | 0 | 49 | 75 | 13 | 137 |

Unit-contract and remaining typed-error counts were zero in all four runs.
P4.3 removed all 1 and 3 unsupported known answers, respectively, but those
rows happened to match the released fact label and became abstentions on known
gold. The error union therefore stayed at 159/137. This is a safety-policy
trade-off, not a quality improvement. It also does not establish that the
long-context prompt is superior: evaluation used only 15 validation patients,
and evidence relevance is unlabelled.

The owner-only aggregate report SHA-256 values are:

- structured source: `b259098870081a7e3662eb8df9b404b9b5ff86bded5b0c2ae9da79378cfb50fb`;
- structured after P4.3: `e6fbd9a995576ac8cee6a28b127d93783ffd07836383f45ee4268be95a7aeb9f`;
- long-context source: `3a818785b3161ee9ae39bc05e905283340777479a7c7eb9c864e1eed9ba8d932`;
- long-context after P4.3: `64f01a98caeffbe647f77011b9d1991caf62d3318fb65b974ce361547bc5c718`.

No locked-test prediction was read or evaluated. Representative-case review
remains pending inside the authorized environment, so P4.5 remains open.
New reports use contract `1.1.0`; the table and hashes above remain historical
`1.0.0` artifacts and are not silently reinterpreted.
