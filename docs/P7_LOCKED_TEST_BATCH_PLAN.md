# P7 locked-test single-batch plan

Status: `owner_approved_protocol_not_test_authorization`

Protocol version: `p7-locked-test-batch-plan/1.0.0`

Prepared: 2026-09-03, before any locked-test prediction, label, metric, or
patient-level output was inspected.

The project owner approved D1--D6 on 2026-09-03. This protocol is still not
authorization to run the locked test. The resulting machine-readable contract
must pass its validator, P7.1 must be explicitly frozen, and the owner must
separately authorize P7.2 before execution can begin.

## Purpose

P7.2 is the project's only locked-test exposure. The complete comparison set,
evaluation set, failure rules, and disclosure boundary must therefore be
fixed before any test result exists. Test day is execution of a reviewed
contract, not another model-selection or protocol-design round.

P4.3 is a deterministic before/after view of a prediction set. It is not an
additional trained/model arm. The proposed batch consequently contains three
base prediction arms and six reportable views.

## Non-negotiable boundaries

- Do not read locked-test membership, labels, predictions, examples, or
  aggregates while preparing or reviewing this draft.
- Do not execute a command with `--split test` until the final P7.1 contract is
  owner-approved and frozen.
- Do not tune a prompt, rule, threshold, unit assumption, verifier, or
  projection from locked-test output.
- This batch is the locked test's only exposure. Do not add, remove, or replace
  an arm, and do not change a threshold, after the batch begins. All P5/P7
  reports must reuse the same immutable batch artifacts.
- Do not run the P5D decomposition test split. Its entry gate was not met.
- Keep every patient-level artifact, identifier, prediction, trace, and report
  in the authorized owner-only environment with mode `0600`.
- Do not commit test-derived aggregates until the separate disclosure review
  has passed. P1.3 still has no governance-approved small-cell threshold.
- Personal files, including `docs/PROJECT_TODO.md`, are outside cleanup,
  staging, and packaging scope.

## Frozen dataset and split identity already available

The following identities were read from existing aggregate metadata only; no
test membership or label was opened.

| Item | Frozen identity |
| --- | --- |
| Dataset | `MIMIC-IV-Ext-Apixaban-Trial-Criteria-Questions/1.0.0` |
| Benchmark content SHA-256 | `0161fe1762ec48aff7a7c78f1fa72560dfec03bd91f885f2349ec65bca23503d` |
| Benchmark manifest SHA-256 | `8b2c295c2a95dd2a2f8e87d8110146e80d958797594b916edecaeb02aabba3ea` |
| Staging-corpus SHA-256 | `f2a37e349bc0eabac2c8b8eae1cda4c859e31865a0c847384f7a125ea85109df` |
| Question-catalog SHA-256 | `c51e07b98c6c380545685ae0585644fcb8eb5a5b5a2e2fee936f2e0dca15bc8f` |
| Frozen split manifest self-hash | `2176996ac84c2df7c4766c0b489befde640921790cc36e25a3893ae443f8cebd` |
| Frozen split file SHA-256 | `1af03ef3696e5a709ec345456739ce23bf559b1660981ff05240a51c3ca78952` |
| Split policy | `grouped_multilabel_greedy/1.2.0`, seed `17` |
| Locked-test size | 15 patients; exact membership remains unread |

The final contract must bind both the split self-hash and file hash. The
self-hash proves the canonical manifest identity; the file hash detects a
byte-level local replacement.

## Proposed base arms and paired P4.3 views

The validation artifact hashes below are provenance anchors only. Locked-test
artifact hashes do not exist yet and must not be guessed.

| Base arm | Frozen method/config identity | Validation raw artifact | Proposed locked-test views |
| --- | --- | --- | --- |
| `rules_1_0_0` | `clinicalmatcher-deterministic-extractor@1.0.0`; canonical rule-set SHA-256 `ffd14a43f6e0d21b0c87b9e7facdfe68aeb7ab407a05269d1faca2b31e66c8f9`; resource file SHA-256 `3bf64349427225e5fe54d8ec233bb39be08f854deb651fc77ab70c13009fc04e` | `fcb43ec18a2f15d4e06cefe120a86ae1826aecc64363c2d969b94ef41b4876ab` | raw + deterministic P4.3 projection |
| `llama31_structured_1_0_0` | canonical inference-config SHA-256 `d26f4b6554d15a03e6a2a241718147a41f2c76dcd75f5383051abff009c435b9`; contract file SHA-256 `421d94a4a8d05fc1147f1e4a2c9b3bbe3a1e6cdc841fa3f28f6d4a45cdeb76a2` | `6fcc96bccd4ce52cff61cd849b9bd3db2cc029cd242a8570707f2e98d679a4f1` | raw + deterministic P4.3 projection |
| `llama31_long_context_1_0_0` | canonical inference-config SHA-256 `693452e427cda1210d9f280041cd2727a772a23c6f11f3082f03e5d920d02217`; contract file SHA-256 `9c0c295b4a3c880c10a5d49d46e1732e0d61ef4852dbd082879cbe15b8c5112b` | `29fad18ed6b864e4cdc40fa1a1a5db5d1255a4d87b2de9557b27649172a652d9` | raw + deterministic P4.3 projection |

Both Llama arms use the same pinned runtime/model/prompt where their matched
contracts require equality:

- Llama 3.1 8B Instruct, Q4_K_M;
- Ollama manifest SHA-256
  `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`;
- model blob SHA-256
  `667b0c1932bc6ffc593ed1d03f895bf2dc8dc6df21db3042284a6f4416b06a29`;
- Ollama `0.32.6`, loopback only, no cloud fallback;
- prompt `apixaban-23-facts-structured-1.0.0`;
- temperature `0`, seed `17`, no repair/retry of schema-invalid content.

The structured arm uses the frozen 8,000-character complete-chunk prefix and
16,384-token context. The long-context arm uses every complete evidence chunk
without a character cap and a 32,768-token context. These policies must not be
changed for test.

### P4.3 version and hash semantics

Current policy `1.1.0` has canonical policy SHA-256
`0946bb21ec4ab8e693c3abc15d6625ae09ead4acbd3700ae42e484b043f36fa8`.
It keeps the source-question-defined
`med_decisions=absent, value=false` empty-citation exception. All future real
projections are required by `docs/ABSTENTION_POLICY.md` to use `1.1.0`.

The existing structured and long-context validation projections are historical
policy `1.0.0` artifacts:

| View | Historical validation projection SHA-256 | Historical projection config SHA-256 |
| --- | --- | --- |
| structured + P4.3 `1.0.0` | `8fa9aa6ce9d379c71fc594981a8d20b1aec04b09e8ef8b0a955f28d7b518cc25` | `306184aa9a3347fb69a8dc9d77aefdba209a42eefbfc243a79f0888e320ce5de` |
| long-context + P4.3 `1.0.0` | `ccdd1e417253ece9b9a78d0975dfd0a116716b8c09bd673772b3806295c36ef0` | `9a512404d817711110a9e0cdc524060e4d30459f6170ef64ba373393a8fc606c` |

They must not be relabeled as `1.1.0`.

A future P4.3 projection's `inference_config_sha256` includes the parent
prediction file SHA-256. Its exact value therefore cannot exist before the raw
locked-test artifact exists. The freeze must instead bind all of the following:

1. the base arm's pre-known canonical config hash;
2. the P4.3 policy version and canonical hash;
3. `verifier_conflict_status=not_evaluable` and the canonical empty-list input
   SHA-256
   `4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`;
4. the rule “the projection parent is exactly the raw artifact produced by the
   named base arm in this same batch”; and
5. the deterministic config-hash formula already implemented in
   `apixaban_abstention.py`.

The batch manifest may fill the raw/projection artifact hashes only as measured
outputs. Its validator must recompute the relationship. This is mechanical
provenance completion, not a post-test configuration choice.

## Proposed evaluation matrix

| Evaluation | Rules raw/projected | Structured raw/projected | Long-context raw/projected | Claim boundary |
| --- | --- | --- | --- | --- |
| P1.5 mixed typed-fact metrics and patient-cluster bootstrap | both | both | both | exact-source extraction, not clinical equivalence |
| P4.3 coverage–risk operating points and reason counts | paired report | paired report | paired report | deterministic safety operating points, not calibrated confidence |
| P4.5 mutually exclusive observable attribution `1.1.0` | both | both | both | diagnostic, not causal; five requested causal dimensions remain `not_evaluable` |
| Runtime: latency, tokens, memory, truncation/exposure | every request + aggregate P50/P95 | every request + aggregate P50/P95; projection overhead separate | every request + aggregate P50/P95; projection overhead separate | projected views inherit the parent model run and must not double-count model inference |
| P4.7 three-axis single-trial diagnostic | owner decision required | owner decision required | owner decision required | agreement with legacy rule-derived reference, not clinical accuracy |

P1.5 must emit typed exact match, boolean accuracy/macro-F1/micro-F1/unknown-F1,
numeric-status metrics, numeric value coverage/MAE/exact-source tolerance
accuracy, per-question metrics, and patient-cluster bootstrap intervals.

P4.5 must retain its existing six observable, mutually exclusive categories and
reconcile them to the complete 15 × 23 grid. Patient-local citations are not
gold evidence. Retrieval failure, reasoning failure with relevant evidence,
time error, negation error, and true false-abstention remain `not_evaluable`
unless new independently reviewed evidence/trace data is approved before the
test run. No such data currently exists.

P4.7 must keep its three axes on the same page:

1. intended(gold facts) versus mentor legacy rule-derived reference;
2. intended(model facts) versus intended(gold facts); and
3. intended(model facts) versus mentor legacy rule-derived reference.

Its mentor result, mentor candidate CSV, intended-rule contract, unit-adapter
contract, and ID-map hashes must be pinned in the final run contract. The result
is never described as clinical eligibility accuracy.

## Explicit exclusions from the locked batch

- BM25, MedCPT, and RRF are excluded from the proposed final batch. They remain
  frozen validation diagnostics without independent evidence relevance gold
  and were not selected as final input policies.
- LoRA/SFT is excluded because no retained real adapter exists; P5.1 is
  formally deferred for the frozen local 8B × long-context × MLX hardware
  configuration family.
- P5D decomposition is excluded because the initial dev comparison did not
  meet its test-entry gate. Its locked test remains untouched.
- Statistical calibration, Brier score, ECE, and conformal guarantees are
  excluded because the frozen predictions expose no approved continuous
  confidence score and P4.4 remains deferred.
- MedicalGPT and LightRAG are references only; neither is claimed as an
  executed component.

## Proposed single-batch execution boundary

The final executable contract should separate work into four ordered phases:

1. **Preflight without test labels:** clean worktree; exact code/config/input
   hashes; owner-only output directory; disk headroom; Ollama version/model
   digest; loopback-only networking; schema/CLI synthetic checks; and proof that
   no final output path exists.
2. **Raw prediction generation:** rules, structured Llama, and long-context
   Llama run under their frozen configurations. No prediction content or
   aggregate is displayed. Each artifact is immediately schema-validated and
   hash-recorded.
3. **The one gold-backed evaluation batch:** derive all approved P4.3 views and
   run the complete predeclared P1.5/P4.5/P4.7 matrix without interactive
   inspection or conditional branching. Intermediate outputs remain in a
   private staging directory.
4. **Atomic finalization:** only after every mandatory artifact validates and
   all totals reconcile, write one self-hashed batch manifest and expose the
   owner-only review package. Public disclosure is a later, separate review;
   it cannot trigger inference or metric recomputation.

No test metric may choose a component. An arm failure remains a measured
failure under the frozen denominator; it cannot be replaced by a newly selected
model or prompt.

Each base arm must write an owner-only per-request latency trace and a separate
aggregate P50/P95. The trace may contain restricted patient IDs and must never
enter the public package. The disclosure projection may expose only the
predeclared whole-split latency P50/P95.

## Owner decisions frozen before implementation

### D1 — P4.3 policy used by the final batch

**Recommended:** use current policy `1.1.0` for all three projections, because
the repository explicitly requires it for every future real projection. Before
P7.1 is marked frozen, mechanically regenerate all three validation projections
under `1.1.0` and validate their lineage. The decision to use `1.1.0` must be
made now and must not depend on those validation metrics.

This creates a documented tension: the prior P4.7 validation selection used a
historical long-context P4.3 `1.0.0` artifact. Any new P4.7 validation report
under `1.1.0` must be additive, explicitly post-observation, and must not
replace or reinterpret the frozen `1.0.0` result.

Owner decision: **approved as recommended on 2026-09-03**.

### D2 — Scope of P4.7 across the comparison views

**Recommended:** run P4.7 only for the final system view,
`long_context + P4.3 1.1.0`. Run P1.5 and P4.5 on every raw/projected view.
This keeps the three-axis endpoint attached to the validation-selected final
system and avoids promoting unselected P4.7 alternatives on the locked test.

Alternative: predeclare P4.7 for all six views. This is more complete but adds
five test-only endpoints that were not compared under the frozen P4.7
validation protocol.

Owner decision: **approved as recommended on 2026-09-03**.

### D3 — P4.5 representative-case review

**Recommended:** keep aggregate P4.5 reports for all six views and predeclare an
owner-only, deterministic lowest-hash sample of one final-system error per
observed attributable category. Review occurs only after the immutable batch is
complete, cannot change a label/configuration, and publishes no restricted
example. This can close the existing representative-case-review gap without
claiming causal evidence attribution.

Alternative: retain aggregate attribution only and leave P4.5 explicitly
incomplete.

Owner decision: **approved as recommended on 2026-09-03**.

### D4 — Infrastructure failure and retry boundary

**Recommended:** before any gold-backed metric is materialized or exposed, an
exact-config infrastructure failure may be retried once after a logged preflight
fix; no model/prompt/input setting may change. After the gold-backed phase
starts, any failure is final for the affected output and no inference or
evaluation rerun is allowed. No partial result may be inspected to decide the
retry.

Alternative: prohibit every retry, including failures before gold exposure.

Owner decision: **approved as recommended on 2026-09-03**.

### D5 — Public disclosure while P1.3 is unresolved

**Approved amended policy:** generate the complete exact test report owner-side.
Before results exist, freeze an exhaustive allowlist containing only these
whole-split aggregates for possible public release:

1. typed exact-match rate;
2. boolean macro-F1;
3. numeric-status macro-F1;
4. abstained/unknown count for the complete split denominator;
5. per-base-arm request-latency P50; and
6. per-base-arm request-latency P95.

Per-question, per-class, support, confusion-matrix, patient-level,
representative-case, rule-level, unit-diagnostic, and P4.7 three-axis values
remain owner-only until P1.3 is formally resolved. The release candidate must
be created by a strict projection that rejects every non-allowlisted field; no
metric can be chosen after seeing its value. This is a project-level disclosure
policy based on the existing validation precedent, not institutional approval.

Owner decision: **approved as amended on 2026-09-03**.

### D6 — Personal draft and clean-worktree precondition

P7.1/P7.2 require a genuinely clean worktree, while the restored personal file
`docs/PROJECT_TODO.md` is intentionally untracked and outside project cleanup.

**Recommended:** before P7.1 freeze, the owner adds this exact path to the
repository-local `.git/info/exclude`. This keeps the file in place, changes no
tracked repository content, and prevents automation from treating it as a
project artifact. The implementation must not edit, move, delete, stage, or
commit the file.

Alternative: the owner moves the file to an owner-chosen persistent location
outside the repository. A volatile temporary directory is prohibited.

Owner decision: **approved as recommended on 2026-09-03; owner action remains
required before P7.1 freeze**.

## Work required after owner approval, before P7.1 can be frozen

1. Preserve this approved protocol as version `1.0.0`.
2. Add a strict JSON Schema and machine-readable locked-test batch contract.
3. Implement a validator/orchestrator that has no conditional arm-selection
   path and never prints intermediate results.
4. Add synthetic-only tests for hash lineage, exact arm coverage, P4.3 parent
   derivation, no-overwrite behavior, failure semantics, report reconciliation,
   and public-output suppression.
5. Generate the approved P4.3 `1.1.0` validation projections and any required
   additive validation diagnostic without touching test.
6. Pin the final implementation commit, every code/resource hash, hardware
   specification, and complete command in the machine-readable contract.
7. Reinstall the working tree, run the full public test suite and public-data
   guard, require a genuinely clean Git status, push, and confirm CI green.
8. Obtain a final explicit owner statement: “P7.1 frozen; authorize the one
   P7.2 locked-test batch.”

Until all eight steps are complete, locked-test execution remains prohibited.
