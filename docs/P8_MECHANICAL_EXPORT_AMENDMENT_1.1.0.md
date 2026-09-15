# P8 1.1.0 — one-time mechanical partition export

Owner decision: 2026-09-15. This versioned amendment supersedes the conflicting
export and independence wording of P8 specification 1.0.0. It is not an E0
policy change, a locked-test authorization, or a secondary-holdout evaluation
authorization. Original reservation/split artifacts and the P7 terminal record
remain unchanged.

## Authorized operation and exclusions

Exactly one project-lifetime mechanical export may read the original full
benchmark and staging JSON, using the frozen split/reservation pseudonymous
memberships. It may copy only train-fit, validation and secondary-holdout
evidence and gold into partition artifacts. No metric, clinical label check,
model call, example selection or inference is permitted. No content row may be
printed, logged or shown to a human. Generic operation/failure codes and hashes
remain owner-only. No secret key, original-ID map, prediction file or P7 sealed
artifact is an exporter input.

Test bytes necessarily pass through the source reader. No test artifact or
test row detail may be written; reconciliation reports only the pre-existing
test patient count of 15. This exception confers no locked-test evaluation or
inspection right and does not change P7's terminal failure or permit its retry.

The output is deterministic: preserve source row/chunk order, copy gold rows
without interpretation, and remove staging legacy answers from evidence.
Reconcile membership and patient-question keys only, without inspecting answer
values/statuses. The source split remains 70/15/15; train becomes train-fit 55
plus reserved 15. Record source byte-hash pins, split/reservation self-hash pins,
partition byte/content/self-hash pins and counts with paired producer/consumer
semantics. Hash output buffers during creation, never by reopening holdout.

## Creation, sealing and exposure

Exposure means reading for evaluation, subject to the existing one-exposure
gate. Creation is not exposure. This distinction does not authorize holdout
development, manual inspection, readback, new arms or retries.

Holdout files are created directly under the fixed project guard vault with
`O_EXCL`, `O_NOFOLLOW`, mode `0600`, inside `0700` directories. There is no
unguarded temporary path or write-then-move interval. Their registry partition
is `secondary_holdout` from the outset; development readers reject it before
I/O. The exporter never reopens these files. Hashes come from the write buffer.
The owner is trusted to respect the guard; filesystem permissions are not a
security boundary against the same owner bypassing application code.

A separate durable project-level export-consumed marker is reserved before
the first full-source read. Changing output directories, processes, branches
or clones cannot reset it. Failure, partial output or a partial marker is
terminal for this export; no automatic rerun, resume, overwrite or reset.
Validation infrastructure retry rules do not apply to this one-shot export.
All artifacts, attempts and failures stay local and owner-only.

After export, full-source paths remain solely in the sealed historical export
contract, never the development registry. All subsequent P8 data readers accept
registered partition artifacts only. Original full sources are not deleted,
rewritten or moved. Existing frozen raw validation predictions remain the E0
sources; the exporter does not inspect or alter them.

## Evidence-limited historical independence

The claim is limited to the explicitly enumerated audit records:

> No recorded development artifact in the audited inventory references the
> reserved patients as a development population. Audited run, export and report
> manifests bind to validation, train-fit or locked-test membership. Reserved
> membership was not consumed by an input plan, silver artifact or evaluation.

Publish or attach the audit list and distinguish directly checked metadata,
historical public termination records, owner attestations and excluded files.
No inference from a filename alone is accepted. The owner attests that no
unrecorded manual inspection occurred and that real-data SFT export and silver
generation never ran because training was deferred first. The original
reservation and mechanical import/split preparation are not development uses
in this evidence-limited claim; describe them as preparation, not as physically
unread data. The new mechanical export is separately logged under this amendment.

The public short label is **secondary holdout, single exposure**. Do not use
"untouched by all prior development" or imply an exhaustive proof of non-use.
Unexpected contradictory evidence closes the gate; never replace the cohort.

## Migration and execution order

1. Commit this 1.0.0 → 1.1.0 specification migration before real source access.
2. Add a versioned export policy/contract, source pin checks, single-use marker,
   owner-only manifest/event chain and restricted CLI with no reset option.
3. Synthetic checks must cover exact membership, test zero output, no content
   display, no holdout readback, no label interpretation, overwrite/concurrency
   refusal, failure terminality, source hash-kind mismatch and guarded reads.
4. Reinstall, run the full suite/public checks, review, commit and verify hosted
   CI before real export. Freeze the concrete source-bound export contract.
5. Export once, complete the evidence-limited access manifest, then freeze/run
   E0 over isolated validation and the three original raw sources. Retain all
   candidates, selection record, fixed winner raw/safety views and full history.

Partition-envelope and E0 policy formats remain 1.0.0; their semantics have not
changed. New access manifests use schema 1.1.0, explicitly identify this claim
and bind the completed mechanical export. Older synthetic/access format tests
remain supported; new real execution must use the 1.1.0 gate.
