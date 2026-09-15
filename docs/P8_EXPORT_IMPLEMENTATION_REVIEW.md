# P8 1.1.0 export implementation review

Date: 2026-09-15. Scope: the owner-authorized mechanical export and amended
access gate, followed by E0. The protocol amendment was committed first as
`4e10f4d`; original reservation/split and P7 terminal artifacts are unchanged.

## Implementation reviewed

- The exporter accepts exactly the two named source JSON files plus frozen
  membership metadata and a source-bound contract. Source byte pins are copied
  from the original split; no secret key, ID map, raw prediction or P7 sealed
  artifact is accepted. Contract preparation performs metadata/stat checks only.
- A fixed project marker consumes mechanical export before content reads.
  Process/output/branch changes cannot reset it. Failure and partial writes
  remain terminal; no resume/reset command exists.
- The only full-source reader hashes and parses each source once, reconciles
  patient/question keys, and copies source-order evidence/gold. It neither
  inspects gold values nor scores, infers, selects examples or prints rows.
  Evidence excludes legacy answers; gold preserves source rows unchanged.
- Exactly six artifacts are produced for train-fit, validation and reserved
  holdout. Test membership is used only in memory for reconciliation; output
  metadata records only its original patient count. No test artifact exists.
- Holdout outputs are created directly inside the fixed `0700` guard vault via
  exclusive `0600` writes. File/content/self pins come from write buffers;
  there is no holdout readback. Creation does not consume evaluation exposure.
- The completed receipt binds source and partition hashes. Access schema 1.1.0
  binds that receipt, the exact registry and the evidence-limited claim plus
  explicit owner attestations. New real development rejects legacy 1.0.0
  access manifests. Full-source paths never enter the development registry.
- Public E0 projection constructs only allowed whole-split metric fields. It
  omits patient/question diagnostics, bootstrap intervals, extra counts and
  raw attempt details. E0 has no new inference latency to report.

## Self-review findings

1. The system Python lacked jsonschema; resource generation switched to the
   existing locked CPython 3.11.16 verification environment. No real source was
   accessed during that environment error.
2. New raw-source checking rejects even three mutually agreeing predictions
   when their benchmark differs from the frozen source. An older E0 fixture
   omitted this binding and failed the first synthetic run; it was corrected,
   with a separate wrong-benchmark regression test. The first run was 37 tests
   with one error, not a passing result or a real-data attempt.
3. Export schemas check contract/receipt structure; source and output pins carry
   separate storage/consumption semantics. Synthetic source self/file hashes
   are deliberately different. Sources are never rehashed as part of later
   partition access.
4. Tests exercise concurrent exports, new output paths, partial markers,
   source mismatch, output-write failure, exact membership, deterministic
   partition bytes, test exclusion, no display and no holdout readback.
5. A separate process alone would not satisfy the old zero-read policy. The
   new operation relies on the explicit 1.1.0 amendment; it is not presented as
   retroactive compliance with 1.0.0 or as proof of physically unread data.
6. A second targeted run was invalidated by reinstalling its environment while
   it was still running, causing missing-resource errors (40 tests, 15 errors).
   This was an orchestration mistake, not a product result. Its log is retained;
   the final installation is kept unchanged until the full suite finishes.

## Verification and execution record

Final non-editable reinstall/full suite: **481 tests, 478 passed, 3 conditional
skips, no failures**. Four local test-process shards completed in 152.93 seconds;
the installed environment remained unchanged during this run. Public-data
checks include new untracked files; schema and Markdown checks also passed.
The local summary is `/tmp/p8-1.1-full-suite-20260915.json`.

Code-round self-review: **PASS**. Hosted CI for the execution commit is still
required before real export. No real export or E0 result is claimed here.
Runtime audit records, source-bound decisions, export receipt, access manifest
and E0 attempt history must remain owner-only. The final outcome is recorded
separately after hosted CI for the actual execution commit is green.
