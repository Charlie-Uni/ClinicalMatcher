# Capacity-bound benchmark design

Status: P2.2 planning interface; no clinical gold has been declared ready.

ClinicalMatcher chooses benchmark size from the patient-trial gold workload,
not from the number of public trials the registry can return.

## Annotation unit

One unit is one complete `patient × trial` annotation bundle. It includes the
trial-level eligibility/relevance judgment plus every criterion decision,
supporting evidence span, and unknown/missing-evidence decision required by the
frozen schema. Counting a single criterion as one unit would materially
underestimate workload.

Every unit requires two independent annotations followed by adjudication of
disagreements. Patient records and row-level annotations remain in the
authorized environment.

## Pilot before scale

Run a small, representative pilot before choosing trial or patient counts:

1. Preselect pilot units across more than one trial and across easy, uncertain,
   and likely-ineligible patients without inspecting model predictions.
2. Have both annotators label the same units independently.
3. Record active person-minutes per annotation, disagreement rate, total
   adjudication person-minutes, unresolved cases, and criterion counts.
4. Use a conservative annotation-time estimate, such as the observed 75th
   percentile, rather than the fastest or mean case.
5. Re-run the capacity plan with `estimate_source=pilot_measurement`.

`minutes_per_adjudication` means total person-minutes. A ten-minute meeting
attended by two annotators costs twenty person-minutes.

## Capacity equation

The planner computes:

```text
gross person-minutes
  = annotator_count × hours_per_annotator × 60

usable person-minutes
  = gross person-minutes × (1 - reserve_fraction)

expected person-minutes per patient-trial unit
  = required_annotations × minutes_per_annotation
    + expected_adjudication_rate × adjudication_person_minutes

maximum patient-trial units
  = floor(usable / expected unit person-minutes)
```

It then reports every feasible `trial_count × patient_count` rectangle inside
that capacity. The tool does not silently choose between broader trial
coverage and more patients per trial.

```bash
clinical-matcher-plan-capacity \
  --annotator-count 2 \
  --hours-per-annotator <HOURS_EACH> \
  --minutes-per-annotation <PILOT_P75_MINUTES> \
  --expected-adjudication-rate <PILOT_DISAGREEMENT_RATE> \
  --minutes-per-adjudication <TOTAL_PERSON_MINUTES> \
  --reserve-fraction 0.2 \
  --estimate-source pilot_measurement \
  --pilot-unit-count <COMPLETED_PILOT_UNITS> \
  --minimum-trials 2 \
  --maximum-trials <MAX_TRIALS_TO_CONSIDER> \
  --minimum-patients-per-trial <MIN_PATIENTS> \
  --selected-trial-count <REVIEWED_TRIAL_COUNT> \
  --output artifacts/benchmark/af-capacity-plan.json
```

A planning assumption produces `status=provisional` and cannot authorize a
snapshot. Only a pilot-derived plan with an explicitly selected feasible design
sets `snapshot_design_allowed=true`.

## Deterministic trial selection

The selected trial count comes directly from the capacity plan. The registry
candidate set is then:

1. queried by the frozen condition and recruitment statuses;
2. fetched completely, with reported total count equal to fetched count;
3. filtered by frozen study type, recruitment status, eligibility-text
   presence, and inclusive first-posted date range;
4. sorted by `SHA256(method_version | capacity_plan_sha256 | NCT_ID)`; and
5. truncated to the capacity-bound trial count.

No registry `sort` parameter is used. `LastUpdatePostDate` and registry response
order never determine inclusion.
The sampling seed is derived from the capacity-plan hash rather than chosen by
an operator, reducing seed-shopping risk.

The snapshot retains an audit row for every registry hit with selection-relevant
metadata, source hash, all filter exclusion reasons, sampling hash, selected
flag, and inclusion/non-inclusion reason. Its flow records:

```text
registry total
  -> completely fetched
  -> passed explicit filters
  -> eligible but outside capacity-bound hash sample
  -> finally selected
```

## What capacity does not prove

Capacity is an operational upper bound, not a statistical power calculation.
Before release, the selected grid still needs a precision/uncertainty review,
patient-selection policy, class-balance report, independent annotation-record
validator, and governance approval. If the feasible grid is too small for
credible evaluation, the correct action is to increase labeling capacity or
narrow the research claim—not to label the small result a benchmark.
