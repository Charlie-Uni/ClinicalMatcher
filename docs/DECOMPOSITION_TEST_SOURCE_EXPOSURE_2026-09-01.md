# P5D test-source exposure record

Status: **Plan A current-snapshot remediation frozen by owner approval;
execution pending**

Date: 2026-09-01

Affected selection: `decomposition-selection-befdca243400ea10`

## What happened

After the dev concept-catalog review draft had been completed and validated, a
diagnostic `rg` command intended to inspect three dev-trial protocol files was
mistakenly scoped to the complete frozen public source snapshot. Its returned
output included public text from at least two trials assigned to the current
test split: `NCT05997914` and `NCT06953778`. The command itself searched the
whole snapshot and its output was truncated, so the incident cannot honestly be
limited to only those two visible trial IDs.

No patient data, MIMIC data, test gold, test model prediction, or test metric was
read. The exposed material was public ClinicalTrials.gov source text. This is
therefore not a privacy incident, but it does violate the benchmark's clean
test-source timing claim for the current test split.

## Chronology and preserved dev draft

The dev-only catalog review draft and dev issue-log draft were complete and had
passed the frozen catalog validator before the over-broad command ran. Their
unchanged hashes at incident recording time are:

- `dev_concept_catalog_review_draft.json`:
  `4091c7af21315482ca35ee75728691db64d9cd676911adbff478f1cbbf9617c3`
- `dev_annotation_issue_log_draft.json`:
  `b05b5e6b126a5f74a61f2e3a310a977cf8d56496562a2c23d7d9873a6b6ee238`

No test-derived alias, concept, annotation, prompt choice, or model setting was
added to either draft after the exposure.

## Consequence

The current 40-item test split cannot retain the claim that its source text was
unseen until after prompt/model configuration freeze. Continuing with it would
require an explicit contaminated-test limitation and would weaken the primary
evaluation. No test annotation or inference may start while this record remains
unresolved.

## Owner-approved remediation

The owner approved preserving the existing dev split and retiring the complete
current test split, not merely the two visible trials. The replacement test is
drawn only from the 506 filter-passed records that were not among the 40 studies
downloaded into the original snapshot. It may not be redrawn from any member of
that snapshot because the truncated search prevents a defensible unseen claim
for every non-dev member.

The exact headless fetch, frozen source-hash check, reason-code-only failure,
first-feasible-prefix selection, unchanged quota, retirement, storage, and
search-isolation rules are frozen in benchmark protocol 1.1.0 and
`decomposition-test-remediation-contract/1.0.0`. Parser failures that would
require human inspection are skipped. The old test records remain in the new
manifest with retirement status and this event ID.

This record will be updated to `remediated` and bind the final replacement
selection and test-source snapshot identities after execution passes
verification. Until then, no test catalog, annotation, model output, or metric
may be created.

## First execution outcome

The first execution under the frozen contract exhausted the complete 506-trial
remainder without satisfying the replacement-test requirements. It created no
selection, dev/test source directory, annotation, prediction, or metric. The
initial implementation raised the correct fail-closed state but did not retain
the required reason-code report. Before any retry, an audit-only reporting path
was added: it preserves the unchanged contract, order, source-hash gate, parser,
and quotas, and writes only NCT IDs, structural metadata, hashes, status, and
reason codes. It never writes or displays criterion text. A second execution is
permitted solely to materialize that missing report and does not authorize a
selection-policy change.

The evidence-only rerun produced validated report
`decomposition-test-failure-5f38e542e0d24b1c`, SHA-256
`5f38e542e0d24b1c88c6882cc4a5710d8e3540949956460bd6fcb17093425297`.
It is bound to builder commit `1d4439f43e836b2fb18b8d97d0e5449567f49c0d`,
the frozen remediation contract, and the original source audit. All 506
remainder trials were attempted; all 506 were skipped with
`source_hash_mismatch`, none reached parsing, and no selection or source
snapshot was created. The report contains NCT IDs, structural metadata, hashes,
and reason codes but no criterion, normalized, or eligibility text.

This establishes only that current individual-study API object hashes do not
reproduce the full-study object hashes stored by the 2026-08-31 complete-search
audit. The run did not retain a current API-version field for hash-mismatch
outcomes, so it cannot distinguish registry-version drift from endpoint
representation differences or study-content changes. No causal claim is made.
Changing the source identity rule, querying a fresh pool, or accepting current
hashes requires a new owner-reviewed contract; none occurs automatically.

## Owner decision after the failed exact-hash execution

The owner approved Plan A on 2026-09-01 before any new replacement membership,
parser outcome, or quota result was observed. The same 506 never-downloaded NCT
IDs and their frozen sampling order remain the only candidate pool. Contract
`decomposition-test-remediation-contract/1.1.0` freezes a uniform current
ClinicalTrials.gov individual-response snapshot instead of requiring equality
to the historical complete-search object hashes.

Historical hashes remain immutable provenance and are not claimed to equal the
current responses. Every current response must have the requested NCT ID and
must re-pass the original study-type, recruiting-status, first-posted-date, and
eligibility-text filters. API version and data timestamp must remain uniform.
The parser, duplicate policy, quotas, trial cap, minimum-trial rule, headless
handling, text-display prohibition, and no-manual-diagnosis rule are unchanged.
No test catalog, annotation, model prediction, or metric may be created until
the new snapshot and selection complete and independently validate.
