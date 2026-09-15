# P8.3 prompt-component implementation review

Date: 2026-09-15. Scope: review-stage components, not a completed E1 runner.
Status: `local_component_review_passed`; prompt approval and E1 execution remain pending.

## Authorization and stage boundaries

The owner accepted the completed E0 package and asked to continue. That decision
is persisted in the existing restricted protocol records and E0 event ledger.
It authorizes the next implementation stage; it does not approve the v2 text
created in this stage. The specification's explicit dual-role prompt review
still applies. No real train-fit selection, model request or E1 score was used.

The master specification remains version 1.1.0. This is a separately versioned
proposal, not an unreviewed amendment to the approved study or a new experiment.
E0, the mechanical exporter, holdout guards, P5D and historical closed tasks are
unchanged. `docs/PROJECT_TODO.md` is untouched.

## Implemented and checked

| Boundary | Implementation and evidence |
| --- | --- |
| Request unit | Fixed catalog order; exactly 23 single-question requests or the declared 5/5/5/4/4 groups. No prior-response input. |
| Full notes | Original evidence array and text retained before the variable question/example suffix. A synthetic injection string remains JSON data and does not alter the system message. |
| Typed output | Finite numeric present/unknown only; boolean present/absent/unknown; unit null; exact question identity. |
| Quote and citations | Current-patient ownership, exact substring, 400-character cap, unique citations; known-answer citation/quote requirement with only the source-defined med_decisions empty default. This stronger quote condition is explicitly pending review. |
| Invalid output | Duplicate keys, nonfinite values, malformed JSON, extra fields and wrong/duplicate/missing questions rejected. Whole group abstains; no repair or retry path. |
| Projection | Drops quote mechanically, preserves accepted typed answers, adds prediction-row bookkeeping. Does not infer clinical support from a valid quote. |
| Example support | Full-patient, source-window and quote rule agreement with frozen source gold; source citations, literal offsets, fixed window budget and deterministic tie order. Unknown/default-only unsupported examples excluded. |
| Selection | Fixed salt and source-bound ranking, at most two distinct train-fit patients per question, all exclusions and eligible overflow candidates retained, one/zero fallback. |
| Pre-read gate | A persisted dual-reviewed metadata plan must reproduce against the access registry before either train-fit source read; wrong approval or source pins fail before registry I/O. |
| Hash meanings | Paired file/content/self semantics inherited from P8. Tests use different self/file values and reject swapped consumers. Public proposal JSON and Markdown text were checked against the resource. |
| Timing switch | Pure helper accepts all 23 pilot slot durations, estimates 15-patient wall time, switches only when strictly above three hours. No score input. Actual measurement is not implemented here. |
| Disclosure | No patient content or real examples were opened or printed. The new public files passed the public-data scanner. |

## Self-review findings and limits

1. Fixed a determinism defect during implementation: JSON schema `required`
   fields were initially built from a set. They now use sorted order. A
   subprocess test compares outputs under different Python hash seeds.
2. Kept the numeric source transformation intact: synthetic LVEF 65 yields the
   source-defined answer 55, with the original 65 quotation. Numeric examples
   must preserve the full patient's extremum, not merely match a local number.
3. The automatic example screen is lexical agreement, not clinical adjudication.
   It can favor easy explicit facts, miss useful examples, or share rule errors.
   The proposal states these limits rather than claiming clinical support from
   citation ownership alone. Neither full-patient agreement nor a literal quote
   guarantees that all temporal or clinical interpretations are correct.
4. `select_examples` and `build_messages` are pure components for already
   authorized inputs. A self-hash detects changed bytes; it is not a signature
   or an authorization. The future runner must pin the persisted example set,
   plan, code and request artifacts in its run contract. No inference CLI is
   exposed by this module, and the example plan explicitly does not authorize
   inference. These helpers do not claim to be a complete run boundary.
5. Context counting is unresolved at runtime: the existing HF SFT tokenizer
   cannot be asserted equivalent to the actual Ollama rendering template.
   Actual tokenizer/template pinning and pre-send budget checks are required
   before E1. No truncation workaround or assumed cache speedup was introduced.
6. Runner responsibilities remain pending: durable request/response recording,
   exact retry classification/recovery, measured pilot/model state, immutable
   example pins, prediction-set envelope, latency reporting, ablation matrix,
   full-grid evaluation and post-selection safety projection. P8.3 remains open.
7. The first E0 owner-approval event append used a new enum name not accepted by
   the existing event schema. Validation rejected it before an event file was
   written; the approval record itself was already durable. The metadata-only
   failure and successful existing `review_passed` event were appended without
   overwriting any record. No experiment or clinical read was involved.

## Verification

Initial installed-package targeted run: 24 tests passed. Two further tests were
added for cross-process schema determinism and numeric extremum preservation.
The complete noneditable installed-package suite passed: **507 tests, three
conditional skips, no failures** (504 passed). Four process shards completed in
149.39 seconds. The installed module bytes were checked against the working
source before testing; no installation was changed while the suite ran.
All 26 new tests passed in that full run. The public-data guard, catalog and
synthetic document validators, and smoke check also passed. No dependency or
lockfile changed.

Hosted CI is checked against the submitted commit before reporting this stage
back to the owner. Its exact run identity and conclusion are recorded in the
owner-only component verification receipt, without adding patient data to this
public review. The result associated with a commit is available from its
GitHub checks; local test success is not represented as hosted CI success.

The concrete prompt and source-selection protocol are in the
[v2 review package](P8_V2_PROMPT_REVIEW.md). The next stage may select real
examples only after its persisted plan binds the exact approved proposal.
E1 still requires a complete frozen runner; E2 and holdout keep their separate
owner decision/authorization gates.
