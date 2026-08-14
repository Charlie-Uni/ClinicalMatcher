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
