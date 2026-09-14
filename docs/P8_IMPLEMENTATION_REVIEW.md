# P8 implementation self-review

Date: 2026-09-14. Round: P8.1 guards and P8.2 E0 implementation.

This is a code-review record, not a real-data result, historical-independence
attestation, final configuration freeze, or holdout authorization. The current
work follows the [execution specification](P8_INFERENCE_OPTIMIZATION_SPEC.md).
No P7 sealed raw artifact or holdout clinical content was read during this work.

## Implemented boundaries

- `p8_safety.py`: distinct file/content/self hash pins with explicit paired
  storage/consumption semantics; exact membership metadata; predeclared
  partition-only input registry; development purpose checks before file reads;
  owner-only exclusive outputs; append-only event chain; durable lifetime
  exposure consumption independent of the run/output directory.
- `p8_e0.py`: raw-only A1–A4 over both frozen LLM sources, complete-grid checks,
  exact selected-row provenance, unchanged P1.5 metric functions over isolated
  rows, deterministic raw selection, then P4.3 1.1.0 pure row projection.
  The known-answer citation exception is restricted to the source-defined
  medical-decisions default. A2/A3/A4 are reported as equivalent policies.
- `p8_cli.py`: metadata preparation/checking, run-identity freeze, and guarded
  validation-only E0. It provides no full-corpus exporter, test split selector,
  synthetic-to-real switch, holdout authorization editor or holdout runner.
- Checked-in E0 policy and strict schemas cover access metadata, partition
  envelopes, hash pins, event records and arbitrated predictions. Real
  reservation identity belongs in an owner-only bound access manifest; no
  placeholder digest has been passed off as the original reservation hash.

## Review findings and dispositions

1. The existing environment failed to import the new module even after an
   editable reinstall. Validation moved to a new, isolated CPython 3.11.16
   environment, synchronized from `requirements/public-py311.lock` with hashes.
   These import failures are environment failures, not passing test evidence.
2. Self-hashes and file-byte hashes are deliberately unequal in synthetic
   fixtures. Producer and consumer round trips pass; swapped values or kinds
   fail. No helper replaces both kinds with one file digest.
3. The arbitration table covers 54 combinations of boolean/numeric/default
   question, known/unknown answer and usable/unusable rule. Raw LLM answers are
   not preprojected; unavailable rules cannot win an agreement case.
4. E0 does not call the old full-benchmark evaluator or any inference client.
   A synthetic comparison establishes equality with the P1.5 metric kernel.
   Winner selection is persisted before the safety view is produced.
5. The registry is a trusted, reviewed metadata boundary. Its file/content
   pins establish the identity of an already isolated artifact; they do not
   magically prove isolation before bytes exist. No arbitrary full-corpus path
   is accepted as a replacement for the missing isolated material.
6. The exposure primitive is not a complete P8.5 batch runner. It has no reset
   or retry path, and a partial consumption file remains terminal. The final
   batch still needs its own complete implementation/configuration review.
7. Self-review replaced an overly uniform E0 fixture with the actual differing
   source schemas. This exposed a rule/LLM field mismatch: rules 1.1.0 stores
   the rule configuration in `rule_set_sha256`, while model 1.2.0 stores
   `inference_config_sha256`. The consumer now freezes that per-arm field map;
   the end-to-end fixture uses the real schema distinction. The failing
   synthetic attempt was never a real-data experiment.
8. A membership/count check alone would not reject a resealed exchange between
   validation and holdout that preserved disjointness. The access manifest now
   embeds the original split/reservation metadata, validates their self-hash
   pins and reservation derivation, and reconstructs the exact populations
   before I/O. A synthetic disjoint population-swap test exercises this case.
9. Final review added rejection of reused attempt IDs, output-path checks before
   clinical input reads, and frozen Python/platform/evaluation dependency
   identities. Targeted regression checks and the final full suite passed.

## Verification status

- Final full suite: 466 tests across all 54 test modules; 463 passed, 3 skipped,
  zero failures. Four local test-process shards completed in 151 seconds.
  These are test processes, not delegated implementation agents.
- Verification used a fresh non-editable installation in
  `/tmp/clinicalmatcher-p8-final-20260914`, after lockfile synchronization with
  hashes. The regenerated public lock retained the same dependency pins/hashes.
- All 14 non-unittest CI guard/contract/synthetic workflow commands passed in
  a clean temporary checkout containing public code and synthetic fixtures.
  The original repository was not committed or pushed. Early temporary harness
  errors and the dirty-worktree rejection were corrected before this passing run.
- The suite covers the 54-case E0 truth table, actual rule/LLM schema differences,
  metadata population swaps, hash-kind swaps, purpose checks before reads,
  owner-only exclusive outputs, event chains and single-use exposure guards.
- Public-data checks explicitly include new untracked P8 files; the existing
  repository script scans tracked files only.
- Hosted CI has not been claimed for this uncommitted working tree.

Code-round self-review: **PASS**. Real-data stage gate: **CLOSED pending inputs**.
This pass covers P8.1 guard primitives and the E0 implementation; it does not
complete the source-bound P8.1 freeze or the required real E0 candidate table.
Local synthetic verification logs are in
`/tmp/clinicalmatcher-p8-reviewed-suite-summary.json` and
`/tmp/clinicalmatcher-p8-reviewed-shard-{0,1,2,3}-20260914.log`;
the CI-equivalent command inventory is `/tmp/clinicalmatcher-p8-smoke-summary.txt`.

## Real-run entry conditions still pending

Owner review accepted on 2026-09-15. The owner reported an independent
post-reinstall full-suite run (466 tests, including 3 conditional skips) and
passing public-data checks. The authorized sequence is commit/push and green
hosted CI, then bind supplied local metadata/input paths and existing owner-only
historical records, then run E0. E0 review must include the complete same-grid
candidate table, selection record, winner raw/safety views and attempt history
before the v2 text review. Slow-test tagging is a low-priority follow-up, outside
this reviewed implementation. This acceptance does not authorize holdout access.

The known repository artifact directories contain model/P5 feasibility
artifacts; the required original reservation and pre-isolated validation /
train-fit data were not located in the checked locations. Discovery inspected
directory names only, not clinical payloads or sealed P7 outputs. The owner was
asked for metadata/input locations, not a repeat implementation approval.

Before real E0, the owner-only source binding must provide:

- the existing frozen split and original calibration-reservation metadata;
- historical-independence review evidence, including all uses listed by the
  protocol and any unresolved items;
- a reviewed registry of already isolated validation evidence and gold plus
  the three original raw prediction artifacts, with precise file/content pins;
- exact source membership and configuration checks, followed by an E0 run
  contract frozen before scoring.

No real score, improvement, actual few-shot pool, approved optional E2 model or
final holdout arm set exists for P8 yet. P8.1 real-data readiness and P8.2 real
evaluation remain open. Do not advance to E1/E2/E3 real experiments or describe
this code round as the final holdout-review stage.

## CLI sequence after the required isolated metadata is available

Use `clinical-matcher-p8 --help` for arguments. All material/output paths must
remain owner-only and local; all commands require the restricted-data
acknowledgement. No command prints a patient row or metric.

1. `prepare-access` binds the supplied split/reservation/review/registry
   metadata without opening clinical artifacts. An unverified review keeps
   development closed.
2. `check-access` checks that the metadata-bound P8.1 development gate passes.
3. `prepare-e0` binds the five registered input IDs and the exact public
   implementation bytes. It does not open evidence, gold or predictions.
4. `run-e0` checks the frozen configuration before reading registered
   validation-only inputs, creates a new attempt directory, and retains the
   complete raw candidate table, fixed selection, P4.3 view and report.

An existing output directory is never reused. Infrastructure retry requires a
new attempt and preserved prior records under the protocol; it is never a
reason to replace an already successful model response or reopen holdout.
