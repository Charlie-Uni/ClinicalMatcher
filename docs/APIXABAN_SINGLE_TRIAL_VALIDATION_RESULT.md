# Apixaban single-trial validation result

Status: frozen disclosure-reviewed qualitative result; exact small-cell
aggregates remain owner-only

Evaluation protocol:
`apixaban-single-trial-evaluation-1.0.0`

Scoring contract: `apixaban-intended-rule-contract-1.0.0`

Model run contract:
`apixaban-single-trial-long-context-p4.3-validation-v1`

## Scope

The one predeclared validation execution completed with the owner-selected
long-context P4.3 abstention projection. Model selection used only the earlier
fact-level P2.3 comparison, before any P4.7 final-class result was viewed. The
structured alternative was not evaluated, and locked test was not accessed.

The three mandatory axes were generated from one immutable result object:

1. intended(gold facts) versus the mentor-designated legacy rule-derived
   reference;
2. intended(model facts) versus intended(gold facts); and
3. intended(model facts) versus the mentor-designated reference.

This is a rule-reference and error-propagation diagnostic. It is not clinical
eligibility accuracy, and the mentor reference is not independent clinical
gold.

## Disclosure boundary

The validation population is too small to publish exact class cells under the
repository's pending P1.3 disclosure policy. P1.3 requires a
governance-approved threshold and a non-sensitive approval reference before
small-cell aggregates can be released. No such approval is claimed here.

Accordingly, this public note omits all final-class counts, confusion-matrix
cells, percentages, confidence intervals, per-rule values, and per-question
unit-diagnostic values. The complete owner-only review summary remains local.
Its SHA-256 is
`37a3d7fb2429b834844fb68b794f442035aa6a05b4423c1a69dbea68c8559acc`.

## Qualitative finding

The frozen validation result exposed final-class concentration rather than a
useful three-class performance signal. This is not presented as model success
or as a clinical-performance failure rate.

The mechanism must be described precisely. Under contract `1.0.0`, Rules 1--4
are hard: any explicit `INELIGIBLE` decision among them projects to
`non-ideal`. Only a patient for whom all four are `ELIGIBLE` reaches Rule 5,
which distinguishes `ideal` from `semi-ideal`. When there is no explicit hard
failure but a required distinction remains unresolved, Kleene three-valued
logic preserves `UNKNOWN` rather than treating missing information as passing.
This combination makes the final projection highly sensitive to a single hard
failure while keeping genuine unresolved cases visible.

The observed concentration therefore cannot be attributed solely to missing
facts or UNKNOWN propagation. The pre-validation freeze review had already
identified fact-target adequacy as an open limitation: some official fact
questions may not fully represent the intended rule atoms. That limitation may
affect determinability, but the current diagnostic does not establish a causal
contribution.

Some legacy materials use missing-as-passing behavior. Such behavior can reduce
UNKNOWN by construction, but it is neither a safer semantic choice nor evidence
of greater correctness. No legacy-compatibility counterfactual was run, and the
missing legacy generator prevents a reproduction claim.

## What the experiment validated

The diagnostic produced the distinctions it was designed to expose:

- Axis A kept disagreement with the legacy rule-derived reference separate
  from model-fact errors.
- Axis B measured propagation from model facts under one unchanged intended
  evaluator.
- Axis C retained the combined project-reference view without being promoted
  to clinical accuracy.
- Criterion-level and UNKNOWN diagnostics remained visible owner-side, so a
  concentrated final class could not hide rule-level disagreement or
  abstention behavior.

The useful outcome is therefore a validated evaluation design and an identified
task/label limitation, not a headline final-class score.

## Post-observation semantic lock

Validation has now been observed. Contract `1.0.0` and this result are
immutable. No change intended to reduce class concentration may be folded back
into this run, including relaxing UNKNOWN handling, removing required atoms,
weakening hard rules, changing thresholds, or changing final-class projection.

Any alternative semantics require all of the following:

1. a new versioned and hash-bound contract;
2. an explicit `post-observation exploratory` label;
3. a new recorded owner decision and separate result artifact;
4. preservation of this frozen result without replacement or reinterpretation;
   and
5. no use of locked-test labels for design or selection.

This rule also applies to any future legacy-compatible permissive mode.

