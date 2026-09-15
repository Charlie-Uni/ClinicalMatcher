# P8 execution status

Date: 2026-09-15. Status: `E0_owner_approved_v2_review_preparation`.

The owner-approved specification amendment was committed as `4e10f4d` before
source access. Export/E0 execution used `5e4c37af07963d7602528b014df74eb83000b57c`.
[Hosted CI passed](https://github.com/Charlie-Uni/ClinicalMatcher/actions/runs/34912759684):
481 tests, three conditional skips, no failures; the test step took 789.249
seconds. The installed package bytes were checked against the execution commit.

P8.1 completed under the 1.1.0 amendment. The evidence-limited audit enumerates
inspected metadata and distinguishes owner attestations and the public P7
termination record from direct checks. One mechanical export completed, with
source/partition pins and a durable event chain. No test artifact was created.
Holdout files were created directly in the guard vault without readback; their
creation did not consume evaluation exposure. Secret keys, the original-ID map
and P7 sealed raw were not exporter inputs.

The amended access manifest registers only partition artifacts and the three
original frozen raw validation predictions. Immutable original split metadata
may retain historical command strings; those are not executable input paths in
the development registry. Subsequent P8 development used the registered inputs.

E0 completed once with no retry or new inference. It retained all eight named
arbitration candidates and three raw baselines on the same validation grid.
The declared A2/A3/A4 equivalence held for each LLM source. Raw selection was
persisted before the winner's P4.3 safety view. All detailed metrics, bootstrap
intervals, provenance, attempts and hashes remain in the owner-only review
package; no result number is published by this status document.

Final stage self-review passed. The owner-only package includes `E0_REVIEW.md`,
`execution-review.json`, a reproducibility capsule, the source-bound contracts,
export receipt, evidence-limited audit and the E0 report/event chain. The E0
result table passed owner review ("通过 继续"). The decision was persisted in
`e0-owner-result-review.json` and linked by a `review_passed` event. An initial
metadata-only event append used an undeclared event enum and was rejected before
write; that failure was subsequently recorded using the existing event schema.
No clinical inputs were read and no experiment was repeated for this correction.

P8.3 review-stage components and the [complete v2 proposal](P8_V2_PROMPT_REVIEW.md)
are now prepared. Only public definitions and synthetic examples were used.
No real train-fit example selection, model request or E1 evaluation has run.
The reviewed prompt/example protocol, runtime budget verification and complete
runner are still required before E1. Secondary-holdout evaluation remains
unauthorized and unexposed.
