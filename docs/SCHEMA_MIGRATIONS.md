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
