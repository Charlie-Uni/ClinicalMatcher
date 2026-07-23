# Data ingestion boundaries

Status: P2.0 foundation

ClinicalMatcher separates public trial ingestion from restricted patient
regeneration. Public protocol text may be fetched from its official registry.
Restricted patient data are never downloaded, uploaded, or redistributed by
this repository.

## Public ClinicalTrials.gov protocols

The importer uses the modern ClinicalTrials.gov REST API v2:

- `/api/v2/version` for the API version and registry data timestamp;
- `/api/v2/studies/{nctId}` for a study record;
- `protocolSection.eligibilityModule.eligibilityCriteria` for the criteria
  markup;
- the status and derived metadata modules for record and snapshot dates.

Every normalized protocol records:

- ClinicalTrials.gov/NLM attribution and terms URL;
- NCT ID and canonical study URL;
- API version, data timestamp, registry snapshot date, and study update date;
- complete eligibility source text and SHA-256;
- importer Git commit and retrieval time;
- a complete list of normalization modifications.

The source record version combines the study update date and eligibility-text
hash. It therefore changes when the relevant text changes but does not change
merely because the global registry snapshot advances.

### Criterion segmentation

The P2 importer segments explicit inclusion/exclusion sections into traceable
text blocks. Each block has:

- a stable content-derived criterion ID;
- inclusion/exclusion polarity;
- a global, zero-based, end-exclusive span into the complete source text;
- exact source text;
- whitespace-normalized text for downstream processing.

List markers are excluded from criterion spans. Escaped comparison characters
are normalized only in `normalized_text`; the original text remains unchanged.
If explicit polarity headings are absent, import fails rather than guessing.
Unbulleted paragraphs are preserved conservatively as whole criteria.

These records are protocol text blocks, not yet atomic executable conditions.
P3 decomposition must create atoms with its own provenance and validation.

ClinicalTrials.gov changes daily. Regenerate protocol artifacts before a
benchmark run, retain the reported data timestamp, attribute the source, and
document modifications. Live imports belong under ignored `artifacts/`, not as
stale committed study copies.

```bash
clinical-matcher-import-trial \
  --nct-id NCT00000419 \
  --output artifacts/trials/NCT00000419.json
```

For offline tests, `--study-json` and `--version-json` accept the independently
authored synthetic API-shaped fixtures.

## Restricted patient sources

The public adapter contract is a versioned normalized JSON source containing:

- source dataset ID and version;
- credentialed/restricted access policy and terms URL;
- typed patients, facts, dates, units, evidence IDs, and evidence text.

`clinical-matcher-regenerate-patients` validates the complete source and
semantic evidence links, canonicalizes JSON without changing clinical values,
and writes:

- a restricted normalized patient file;
- a separate aggregate regeneration manifest containing hashes, code commit,
  adapter version, patient count, and modification notes.

The command requires an explicit local-only acknowledgement. Inputs and outputs
inside the repository must resolve to an ignored path such as `private_data/`
or `artifacts/`.

```bash
clinical-matcher-regenerate-patients \
  --input private_data/normalized-patient-source.json \
  --output artifacts/patients/normalized.json \
  --acknowledge-restricted-data-local-only
```

The current adapter starts from the normalized patient-source contract. It does
not yet transform raw MIMIC tables or the Apixaban extension. That
dataset-specific mapper must be developed and executed only in an authorized
environment, with schema/column diagnostics and aggregate logs used for remote
debugging.

## Disclosure boundary

Detailed split manifests, semantic pair files, patient-level reports, and
normalized patient bundles contain row-level identifiers or restricted
derivatives and stay local.

The semantic audit command can emit a text-free aggregate summary with:

- embedding model ID/revision, pooling, and normalization;
- exhaustive versus ANN search method;
- expected and evaluated cross-split pair counts;
- ANN candidate-recall estimate;
- threshold and aggregate leakage counts;
- a canonical hash of the local detailed pair payload.

An exhaustive claim is rejected unless every cross-split pair was evaluated.
ANN scans must report a measured candidate-recall estimate. This makes the
scanner's recall limitation visible, but it does not prove that the embedding
or threshold is clinically appropriate.

Even a text-free aggregate is a restricted-data derivative until the applicable
governance process permits disclosure. Detailed IDs should not be sent through
an API merely because note text was removed.

## Official references

- [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/api)
- [ClinicalTrials.gov study data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure)
- [ClinicalTrials.gov terms and conditions](https://clinicaltrials.gov/about-site/terms-conditions)
- [PhysioNet guidance for MIMIC-derived datasets](https://physionet.org/content/mimiciv/)
- [PhysioNet guidance for LLMs and online services](https://physionet.org/news/post/llm-responsible-use/)
