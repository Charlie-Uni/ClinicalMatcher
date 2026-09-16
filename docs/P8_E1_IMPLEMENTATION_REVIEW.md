# P8.3 / E1 runner implementation self-review

Date: 2026-09-16. Scope: E1 runner (`p8_e1.py`), CLI subcommands
(`approve-v2`, `plan-examples`, `build-examples`, `prepare-e1`, `pilot-e1`,
`run-e1`), frozen ablation matrix resource, and synthetic tests. No real
example set, pilot, or validation inference has run.

## Role disclosure

From this round the implementer and the per-round reviewer are the same agent
(role reassigned by the owner on 2026-09-16). Earlier rounds separated these
roles. Mitigations: the executed spec was written and cross-reviewed while the
roles were separate; every finding below is recorded rather than silently
fixed; the owner remains the approver for the dual-review decision, E2 start,
and the holdout batch.

## Findings and dispositions

1. Two implementation bugs were caught by self-review before any test ran:
   a row-list concatenation error in `run_e1` that dropped and duplicated
   grid rows, and use of `model_id()`'s pinned identity string as the Ollama
   API model name, which would have failed against the real runtime. Both
   were fixed and are visible in the working history of this change.
2. Test fixtures initially failed because macOS temporary directories cross
   the `/var` symlink; resolved by adopting the existing resolved-path
   fixture pattern. The private-path symlink guard behaved as designed.
3. The context-budget precheck floor (2.0 chars/token) deliberately
   over-estimates token counts so a precheck pass can never mask a real
   overflow; the authoritative check is the runtime's own
   `prompt_eval_count + num_predict <= num_ctx` post-check. Both are frozen
   in the contract and covered by tests.
4. Latency percentiles exclude the flagged cold-start slot and zero-wall
   over-budget entries; the source definition is a frozen constant and the
   pilot's slot timings remain separate, per the spec's timing rules.
5. Pilot reuse requires an exact contract self-pin match, the same frozen
   first patient, and a mode that matches the pilot's timing decision; a
   contract/pilot mode mismatch is rejected (tested).
6. Transport failures retry exactly once after a fixed 5-second wait and
   only when no model content was received; any received content is final.
   Retries, outcomes, and estimated prompt tokens are logged per request
   with aggregate-only stdout.
7. The ablation matrix (F1 per-question split, F2 few-shot, F3 boolean
   absent semantics, F4 quote constraint) is sealed as
   `p8-e1-ablation-matrix-1.0.0.json` and pinned by every E1 contract;
   variants exist only as declared identifiers until triggered on
   validation.

## Verification status

- Targeted: 9 new E1 tests; 37 P8-family tests pass together, and the three
  adjacent modules (prompt/export/P7) pass as a separate 48-test batch.
- An intermittent single-test error was observed three times (the first
  full 516-test run, a 77-test and a 79-test P8-family batch) and never on
  an immediate identical rerun; every sighting lost the identity to
  truncated reporting, and every affected batch contained `test_p8_e1`.
  Probable cause found by code review rather than capture: `run_e1`
  filtered latency samples by the truthiness of `wall_seconds`, so a
  synthetic request measuring exactly 0.0 seconds was dropped and, when all
  non-cold-start samples measured 0.0, `_percentile` raised on an empty
  list. The filter now keys on outcome only, and a frozen-clock regression
  test forces every request to 0.0 seconds. If the flake recurs after this
  fix, the retained verbose logs will name it; hosted CI never reproduced it.
- Public-data guard passes; the final full-suite result and hosted CI are
  recorded against the actual implementation commit before any real E1 step.

## Addendum (2026-09-16): structurally empty real example set

Real example selection returned zero examples for all 23 questions:
1,119/1,265 train-fit rows were rejected as `source_without_local_citation`
and 146 as `source_unknown`. The frozen eligibility rule requires source
gold to carry patient-internal citations, but the official release contains
no evidence links (0/2,300 per the P3.5 audit) — the premise is
structurally unsatisfiable on real data, and synthetic fixtures masked it
because they carry citations. Per the predeclared shortfall fallback, E1
proceeds with an empty demonstration set; factor F2 is inert for this run
and the F2 ablation variant is meaningless against it. A protocol revision
that sources citations from deterministic-rule predictions (the P5 D-silver
precedent) is a possible later variant and remains an owner decision; it
was not adopted unilaterally.

## Addendum (2026-09-16): engine version deviation re-pinned explicitly

The live loopback runtime is Ollama 0.34.0 while the inherited v1 contract
pins 0.32.6; the pinned model digest matches exactly. Rather than bypassing
the check, the E1 contract now pins its own probed engine version, records
`engine_version_deviation_from_parent`, and runtime verification requires
the live engine to equal the probe and the model digest to equal the parent
pin. The engine difference is a declared confounder for any v1-vs-v2
validation comparison; the planned same-holdout v1 control runs under the
same current engine, which removes it there.

## Addendum (2026-09-16): E1 attempt #1 failed by a pipeline defect

The first real E1 run (contract `45d95a41…`, engine 0.34.0) completed all
345 requests, and every one was rejected as `invalid_output`
(typed exact match 0.1188 = exactly the 41 gold-unknown rows). A synthetic
replay showed the model emitting two-field objects such as
`{"fact_status":"present","value":true}`. Root cause: the output schema
placed an outer `required` list on the item object and refined status/value
pairs in a nested `oneOf`; the runtime's schema-to-grammar conversion does
not propagate the outer `required` through that refinement, so only the
refined fields were generated. A minimal flat schema with nested
`required`/`const`/`enum` was honored exactly on the same runtime.

Disposition: `output_schema` now emits one complete flat variant per
(question, status), which additionally encodes the known-answer quote
requirement, the `med_decisions` exception, the patient's real evidence-ID
enumeration and "numeric never absent" directly in the grammar;
`uniqueItems` (unsupported by grammar converters) is dropped from the
schema while the parser still enforces uniqueness. The E1 contract records
`output_schema_version` and bumps to 1.0.1. Attempt #1 artifacts are kept
unmodified as a failed attempt; a full-run rerun on validation is
permitted development iteration and will be recorded as attempt #2.

Process defect owned here: the full run was launched without inspecting
the pilot's outcome distribution, which already showed 23/23
`invalid_output`. The runner now refuses a full run whenever the pilot has
zero accepted responses (tested), and the operating rule is that pilot
outcomes are inspected before any full run.

## Pending before real E1

Persist the real dual-review decision, example plan, and example set;
freeze the run contract with a live runtime probe; timed pilot; full run.
E2 remains an owner decision; the secondary holdout remains unauthorized
and untouched.
