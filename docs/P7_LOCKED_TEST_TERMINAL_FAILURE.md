# P7 locked-test terminal failure record

Status: `terminal_failure_no_rerun_permitted`

Incident date: 2026-09-03

This record describes the sole authorized P7 locked-test exposure. It contains
no patient text, patient identifier, prediction value, label, metric, or
fine-grained result.

## Frozen identity

- Owner-authorized contract SHA-256:
  `6c6298636941e15c717d63af25711aea181234728fe18a2826cf38dd95acc652`
- Execution commit:
  `31e7aa7dd3ef04ff5145181d476ba8f981b74c40`
- Pinned implementation commit:
  `251bf2fa2eceffa0c75878f5ef7c3f8caaeaa726`
- Execution-preflight CI: run `33693250290`, conclusion `success`
- Attempt: exactly one; the approved pre-gold retry was not used

The checked-in authorization contract is preserved unchanged after the
attempt. It must not be edited to make the failed execution appear successful.

## Event sequence

The owner-only event chain records `attempt_started`, `raw_complete`,
`gold_phase_started`, and `terminal_failed`, in that order. All three frozen raw
prediction arms were present before gold access began. Exact timestamps and
artifact commitments remain owner-only.

The gold-backed phase failed before a P4.3 projection, P1.5 report, P4.5 report,
P4.7 report, representative case package, public-release candidate, or final
batch manifest was created. No test metric was materialized or displayed.

The benchmark file was opened for the frozen byte-hash check after
`gold_phase_started`; this counts as the one test-label exposure even though no
label or metric was printed. The attempt cannot be reclassified as pre-gold.

## Root cause

The machine-readable contract stored
`8b2c295c2a95dd2a2f8e87d8110146e80d958797594b916edecaeb02aabba3ea`
in `dataset.benchmark_manifest_sha256`. That value is the manifest's canonical
self-hash stored inside the JSON document. The runner used the same field as an
expected byte-level file SHA-256, but the two hashes differ by design. The
byte-level value remains in the owner-only incident evidence.

All other frozen gold-input byte hashes matched their files. The second input
check in the gold-backed phase therefore failed deterministically before any
evaluation function or projection ran. This was a contract/hash-semantics bug,
not evidence that the dataset, model, or prediction files changed.

The synthetic test helper replaced every dataset hash with the corresponding
fixture file hash. It consequently could not represent a field whose canonical
document self-hash intentionally differs from its file-byte hash. That missing
test case allowed the ambiguity to pass preflight and CI.

The resulting review rule is broader than this one field: every hash must be
audited as a producer/consumer semantic pair. Review must establish both what
the producer stores and what the consumer assumes, because schema-valid length
and hexadecimal format do not distinguish a canonical document self-hash from
a byte-level file hash.

## Preserved owner-only raw artifacts

The deterministic-rules, structured-Llama, and long-context-Llama raw
prediction artifacts completed before gold access. Their predictions, latency
traces, run reports, exact file hashes, event timestamps, and event documents
remain owner-only. All preserved files were verified as mode `0600`; none is a
public evaluation result.

## Consequences and non-actions

- P7.2 did not produce a final locked-test performance report.
- No locked-test accuracy, F1, coverage-risk, error-attribution, P4.7, or public
  disclosure number may be claimed.
- The raw prediction artifacts must not be evaluated in a second command. That
  would rerun the gold-backed phase after exposure and violate D4.
- The failed batch is not replaced, diluted, or re-described as a successful
  run. Validation results remain the only available performance evidence.
- A future contract may separate `benchmark_manifest_self_sha256` from
  `benchmark_manifest_file_sha256` and add a non-equal-hash synthetic fixture,
  but such a repair cannot authorize another run on this locked test.

This terminal failure is a reproducible engineering result and a limitation of
the final project package, not a basis for post-test protocol changes.
