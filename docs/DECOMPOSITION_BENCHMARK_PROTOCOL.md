# Public criteria-decomposition benchmark protocol

Status: **frozen by owner approval on 2026-08-29; implementation and synthetic
verification are authorized, but benchmark annotation remains subject to the
staffing gate in this protocol**

Protocol version: `decomposition-benchmark-protocol/1.0.0`

This protocol defines the public P5D benchmark before any decomposition model
output is generated or inspected. It covers ClinicalTrials.gov criterion text
only. It does not use MIMIC, patient records, patient-trial labels, or any other
restricted artifact.

## Prediction unit and source freeze

The prediction unit is one immutable ClinicalTrials.gov criterion text block to
one frozen-schema `ATOM/ALL/ANY/NOT` expression tree. Every selected unit must
bind:

- source snapshot manifest path and SHA-256;
- NCT ID, study version/update date, and normalized protocol SHA-256;
- complete eligibility-text SHA-256;
- criterion ID, inclusion/exclusion type, exact source text, and its global
  zero-based half-open span;
- split-specific frozen decomposition concept-catalog version and SHA-256.

The write-once selection manifest binds the source snapshot and both split
memberships, but it does not bind a not-yet-authored concept catalog. The
applicable catalog version and SHA-256 are instead required in each annotation
and gold artifact for that split. This preserves the leakage-safe catalog
timeline without rewriting selection history.

The source is a verified immutable public snapshot. Live API responses are
never annotation or evaluation inputs. Parser-skipped trials and criteria whose
source span does not reproduce the exact text are ineligible. The selection
manifest is written once without overwrite and records every inclusion and
exclusion reason.

## Concept catalog prerequisite

The frozen core schema permits a normalized `field` string but does not define a
closed clinical ontology. Exact atom matching would therefore confound semantic
decomposition with uncontrolled naming synonyms. Before any annotation, the
catalog-construction rules must be frozen. A separate versioned catalog is then
authored from each split's **source text only** and frozen by hash before that
split is annotated.

Each catalog contains only an allowed field ID, a plain-language definition,
and source-grounded aliases. It contains no criterion-specific operator, value,
unit, time window, polarity, tree, or annotation. Annotators and the model
receive the same split catalog. The dev catalog is frozen before dev annotation.
The test catalog is not authored until after the model/prompt configuration is
frozen; it is then frozen before test annotation and inference. No field alias
may be added after annotation or model output for that split is inspected. An
unseen concept stops the affected split and requires an owner-reviewed catalog
version plus fresh annotations and predictions for that split.

## Candidate sample size

The proposed benchmark contains exactly **80 criteria**, within the approved
60–100 range:

- 40 development criteria;
- 40 locked-test criteria;
- 40 inclusion and 40 exclusion criteria overall;
- no more than 8 selected criteria from one trial across the benchmark;
- at least 5 distinct trials in each split.

If the frozen snapshot cannot satisfy every quota, selection fails closed. It
does not query the live API, relax a quota, substitute a parser failure, or
inspect annotations to fill the gap. A larger source snapshot or changed quota
requires a separately approved protocol revision.

## Label-independent complexity proxy

Complexity is computed from the criterion source text before annotation. It is
not a clinical or decomposition label. Detection text is Unicode NFKC,
case-folded, and whitespace-collapsed; the original source text and spans are
never changed.

Each criterion receives the following integer score:

1. length points over the collapsed Unicode code-point count: 0 for `<=80`, 1
   for `81..160`, and 2 for `>160`;
2. `+1` when a digit, symbolic comparator, or one of `greater than`, `less
   than`, `at least`, `at most`, or `between` occurs;
3. `+1` when a connector from `and`, `or`, `either`, `both`, `any of`, or `all
   of` occurs as a word or exact phrase;
4. `+1` when a negation cue from `no`, `not`, `without`, `absence of`,
   `negative for`, or `free of` occurs as a word or exact phrase;
5. `+1` when a temporal cue occurs: `within`, `prior`, `previous`, `history
   of`, `currently`, or a digit followed by `day(s)`, `week(s)`, `month(s)`, or
   `year(s)`.

The mutually exclusive strata are:

- low: score 0–1;
- medium: score 2–3;
- high: score 4–6.

The exact regexes and Unicode handling must live in a versioned public resource
and be tested before selection. They cannot be changed after candidate counts
are visible without a new protocol version.

Each split has the same joint criterion-type/complexity quota:

| Criterion type | Low | Medium | High | Total |
| --- | ---: | ---: | ---: | ---: |
| Inclusion | 5 | 7 | 8 | 20 |
| Exclusion | 5 | 7 | 8 | 20 |
| Total | 10 | 14 | 16 | 40 |

## Deterministic selection and trial isolation

Selection algorithm `sha256_trial_isolated_decomposition_sample/1.0.0` uses
UTF-8 SHA-256 over NUL-joined tuples. Empty components and embedded NULs are
rejected.

1. Exact duplicate normalized criterion texts are grouped across trials. The
   duplicate-resolution digest is SHA-256 over algorithm version, source
   snapshot SHA-256, selection salt, NCT ID, and criterion ID. Retain only the
   member with the lowest duplicate-resolution digest and report all duplicate
   exclusions. This digest is independent of the not-yet-assigned split.
2. Trial digest tuple: algorithm version, source snapshot SHA-256, selection
   salt, and NCT ID. The salt is
   `clinicalmatcher-public-decomposition-benchmark-v1`.
3. Sort trials by digest and assign alternating trials to dev then test. A trial
   and all of its candidate criteria belong to exactly one split.
4. Criterion digest tuple: algorithm version, source snapshot SHA-256, selection
   salt, split, NCT ID, and criterion ID.
5. Process strata in this exact order: inclusion-low, inclusion-medium,
   inclusion-high, exclusion-low, exclusion-medium, exclusion-high. Within each
   split and stratum, take criteria by ascending digest while enforcing the
   benchmark-wide cap of eight per trial. There is no backtracking after a
   later stratum shortage.
6. Require every exact quota and at least five selected trials per split. Any
   shortage fails closed; there is no cross-stratum or cross-split spillover.

The selection implementation must recompute every digest, quota, duplicate
group, cap, and split assignment during verification. The manifest freezes both
split memberships before annotation begins.

## Annotation modes and current staffing

The approved standard mode is `dual_independent_with_adjudication`. The current
staffing state is one confirmed annotator (the owner) and no confirmed second
annotator. Therefore standard-mode annotation is blocked until a second person
qualifies; protocol, schema, selection, and evaluator implementation may proceed
without annotation.

### Annotator qualification

An annotator need not hold MIMIC access and need not be a licensed clinician,
because all inputs are public protocol text. The owner is the lead annotator
who freezes the guide, concept catalog, and practice key before benchmark
selection. The lead's relevant training and role are disclosed; the lead is not
given a meaningless blind score against a key they authored.

Before benchmark annotation, every additional annotator must:

- read the frozen annotation guide and concept catalog;
- complete the same eight public practice criteria without seeing the practice
  key or any model output;
- produce schema-valid trees for all eight;
- achieve atom F1 of at least 0.80 and normalized-tree exact match on at least
  six of eight against the pre-frozen lead-authored practice key. Atom F1 and
  normalized-tree exact match are computed by the same frozen P5D.3
  normalization and matching implementation used for IAA and model evaluation.

Practice items are disjoint by trial from benchmark dev and test. The lead may
explain errors only after the additional annotator's first attempt is locked.
One fresh retry on eight new, pre-keyed, trial-disjoint practice items is
permitted; failure on that retry means the person is not qualified for this
benchmark version. Qualification results are recorded, but practice items never
enter benchmark metrics.

### Dual-independent standard mode

Both qualified annotators independently annotate every selected item using the
schema validator and concept catalog. The validator may report structural or
type errors; it may not suggest atoms, operators, values, spans, or repairs. No
annotator may view model output or the other annotator's work before both files
are complete, attested, hash-bound, and locked.

After both independent files are locked, a generated disagreement package is
adjudicated by the two annotators through recorded consensus. An unresolved
item requires a third qualified adjudicator; without resolution, the gold
artifact cannot freeze and the item cannot be silently dropped or replaced.

### Predeclared single-annotator downgrade

If no second qualified annotator is available, the owner may approve a new
manifest with `annotation_mode=single_annotator` **before the first benchmark
annotation is created**. The artifact must be described as single-annotator
reference gold with limited external validity. It reports no IAA and cannot be
called dual-independent or adjudicated gold.

A later spot check does not upgrade that artifact. Upgrading requires a new
version in which a second qualified annotator independently annotates every
original source item without seeing the first annotation or model output,
followed by the full adjudication protocol.

## Leakage-safe dev/test timeline

The following order is mandatory:

1. freeze source snapshot, selection manifest, annotation guide, catalog-
   construction rules, normalization, matching, and metrics;
2. author and freeze the dev concept catalog from dev source text only;
3. implement and test the schema validator and evaluator using synthetic trees;
4. independently annotate and adjudicate **dev only**, then freeze dev gold;
5. develop the prompt and runtime configuration using dev only;
6. freeze the exact model manifest, Ollama version, prompt, output schema,
   decoding settings, and code commit;
7. author and freeze the test concept catalog from test source text, then
   independently annotate and adjudicate test without viewing model output;
8. freeze test gold and run locked-test inference and scoring exactly once;
9. publish the immutable prediction, gold, and report hashes. No prompt,
   evaluator, matching, threshold, or model change follows test exposure.

This order permits the owner to be an annotator without allowing test gold to
influence prompt or model selection. The owner must attest that test source
items and the test catalog were not used for prompt examples or configuration
decisions. Test gold is not committed before step 6; after the configuration is
frozen it may be committed because the source and annotations are public. This
prevents leakage into the pinned offline baseline, but a public test set may be
present in training corpora for models trained after its 2026 release; results
from such models require an explicit pretraining-contamination limitation.

## Deterministic tree normalization

Normalization creates a comparison representation and never rewrites an
annotation or prediction artifact.

1. Validate the original tree first; invalid trees receive no semantic repair.
2. Push every `NOT` to leaves using De Morgan's laws. A toggled leaf records
   canonical polarity `negated`; double negation cancels.
3. Flatten directly nested operators of the same associative type (`ALL` inside
   `ALL`, `ANY` inside `ANY`).
4. Sort `ALL` and `ANY` children by the bytewise UTF-8 order of their canonical
   JSON serialization. `NOT` is eliminated from internal nodes by step 2.
5. Do not distribute `ALL` over `ANY`, distribute `ANY` over `ALL`, complement
   comparison operators, perform unit conversion, or consult patient facts.

Thus `NOT(ANY(a,b))` and `ALL(NOT(a),NOT(b))` normalize identically, while more
general logical equivalence is not guessed. A pair is flagged for equivalence
review only when its canonical atom multisets match exactly but its normalized
trees differ. Reviewers label the pair `equivalent`, `not_equivalent`, or
`uncertain` under Boolean structure alone; they cannot alter an atom or gold
tree. This review is an auxiliary diagnostic. It never changes the fixed
normalized-tree exact-match score or its denominator, preventing post-test
manual credit from becoming a tunable primary metric. Reports expose queued and
all three reviewed counts separately.

## Atom matching

The primary atom identity is a binary exact match over this canonical tuple:

1. concept-catalog field ID;
2. comparison operator;
3. typed value type and canonical scalar value;
4. unit, including exact distinction between `null` and a string;
5. time-window days, direction, and `relative_to`, including exact `null`;
6. repeated-fact selection policy (`ANY`, `ALL`, or `LATEST`);
7. normalized leaf polarity (`positive` or `negated`).

Condition IDs, annotation IDs, decomposition method, model ID, prompt version,
and source span are excluded from atom identity. Source span is scored
separately. Numeric JSON values are compared by exact mathematical decimal
value so `50` and `50.0` match; strings and dates remain exact after schema
parsing. There is no partial atom credit and no post-error synonym table.

Duplicate canonical atoms are treated as a multiset. True positives are the
multiset intersection; unmatched prediction atoms are false positives and
unmatched gold atoms are false negatives. For span scoring only, duplicate
matched atoms are paired after independently sorting each side by
`(source_span.start, source_span.end, condition_id)`; span cannot influence
whether an atom is a true positive. Report micro precision, recall, and F1
across atoms plus per-criterion macro F1. A secondary concept diagnostic uses
only `(field ID, polarity)` and is explicitly not the primary score.

## Structure and source-span metrics

The evaluator reports these dimensions separately:

- normalized-tree exact match: canonical operators and complete atom identities
  match exactly;
- operator-topology exact match: the canonical rooted operator tree matches
  after replacing every leaf with `ATOM`;
- atom micro precision/recall/F1 and per-criterion macro F1;
- exact source-span rate among primary-identity-matched atoms;
- character-span intersection-over-union among those same matched atoms,
  reported as mean plus exact-match count;
- schema-valid rate and verifier-load rate as validity floors only.

Primary metric estimates additionally receive trial-cluster bootstrap
confidence intervals: an NCT ID is the resampling cluster and all criteria from
the sampled trial move together. The report records the number of trials,
resample count, seed, confidence level, and interval bounds. Intervals from this
small benchmark are expected to be wide and are reported without treating
criterion rows as independent observations.

Span does not affect atom true-positive assignment. Invalid or missing model
output stays in the benchmark denominator, contributes zero semantic credit,
and is reported by failure class. A verifier pass never substitutes for any
gold metric.

## Pre-adjudication inter-annotator agreement

IAA uses the same frozen normalization and matching code as model evaluation
and is calculated over all independently double-annotated items before any
adjudication. Report:

- normalized-tree exact agreement rate;
- operator-topology exact agreement rate;
- atom micro F1 and per-item macro F1 between annotators;
- exact span agreement and mean character-span IoU for matched atoms;
- count and rate entering equivalence review;
- disagreement counts by atom omission/addition, field, operator, value/type,
  unit, time window, fact selection, polarity, structure, and span.

No single kappa statistic is claimed for the structured tree as a whole. The
final adjudicated gold is reported separately and cannot be used to inflate
pre-adjudication IAA.

## Outputs and stop conditions

All public outputs are versioned, self-hashed, and refuse overwrite. At minimum
they include the source/selection manifest, concept catalog, annotation files,
adjudication record, gold manifest, model predictions, equivalence-review
record, and JSON/Markdown evaluation report.

Stop and return to owner review if any of the following occurs:

- a quota, trial cap, trial-level isolation, source hash, or span check fails;
- the standard mode lacks two qualified annotators;
- an annotator sees model output before locking independent work;
- test gold or metrics influence prompt/model/evaluator configuration;
- a required concept is absent from the frozen catalog;
- normalization or matching rules would need revision after annotations or
  predictions are visible;
- an unresolved annotation disagreement remains;
- any selected file contains patient or restricted data.

Passing this protocol establishes a small public decomposition benchmark. It
does not establish clinical eligibility correctness, patient matching quality,
or readiness for autonomous trial recruitment.

## Implemented adjudication and gold boundary

The P5D.2 offline workflow is implemented without creating benchmark gold.
Version `1.0.0` adjudication records bind both completed independent annotation
hashes, the frozen catalog and guide, the P5D.3 normalization and matching
versions, per-item disagreement routing, pre-adjudication IAA, and auxiliary
equivalence-review judgments. An agreed item cannot be changed during
adjudication, and a completed record cannot contain an unresolved item.

The resulting final-gold manifest is separately self-hashed and references the
source annotation/adjudication artifacts without copying their trees. Standard mode is
labelled `adjudicated_gold` and must bind its completed adjudication record. The
single-person path is labelled `single_annotator_reference_gold`, requires a
hash-bound owner downgrade decision attesting approval before the first
annotation, reports no IAA, and cannot contain an adjudication reference. These
mechanisms do not approve the downgrade or satisfy the current staffing gate;
P5D.4 remains unstarted until the owner chooses a protocol-compliant staffing
path.
