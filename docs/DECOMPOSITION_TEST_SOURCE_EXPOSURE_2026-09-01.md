# P5D test-source exposure record

Status: **remediated under owner-approved Plan A; replacement test source is
frozen and verified; annotation has not started**

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

## Plan A execution outcome

Plan A completed under builder commit
`7d5f731bc7411a368293c09158fa80205ca93955`. The headless builder attempted the
first 10 trials in the unchanged frozen remainder order; all 10 re-passed the
current filters and parsed, and none was skipped. The unchanged quotas selected
40 replacement test criteria from those 10 trials. No criterion text was
displayed during build, metadata audit, or independent verification.

The final replacement selection is
`decomposition-selection-b83592f4e26d0874`, SHA-256
`b83592f4e26d0874ef61ab74f06ebc432f4059251f9da05eb5184bfeb2ffe97e`.
Its test snapshot is `decomposition-test-source-a814e1019ffd0186`, SHA-256
`a814e1019ffd0186999ff448f641dff6969fe15b2f986da9895004121246a941`,
bound to ClinicalTrials.gov API version `2.0.5` and data timestamp
`2026-08-31T09:00:04`. The preserved dev snapshot is
`decomposition-dev-source-9d8d925255a65acd`, SHA-256
`9d8d925255a65acd515d965dce1f3ad54e6426a78d917473a5604f2f291550d9`.

All 10 current response hashes differ from their historical complete-search
object hashes. This is retained as a provenance observation only, consistent
with the approved source-identity change; it is not evidence for a particular
cause. The old failed selection remains retired, and the old 1.0.0 failure
report remains valid historical evidence. The exposure is now remediated for
future P5D work, but the replacement test source must remain unseen during dev
catalog annotation and prompt/model configuration.
