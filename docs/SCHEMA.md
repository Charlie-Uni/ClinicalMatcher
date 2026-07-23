# Schema decision record

Status: frozen baseline
Version: `1.0.0`

The executable wire-format contract is
[`src/clinical_matcher/schemas/clinicalmatcher-1.0.0.schema.json`](../src/clinical_matcher/schemas/clinicalmatcher-1.0.0.schema.json).
The JSON Schema validates document shape and primitive constraints. The Python
loader additionally validates relationships that JSON Schema cannot express,
including referenced evidence IDs, source-span bounds, unique IDs, and complete
gold coverage. Documents without an exact supported `schema_version` are
rejected; migration is never implicit.

## Prediction units

The auditable prediction unit is:

```text
patient_id × trial_id × criterion_id
```

A trial-level result is an aggregation of criterion decisions. It never replaces
them.

## Criterion expressions

A protocol `Criterion` preserves whether it is an inclusion or exclusion rule
and owns a restricted expression tree:

- `ATOM`: one typed field comparison;
- `ALL`: logical AND across one or more children;
- `ANY`: logical OR across one or more children;
- `NOT`: logical negation of exactly one child.

This keeps leaf checks deterministic while representing real compound rules.
Ranges are expressed as `ALL` over lower and upper bound atoms. A bounded time
window can be attached to an atom. Arbitrary Python, free-form formulas, and
implicit unit conversion are not allowed.

Query decomposition must output this structure and retain a mapping from every
atom to the source criterion text.

## Decomposition provenance

Every criterion stores its immutable source ID, verbatim source text,
inclusion/exclusion section, and protocol document version. Every atom stores:

- the matching criterion source ID;
- a zero-based, end-exclusive character span into the source text;
- the decomposition method: `human`, `rule`, or `llm`;
- for `llm`, the model ID and prompt version.

The loader rejects source-ID mismatches and spans beyond the source text.
Human/rule records cannot contain model metadata, while LLM records must
contain it. The provenance identifies how a decomposition was produced; it
does not certify clinical correctness.

## Typed facts and units

Every `Fact` has:

- a stable fact ID and normalized field name;
- a `TypedValue` (`number`, `boolean`, `string`, or `date`);
- an explicit unit for dimensional numeric values;
- zero or one observation date;
- explicit evidence IDs.

Every atom also declares how repeated facts are selected:

- `ANY`: existential match, appropriate for events such as any recent bleed;
- `ALL`: every compatible in-window fact must satisfy the comparison;
- `LATEST`: evaluate only the most recent dated fact.

There is no implicit default in the wire format.

Facts dated after `index_date` are excluded by default, even when an atom has no
explicit time window. Future facts are available only through an explicit
future-directed window. This prevents post-enrollment information leakage.

Numeric comparisons are allowed only when units match exactly. A mismatch
returns `UNKNOWN`; it is never guessed or silently converted. A reviewed unit
normalization layer can be added later before evaluation.

Runtime validation checks that each value matches its declared type. Ordering
comparisons are limited to numbers and dates; booleans and strings accept only
equality/inequality. Fact and evidence IDs are unique within a patient, and
every fact must link to existing evidence.

Repeated facts are allowed. Resolution follows the atom's explicit selection
policy and becomes `UNKNOWN` when no usable fact exists or type/unit
incompatibility prevents a safe decision.

## Three-valued logic and criterion polarity

Expression evaluation uses three-valued logic:

- `ALL`: any false → false; all true → true; otherwise unknown;
- `ANY`: any true → true; all false → false; otherwise unknown;
- `NOT`: true/false invert; unknown stays unknown.

Atomic traces under `NOT` retain their factual evidence but are explicitly
marked `negated`, and their reason states that polarity was inverted. Evidence
therefore remains provenance for the observed fact without being presented as
an unqualified explanation of the parent decision.

The expression truth value is then mapped through criterion polarity:

- inclusion true → eligible; inclusion false → ineligible;
- exclusion true → ineligible; exclusion false → eligible;
- unknown always stays unknown.

## Hard and soft criteria

- A hard ineligible criterion makes the whole trial `INELIGIBLE`.
- An unresolved hard criterion makes the trial `UNKNOWN` unless another hard
  criterion already excludes it.
- A soft ineligible criterion lowers the eligibility score but does not hard
  exclude the trial.
- A soft unknown criterion lowers coverage but does not force trial-level
  abstention when all hard criteria are resolved.

`weight` affects scoring and coverage only. It does not change hard/soft
semantics.

## Score, coverage, and abstention

These are separate outputs:

- `decision`: eligible, ineligible, or unknown under hard-rule semantics;
- `eligibility_score`: weighted eligible fraction among resolved criteria;
- `coverage`: resolved criterion weight divided by total criterion weight;
- `atomic_coverage`: resolved atoms divided by all evaluated atoms;
- `abstained`: whether the trial decision is unknown;
- `abstention_reasons`: unresolved hard criteria or absence of usable facts.
- `data_quality_issues`: incompatible units/types, missing timestamps, excluded
  future facts, and other unresolved atomic branches.

Unknown is not assigned a score of 0.5. If no criterion is resolved,
`eligibility_score` is `null` and coverage is zero.

Ranking uses, in order:

1. decision class: eligible, then unknown, then ineligible;
2. eligibility score;
3. coverage;
4. atomic coverage;
5. stable trial ID tie-break.

This is an explicit baseline policy, not a clinically validated utility
function. P1 evaluation must test alternative aggregation and calibration.

An `ANY` expression may resolve true while another branch remains unknown.
The criterion is not forced to abstain because the true branch is logically
sufficient, but the unresolved branch remains visible through atomic coverage
and data-quality issues.

Whether to expose an additional calibrated confidence such as
`f(eligibility_score, coverage, atomic_coverage, model uncertainty)` is an open
P1 decision. The raw eligibility score must remain available and must not
silently absorb missingness.

## Independent gold

Gold labels are stored separately from pipeline outputs:

- criterion decision and supporting evidence IDs for each
  patient–trial–criterion tuple;
- trial eligibility decision and graded relevance for each patient–trial pair.

Retrieval metrics use the independently authored gold evidence IDs, never the
evidence links produced by the evaluator. Ranking metrics use graded gold
relevance, never scores derived from the ranking function.

Each gold item contains at least two annotations with unique annotator IDs,
followed by a separate adjudication record. Criterion annotations record a
three-way decision, supporting evidence IDs, and a rationale. Trial annotations
record a decision, relevance grade, and rationale. Evaluation reads only the
adjudicated result while preserving disagreements for later agreement analysis.

Trial relevance uses an ordinal research ranking label:

- `0`: contraindicated or not a viable match;
- `1`: weak match, with major unresolved or limiting criteria;
- `2`: plausible match, with only limited uncertainty or soft limitations;
- `3`: strong match, with the relevant criteria supported.

The grade is not a probability, enrollment decision, or substitute for
criterion-level labels. Clinical data must use a reviewed annotation manual
with examples and explicit adjudication rules before these labels are treated
as benchmark gold.

The current fixture is independently authored synthetic test data. Its two
synthetic annotations exercise the data contract; they do not constitute a
clinically adjudicated benchmark.

## Deferred before production use

- terminology and unit conversion registry;
- temporal interval and data-coverage semantics beyond index-date windows;
- calibrated probabilities and abstention thresholds;
- clinically reviewed aggregation policy.
