# Patient-trial timing pilot and annotation protocol

Status: executable pilot protocol `1.0.0`; the real restricted-data pilot has
not yet been run.

The pilot estimates the person-time needed for a defensible multi-trial gold
set. It is not itself a benchmark result. Real patient records, unit manifests,
independent annotations, rationales, evidence links, and adjudication records
remain inside the authorized environment.

## Unit and sampling

One pilot unit is one complete `patient × trial` bundle:

- one trial-level `eligible / ineligible / unknown` decision and relevance
  grade;
- one decision for every trial criterion;
- the exact evidence IDs supporting each criterion decision; and
- an active annotation-time measurement.

Use at least 6–10 units across at least two trials for the first real timing
pilot. Preselect a mixture of likely eligible, likely ineligible, and uncertain
cases using a documented, model-blind rule. Do not inspect system predictions
when choosing cases. The small pilot estimates workflow cost and disagreement;
it does not establish clinical performance or statistical power.

## Independent annotation

Exactly two assigned annotators receive separate templates. Before marking a
file `completed`, each must attest that they did not view:

1. the peer annotation; or
2. any model prediction or retrieved model output for the unit.

The validator verifies separate identities, complete unit/criterion coverage,
evidence-ID containment, and attestations. It cannot prove that humans actually
worked independently; access controls and operating procedure must enforce
that condition.

Annotators review criterion judgments before assigning the trial judgment:

- `eligible`: available evidence supports satisfaction of the criterion;
- `ineligible`: available evidence supports failure of the criterion;
- `unknown`: evidence is absent, temporally invalid, ambiguous, or
  incompatible, so the criterion cannot be resolved safely.

Use relevance grades `0–3` according to [SCHEMA.md](SCHEMA.md). Record only
active working minutes; exclude breaks and unrelated work. Evidence IDs must
come from the patient bundle listed in the frozen manifest. Rationale text and
evidence links are restricted derivatives even when direct identifiers are
absent.

## Adjudication

Only after both independent files are completed may the adjudicator open them
together. The generated template identifies disagreements in trial decision,
trial relevance, criterion decision, and criterion evidence.

Every disputed unit must be marked `resolved`, with a final judgment, rationale,
and positive `active_person_minutes`. This value is total person-minutes: a
10-minute meeting attended by two people costs 20 person-minutes. A
non-disputed unit is locked to `agreed_without_dispute`; adjudication cannot
silently alter an agreed label.

Any unresolved unit blocks the aggregate summary and capacity plan.

## Restricted local workflow

Create a manifest specification whose source policy is `restricted_local`.
Inputs and outputs inside the repository must live under an ignored directory
such as `private_data/` or `artifacts/`.

```bash
clinical-matcher-pilot finalize-manifest \
  --input private_data/pilot/manifest-spec.json \
  --output private_data/pilot/manifest.json

clinical-matcher-pilot templates \
  --manifest private_data/pilot/manifest.json \
  --output-dir private_data/pilot/independent

clinical-matcher-pilot adjudication-template \
  --manifest private_data/pilot/manifest.json \
  --annotation private_data/pilot/independent/annotation-1.json \
  --annotation private_data/pilot/independent/annotation-2.json \
  --adjudicator-id <PSEUDONYMOUS_ID> \
  --output private_data/pilot/adjudication.json

clinical-matcher-pilot summarize \
  --manifest private_data/pilot/manifest.json \
  --annotation private_data/pilot/independent/annotation-1.json \
  --annotation private_data/pilot/independent/annotation-2.json \
  --adjudication private_data/pilot/adjudication.json \
  --output artifacts/benchmark/pilot-summary.json
```

The summary omits patient, trial, criterion, evidence, and annotator IDs and all
clinical text. It contains counts, annotation-time percentiles, total
adjudication person-time, disagreement/agreement measures, code commit, and a
content hash. It is still a restricted-data derivative until governance
approves disclosure.

The capacity planner accepts that validated summary directly:

```bash
clinical-matcher-plan-capacity \
  --pilot-summary artifacts/benchmark/pilot-summary.json \
  --hours-per-annotator <HOURS_EACH> \
  --reserve-fraction 0.2 \
  --minimum-trials 2 \
  --maximum-trials <MAX_TRIALS_TO_CONSIDER> \
  --minimum-patients-per-trial <MIN_PATIENTS> \
  --selected-trial-count <REVIEWED_TRIAL_COUNT> \
  --output artifacts/benchmark/af-capacity-plan.json
```

Manual timing values remain useful for planning but cannot authorize a trial
snapshot. Only a hash-bound summary derived from completed records can set
`snapshot_design_allowed=true`.

## Synthetic verification

The public synthetic bundle in `fixtures/synthetic/pilot/` deliberately
contains one disagreement so CI exercises resolution, aggregation, and capacity
binding. It demonstrates file structure only and must not be interpreted as a
real staffing estimate.
