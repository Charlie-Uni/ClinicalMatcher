# Schema migrations

ClinicalMatcher documents declare an exact `schema_version`. Loaders reject
unknown or missing versions and never mutate data silently.

## `0.2-draft` to `1.0.0`

The draft format was internal and is not accepted by the current loader.
Version `1.0.0` adds:

- a top-level `schema_version`;
- criterion source text, section, and document version;
- source-span and decomposition-method provenance on every atom;
- at least two criterion and trial annotations;
- an explicit adjudication record for each gold judgment;
- strict JSON Schema validation before Python semantic validation.

Synthetic fixtures should be regenerated or explicitly converted and then
reviewed. Restricted clinical data must be migrated only in its authorized
local environment; converted records must remain ignored by Git.

Future breaking changes require a new schema file and a separate, explicit
conversion command. A converter must preserve the original file, emit its
source and target versions, and fail rather than invent required provenance or
gold labels.

## P7 locked-test batch contracts `1.0.0`

P7 adds new, strict `1.0.0` contracts for the predeclared single locked-test
batch, immutable state events, per-request latency traces, the complete batch
manifest, the deterministic representative-case package, and the public
release candidate. These are new audit artifacts; they do not migrate or
reinterpret an earlier test result. The checked-in batch contract remains
non-executable until its implementation hashes are complete, P7.1 is frozen,
and the owner separately authorizes P7.2.

The public-release schema admits only the six predeclared whole-split fields.
All per-question, per-class, confusion-matrix, patient, rule, unit, P4.7, and
representative-case values remain owner-only. The batch-manifest validator
recomputes file hashes, raw-to-P4.3 parent/configuration derivation, and report
bindings rather than trusting path labels.

`apixaban-single-trial-report/1.1.0` is additive. It supports the current P4.3
`1.1.0` projection on validation or the one P7 locked test and records whether
locked labels were used. Historical report `1.0.0` remains valid and is never
relabeled, replaced, or reinterpreted by the additive validation diagnostic.

## Decomposition benchmark protocol `1.2.0` to `1.3.0`

Protocol `1.3.0` records the completed LLM-assisted dev reference and freezes
the disclosure and observation-lock rules for its descriptive local-model
comparison. It adds four reporting obligations: the 40/40
`accepted_unchanged` and zero-note owner-review distribution; the prohibition
on changing silver after model disagreements are observed; the eight-item
information-asymmetry subgroup whose owner resolutions are hidden from the
evaluated model; and explicit identification of the Codex draft model and the
evaluated local Llama model.

This is a governance and evaluation-disclosure migration, not a data-schema
conversion. Frozen selection, source snapshot, catalog, issue-log, and
annotation-package artifacts retain their original
`decomposition-benchmark-protocol/1.2.0` bindings and hashes. Relabelling or
regenerating them as 1.3.0 is prohibited. New P5D.5 contracts and reports bind
those immutable inputs while declaring protocol 1.3.0.

P5D.6 introduces `decomposition-disagreement-report/1.0.0` as a new retained
diagnostic artifact; it does not migrate or rescore the P5D.5 prediction or
comparison schemas. Its primary categories are mutually exclusive and must
reconcile to all 40 dev items. Component-level overlaps are explicitly
non-primary marginal diagnostics. The report binds the unchanged P5D.5 files,
assisted silver, disclosure manifest, analysis contract, and analysis code
commit. It cannot unlock test or upgrade the assisted reference to gold.

## Apixaban abstention and error-attribution reports `1.0.0` to `1.1.0`

Version `1.1.0` records the sole source-question exception permitting
`med_decisions=absent, value=false` to remain known without an evidence ID.
The exception is machine-readable in each report policy. Error attribution
uses the same rule so it does not classify that result as unsupported.

Existing `1.0.0` reports remain valid historical artifacts and are never
relabeled or converted because their row-level outcomes may differ. To produce
`1.1.0`, rerun the pinned source prediction through the new policy inside the
authorized environment and record new hashes.

## Apixaban prediction set `1.0.0` to `1.1.0`

Version `1.1.0` adds evidence-level traceability for deterministic and later
retrieval baselines:

- `rule_set_sha256` at the prediction-set level;
- stable `evidence_ids` on every prediction;
- stable `rule_ids` explaining the extraction path.

The evaluator continues to accept `1.0.0` so existing model outputs remain
measurable. It does not fabricate the new fields when loading an old file.
New deterministic outputs use `1.1.0`; converting an old output requires
regeneration from its original evidence and rules rather than an automatic
placeholder migration.

## Apixaban prediction set `1.1.0` to `1.2.0`

Version `1.2.0` supports structured local-model inference. It replaces the
deterministic extractor's `rule_set_sha256` with
`inference_config_sha256`, and replaces `rule_ids` with general `trace_ids`.
Stable `evidence_ids` remain mandatory. The evaluator accepts all three
prediction-set versions and does not silently translate between them.

Version `1.2.0` outputs must be regenerated from the pinned model, prompt,
input policy, and evidence. A `1.1.0` rule output cannot be relabeled as a model
output, and a model output cannot invent deterministic rule provenance.

P2.2 also introduces `apixaban-structured-run-report-1.0.0`. It is a separate
aggregate artifact rather than a prediction-set extension because inference
latency, token counts, schema validity, truncation, and memory describe the run
as a whole. The report binds to the benchmark, frozen split, model manifest,
prompt/configuration, code commit, and canonical prediction-set content hash.
The original validation artifact uses the shorter legacy field name
`prediction_content_sha256`; new reports use the unambiguous
`prediction_set_content_sha256`. Schema `1.0.0` accepts exactly one of these
aliases because both values hash the same complete canonical prediction set.

P2.3 reuses run-report schema `1.0.0` without invalidating P2.2 artifacts. The
`max_note_characters` field may be `null` only for a no-character-cap policy,
and new reports may record note-character exposure, maximum observed prompt
tokens, and the number of requests whose observed prompt token count reached
the configured context limit. These fields are optional in the schema so the
already frozen P2.2 report remains valid; current writers emit all three.

P3.1 introduces `apixaban-evidence-index-manifest-1.0.0`. This owner-only
aggregate manifest does not contain note text or row-level labels. It freezes
the evidence-only index projection, patient-local retrieval scope, chunk
contract hash, split/corpus provenance, deterministic index ID, counts, and
span/isolation validation results. It is deliberately separate from the
staging-corpus schema: P3.1 preserves the already frozen evidence chunks, so no
P2 corpus, split, prediction, or report needs migration or regeneration.

P3.2 introduces the owner-only `apixaban-bm25-run-1.0.0` artifact and reuses
prediction-set schema `1.2.0` for the downstream deterministic diagnostic. The
BM25 artifact stores IDs, query hashes, ranks, positive scores, frozen
configuration, performance/exposure aggregates, and complete provenance, but
no note or question text. Its semantic validator additionally requires the
complete patient-by-question grid and reconciles every aggregate count. No P2
or P3.1 artifact is migrated. Because independent evidence-ID gold is absent,
the schema fixes the boundary that retrieval-relevance metrics are not reported
and downstream answer metrics are diagnostic only.

P3.3 adds two owner-only artifacts without migrating earlier schemas.
`apixaban-dense-index-1.0.0` binds the paired MedCPT model revisions, vector
representation, P3.1 evidence index, ordered evidence IDs, vector-file hash,
byte/count invariants, and deterministic index identity.
`apixaban-dense-run-1.0.0` binds that index to the complete patient-question
retrieval grid, frozen configuration, performance/exposure aggregates, and the
schema-`1.2.0` downstream prediction set. Neither schema permits note or
question text. The index manifest, vectors, ranks, pseudonyms, scores, and
predictions all remain restricted local artifacts.

P3.4 introduces owner-only `apixaban-rrf-run-1.0.0` without migrating the BM25,
dense-index, dense-run, or prediction schemas. It binds the exact component run
hashes and dense index identity to a complete patient-question fusion grid,
fixed RRF-60 configuration, component input depths, per-item component ranks,
recomputable fusion scores, resource/exposure aggregates, and a schema-`1.2.0`
downstream prediction set. The schema contains no note or question text and
fixes `reranker_included=false`. Ranks, scores, patient pseudonyms, predictions,
and the fusion run remain restricted local artifacts.

P3.5 adds owner-only aggregate
`apixaban-numeric-occurrence-report-1.0.0` without migrating any retrieval or
prediction artifact. It binds the official benchmark and staging hashes,
frozen validation split, three component run hashes, question catalog, and
versioned weak-diagnostic contract. It records only reconciled population
exclusions and exact numeric-token occurrence@1/@3 counts/rates—never note
text, patient IDs, per-row results, or evidence-relevance metrics. The report
remains restricted because its aggregates are derived from MIMIC text/labels.
