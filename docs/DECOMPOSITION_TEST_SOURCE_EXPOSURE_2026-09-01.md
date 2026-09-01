# P5D test-source exposure record

Status: **owner-approved remediation blocked after a fail-closed execution;
metadata-only failure report pending**

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
