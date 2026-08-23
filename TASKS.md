# ClinicalMatcher implementation tasks

This is the authoritative implementation plan. The resume-first mainline is a
note-grounded clinical fact-extraction and evidence-verification system built
on the 100 patients, 23 questions, and 2,300 human-reviewed answers in the
official MIMIC-IV-Ext Apixaban dataset. Multi-trial ranking remains a supported
research extension, but it is not allowed to block the mainline.

“Done” means that code, tests, documentation, and a reproducible command exist.
An isolated notebook, training loss, hand-edited output, or unreviewed result
file is not done.

## Scope and claim boundary

The main benchmark asks whether a fact is supported by one released clinical
note. It does **not** provide gold for complete patient eligibility, multiple
trials, trial ranking, or all facts available elsewhere in the EHR.

The primary output is therefore a typed fact assessment:

```text
present | absent | unknown
+ optional typed value and unit
+ supporting evidence IDs
+ abstention and validation metadata
```

Eligibility is a separate deterministic mapping from an extracted fact to a
specific inclusion or exclusion criterion. A positive adverse fact such as
`serious_bleeding = present` must never be treated as `eligible` merely because
the fact is true.

For the released boolean labels, `absent` means that the note received `No`
under that question's annotation instructions. It is not proof that the
condition is clinically absent from the patient's complete record.

The planned multi-source extension means unstructured notes plus structured
EHR events and a patient-relative timeline. It does not mean medical imaging in
the resume-first release. Structured EHR facts that were not available to the
original note annotator must not be scored as errors against note-only gold.

## Mandatory self-check for every implementation turn

Every task below is subject to this gate before it can be checked off:

1. **Correctness:** run focused tests and the relevant end-to-end command;
   inspect failure, empty-input, missing-data, and deterministic-repeat cases.
2. **Compliance:** run `scripts/check_public_data.py`; keep MIMIC text, row-level
   derivatives, IDs, mappings, embeddings, indexes, model outputs, and secrets
   outside Git and ordinary online services.
3. **Leakage:** confirm that no test patient, label, answer-derived query,
   prompt choice, threshold, or checkpoint decision influenced training or
   development.
4. **Completeness:** update the data contract, command documentation,
   configuration/version lineage, and acceptance test together with the code.
5. **Scope control:** implement the cheapest adequate baseline first; add a
   component only when it answers a declared research question and has an
   ablation or explicit operational requirement.
6. **Claim control:** report uncertainty and limitations; do not present
   synthetic, single-note, silver-label, or pilot results as clinical
   validation, full eligibility, or autonomous recruitment.

If a task cannot pass the gate, record the blocker and leave it unchecked.

---

## F0 — Completed public and compliance-safe foundation

- [x] **F0.1 Public reproducibility boundary.** Provide independently authored
  synthetic patients, trials, criteria, evidence spans, and expected rankings;
  run the public pipeline and tests without MIMIC, a GPU, or an online model.
  - Entry condition: establish a new public repository that contains no copied
    restricted history.
  - Constraints: no restricted or reconstructable row-level data in Git.
  - Verify: clean-clone smoke test, unit suite, and public-data guard pass.

- [x] **F0.2 Typed trial-matching schema.** Define Patient, Trial, Criterion,
  Evidence, Fact, CriterionDecision, and TrialMatch with typed values, units,
  source spans, provenance, `ALL/ANY/NOT/ATOM`, and three-valued logic.
  - Entry condition: F0.1 provides a synthetic fixture and public boundary.
  - Constraints: `unknown` remains distinct from false/ineligible; unsupported
    schema versions and broken links fail closed.
  - Verify: valid and invalid schema fixtures exercise type, link, polarity,
    unit, time-window, and compound-expression behavior.

- [x] **F0.3 Deterministic verifier and aggregation baseline.** Implement
  criterion polarity, future-fact exclusion, hard/soft semantics, eligibility
  score, coverage, atomic coverage, abstention, and deterministic ranking.
  - Entry condition: F0.2 freezes the typed expression and decision semantics.
  - Constraints: it is a research baseline, not a clinically validated utility
    function; unresolved data-quality branches remain visible.
  - Verify: synthetic tests cover inclusion/exclusion, soft failure, negation,
    incompatible values, OR branches, and missing facts.

- [x] **F0.4 Restricted Apixaban staging adapter.** Verify the official source
  checksum, create HMAC pseudonyms, split evidence, preserve unresolved source
  anomalies, and store the raw-ID crosswalk separately with owner-only access.
  - Entry condition: official authorized source, checksum, license, and local
    restricted-output directory are identified.
  - Constraints: generated corpus, ID map, key, annotations, and patient-level
    manifests are ignored local artifacts and never GitHub content.
  - Verify: schema and semantic validation, file-mode checks, source/content
    hashes, and absence of raw IDs in the main staging corpus.

- [x] **F0.5 Reproducible evaluation infrastructure.** Provide lineage-bound
  split manifests, duplicate assertions, semantic-scan audit interfaces,
  retrieval/decision/ranking metrics, patient-cluster bootstrap intervals,
  coverage-risk curves, error attribution, and JSON/Markdown reports.
  - Entry condition: F0.2 and F0.3 expose stable prediction and gold contracts.
  - Constraints: current synthetic outputs validate implementation only; they
    are not clinical performance estimates.
  - Verify: manifest hash mismatch, cross-split leakage, and unknown IDs fail;
    reports include data, code, model, prompt, index, seed, and config lineage.

- [x] **F0.6 Public trial and annotation extension infrastructure.** Provide
  versioned ClinicalTrials.gov ingestion, deterministic selection, immutable
  snapshots, parser coverage, offline loading, gold-readiness gates, capacity
  planning, blinded annotation templates, adjudication, and PHI-free summary.
  - Entry condition: multi-trial work is isolated from the single-note main
    benchmark and public trial provenance can be frozen.
  - Constraints: this infrastructure does not make a snapshot a benchmark and
    does not imply that multi-trial gold exists.
  - Verify: synthetic snapshot, pilot, adjudication, capacity, and readiness
    commands run in CI and false readiness claims fail closed.

- [x] **F0.7 Continuous integration.** Run the public-data guard, validation,
  tests, smoke pipeline, split checks, trial ingestion, pilot workflow,
  snapshot verification, gold-readiness gate, and evaluation on every push.
  - Entry condition: all public commands have synthetic inputs and require no
    credential or restricted artifact.
  - Constraints: CI must remain free of restricted data, credentials, online
    model calls, and GPU requirements.
  - Verify: `.github/workflows/ci.yml` passes from a public clean clone.

- [x] **F0.8 Lock the public runtime and test dependencies.** Add a reviewed,
  reproducible lock strategy without coupling the later training stack to
  the lightweight public package.
  - Entry condition: select one supported lock tool and Python-version policy.
  - Constraints: keep MLX and any optional MedicalGPT/PEFT/CUDA dependencies in
    separate training environments; do not add a large framework before its
    task begins.
  - Verify: recreate a fresh environment from the lock and pass the complete
    public CI workflow.
  - Frozen decision: CPython 3.11.x, with CI on 3.11.16, and `uv` 0.12.5.
    `requirements/public-py311.lock` contains the hash-locked public runtime
    and build backend only. The P5 training stack remains absent until P5.1
    freezes its model, framework, accelerator target, and execution environment.
  - Completion: on 2026-08-21, an ARM64 macOS environment was rebuilt with
    CPython 3.11.16 and `uv` 0.12.5. Strict hash sync installed the seven locked
    public/build packages, and the frozen compile command reproduced
    `requirements/public-py311.lock` byte for byte. A separate clean Git
    snapshot then passed the public-data guard, both public contract validators,
    all 246 tests, and every synthetic end-to-end command in CI. No MLX,
    MedicalGPT, PEFT, CUDA, model weight, or restricted-data dependency entered
    the public lock.

Acceptance: a clean public clone is deterministic, compliance-safe, CPU-only,
and honest about which results are synthetic.

---

## P1 — Freeze and materialize the 2,300-answer main benchmark

- [x] **P1.1 Freeze the note-grounded prediction contract.** Version a schema
  for boolean facts, numeric facts, and unknown/not-specified labels, including
  typed values, units where the released question defines them, evidence IDs,
  and abstention metadata.
  - Entry condition: review all 23 official questions and record their expected
    answer type and aggregation rule, such as minimum or maximum.
  - Constraints: preserve original question wording and label semantics; do not
    relabel every `Yes` as eligible or infer facts outside the released note.
  - Verify: a reviewed mapping table covers exactly 23 unique questions and
    round-trips valid boolean, numeric, and unknown examples.

- [x] **P1.2 Build the restricted benchmark adapter.** Convert the existing
  verified staging corpus into a local benchmark document containing all 100
  pseudonymous patients and all 2,300 official human-reviewed answers.
  - Entry condition: P1.1 is frozen and the official source hash matches the
    pinned manifest.
  - Constraints: no raw ID, note text, label row, patient manifest, or output is
    committed; the adapter must not silently repair the two known anomalies.
  - Verify: exact expected counts, unique-key checks, schema validation, stable
    content hash, and deterministic repeat generation.

- [ ] **P1.3 Produce a data-quality and label-semantics report.** Measure per
  question label counts, unknown rates, numeric ranges, missing/duplicate rows,
  patient completeness, and unresolved anomalies.
  - Entry condition: P1.2 output validates.
  - Constraints: the public report is aggregate and PHI-free; rare classes and
    implausible values are reported, not removed without a reviewed rule.
  - Verify: totals reconcile to 100 patients, 23 questions, and 2,300 answers;
    aggregate cells below a governance-approved disclosure threshold are
    suppressed from public output.
  - Current gate: the reporter and pending-review suppression projection are
    implemented; completion still requires the approved threshold and a
    non-sensitive governance approval reference. A proposed value must not be
    represented as institutional approval.

- [x] **P1.4 Freeze a patient-grouped benchmark split.** Create train,
  validation, and locked test membership with deterministic, label-aware group
  assignment and patient/admission isolation.
  - Entry condition: P1.3 establishes whether the desired class distribution is
    feasible; choose and document the split proportions before model runs.
  - Constraints: never split the 2,300 rows independently; the locked test set
    cannot guide prompts, retriever settings, thresholds, or model selection.
  - Verify: no patient/admission overlap, stable rerun hash, acceptable recorded
    label balance, exact duplicate checks, and a locally audited semantic
    near-duplicate scan.
  - Completion: the deterministic grouped, label-aware split was frozen only
    after a passing authorized local semantic scan and an explicit
    proportion/seed decision. Restricted membership and run details remain
    outside the public repository; subsequent tuning must not inspect or alter
    the locked test membership.

- [x] **P1.5 Extend evaluation for mixed answer types.** Add boolean/unknown
  classification and numeric extraction metrics without conflating them.
  - Entry condition: P1.1 defines normalization and any numeric tolerance.
  - Constraints: report per-question and macro results; choose tolerances from
    clinical/task semantics on train/validation only, never from test outcomes.
  - Verify: tests cover exact match, macro/micro F1, unknown F1, MAE, tolerance
    accuracy, missing values, invalid units, and patient-cluster confidence
    intervals.
  - Completion: the frozen-split-bound evaluator reports boolean and numeric
    status classification separately from numeric value metrics, includes
    per-question and macro views, treats missing predictions as errors, and
    resamples whole patients for confidence intervals. Because catalog 1.0.0
    has no canonical numeric units, its reviewed zero-tolerance policy measures
    exact source-value extraction only and makes no clinical-equivalence claim.

Acceptance: one command regenerates and validates the ignored benchmark, one
frozen split governs all subsequent work, and no test label has influenced the
pipeline.

---

## P2 — Establish non-trained baselines before adding retrieval

- [x] **P2.1 Implement a deterministic extraction baseline.** Add reviewed
  lexical aliases, negation handling, numeric extraction, min/max aggregation,
  and explicit unknown output for the 23 questions.
  - Entry condition: P1 benchmark and metrics are executable.
  - Constraints: rules are derived from question semantics and training data
    only; no patient-specific exception or test-answer lookup is allowed.
  - Verify: unit tests cover positive, negative, ambiguous, multiple-value, and
    absent-fact cases; emit a versioned validation/test report.
  - Completion: rule set `1.0.0` covers all 23 frozen questions and declares
    that locked test labels were not used. Prediction-set `1.1.0` links every
    resolved or ambiguous result to evidence and rule IDs; conflict, missing
    temporal context, and absent evidence produce explicit abstention except
    for the source-defined medical-decision default. An owner-only validation
    prediction set and patient-cluster bootstrap report were generated against
    the frozen split. Locked test evaluation remains deliberately deferred
    until model selection closes; no validation aggregate is presented as a
    clinical-performance claim.

- [x] **P2.2 Implement a local frozen-model structured-output baseline.** Ask a
  pinned open-weight model to return the P1.1 typed-fact contract from the note
  and question.
  - Entry condition: record available hardware, model license, local inference
    path, context limit, and deterministic decoding policy.
  - Constraints: restricted text stays in an approved local environment; no
    ordinary online API; invalid output is a measured failure or abstention,
    not silently hand-corrected.
  - Verify: schema-valid rate, task metrics, latency, token count, memory, model
    revision, prompt version, and reproducible run report are present.
  - Completion: the exact local Llama 3.1 8B Instruct Q4_K_M Ollama manifest,
    Llama Community License status, Ollama version, M3/24 GB hardware, 16K
    context, zero-temperature seed, 8,000-character complete-chunk prefix, and
    prompt version are frozen in contract `1.0.0`. The loopback-only client
    disables proxy/cloud fallback, verifies the model digest before inference,
    and converts a schema-invalid request to 23 measured abstentions without
    retry or repair. Prediction-set `1.2.0` and an owner-only aggregate run
    report record evidence links, schema-valid rate, truncation, latency,
    tokens, memory, and output throughput. A 15-patient validation run and the
    P1.5 metrics completed; locked test inference remains deferred.

- [x] **P2.3 Implement the matched long-context baseline.** Evaluate the same
  frozen model with the full note under a declared truncation/token policy.
  - Entry condition: P2.2 model and output contract are stable.
  - Constraints: use the same question and decision semantics as later RAG;
    disclose truncated notes and do not give one method extra label-derived
    context.
  - Verify: report retained-text proportion, truncation count, effectiveness,
    latency, tokens, memory, and privacy-exposure proxy.
  - Completion: contract `1.0.0` changes only the input policy and context
    window relative to P2.2: the same pinned Llama manifest, prompt, 23-question
    schema, seed, decoding, invalid-output policy, and validation split are
    retained. The owner-only 15-patient run preserved every complete evidence
    chunk, reached neither the application truncation boundary nor the declared
    context limit, and produced schema-valid output for every request. The same
    P1.5 evaluator found mixed validation effects: typed and boolean aggregate
    scores improved, while numeric status, coverage, and tolerance measures did
    not. Patient-bootstrap intervals remain wide and overlapping. Full-note
    input also increased prompt/text exposure and model memory; its lower
    latency in this single sequential run is descriptive, not a speed claim.
    P2.3 is therefore frozen as an honest baseline rather than tuned further;
    locked test inference remains deferred and P3 proceeds with retrieval.

Acceptance: rules, frozen structured inference, and long context provide honest
baselines before any retriever or fine-tuning claim.

---

## P3 — Conventional evidence retrieval

- [x] **P3.1 Freeze the evidence-chunk contract.** Define deterministic chunks,
  stable evidence IDs, note offsets, section metadata when available, and an
  index manifest.
  - Entry condition: prove that chunk generation does not read answer labels.
  - Constraints: preserve exact source spans; embeddings and indexes remain
    restricted local artifacts; changing chunking creates a new index version.
  - Verify: chunks reconstruct their source spans, never cross patients, cover
    documented text according to policy, and regenerate to the same hash.
  - Completion: contract `apixaban-preserved-evidence-chunks-v1` preserves the
    staging adapter's exact chunks without re-splitting or normalization and
    permits only evidence fields; legacy answers, benchmark/prediction labels,
    and queries are forbidden. The patient-local builder verifies HMAC-bound
    evidence IDs, global uniqueness, exact zero-based half-open spans,
    contiguous source coverage, declared chunk limits, and source/patient
    isolation. Missing section metadata is explicit rather than inferred. An
    owner-only validation manifest covering 15 patients and 107 chunks was
    generated from implementation commit `95db920`, then independently
    reproduced from the frozen split/corpus with identical hashes. Locked test
    indexing remains deferred; no embedding or retrieval-quality claim is made.

- [x] **P3.2 Implement BM25 behind a common retriever interface.** Return ranked
  evidence IDs and scores for each question.
  - Entry condition: P3.1 is frozen.
  - Constraints: no answer text or test labels enter query construction; record
    tokenizer, normalization, and BM25 parameters.
  - Verify: controlled ranking tests, patient isolation, deterministic output,
    latency/index-size measurement, and downstream answer metrics.
  - Completion: contract `apixaban-patient-bm25-v1` freezes source-question-only
    queries, Unicode tokenization without hidden normalization, positive-IDF
    BM25 (`k1=1.2`, `b=0.75`), patient-local document frequencies, positive-score
    top-3 selection, and deterministic tie-breaking. A common retriever protocol,
    strict owner-only run schema, complete patient-question-grid validation,
    evidence/prediction binding, controlled synthetic ranking tests, and locked
    test acknowledgements are implemented. Validation processed 15 patients,
    107 evidence chunks, and all 345 patient-question queries from implementation
    commit `ed8d700`; every query returned three positive-score chunks. The
    deterministic serialized index proxy was 209,197 bytes, index construction
    took 9.59 ms, and retrieval averaged 0.035 ms/query on this local run.
    Selected text was 43.4% of per-question full-note exposure (56.6% lower).
    Under the unchanged evaluator, typed exact match was 0.3188 (95% patient
    bootstrap CI 0.2638–0.3797) versus 0.3275 for the full-evidence deterministic
    comparator, while numeric value coverage fell from 0.5696 to 0.3671. The
    intervals overlap and the release has no independent evidence-ID gold, so
    this is a resource/downstream diagnostic—not a relevance or superiority
    claim. All patient-level rankings, predictions, and reports remain local;
    locked-test retrieval was not run.

- [x] **P3.3 Implement one validated dense retriever.** Pin one embedding model
  and revision, pooling, normalization, dimension, and similarity metric.
  - Entry condition: license, local data handling, and hardware are acceptable.
  - Constraints: start with one model; do not collect multiple encoders without
    a predeclared comparison question.
  - Verify: vector/document count consistency, patient isolation, deterministic
    index fingerprint, controlled retrieval tests, and downstream metrics.
  - Completion: contract `apixaban-medcpt-dense-v1` freezes the public-domain
    NCBI MedCPT Query/Article encoders at immutable revisions, official `[CLS]`
    pooling, 768-dimensional unnormalized float32 vectors, exact dot product,
    source-question-only queries, empty-title plus exact-evidence document pairs,
    CPU deterministic inference, local-files-only loading, and the same top-3
    exposure budget as BM25. The paired checkpoints total about 876 MB and ran
    locally within available hardware. Strict owner-only index/run schemas bind
    the P3.1 index, ordered evidence IDs, model revisions, vector bytes/hash,
    complete patient-question grid, and downstream citations. A controlled
    synthetic synonym check ranked an eGFR statement first for a renal-impairment
    query. Validation encoded all 107 chunks for 15 patients at implementation
    commit `6e5c273`; two independent CPU builds produced the identical 328,704
    byte vector SHA and index ID. Document encoding took 22.09 s, query encoding
    1.02 s, and exact retrieval averaged 0.312 ms/query locally. Top-3 exposure
    was 42.9% of full-note-per-question characters (57.1% lower). Typed exact
    match was 0.3159 (95% patient-bootstrap CI 0.2638–0.3710), not an overall
    improvement over BM25 or full evidence. However, numeric value coverage
    recovered from BM25's 0.3671 to 0.5443 and numeric-status accuracy from
    0.5667 to 0.6833, near the full-evidence values 0.5696 and 0.7000. MedCPT is
    retained as a complementary dense input to P3.4 fusion, not as a standalone
    superiority claim. No independent evidence relevance is claimed, all
    vectors/results remain local, and locked test was untouched.

- [x] **P3.4 Add fusion, then reranking only if justified.** Compare BM25,
  dense, and reciprocal-rank fusion; add one cross-encoder reranker only after
  fusion is measured.
  - Entry condition: P3.2 and P3.3 reports share the same split and query set.
  - Constraints: select fusion/reranking settings on validation only; every
    added stage must have an ablation and resource measurement.
  - Verify: paired validation/test reports include downstream quality, latency,
    memory, index size, and confidence intervals.
  - Completion: contract `apixaban-bm25-medcpt-rrf-v1` froze equal-weight
    rank-only RRF with `k=60`, all strictly positive patient-local BM25
    candidates, all patient-local dense candidates, no parameter search, the
    unchanged top-3 budget, and no reranker before validation. The restricted
    run schema binds both component run hashes, dense-index identity, complete
    query grid, component ranks, recomputable fusion scores, downstream
    predictions, and owner-only output. At commit `57075ef`, validation
    completed all 345 queries over 15 patients. Fusion averaged 0.421 ms/query,
    reused the existing 328,704-byte dense vectors with no additional index,
    selected 43.5% of full-note-per-question characters, and the offline
    process peaked at about 832 MiB RSS including MedCPT model loading. Typed
    exact match was 0.3159 (95% patient-bootstrap CI 0.2609–0.3740), equal to
    dense and below BM25 at 0.3188; numeric value coverage was 0.5063 versus
    0.5443 dense, and numeric-status accuracy was 0.6583 versus 0.6833 dense.
    RRF therefore did not improve the validation diagnostic and is retained
    only as a measured ablation, not the selected retrieval path. Under the
    predeclared “rerank only if justified” rule, no cross-encoder was added.
    Independent evidence relevance is still unavailable, all row-level
    artifacts remain local, and locked test was untouched; final test reporting
    remains gated on a later frozen end-to-end selection rather than being used
    to rescue this ablation.

- [x] **P3.5 State the evidence-evaluation boundary.** Use independent evidence
  gold where it exists; otherwise report answer-containing-span diagnostics and
  downstream task accuracy as separate, limited signals.
  - Entry condition: audit the official release for genuinely human-authored
    evidence links before choosing retrieval metrics.
  - Constraints: never evaluate a linking rule against links created by the
    same rule; do not call lexical answer occurrence clinical relevance.
  - Verify: every reported retrieval metric names its gold source, coverage,
    exclusions, and whether it is primary, weak/silver, or diagnostic only.
  - Audit status: the official MIMIC-IV-Ext Apixaban `1.0.0` release was audited
    against its supplied hashes and actual CSV structure. It contains 2,300
    human-reviewed answer rows for 100 notes and 23 questions, but its eight
    fields contain no evidence ID, supporting sentence, source span, rationale,
    or relevance annotation. Real evidence-gold coverage is therefore 0/2,300,
    and all official rows are excluded from Evidence Recall@k, MRR, and nDCG.
    `docs/EVIDENCE_EVALUATION_BOUNDARY.md` records the source, coverage,
    exclusions, signal tiers, circularity prohibitions, mandatory future report
    fields, and requirements that would unlock primary evidence metrics.
    Existing BM25, dense, and RRF schemas already enforce that no real
    retrieval-relevance metric is reported. The approved validation-only
    contract now limits the weak diagnostic to exact decimal-token occurrence
    for known numeric answers that are literally matchable in full context;
    boolean, unknown, ambiguous LVEF=55, and full-context non-occurrence cases
    are excluded. At implementation commit `c5c0852`, the restricted
    validation report reconciled all 345 rows: 120 were numeric, 41 unknown
    rows, two ambiguous LVEF=55 values, and two full-context non-occurrences
    were excluded, leaving 75 evaluable rows. Numeric occurrence@1/@3 was
    0.280/0.600 for BM25, 0.680/0.973 for MedCPT, and 0.480/0.933 for RRF.
    These are explicitly weak literal-token retention diagnostics, not evidence
    Recall or relevance. The aggregate report is owner-only, contains no
    patient IDs or text, and locked test remained untouched.

Acceptance: the simplest reproducible retriever that improves held-out answer
quality is selected; unhelpful stages are removed rather than retained for
novelty.

---

## P4 — Structured reasoning, verification, and abstention

- [x] **P4.1 Connect model fact outputs to the existing typed verifier.** Parse
  boolean, numeric, unit, evidence, and uncertainty fields and deterministically
  map a fact to criterion polarity only when a specific criterion is supplied.
  - Entry condition: at least one P2/P3 model emits the P1.1 contract.
  - Constraints: fact truth and eligibility are separate; invalid or missing
    evidence cannot be invented during mapping.
  - Verify: tests cover adverse positive facts, inclusion/exclusion reversal,
    numeric thresholds, unknown, and missing evidence.
  - Completion: adapter `1.0.0` accepts only a schema-valid P1.1/P2 prediction
    set and an explicitly supplied criterion. It obtains the normalized fact
    field from the frozen catalog, preserves boolean/numeric values and units,
    and creates a core fact only when every cited evidence ID exists in the
    supplied patient-local inventory. Model unknown, missing/unknown evidence,
    and unrelated criterion fields remain `UNKNOWN` with fixed mapping reasons;
    the existing verifier alone applies inclusion/exclusion polarity. Synthetic
    counterexamples cover adverse positive facts, polarity reversal, numeric
    thresholds, uncertainty, evidence failures, unrelated criteria, and invalid
    prediction schema. Observation time is unavailable in P1.1/P2 outputs and
    remains an explicit P4.2 boundary.

- [ ] **P4.2 Complete real-output neuro-symbolic checks.** Apply numeric, unit,
  time, negation, evidence-link, missingness, and polarity checks to model
  outputs while preserving model/verifier disagreements.
  - Entry condition: the relevant source field is available and its semantics
    are defined; unavailable index dates must yield unknown for true temporal
    eligibility checks.
  - Constraints: do not silently overwrite model output; expose conflict and
    review-required status in the audit trace.
  - Verify: controlled counterexamples for every check, conflict-rate report,
    before/after error analysis, and no future-fact leakage.
  - Current gate: real-output readiness audit `1.0.0` validates frozen-split
    lineage, exact patient-question coverage, numeric types, the source unit
    contract, patient-local evidence links, and known-fact evidence
    missingness. It emits only an owner-only aggregate report. The official
    source has null index dates, no observation dates or claim-level negation
    trace, and explicitly labels every question `fact_only_no_direct_mapping`;
    current model outputs also contain no criterion decision. Time, negation,
    polarity, conflict rate, and before/after analysis therefore remain
    `not_evaluable` rather than being reported as zero. P4.2 stays unchecked
    until a reviewed real criterion binding and the required temporal/decision
    fields exist.
  - Validation result: owner-only audits of the frozen structured-prefix and
    matched long-context Llama outputs each covered 15 validation patients and
    all 345 patient-question rows. Numeric shape, the null-unit source
    contract, and patient-local evidence ownership had zero failures. Strict
    known-fact evidence missingness flagged 1 and 3 review-required rows,
    respectively; all were the contract's existing evidence-free
    `med_decisions=absent` exception. No output was overwritten. The two report
    hashes and limitations are recorded in `docs/NEUROSYMBOLIC_AUDIT.md`;
    neither report is an eligibility result or a P4.2 completion claim.

- [x] **P4.3 Add deterministic abstention baselines.** Abstain on missing facts,
  invalid schema, unusable evidence, incompatible units, and verifier conflict.
  - Entry condition: each abstention reason has a machine-readable code.
  - Constraints: unknown is not assigned an arbitrary probability or folded
    into an eligibility score.
  - Verify: coverage-risk curves and reason counts reproduce exactly; known and
    unknown cases are tested separately.
  - Completion: policy `1.1.0` writes a separate hash-bound prediction
    projection and never edits its source. Six mutually exclusive reason codes
    have fixed precedence: invalid schema, unusable evidence, missing evidence,
    incompatible unit, verifier conflict, and missing fact. Unknown receives no
    probability. Synthetic tests cover every reason, precedence, known versus
    unknown, source immutability, exact risk recomputation, content tampering,
    and unavailable conflict input. Version `1.1.0` adds one source-question
    exception: `med_decisions=absent, value=false` may remain known with an
    empty citation; present and every other known fact still require evidence.
    Historical owner-only validation projections used `1.0.0` and changed
    1 structured-prefix and 3 long-context known rows to `missing_evidence` and
    normalized existing unknown rows. Coverage decreased and typed selective
    risk rose slightly because the removed unsupported rows happened to match
    fact gold; the result is recorded as a safety baseline, not a performance
    or calibration gain. Exact operating points and artifact hashes are in
    `docs/ABSTENTION_POLICY.md`; locked test remained untouched.

- [ ] **P4.4 Add probabilistic calibration only when probabilities exist.** Fit
  calibration and review thresholds using validation predictions, then freeze
  them before the locked test run.
  - Entry condition: a model produces meaningful, reproducible probabilities
    or scores and there are enough validation patients to estimate calibration.
  - Constraints: deterministic coverage is not called calibrated confidence;
    report small-sample limitations.
  - Verify: Brier score, calibration error, reliability/coverage-risk outputs,
    frozen threshold provenance, and no test-set threshold tuning.
  - Current gate: frozen Llama prediction-set `1.2.0` exposes neither class
    probabilities nor a reproducible continuous confidence score. The P4.3
    deterministic answered fraction is coverage, not calibrated confidence.
    P4.4 remains deferred; no synthetic probability, Brier score, calibration
    error, or tuned threshold may be reported from the current outputs.

- [ ] **P4.5 Produce mutually exclusive error attribution.** Separate retrieval
  failure, reasoning failure with usable evidence, numeric/unit/time/negation
  errors, false abstention, and unsupported answering.
  - Entry condition: pipeline stages emit sufficient trace metadata.
  - Constraints: attribution is diagnostic, not causal proof.
  - Verify: categories reconcile to the total errors and representative cases
    are reviewed only inside the authorized environment.
  - Current gate: observable attribution contract `1.1.0` assigns each row by
    frozen precedence to unsupported answering, source-unit error, abstention
    on a known gold fact, numeric value error, cited fact-status error, or a
    remaining typed error. It validates the complete frozen grid and writes an
    owner-only aggregate report with no row identifiers. Retrieval failure,
    reasoning failure with relevant evidence, time, negation, and true false
    abstention remain `not_evaluable`: the official source has no evidence
    relevance gold, dates, or claim-negation trace. A patient-local citation is
    not treated as relevant evidence. P4.5 stays unchecked until authorized
    representative-case review is recorded and the missing causal dimensions
    are either supported by reviewed gold/trace fields or formally scoped out.
    Version `1.1.0` mirrors the P4.3 source-question exception so
    `med_decisions=absent, value=false` with an empty citation is not mislabeled
    unsupported; historical aggregate reports remain version `1.0.0`.
  - Validation result: owner-only aggregate reports from implementation commit
    `347375b` reconciled all 345 validation rows for the structured and
    long-context Llama outputs and their separate P4.3 projections. P4.3
    removed 1 and 3 unsupported known answers, respectively, but each became an
    abstention on a known released label; attributed totals therefore remained
    159/137 rather than improving. Exact category counts, artifact hashes, and
    interpretation limits are recorded in `docs/ERROR_ATTRIBUTION.md`. Locked
    test remained untouched and representative-case review remains pending.

Acceptance: verification and abstention reduce risk under a declared coverage
trade-off and do not hide unresolved clinical information.

---

## P5 — MedicalGPT-compatible LoRA-SFT adaptation

- [ ] **P5.1 Freeze the training decision.** Select one license-compatible
  local base model, LoRA or QLoRA precision, local hardware budget, context
  policy, and success criterion against the frozen-model baseline.
  - Entry condition: P2 and the chosen P3/P4 baseline are complete.
  - Constraints: choose one primary model; training is optional if compute,
    license, or expected value is inadequate.
  - Verify: decision record pins model/tokenizer revisions, license, memory
    estimate, training budget, and metric required to justify retention.
  - Current decision: the full pre-training procedure is recorded in
    `docs/P5_TRAINING_DECISION.md`. The primary route is local MLX QLoRA with
    pinned Llama-3.1-8B-Instruct and an untuned same-base comparison. Real
    restricted data and derived adapters remain local; Colab is synthetic
    mechanism testing only. P5.1 remains open until the conversion chain,
    synthetic memory result, complete threshold gate, train-fit-only
    complete-sequence input policy, training budget, runtime route, and
    row-filter tests pass.
  - Progress: calibration reservation contract `1.0.0` implements a fixed
    source-bound SHA-256 ranking over the frozen train membership, requires an
    explicit count, writes owner-only output, and has no validation/test label
    input. The owner approved 15 calibration-only patients from the frozen
    70-patient train split, leaving 55 train-fit patients; the real manifest is
    generated and validated only in the authorized local environment.
  - Progress: the owner approved the numerical D/E gates before coverage was
    inspected: overall citation-required known coverage at least 60%; every
    applicable question both at least 30% and at least five accepted rows; at
    most 100 deterministically sampled reviews per source; and support rate at
    least 90%, with ambiguous counted as failure. The two zero-tolerance
    categories are now frozen as any cross-patient citation and any citation
    outside the student-visible chunk set. Both must be checked over the full
    candidate artifact, and either defect forces complete regeneration rather
    than row deletion. Audit samples are jointly stratified by question ID,
    numeric/boolean answer type, and present/absent fact status, with every
    represented question sampled. Sampling protocol
    `sha256_stratified_silver_audit_sampling/1.0.0` freezes the artifact-bound
    SHA-256 tuple and salt, `N=100`, floor-one allocation, capacity-safe
    largest-remainder distribution, exact three-label rubric, and one-owner
    review. The input is explicitly a pre-audit candidate artifact; accepted
    silver exists only after the audit passes. Numeric review checks exact
    value and context without inventing a gold unit because catalog `1.0.0`
    defines `canonical_unit = null`. The full gate remains open until the input
    policy is frozen and the builder, validator, and calculator pass synthetic
    tests before any real candidate content is inspected.
  - Progress: the separate local mechanism environment now records CPython
    3.11.16, `mlx==0.31.2`, `mlx-lm==0.31.3`, their official release commits,
    and all observed exact package versions in `requirements-mlx.txt`. An Apple
    GPU import/compute and LoRA-CLI smoke test passed. Official Llama 3.1 8B
    access, source revision, tokenizer/config hashes, and the exact chat-template
    hash are now verified. This does not substitute for the 8B conversion or
    synthetic memory/throughput dry run.
  - Progress: the 41 GiB disk preflight makes sequential hash/verify/manifest/
    delete handling mandatory for regenerable conversion intermediates; shared
    caches, restricted data, tokenizer/license files, adapters, and required
    evaluation artifacts are never cleanup targets. The memory dry run must
    test the exact train-fit-length-selected context tier; a shorter passing
    tier cannot close P5.1. The prior Ollama Q4_K_M validation result is only a
    conversion-health diagnostic because its artifact, runtime, and input
    policy are not identical to the new matched untuned/tuned chain.
  - Progress: machine-readable length contract `1.0.0` freezes
    `all-complete-evidence-v1`, prompt
    `apixaban-single-fact-sft-1.0.0`, a 512-token output reserve, and the
    smallest-100%-fit rule over 2,048/4,096/8,192/16,384-token tiers. The
    owner-only report uses only the 55-by-23 train-fit grid and emits no patient
    text, IDs, or row lengths. Input-plan `1.1.0` binds the selected report,
    model/tokenizer/chat-template hashes, tier, and no-truncation holdout
    policy. Mechanism tests do not substitute for the pending real report or
    exact-tier 8B memory gate.

- [ ] **P5.2 Export training folds to the canonical SFT dataset and compatibility
  formats.** Build a versioned adapter from P1.1 records to the owner-only
  canonical JSONL, then derive the MLX-LM training representation and the
  MedicalGPT-compatible export.
  - Entry condition: the split manifest is frozen and MedicalGPT remains pinned
    to the reviewed commit in `docs/REFERENCES.md`.
  - Constraints: export train only for fitting; validation is separate; test
    records, labels, outputs, and patient text never enter training artifacts.
  - Verify: schema validation, exact patient-membership assertions, dataset
    hash, chat-template snapshot, sample round-trip, and label distribution.
  - Progress: canonical record contract `1.0.0` and export-manifest contract
    `1.1.0` now derive
    MLX `messages` and MedicalGPT ShareGPT `conversations` from one semantic
    source. Synthetic tests enforce exact train-fit coverage, calibration/
    validation/test exclusion, D-before-E accepted-silver precedence, typed
    agreement, patient ownership, visible citations, known-row filtering,
    empty-unknown/default-absent targets, owner-only outputs, hash binding, and
    cross-format message equality. Export `1.1.0` additionally re-renders every
    training row with the frozen tokenizer and fails if its actual target
    exceeds 512 tokens or its prompt plus reserve exceeds the selected tier.
    This does not close P5.2: the real length-bound input plan,
    threshold-approved D/E audit artifacts, and owner-only real export remain
    pending.
  - Silver-audit mechanism: contracts `1.0.0` now separate pre-audit
    candidates, deterministic hash-stratified review packages, completed
    single-owner judgments, quality reports, and accepted silver. Synthetic
    tests cover the 100-row allocation cap, complete-set ownership/visibility
    failures, immutable judgment binding, the 90% source gate, reviewed-failure
    removal, coverage recomputation, owner-only writes, and CLI acknowledgement.
    This is mechanism evidence only; no real candidate has been generated or
    inspected and no gate is claimed to have passed.
  - Evidence supervision: export D deterministic-rule silver first and use a
    frozen-teacher E source only as audited backoff for uncovered gold-known
    rows. Keep source-level provenance and stratified coverage reports. These
    links are weak citation proxies and may never be retrieval relevance gold.
    Every source is manually audited before use; failed candidates are removed
    and coverage is recomputed before D-only or D+E can pass. The first run uses
    stock whole-completion loss: retain known rows only when
    accepted silver is visible in the student input, retain unknown rows with a
    legal empty list, retain `med_decisions=absent, value=false` under the sole
    known empty-evidence exception, and report all excluded known rows and
    distribution shifts. The exception is reported but excluded from citation
    coverage gates. If the predeclared overall/per-question coverage gate
    fails, stop
    P5.2 and review field-selective loss as a separate fallback rather than
    silently adding a custom trainer. Exact rules and closure gates are in
    `docs/P5_TRAINING_DECISION.md`.

- [ ] **P5.3 Run one LoRA-SFT experiment in a separate environment.** Record
  base model, adapter config, seed, optimizer, schedule, precision, checkpoint,
  MLX and MLX-LM versions. Record Transformers, PEFT, MedicalGPT, and CUDA only
  for a synthetic compatibility run that actually uses them.
  - Entry condition: P5.1 and P5.2 pass; restricted data remains local.
  - Constraints: no unrestricted tracking service receives prompts or patient
    text; training loss alone is not evidence of benefit.
  - Verify: resume/load test, held-out validation report, adapter provenance,
    and reproducible inference on synthetic fixtures.
  - First-run checkpoint rule: use a fixed training budget and evaluate only
    the endpoint checkpoint; intermediate checkpoints are recovery artifacts,
    not task-metric candidates. The local MLX environment uses one reviewed
    exact-version `requirements-mlx.txt`, not a second lock infrastructure.

- [ ] **P5.4 Evaluate whether the adapter earns its complexity.** Compare rules,
  existing Ollama-8B/RAG references, and the primary matched untuned-8B versus
  tuned-8B pair on validation. Treat P4.3/verifier as pre/post deterministic
  projection, not a separate experimental arm.
  - Entry condition: model and retriever selection are frozen on validation.
  - Constraints: disclose overfitting, invalid output, unknown recall, latency,
    and memory; remove LoRA from the final default if it does not provide a
    meaningful held-out benefit.
  - Verify: paired report with patient-bootstrap intervals and a documented
    keep/drop decision.
  - Locked-test boundary: P5.4 freezes the validation-stage decision and final
    configuration. P7.2 performs the only locked-test exposure as one batch and
    reuses those immutable artifacts for all final P5/P7 reporting.

Acceptance: LoRA is described as a successful contribution only if it beats a
strong frozen baseline without degrading evidence linkage or unknown handling.

---

## P6 — Conditional multi-source EHR extension

- [ ] **P6.1 Audit access and local source availability.** Inventory authorized
  MIMIC-IV/Note versions and the minimum required tables: note metadata,
  admissions, patients, labs/dictionaries, diagnoses/dictionaries, medication,
  and procedures.
  - Entry condition: the text-only mainline through P4 is reproducible; P5 may
    proceed independently.
  - Constraints: no new dataset is used until access, DUA, version, checksum,
    fields, date semantics, and publication boundary are recorded.
  - Verify: a local-only inventory and PHI-free aggregate availability report;
    missing sources produce an explicit skip decision rather than guessed data.

- [ ] **P6.2 Join authorized note metadata and establish index dates.** Promote
  eligible local staging records to the runtime patient-source contract.
  - Entry condition: verified linkage keys and compatible source versions.
  - Constraints: join through the owner-only map; preserve deidentified patient
    relative time; never infer dates from placeholders.
  - Verify: one-to-one/one-to-many cardinality audit, unmatched/ambiguous counts,
    no raw IDs in runtime output, and future-information exclusion tests.

- [ ] **P6.3 Implement typed structured-evidence adapters.** Normalize labs,
  diagnoses, medications, and procedures into stable source-aware Evidence and
  Fact records.
  - Entry condition: define one source at a time and begin with labs because the
    official benchmark contains numeric questions.
  - Constraints: retain source table, concept ID, value, unit, observed time,
    and provenance; do not treat a billing code as equivalent to a confirmed
    clinical diagnosis without a declared rule.
  - Verify: source-to-normalized count reconciliation, unit/type tests, patient
    isolation, deterministic IDs, and provenance round-trip.

- [ ] **P6.4 Build a patient-relative timeline and evidence fusion policy.**
  Combine note and structured evidence without erasing modality/source or
  conflicts.
  - Entry condition: at least note metadata and one structured adapter validate.
  - Constraints: no post-index facts unless an explicit future-directed task
    allows them; conflicting sources trigger a trace or abstention, not silent
    precedence.
  - Verify: tests for missing modality, repeated values, conflicts, out-of-window
    events, and deterministic fusion order.

- [ ] **P6.5 Freeze a separate evaluation contract for the extension.** Define
  what can be programmatically derived from structured tables, what remains
  note-only, and what would require independent clinical validation.
  - Entry condition: inspect actual joined coverage before selecting tasks.
  - Constraints: do not score a correct structured fact as wrong because the
    original note-only label was `not_specified`; do not call coded or
    automatically derived labels clinical gold.
  - Verify: every metric names its information sources and gold provenance;
    note-only primary results remain separately reproducible.

- [ ] **P6.6 Run minimal source ablations.** Compare text only, structured only,
  text+structured, and text+structured+time for tasks with valid labels.
  - Entry condition: P6.5 approves a sufficiently covered evaluation subset.
  - Constraints: add no image encoder or new modality unless a criterion and
    independent evaluation justify it.
  - Verify: same patients, model, and evaluation contract across ablations;
    report coverage, conflicts, quality, latency, and storage cost.

Acceptance: the extension demonstrates a measurable, correctly scoped benefit
from structured EHR or is honestly reported as unsupported and omitted.

---

## P7 — Final evaluation, public demo, and resume evidence

- [ ] **P7.1 Freeze one final system configuration.** Select components using
  validation results and record the exact dataset, split, code, model, prompt,
  index, verifier, threshold, and hardware specification.
  - Entry condition: required mainline ablations are complete.
  - Constraints: no post-test component swapping; conditional components that
    failed their keep criterion are excluded.
  - Verify: configuration hash is immutable and recreates the validation run.

- [ ] **P7.2 Run the locked test once and generate the final report.** Include
  mixed-type task metrics, patient-bootstrap intervals, coverage-risk, error
  attribution, latency, tokens, memory, and limitations.
  - Entry condition: P7.1 is frozen and the worktree/config are clean.
  - Constraints: patient-level output remains restricted; only reviewed
    aggregates and disclosure-safe examples can be public. Execute every
    frozen final arm in one batch; after any test result is visible, do not
    rerun inference, replace a component/checkpoint, or change a threshold.
    P5 and P7 reports must reuse these immutable test artifacts.
  - Verify: JSON and Markdown reports agree, totals reconcile, the public-data
    guard passes, and no metric lacks dataset/split/model provenance.

- [ ] **P7.3 Build a synthetic public demonstration.** Show evidence retrieval,
  typed facts, unknown reasons, verifier conflicts, and an audit trace on
  fictional patients.
  - Entry condition: final public interfaces are stable.
  - Constraints: no real patient text, real row-level label, autonomous
    enrollment, or medical-advice wording; multi-trial display remains clearly
    synthetic unless adjudicated gold later exists.
  - Verify: clean-clone demo works CPU-only, offline where documented, includes
    research-only warnings, and fails safely on invalid input.

- [ ] **P7.4 Complete the public research package.** Publish architecture,
  methods, experiment commands, aggregate results, model/data cards, licenses,
  reference attribution, limitations, and reproduction instructions.
  - Entry condition: P7.2 and P7.3 pass.
  - Constraints: distinguish implemented, evaluated, optional, and future work;
    do not claim that MedicalGPT or LightRAG components were used unless their
    corresponding experiment actually ran.
  - Verify: a fresh reviewer can identify every contribution, reproduce the
    synthetic path, understand the restricted-data path, and trace every public
    number to a run specification.

Acceptance: the repository supports a defensible resume claim with runnable
public code and evidence-backed restricted-data aggregate results.

---

## Deferred research extensions — not resume-release blockers

- [ ] **X1 Multi-trial clinical gold.** Resume the frozen four-patient selector,
  two-trial offline pack, two authorized annotators, adjudication, capacity
  study, and formal patient–trial gold only when a publication-quality
  multi-trial benchmark is the active objective.
  - Entry condition: qualified annotators, data authorization, governance,
    annotation budget, and a reviewed protocol are available.
  - Constraints: single-annotator or model-generated labels are not dual-reviewed
    clinical gold; public release contains only approved aggregate metadata.
  - Verify: gold-readiness gate passes before any ranking performance claim.

- [ ] **X2 LightRAG graph-retrieval ablation.** Index public trial criteria and
  protocols only after the conventional BM25+dense+reranker baseline exists.
  - Entry condition: a multi-hop retrieval question and evaluation set are
    declared in advance.
  - Constraints: do not persist raw patient records in a shared graph; pin the
    reviewed LightRAG commit and measure build/runtime cost.
  - Verify: retain only if it reproducibly improves retrieval or downstream
    quality under the same split and budget.

- [ ] **X3 Preference/RL training.** Consider DPO or GRPO only after stable SFT,
  trustworthy preference/reward data, and a non-gameable held-out protocol.
  - Entry condition: P5.4 shows a remaining error that preference optimization
    is plausibly designed to address.
  - Constraints: no reward for format alone, no test feedback, and no addition
    for resume keyword coverage.
  - Verify: beat the retained SFT system with confidence intervals and no loss
    of evidence fidelity, calibration, or unknown handling; otherwise drop it.

- [ ] **X4 Medical-image modality.** Consider MIMIC-CXR or another image source
  only when selected criteria genuinely require image evidence.
  - Entry condition: separate access, linkage, image-specific gold, clinical
    reviewer expertise, compute budget, and an explicit research question.
  - Constraints: a radiology report is text, not proof that raw-image modeling
    adds value; avoid a vision stack without an evaluable contribution.
  - Verify: image ablation demonstrates incremental benefit over report text
    and structured EHR under the same cohort.

These extensions are successful when they answer a research question, not when
they merely add another framework or model name.
