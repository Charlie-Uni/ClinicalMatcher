# Apixaban single-trial evaluation protocol

Status: frozen metric design before validation; unit adapter and evaluator
implemented; the owner selected the one model-prediction artifact and real
validation has not yet run

Protocol version: `apixaban-single-trial-evaluation-1.0.0`

Decision date: 2026-08-31

## Scope and source boundary

This protocol evaluates the owner-selected intended five-rule diagnostic. It
does not establish clinical eligibility accuracy. The official Apixaban release
provides human-reviewed patient-question facts; the supplied screening result
is a mentor-designated, rule-derived project reference with a missing generator.
The locked test remains unexposed.

The owner decided not to send
`docs/APIXABAN_CRITERIA_REVIEW_CHECKLIST_ZH.md` to the mentor. The reason recorded
for this project decision is that remaining semantic questions will be put to
the owner directly. Consequently, every future report must retain this wording:
the intended contract was **specified by the owner through a source-precedence
rule and was not confirmed through item-by-item qualified clinical review**.

## Later mentor-response policy

This rule is frozen before validation even though no mentor response is
currently being requested:

1. If a hashable mentor response arrives before validation and conflicts with
   the intended contract, validation remains blocked. The response is added to
   provenance, the scoring contract receives a new version and hash, and all
   schema/semantic/synthetic checks are rerun before one validation execution.
2. If a conflicting mentor response arrives after validation, the existing
   contract and result remain immutable. A discrepancy note records the new
   source and affected rules. Any corrected rerun requires a new contract and
   report version plus explicit owner approval; it cannot overwrite or silently
   reinterpret the first result.
3. A post-validation correction driven solely by an external source is reported
   as protocol correction, not result-driven tuning. Both validation runs and
   their reason remain disclosed. Locked-test labels remain unavailable for all
   such decisions.

## Mandatory three-axis report

Every model-fact evaluation report must present the following three axes on the
same page and with the same frozen validation membership. No axis may be shown
alone as the project headline.

### Axis A: intended(gold facts) versus mentor reference

This is the **observed reference discrepancy**. It quantifies how the intended
contract projects the released human-reviewed facts relative to the supplied
three-class reference.

It is not called pure semantic distance. The missing reference generator means
the discrepancy may also contain unknown preprocessing, fact aggregation,
missing-value, or mapping differences.

### Axis B: intended(model facts) versus intended(gold facts)

This is the primary end-to-end pipeline-quality axis. Both sides use the same
intended evaluator and differ only in whether the input facts are model outputs
or released fact labels. It measures observed fact-error propagation under the
frozen adapter and abstention policy; it is not a causal decomposition of model
reasoning errors.

### Axis C: intended(model facts) versus mentor reference

This is the combined mentor-designated project-reference result. It mixes model
fact errors with the Axis A discrepancy and is interpretable only beside Axes A
and B.

## Frozen denominators and metrics

- The patient denominator is the complete frozen validation membership and
  never excludes abstentions or missing predictions.
- Intended projections use four outcomes: `ideal`, `semi-ideal`, `non-ideal`,
  and `unknown`. The mentor reference has the three non-unknown classes.
- Every axis reports the full confusion matrix, total patients, intended-known
  count, intended-unknown count, coverage, and exact agreement on the complete
  denominator.
- Axes A and C may additionally report conditional three-class agreement among
  intended-known patients, but it must be labelled conditional and shown beside
  coverage.
- Axis B reports patient-level four-outcome agreement and per-rule
  eligible/ineligible/unknown agreement so that final-class cancellation cannot
  hide criterion errors.
- Any confidence interval resamples at patient level. No criterion-row bootstrap
  is permitted.
- Machine-readable output binds the split hash, scoring-contract hash,
  unit-adapter hash, fact-source artifact hashes, code commit, and report hash.
  Human-readable output is generated from the same object.

## Rule-version sensitivity is diagnostic, not causal attribution

The available `legacy/apixaban/apixaban_processing.py` is not the named missing
generator. It may be used only after issuing a separate, hash-bound
`legacy-candidate` contract. At minimum that candidate differs from the intended
contract in Rule 1 connective and exclusions, Rule 2 thresholds and missing
handling, Rule 3 bilirubin threshold, and Rule 4 polarity.

If sensitivity analysis is run, it must report:

1. one-at-a-time counterfactual substitutions from intended semantics;
2. the complete legacy-candidate substitution;
3. overlap counts among changed-patient sets; and
4. an explicit statement that non-exclusive counterfactual counts are not
   additive and do not identify how `screening_results.json` was generated.

The phrases `legacy defect contribution`, `caused by`, and `generator
reproduction` are prohibited unless the missing generator is recovered and a
clean regeneration match is demonstrated.

## Execution and publication gate

Before validation, the owner must approve the exact scoring contract and the
unit-adapter contract. The evaluator, report schema, and synthetic reconciliation
tests must then pass without accessing validation labels. Validation produces
owner-only row-level artifacts and disclosure-reviewed aggregates. No patient
identifier, row-level fact, note text, or rare-class aggregate enters Git.

The owner approved unit-adapter contract `1.0.0` on 2026-08-31. It is stored as
`src/clinical_matcher/resources/apixaban-unit-adapter-contract-1.0.0.json` and
binds the intended scoring contract without mutating its historical
pre-validation hash. Its range checks remain extreme-error guards rather than
unit proof: for example, hemoglobin around `6.2 mmol/L` or some glucose values
in `mmol/L` can fall inside the assumed-unit range and remain undetected. Every
runtime report must show out-of-range counts and rates separately for all eight
numeric questions.

## Frozen model-artifact selection

Before any P4.7 three-class result was viewed, the owner selected the
long-context P4.3 abstention projection for the one validation execution. The
selection used only the already recorded P2.3 fact-level results: structured
had 187/345 typed matches and long-context had 211/345. The corresponding
pre-projection error-attribution report hashes and the exact selected
prediction artifact hash are frozen in
`apixaban-single-trial-run-contract-1.0.0.json`.

The structured P4.3 artifact remains available but is explicitly not evaluated
in this run. Any later descriptive comparison requires a new recorded owner
decision. The run contract authorizes validation only, records that locked-test
labels were not used, and requires a separate post-run disclosure review.

## Frozen validation outcome and semantic lock

The single authorized validation execution completed under contract `1.0.0`.
The disclosure-reviewed qualitative result is recorded in
`docs/APIXABAN_SINGLE_TRIAL_VALIDATION_RESULT.md`. Exact class cells, rates,
confusion matrices, confidence intervals, per-rule values, and per-question
unit diagnostics remain owner-only because P1.3 has no governance-approved
small-cell threshold or non-sensitive approval reference.

The result showed final-class concentration. Its mechanism is not described as
UNKNOWN alone: an explicit hard failure in Rules 1--4 is an absorbing
`non-ideal` projection, while Kleene semantics preserves UNKNOWN only when no
hard failure is known and a required distinction remains unresolved. The
predeclared fact-target-adequacy limitation remains relevant but is not assigned
causal responsibility by this diagnostic.

Because validation has now been observed, contract `1.0.0` and its result are
immutable. Any later change intended to reduce concentration is a separately
versioned, hash-bound, `post-observation exploratory` contract. It must not
replace, relabel, or dilute this result, and it must not use locked-test labels.
