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
- An intermittent single-test error was observed four times and never on an
  isolated rerun. Retained verbose logging finally captured its identity:
  `test_p8_e0...test_end_to_end_has_eight_candidates_and_selects_before_projection`
  raising "Frozen E0 implementation, inputs or configuration changed". The
  E0 freeze pins the exact bytes of every `.py`/`.json` file in the
  installed package directory and re-derives them at validation time; on
  several affected runs the operator (this agent) executed
  `uv pip install --reinstall` while a background test run was in flight,
  so the package bytes changed between the in-test freeze and its
  validation. That check is working as designed. Operating rule from here:
  no reinstall or package write of any kind while a test run is in
  progress, and full-suite runs execute with nothing else touching the
  environment. The earlier suspected latency-filter zero-duration bug was
  real and is fixed with a frozen-clock regression test, but it was not
  this flake's cause. Hosted CI, which never reinstalls mid-run, never
  reproduced it.
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

## Addendum (2026-09-17): attempt #2 pilot — quote matching and the F4 cost

With the flat-variant schema the pilot's 23 real requests all produced
complete seven-field objects (eval_count 86–172 vs 16–31 before), but
22/23 were still rejected — every one by the supporting-quote check, per a
content-free parser-error taxonomy. A synthetic multi-line note reproduced
the mechanism: the model collapses runs of whitespace and line breaks when
copying, so raw-substring identity fails on real note formatting. A
content-free classification of the 22 failures: 9 differ only in
whitespace/NFKC form, 1 only in case/punctuation, 1 quotes a chunk the
model did not cite, and 11 are paraphrases or other non-verbatim copies.

Disposition: the parser now compares quotes under whitespace-collapsed
NFKC normalization (`quote_match_policy` =
`whitespace-nfkc-normalized-verbatim/1.0.0`, recorded in the E1 contract);
case, punctuation and wording must still match exactly, and the frozen
`parse_policy` text is untouched because it never defined quote matching.
The case/punctuation and paraphrase classes are deliberately not relaxed:
they are the real cost of the owner-approved F4 hard constraint, which the
sealed ablation matrix already anticipates as variant `2.0.0-a4`. The
frozen v2 therefore runs as attempt #3 first; if it fails to beat the
incumbent, `2.0.0-a4` is triggered per the matrix rather than relaxing the
parser further. The pilot-acceptance gate held: the full run was not
started on a 1/23 pilot.

## Current status (2026-09-17)

Persisted owner-side: the dual-review decision, example plan, the
structurally empty example set, contracts for attempts #1 and #2, both
pilots, and the attempt #1 full run (all 345 requests invalid). Attempt #2
stopped at its pilot by the acceptance gate. Attempt #3 (flat-variant
schema plus normalized quote matching) is the next real step: freeze a new
contract against the CI-verified commit, run the pilot, inspect its outcome
distribution, and only then run the full validation set. If frozen v2 does
not beat the incumbent `long_context.A1` (0.6290), ablation variant
`2.0.0-a4` is triggered per the sealed matrix; E3 applies the E0-selected
A1 policy to whichever v2 run is retained. E2 remains an owner decision;
the secondary holdout remains unauthorized and untouched.

## Addendum (2026-09-17): attempt #3 result and the F4 ablation trigger

Attempt #3 (contract `ff13c842…`, flat-variant schema, normalized quote
matching, engine 0.34.0) completed all 345 validation requests:
139 accepted, 206 `invalid_output`, typed exact match **0.1855** (64/345),
unknown 215, request latency p50 26.6 s / p95 57.6 s. Applying the
E0-selected A1 policy (E3, `prompt_v2.A1`) filled 70 abstentions from
cited rule answers and reached 0.3130 raw and safety views alike. Both
remain far below the incumbent `long_context.A1` (0.6290) and the v1
reference (0.6116). These are validation development diagnostics.

The sealed ablation matrix's trigger condition is therefore met. The
evidence isolates factor F4: 60% of requests were rejected solely by the
known-answer quote hard constraint after whitespace normalization, with
paraphrase/case/punctuation failures dominating. Variant `2.0.0-a4`
(quotes optional, citations still required, everything else identical) is
triggered as the single-factor test of F4. It is implemented as prompt mode
`v2-a4` with its own prompt version, recorded `ablation_variant` and
`removed_factor` contract fields, a whitespace-tolerant single-sentence
prompt substitution, and a parser that nulls unverified quotes with a
`quote_unverified` trace marker instead of invalidating the typed answer.
No other factor is changed, and no variant runs by default.

## Addendum (2026-09-17): a4 pilot passed, a mode-gate defect found before launch

The a4 contract (`405fa85e…`, variant `2.0.0-a4`, engine 0.34.0) was frozen
against the CI-verified commit `5b232e1` and its first-patient pilot ran
after an explicit unload: **23/23 accepted** (attempt #3's pilot: 10/23),
no `invalid_output`, prompt tokens 3,755–4,004 per request, wall p50
24.5 s / max 44.0 s, estimated full validation 8,572 s (below the 10,800 s
per-question budget). Pilot rows: 16 present, 2 absent, 5 unknown; every
known answer carried at least one evidence citation. Aggregates only; no
clinical content was displayed.

Before starting the full run I inspected the pilot document and found a
runner defect: `timing_mode` reports the affordability grouping ("v2"
per-question affordable, "v2b" grouped fallback) and `run_e1` compared
that literal against the contract mode, so every `v2-a4` contract would
have been refused with "Contract mode differs from the pilot timing
decision". The a4 tests covered grouping, schema, parsing and contract
fields but not the pilot-to-run gate, which is the self-review miss. The
same comparison would also have refused a `v2b` contract whose pilot came
in under budget.

Fix (runner 1.0.1 → 1.0.2): the pilot decision now records
`timing_grouping` (the affordability verdict, unchanged semantics) and
`mode` becomes the mode the full run must use: a per-question contract
(`v2`, `v2-a4`) keeps its own mode while affordable and falls back to
`v2b` otherwise; a grouped contract keeps `v2b`. There is no grouped
ablation variant, so an over-budget variant pilot still refuses the full
run. Regression tests: a4 pilot decides `v2-a4` and the full run proceeds;
an over-budget a4 pilot (clock patched to 500 s per request) falls back
and is refused; a `v2b` contract's pilot keeps `v2b` and runs. The E1/E3
modules pass 23/23 after reinstalling the snapshot with nothing in flight.

Consequence for the real chain: the sealed a4 pilot carries decision mode
"v2" under runner 1.0.1 and stays on record as attempt a4-#1 (pilot only,
never used for a run). The contract embeds the runner version, so a4 is
re-frozen as `e1-contract-v2-a4-r2.json` under 1.0.2 and the pilot is
re-run before the full validation run. Cost: one additional 23-request
pilot (about ten minutes); no result is discarded or reinterpreted.
