# Public criteria-decomposition benchmark protocol

Status: **version 1.2.0 frozen by owner approval on 2026-09-01; the replacement
test source uses a uniform current API snapshot while preserving historical
hashes as provenance; the predeclared single-annotator downgrade remains in
force; real annotation has not started**

Protocol version: `decomposition-benchmark-protocol/1.2.0`

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

### Frozen single-domain source pool

This benchmark is limited to **atrial-fibrillation interventional trials**. Its
decomposition metrics measure performance in that single disease domain and
must not be presented as disease-independent or general clinical-criteria
decomposition performance. This scope limitation is cumulative with the
limited external validity of the single-annotator reference set.

Owner-approved source-pool contract
`decomposition-source-pool-contract/1.0.0` freezes the ClinicalTrials.gov v2
condition query, three recruiting statuses, `INTERVENTIONAL` study type,
eligibility-text requirement, inclusive first-posted interval 2000-01-01
through 2026-08-31, observed registry total of 833, and a 40-trial NCT-hash
sample. The complete query is fetched before local filtering. Registry order,
last-update recency, and criterion content do not affect trial sampling.

The snapshot stores the 40 selected public source studies and binds a separate
self-hashed query-audit file containing the complete 833-NCT hit list, status at
query time, selection fields, source-study hash, filter outcome, and sampling
digest. The top-level manifest also freezes the exact query parameters, query
timestamp, API version, API data timestamp, page count, and audit hash. A
registry-total change, incomplete fetch, fewer than 40 filter-passed studies,
or downstream criterion-quota failure stops execution. No automatic query,
trial-count, date, status, or quota expansion is permitted.

### Version 1.1.0 locked-test remediation

On 2026-09-01, after the dev catalog draft had been completed, an over-broad
local search touched the complete original 40-trial source snapshot. Because
the terminal output was truncated, no trial in the original non-dev snapshot
can retain an unseen-source claim. This was public ClinicalTrials.gov text, not
patient data or a privacy incident, but it invalidated the original locked-test
source timing. The event is recorded in
`docs/DECOMPOSITION_TEST_SOURCE_EXPOSURE_2026-09-01.md`.

The owner approved contract
`decomposition-test-remediation-contract/1.0.0` before replacement source
fetching or quota results were viewed. Its self-hash is
`10aae52ee5b7a8421e9e4baffc31e9c3e860a71d067cc9d4e59c18b57a960dbf`.
Version 1.1.0 preserves the 40 selected
dev criteria, retires all 40 original test criteria with an incident
cross-reference, and draws replacement test trials only from the 506
filter-passed records marked `selected=false` in the frozen 833-hit source
audit. No member of the original 40-trial snapshot is eligible.

Replacement trials are processed in ascending frozen `(sampling_hash, nct_id)`
order. Each individual fetch explicitly pins `format=json` and
`markupFormat=markdown`, matching the original complete query representation.
A live public response must reproduce the source-study SHA-256 already
stored in the audit; a changed study is skipped with
`source_hash_mismatch`. Fetch and parser failures record reason codes only.
Their source text is not persisted, displayed, or inspected for diagnosis.
After each successfully parsed trial, the selector tests the unchanged 5/7/8
inclusion/exclusion complexity quotas, eight-per-trial cap, and five-trial
minimum. It stops at the first frozen trial prefix satisfying all requirements.
An exact normalized-text duplicate of a preserved dev item is ineligible;
within replacement test candidates, the lowest frozen duplicate digest wins.
Exhausting all 506 records without a feasible selection fails closed.

Test source files live under
`benchmarks/decomposition/test_sources_1.1.0/`, separately from dev artifacts,
and that directory is excluded by `.rgignore` from default repository text
search. The public selection manifest contains only test structural metadata,
span lengths, identities, and hashes—never criterion or eligibility text.
Commands print aggregate counts and artifact identities only. Parser debugging
that would require viewing candidate text is prohibited: the trial is skipped.
These controls reduce accidental exposure; they do not make a deliberately
opened test file a clean observation.

Execution under this frozen contract failed closed. Metadata-only report
`decomposition-test-failure-5f38e542e0d24b1c` records that all 506 remainder
objects differed from their frozen complete-search object hashes, so none
entered parsing and no replacement selection exists. This is an infeasibility
result for the exact version 1.1.0 source-identity contract, not permission to
weaken it. A new source identity or snapshot policy requires explicit owner
review before any further test construction.

### Version 1.2.0 current-snapshot source identity

After the exact historical object-hash policy failed closed, the owner approved
Plan A before any replacement membership, parser outcome, or quota result was
viewed. Contract `decomposition-test-remediation-contract/1.1.0`, ID
`decomposition-test-remediation-047dd750572d6807`, SHA-256
`047dd750572d680711bcd4755e81be9b88402e5c2cca9cd6736a2a0df61d38f5`,
changes only the source-identity rule. It does not reinterpret or delete the
1.0.0 contract, the 506 historical source hashes, or failure report
`decomposition-test-failure-5f38e542e0d24b1c`.

The replacement candidates remain the same 506 frozen NCT IDs in the same
ascending `(sampling_hash, nct_id)` order. Current individual-study responses
are fetched with `format=json` and `markupFormat=markdown`. The full current
response hash is frozen as the new source artifact; the old complete-search
object hash is retained only as historical provenance, and equality is neither
required nor implied. The API `apiVersion` and `dataTimestamp` must be non-empty,
must remain unchanged before and after each individual fetch, and must be
identical across the complete accepted construction window. Any API-identity
change fails the whole execution rather than skipping one trial.

Each current response is revalidated, in frozen reason-code order, for exact NCT
ID, `INTERVENTIONAL` study type, one of the three originally approved recruiting
statuses, inclusive first-posted date 2000-01-01 through 2026-08-31, and
non-empty eligibility text. A failed response is skipped without persisting or
displaying its text. Successfully revalidated responses enter the unchanged
parser, exact-normalized duplicate handling, 5/7/8 inclusion/exclusion quotas,
eight-criteria-per-trial cap, five-trial minimum, and first-feasible-prefix stop
rule. Registry response order, current update recency, criterion content, and
historical-hash agreement never determine trial order.

The new write-once outputs are
`benchmarks/decomposition/dev_sources_1.2.0/`,
`benchmarks/decomposition/test_sources_1.2.0/`, and
`benchmarks/decomposition/af_decomposition_selection_1.2.0.json`. The locked
test directory remains excluded from default repository search. Its raw current
responses and parsed protocols are immutable, hash-bound public-source inputs;
the public selection manifest remains text-free. Parser diagnosis requiring a
human to view test candidate text is still prohibited. Quota shortage still
fails closed without automatic query, filter, date, quota, or source-pool
expansion.

Execution completed under builder commit
`7d5f731bc7411a368293c09158fa80205ca93955`. Selection
`decomposition-selection-b83592f4e26d0874`, SHA-256
`b83592f4e26d0874ef61ab74f06ebc432f4059251f9da05eb5184bfeb2ffe97e`,
preserves 40 dev criteria from 15 trials, retires all 40 original test
criteria, and selects 40 replacement test criteria from 10 trials. The first
10 frozen remainder trials all re-passed the current filters and parsed; no
candidate was skipped. Every 5/7/8 stratum quota, the eight-per-trial cap, and
the five-trial minimum passed without relaxation.

The replacement test snapshot is
`decomposition-test-source-a814e1019ffd0186`, SHA-256
`a814e1019ffd0186999ff448f641dff6969fe15b2f986da9895004121246a941`,
under ClinicalTrials.gov API version `2.0.5` and data timestamp
`2026-08-31T09:00:04`. Zero of the 10 current individual-response hashes equal
their historical complete-search object hashes; this is recorded only as an
observed identity difference and is not given a causal interpretation. The
separated dev snapshot is `decomposition-dev-source-9d8d925255a65acd`,
SHA-256
`9d8d925255a65acd515d965dce1f3ad54e6426a78d917473a5604f2f291550d9`.
Both snapshots and the selection passed independent hash-bound verification
without displaying locked-test text.

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

The owner froze construction rules `concept-catalog-rules/1.0.0` on
2026-09-01. The packaged self-authenticating resource is
`src/clinical_matcher/resources/decomposition-concept-catalog-rules-1.0.0.json`
with SHA-256
`e68dfaa973391911b389149ba624b8a694c8f7ec6b9b1172fca23053816fffa9`.
Catalog finalization and validation require this exact version/hash pair; an
arbitrary well-formed hash is not accepted.

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

The version 1.0.0 selection implementation must recompute every digest, quota,
duplicate group, cap, and split assignment during verification. Version 1.1.0
supersedes only the test membership under the remediation contract above; the
dev membership remains unchanged.

### Cross-split lexical-overlap disclosure

After selection and before annotation, an exhaustive report-only diagnostic
compares all 40 x 40 dev/test criterion pairs. Method
`unicode_word_set_jaccard/1.0.0` applies Unicode NFKC normalization and
case-folding, extracts the set of Unicode alphanumeric word tokens with
underscores excluded, and computes set Jaccard similarity. The immutable report
binds the selection file and manifest hashes, evaluates all 1,600 pairs, and
records nearest-rank distribution summaries plus the 20 highest-overlap pairs
with deterministic tie-breaking.

This diagnostic has no threshold and no effect on source selection, split
membership, annotation eligibility, or scoring. It is lexical, not semantic:
high overlap does not establish equivalence, and paraphrases with different
vocabulary may be missed. Its only purpose is to disclose possible shared AF
trial template language across the trial-isolated splits without adding a
post-selection gate.

## Frozen human annotation guide

The owner first froze `decomposition-guide/1.0.0` on 2026-09-01 before real
annotation. After reviewing eight dev-only ambiguities and before writing any
tree, the owner approved the additive `decomposition-guide/1.1.0`, SHA-256
`6ba373984446704f27969b83b2c0e7960839e32022d129758cb069e460289925`.
The latter is the executable guide for protocol 1.2.0. It retains the original
literal-tree rules and additionally freezes prediction-unit preservation,
embedded-definition `ANY`, nearest syntactic attachment, modifier-loss
disclosure, planned-duration handling, truncated-source flags, and immutable
span-contamination handling. Every protocol-1.2 annotation must bind this exact
version/hash pair.

The guide encodes the criterion's literal condition: inclusion trees are true
when the inclusion condition is satisfied, while exclusion trees are true when
the exclusion condition is present. Exclusion text is not inverted into an
eligibility-safe tree. Negation uses `NOT` around a positive-fact atom rather
than `expected=false`. Explicit `AND`/both, `OR`/either, and ranges map to
`ALL`, `ANY`, and bound atoms respectively; unclear grouping is not guessed.
Repeated-fact selection maps current/most-recent to `latest`, history/ever to
`any`, and explicit universal requirements to `all`.

Schema 1.0.0 stores time windows as integer days, so the guide freezes one day,
week, month, and year as 1, 7, 30, and 365 days. The fixed-month/year
approximation is a permanent benchmark 1.0.0 limitation. Atom spans use the
smallest continuous text supporting the full condition. Condition IDs follow
left-to-right source order as `<criterion_id>:a01`, `:a02`, and so on.
Ambiguous or schema-unrepresentable items remain `expression=null` in a draft
and enter a separate owner-review issue log; they cannot enter a completed
annotation until resolved under the frozen schema or an approved schema/guide
revision.

The owner-approved dev issue log contains exactly eight resolutions. The
explicit 2-3 year phrase is encoded as an `ALL` interval with lower and upper
bounds of 730 and 1095 days under the frozen 365-day approximation; it is not
reduced to a selected endpoint. A grouped six-rule source block remains one
prediction unit. A dangling disjunction and a heading-contaminated immutable
span remain in the selected set with `incomplete_source_condition` and
`source_span_contamination` flags respectively. These flags disclose source
limitations and never repair or replace the text.

The dev catalog is additionally bound to the separated dev snapshot and the
remediated selection. Its validator requires each normalized alias to be a
substring of the selected dev corpus and rejects aliases duplicated after
normalization. The resulting annotation package contains 40 public dev items
with `expression=null`; generating it freezes inputs but does not constitute
annotation or gold.

## Annotation modes and current staffing

The preferred standard mode is `dual_independent_with_adjudication`. The
staffing state at the mode decision was one confirmed annotator (the owner) and
no confirmed second qualified annotator. The owner therefore approved the
predeclared `single_annotator` downgrade before any benchmark annotation was
created. This changes the permitted reporting claims, not the source,
selection, schema, split, test-lock, or evaluation requirements.

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

The owner exercised this option on 2026-08-31. The frozen decision is
`src/clinical_matcher/resources/decomposition-single-annotator-decision-1.0.0.json`,
with decision SHA-256
`57bc80ee4cee3aeff3ccd7b51711d51727efa2e6939ef76893d0aa922f4df93e`.
The annotation-template and final-gold CLI paths validate the actual decision
artifact and bind its hash; an unverified version/hash string is not sufficient.

A later spot check does not upgrade that artifact. Upgrading requires a new
version in which a second qualified annotator independently annotates every
original source item without seeing the first annotation or model output,
followed by the full adjudication protocol.

## Leakage-safe dev/test timeline

The following order is mandatory:

1. freeze source snapshot, remediated selection manifest, annotation guide, catalog-
   construction rules, normalization, matching, and metrics;
2. author and freeze the dev concept catalog from dev source text only;
3. implement and test the schema validator and evaluator using synthetic trees;
4. annotate **dev only** under the frozen annotation mode, then freeze dev gold;
5. develop the prompt and runtime configuration using dev only;
6. freeze the exact model manifest, Ollama version, prompt, output schema,
   decoding settings, and code commit;
7. author and freeze the test concept catalog from test source text, then
   annotate test under the frozen mode without viewing model output;
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
gold manifest, model predictions, equivalence-review record, and JSON/Markdown
evaluation report. An adjudication record is required only in the frozen dual
mode and is prohibited in the approved single-annotator mode.

Stop and return to owner review if any of the following occurs:

- a quota, trial cap, trial-level isolation, source hash, or span check fails;
- the executed annotation mode differs from the pre-annotation owner decision;
- an annotator sees model output before locking independent work;
- test gold or metrics influence prompt/model/evaluator configuration;
- a required concept is absent from the frozen catalog;
- normalization or matching rules would need revision after annotations or
  predictions are visible;
- an unresolved annotation disagreement remains in a future dual-mode version;
- any selected file contains patient or restricted data.

Passing this protocol establishes a small public decomposition benchmark. It
does not establish clinical eligibility correctness, patient matching quality,
readiness for autonomous trial recruitment, or decomposition performance
outside the frozen atrial-fibrillation domain.

## Implemented adjudication and gold boundary

The P5D.2 offline workflow is implemented without creating benchmark gold.
Version `1.0.0` adjudication records bind both completed independent annotation
hashes, the frozen catalog and guide, the P5D.3 normalization and matching
versions, per-item disagreement routing, pre-adjudication IAA, and auxiliary
equivalence-review judgments. An agreed item cannot be changed during
adjudication, and a completed record cannot contain an unresolved item.

The resulting final-gold manifest is separately self-hashed and references the
source annotation/adjudication artifacts without copying their trees. Standard
mode is labelled `adjudicated_gold` and must bind its completed adjudication
record. The approved single-person path is labelled
`single_annotator_reference_gold`, requires the validated hash-bound owner
downgrade decision attesting approval before the first annotation, reports no
IAA, and cannot contain an adjudication reference. No real selection, catalog,
annotation, adjudication, or gold artifact was created by the implementation or
staffing-decision stages.

The original owner-approved public-source execution froze snapshot
`ctg-15b1e8aff71f895f`, selection
`decomposition-selection-befdca243400ea10`, and lexical-overlap report
`decomposition-overlap-e8520469215ee1f9`. The selection contains exactly 80
criteria with every predeclared quota satisfied; the report exhaustively covers
all 1,600 original cross-split pairs and remains disclosure-only. Its test split
is now retired by version 1.1.0 and that overlap report is historical, not a
replacement-test diagnostic. No real annotation, gold, or model prediction has
been created. The dev catalog and issue-log drafts predate the exposure and
remain unfrozen and unchanged during remediation.
