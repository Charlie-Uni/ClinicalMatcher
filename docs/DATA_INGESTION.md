# Data ingestion boundaries

Status: P2.1 snapshot and gold-readiness foundation

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

ClinicalTrials.gov changes daily. One-off protocol imports belong under ignored
`artifacts/`, not as unversioned study copies.

```bash
clinical-matcher-import-trial \
  --nct-id NCT00000419 \
  --output artifacts/trials/NCT00000419.json
```

For offline tests, `--study-json` and `--version-json` accept the independently
authored synthetic API-shaped fixtures.

### Immutable multi-trial snapshots

Benchmark evaluation must never query the live registry. The live API is used
only to build a snapshot; all later decomposition, annotation, retrieval, and
evaluation load that verified snapshot.

Benchmark size is first bounded by the pilot-derived double-annotation budget
described in [BENCHMARK_DESIGN.md](BENCHMARK_DESIGN.md). The executable
restricted-local record workflow is in
[ANNOTATION_PROTOCOL.md](ANNOTATION_PROTOCOL.md). A provisional,
assumption-only or manually entered capacity plan cannot authorize a snapshot.

`clinical-matcher-snapshot build` cursor-pages every `/api/v2/studies` match
before selection. It records the exact disease domain, rationale, `query.*`
terms, study types, recruitment statuses, eligibility-text requirement,
inclusive first-posted date range, page size, API version, and API data
timestamp. The final trial count is read from the capacity plan.
The client checks `/api/v2/version` before and after pagination and rejects the
build if the registry data timestamp changes mid-fetch.

No registry `sort` parameter is used. Selection sorts eligible trials by a
SHA-256 of method version, capacity-plan hash, and NCT ID; registry response
order and last-update recency do not affect inclusion. The snapshot retains an
audit row for every registry hit and records the full flow from registry total
to fetched, filter-passed, eligible-not-sampled, and finally selected counts.
Each row includes public selection metadata, source hash, exclusion reasons,
sampling hash, and inclusion reason.

The snapshot retains every selected public source study, including selected
studies the conservative parser later skips. Its manifest freezes, for each
successfully imported NCT ID:

- registry version holder and last-update date;
- complete source-study, eligibility-text, and normalized-protocol hashes;
- source record version and ordered criterion IDs;
- source and normalized relative paths.

The separate coverage report exposes imported/skipped/failed counts and rates,
reason codes, and criterion-count ranges. Ambiguous polarity is therefore
measured rather than silently discarded. `verify` revalidates schemas, path
containment, record order, coverage arithmetic, all hashes, record versions,
and criterion IDs. The builder refuses to overwrite an existing destination.

After a real two-annotator pilot has produced
`artifacts/benchmark/af-capacity-plan.json`, an AF-first candidate build is:

```bash
clinical-matcher-snapshot build \
  --disease-domain atrial_fibrillation \
  --selection-rationale \
  "Capacity-bound deterministic sample from all policy-eligible AF studies" \
  --query-condition "Atrial Fibrillation" \
  --study-type INTERVENTIONAL \
  --overall-status RECRUITING \
  --overall-status NOT_YET_RECRUITING \
  --overall-status ENROLLING_BY_INVITATION \
  --first-posted-from <PREDECLARED_START_DATE> \
  --first-posted-to <SNAPSHOT_CUTOFF_DATE> \
  --capacity-plan artifacts/benchmark/af-capacity-plan.json \
  --output-dir artifacts/trial_snapshots/af_candidate_v1

clinical-matcher-snapshot verify \
  --snapshot-dir artifacts/trial_snapshots/af_candidate_v1
```

This command defines a capacity-bound candidate pool, not a benchmark. It fails
if the registry total was not fetched completely, too few trials pass filters,
the capacity plan is provisional, or the plan has no reviewed design. A
selected trial that the conservative parser cannot segment remains visible and
prevents gold readiness rather than being silently replaced.

If a release retains a frozen registry snapshot for reproducibility, its README
must retain the attribution, processing date, modification notes, and warning
that current registry records may differ.

### Patient-trial gold is a release gate

A public trial snapshot supplies trials and protocol criteria only. It does not
say which patients are eligible or which evidence supports each decision.
`clinical-matcher-gold-readiness` emits a PHI-free aggregate report and refuses
a ready status unless:

- the snapshot contains multiple successfully parsed trials;
- imported trial count, patient count, and patient-trial unit count match the
  capacity-bound design;
- the gold covers the same imported trial count and at least one patient;
- all declared patient × trial units are adjudicated;
- all declared patient × trial × criterion evidence units are adjudicated;
- every unit has at least two independent annotators; and
- no adjudication remains unresolved.

With no authorized gold, the honest report is intentionally `not_ready`:

```bash
clinical-matcher-gold-readiness \
  --snapshot-dir artifacts/trial_snapshots/af_candidate_v1 \
  --gold-source-description "No adjudicated multi-trial gold available" \
  --output artifacts/trial_snapshots/af_candidate_v1.gold-readiness.json
```

CLI counts are recorded as `self_reported_aggregate` and therefore always leave
the blocking gap `gold_counts_not_derived_from_validated_records`, even when
all numeric counts look complete. A future authorized annotation validator
must derive the counts from row-level records and mark their provenance
`validated_annotation_records`; manually entered counts cannot unlock a
benchmark claim.

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
