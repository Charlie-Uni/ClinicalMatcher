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
