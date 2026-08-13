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

- [ ] **F0.8 Lock the public runtime and test dependencies.** Add a reviewed,
  reproducible lock strategy without coupling the later GPU training stack to
  the lightweight public package.
  - Entry condition: select one supported lock tool and Python-version policy.
  - Constraints: keep MedicalGPT/PEFT/CUDA dependencies in a separate optional
    environment; do not add a large framework before its task begins.
  - Verify: recreate a fresh environment from the lock and pass the complete
    public CI workflow.

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

- [ ] **P1.2 Build the restricted benchmark adapter.** Convert the existing
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

- [ ] **P1.4 Freeze a patient-grouped benchmark split.** Create train,
  validation, and locked test membership with deterministic, label-aware group
  assignment and patient/admission isolation.
  - Entry condition: P1.3 establishes whether the desired class distribution is
    feasible; choose and document the split proportions before model runs.
  - Constraints: never split the 2,300 rows independently; the locked test set
    cannot guide prompts, retriever settings, thresholds, or model selection.
  - Verify: no patient/admission overlap, stable rerun hash, acceptable recorded
    label balance, exact duplicate checks, and a locally audited semantic
    near-duplicate scan.

- [ ] **P1.5 Extend evaluation for mixed answer types.** Add boolean/unknown
  classification and numeric extraction metrics without conflating them.
  - Entry condition: P1.1 defines normalization and any numeric tolerance.
  - Constraints: report per-question and macro results; choose tolerances from
    clinical/task semantics on train/validation only, never from test outcomes.
  - Verify: tests cover exact match, macro/micro F1, unknown F1, MAE, tolerance
    accuracy, missing values, invalid units, and patient-cluster confidence
    intervals.

Acceptance: one command regenerates and validates the ignored benchmark, one
frozen split governs all subsequent work, and no test label has influenced the
pipeline.

---

## P2 — Establish non-trained baselines before adding retrieval

- [ ] **P2.1 Implement a deterministic extraction baseline.** Add reviewed
  lexical aliases, negation handling, numeric extraction, min/max aggregation,
  and explicit unknown output for the 23 questions.
  - Entry condition: P1 benchmark and metrics are executable.
  - Constraints: rules are derived from question semantics and training data
    only; no patient-specific exception or test-answer lookup is allowed.
  - Verify: unit tests cover positive, negative, ambiguous, multiple-value, and
    absent-fact cases; emit a versioned validation/test report.

- [ ] **P2.2 Implement a local frozen-model structured-output baseline.** Ask a
  pinned open model to return the P1.1 schema from the note and question.
  - Entry condition: record available hardware, model license, local inference
    path, context limit, and deterministic decoding policy.
  - Constraints: restricted text stays in an approved local environment; no
    ordinary online API; invalid output is a measured failure or abstention,
    not silently hand-corrected.
  - Verify: schema-valid rate, task metrics, latency, token count, memory, model
    revision, prompt version, and reproducible run report are present.

- [ ] **P2.3 Implement the matched long-context baseline.** Evaluate the same
  frozen model with the full note under a declared truncation/token policy.
  - Entry condition: P2.2 model and output contract are stable.
  - Constraints: use the same question and decision semantics as later RAG;
    disclose truncated notes and do not give one method extra label-derived
    context.
  - Verify: report retained-text proportion, truncation count, effectiveness,
    latency, tokens, memory, and privacy-exposure proxy.

Acceptance: rules, frozen structured inference, and long context provide honest
baselines before any retriever or fine-tuning claim.

---

## P3 — Conventional evidence retrieval

- [ ] **P3.1 Freeze the evidence-chunk contract.** Define deterministic chunks,
  stable evidence IDs, note offsets, section metadata when available, and an
  index manifest.
  - Entry condition: prove that chunk generation does not read answer labels.
  - Constraints: preserve exact source spans; embeddings and indexes remain
    restricted local artifacts; changing chunking creates a new index version.
  - Verify: chunks reconstruct their source spans, never cross patients, cover
    documented text according to policy, and regenerate to the same hash.

- [ ] **P3.2 Implement BM25 behind a common retriever interface.** Return ranked
  evidence IDs and scores for each question.
  - Entry condition: P3.1 is frozen.
  - Constraints: no answer text or test labels enter query construction; record
    tokenizer, normalization, and BM25 parameters.
  - Verify: controlled ranking tests, patient isolation, deterministic output,
    latency/index-size measurement, and downstream answer metrics.

- [ ] **P3.3 Implement one validated dense retriever.** Pin one embedding model
  and revision, pooling, normalization, dimension, and similarity metric.
  - Entry condition: license, local data handling, and hardware are acceptable.
  - Constraints: start with one model; do not collect multiple encoders without
    a predeclared comparison question.
  - Verify: vector/document count consistency, patient isolation, deterministic
    index fingerprint, controlled retrieval tests, and downstream metrics.

- [ ] **P3.4 Add fusion, then reranking only if justified.** Compare BM25,
  dense, and reciprocal-rank fusion; add one cross-encoder reranker only after
  fusion is measured.
  - Entry condition: P3.2 and P3.3 reports share the same split and query set.
  - Constraints: select fusion/reranking settings on validation only; every
    added stage must have an ablation and resource measurement.
  - Verify: paired validation/test reports include downstream quality, latency,
    memory, index size, and confidence intervals.

- [ ] **P3.5 State the evidence-evaluation boundary.** Use independent evidence
  gold where it exists; otherwise report answer-containing-span diagnostics and
  downstream task accuracy as separate, limited signals.
  - Entry condition: audit the official release for genuinely human-authored
    evidence links before choosing retrieval metrics.
  - Constraints: never evaluate a linking rule against links created by the
    same rule; do not call lexical answer occurrence clinical relevance.
  - Verify: every reported retrieval metric names its gold source, coverage,
    exclusions, and whether it is primary, weak/silver, or diagnostic only.

Acceptance: the simplest reproducible retriever that improves held-out answer
quality is selected; unhelpful stages are removed rather than retained for
novelty.

---

## P4 — Structured reasoning, verification, and abstention

- [ ] **P4.1 Connect model fact outputs to the existing typed verifier.** Parse
  boolean, numeric, unit, evidence, and uncertainty fields and deterministically
  map a fact to criterion polarity only when a specific criterion is supplied.
  - Entry condition: at least one P2/P3 model emits the P1.1 contract.
  - Constraints: fact truth and eligibility are separate; invalid or missing
    evidence cannot be invented during mapping.
  - Verify: tests cover adverse positive facts, inclusion/exclusion reversal,
    numeric thresholds, unknown, and missing evidence.

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

- [ ] **P4.3 Add deterministic abstention baselines.** Abstain on missing facts,
  invalid schema, unusable evidence, incompatible units, and verifier conflict.
  - Entry condition: each abstention reason has a machine-readable code.
  - Constraints: unknown is not assigned an arbitrary probability or folded
    into an eligibility score.
  - Verify: coverage-risk curves and reason counts reproduce exactly; known and
    unknown cases are tested separately.

- [ ] **P4.4 Add probabilistic calibration only when probabilities exist.** Fit
  calibration and review thresholds using validation predictions, then freeze
  them before the locked test run.
  - Entry condition: a model produces meaningful, reproducible probabilities
    or scores and there are enough validation patients to estimate calibration.
  - Constraints: deterministic coverage is not called calibrated confidence;
    report small-sample limitations.
  - Verify: Brier score, calibration error, reliability/coverage-risk outputs,
    frozen threshold provenance, and no test-set threshold tuning.

- [ ] **P4.5 Produce mutually exclusive error attribution.** Separate retrieval
  failure, reasoning failure with usable evidence, numeric/unit/time/negation
  errors, false abstention, and unsupported answering.
  - Entry condition: pipeline stages emit sufficient trace metadata.
  - Constraints: attribution is diagnostic, not causal proof.
  - Verify: categories reconcile to the total errors and representative cases
    are reviewed only inside the authorized environment.

Acceptance: verification and abstention reduce risk under a declared coverage
trade-off and do not hide unresolved clinical information.

---

## P5 — MedicalGPT-compatible LoRA-SFT adaptation

- [ ] **P5.1 Freeze the training decision.** Select a license-compatible 3B–7B
  class base model, LoRA or QLoRA precision, local hardware budget, context
  policy, and success criterion against the frozen-model baseline.
  - Entry condition: P2 and the chosen P3/P4 baseline are complete.
  - Constraints: choose one primary model; training is optional if compute,
    license, or expected value is inadequate.
  - Verify: decision record pins model/tokenizer revisions, license, memory
    estimate, training budget, and metric required to justify retention.

- [ ] **P5.2 Export training folds to the pinned MedicalGPT SFT format.** Build
  a versioned adapter from P1.1 records to training JSONL.
  - Entry condition: the split manifest is frozen and MedicalGPT remains pinned
    to the reviewed commit in `docs/REFERENCES.md`.
  - Constraints: export train only for fitting; validation is separate; test
    records, labels, outputs, and patient text never enter training artifacts.
  - Verify: schema validation, exact patient-membership assertions, dataset
    hash, chat-template snapshot, sample round-trip, and label distribution.

- [ ] **P5.3 Run one LoRA-SFT experiment in a separate environment.** Record
  base model, adapter config, seed, optimizer, schedule, precision, checkpoint,
  CUDA, Transformers, PEFT, and MedicalGPT versions.
  - Entry condition: P5.1 and P5.2 pass; restricted data remains local.
  - Constraints: no unrestricted tracking service receives prompts or patient
    text; training loss alone is not evidence of benefit.
  - Verify: resume/load test, held-out validation report, adapter provenance,
    and reproducible inference on synthetic fixtures.

- [ ] **P5.4 Evaluate whether the adapter earns its complexity.** Compare rules,
  frozen model, frozen+RAG, LoRA-SFT, LoRA+RAG, and LoRA+RAG+verifier on the same
  locked test split.
  - Entry condition: model and retriever selection are frozen on validation.
  - Constraints: disclose overfitting, invalid output, unknown recall, latency,
    and memory; remove LoRA from the final default if it does not provide a
    meaningful held-out benefit.
  - Verify: paired report with patient-bootstrap intervals and a documented
    keep/drop decision.

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
    aggregates and disclosure-safe examples can be public.
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
