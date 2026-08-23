# P5 training decision record

Status: **decision procedure frozen; P5.1 remains open until the recorded gates
pass.**

This record defines the P5 LoRA-SFT path before restricted training data are
exported. It does not claim that training, evidence relevance evaluation, or a
held-out improvement has occurred.

## Scope and compliance boundary

- The real MIMIC-derived corpus, labels, prompts, completions, adapters,
  checkpoints, optimizer state, row-level manifests, and run logs remain on the
  authorized local Mac under ignored `artifacts/` paths.
- Ordinary Colab is limited to synthetic mechanism tests. No restricted text,
  labels, derived rows, model updates trained on them, or reversible metadata
  may be uploaded.
- Before any synthetic upload, `scripts/check_public_data.py` must pass for the
  public repository. P5.2 must additionally implement a manifest-based guard
  for the exact synthetic upload bundle; the current script scans Git-tracked
  files and is not falsely represented as a general directory scanner.
- MedicalGPT is an implementation and format reference, not vendored runtime
  code. Its pinned provenance remains in `docs/REFERENCES.md`.

## Base model and local training route

- Primary base: `meta-llama/Llama-3.1-8B-Instruct`.
- Hugging Face revision:
  `0e9e39f249a16976918f6564b8830bc894c89659`.
- License boundary: Llama 3.1 Community License; the final documentation must
  retain the applicable attribution and use restrictions.
- Real training target: local Apple Silicon through MLX/MLX-LM. No CUDA or
  Colab dependency is part of the restricted-data path.
- Primary precision: 4-bit QLoRA to preserve unified-memory headroom for the
  frozen context policy. A short bf16 LoRA feasibility run may be recorded, but
  it may not silently replace the primary configuration.

The owner approved this revision on 2026-08-23 before tokenizer-length
statistics, silver generation, or real fitting. Llama 3.2 3B access remained
unapproved, while official Llama 3.1 8B access was verified. The 8B choice also
preserves the earlier Llama 3.1 evaluation lineage and is closer to the
original 7B-class project requirement. It is not retained merely because it is
larger: the 24 GB unified-memory Mac must pass the frozen-context synthetic
memory and throughput gate before restricted training. Failure stops the 8B
route and triggers a separately reviewed fallback; it must not be hidden by
silently shortening inputs or changing the task.

The source tokenizer/configuration subset was downloaded from the frozen
revision into the ignored local artifact directory and verified as follows:

| Source file | SHA-256 |
|---|---|
| `config.json` | `29e4c210b0d6ac178b16b2a255a568bdb23b581e50ca1ef6a6d071dd85704e6e` |
| `generation_config.json` | `189fb0c0d7fd8a527db217c0a60a0e013f0394cd8800f9697a666a9e75e5f7fd` |
| `special_tokens_map.json` | `6f38c73729248f6c127296386e3cdde96e254636cc58b4169d3fd32328d9a8ec` |
| `tokenizer.json` | `79e3e522635f3171300913bb421464a87de6222182a0570b9b2ccba2a964b2b4` |
| `tokenizer_config.json` | `177c7b61e616fecb84c17ce0591acb92c6c4d60e9ac5ababfb940ff23bbcd424` |

The SHA-256 of the exact UTF-8 `chat_template` string stored in
`tokenizer_config.json` is
`e10ca381b1ccc5cf9db52e371f3b6651576caee0a630b452e2816b2d404d4b65`.
Its declared `model_max_length` is 131,072 tokens; that metadata is not a
training-context decision. The real context tier remains blocked on the
train-fit-only complete-sequence length report and the 8B feasibility run.

The reproducibility pin is the complete conversion chain, not only the source
model revision. Before P5.1 closes, the record must add:

1. `mlx` and `mlx-lm` versions and source revisions;
2. tokenizer revision and chat-template hash;
3. conversion command, quantization bits, group size, and other parameters;
4. SHA-256 values for converted weights and tokenizer artifacts;
5. seed, context length, batch/accumulation policy, LoRA targets/rank/alpha,
   optimizer, schedule, fixed training budget, recovery-checkpoint cadence,
   and the endpoint-checkpoint rule;
6. measured peak memory and throughput from a synthetic dry run.

### Disk-bounded artifact lifecycle

Disk lifecycle is a required conversion constraint, not an optional cleanup.
The 2026-08-23 preflight found 41 GiB available on the data volume. That leaves
no defensible operating headroom for the planning estimates of the source bf16
weights, converted MLX 4-bit base, fused fp16/GGUF intermediate, and final
Q4_K_M artifacts to coexist, and actual binary sizes may exceed the estimate.
Actual byte sizes, rather than these estimates, must be recorded in the run
manifest.

Every large stage uses a task-specific ignored path. The shared Hugging Face
cache, restricted corpus, benchmark, split, calibration reservation, tokenizer,
license, configuration, adapters, and final required evaluation artifacts are
never cleanup targets. For each regenerable model intermediate, the required
order is:

1. record the exact source revision, command, tool versions, parameters, byte
   size, and SHA-256;
2. build the downstream artifact into a distinct explicit path;
3. verify the downstream hash and perform its applicable load or numerical
   compatibility check;
4. durably write and re-read the conversion manifest;
5. delete only the now-regenerable upstream intermediate by its resolved exact
   task path, then record the deletion and recovery recipe.

A hash without a verified downstream artifact and recovery recipe is not
sufficient authorization to delete. Moving an intermediate to Trash does not
count because it does not release the required space. The source conversion is
shared by untuned and tuned runs rather than duplicated. Runtime-A untuned and
tuned fused/GGUF artifacts may be produced, evaluated, hashed, and retired
sequentially; they need not coexist once their immutable predictions and run
records have passed validation. No deletion occurs merely because this policy
is documented: each execution still resolves and reviews the exact target.

The public CPU package and local MLX training environment remain separate, but
P5 does not introduce a second lock system. A reviewed `requirements-mlx.txt`
with exact versions, together with the model/conversion/run manifest, is the
training-environment record for the first experiment.

The local mechanism environment was materialized on 2026-08-22 with CPython
3.11.16 on ARM64 macOS 26.5.2:

- `mlx==0.31.2`, release commit
  `68cf2fddd8de5edd8ab3d926391772b2e2cedad8`;
- `mlx-lm==0.31.3`, release commit
  `ed1fca4cef15a824c5f1702c80f70b4cffc8e4dd`;
- the complete observed 39-package environment is recorded in
  `requirements-mlx.txt`.

The five project-validation dependencies (`attrs`, `jsonschema`,
`jsonschema-specifications`, `referencing`, and `rpds-py`) reuse the exact
versions from the frozen public lock. They make the owner-only length and SFT
validation CLIs self-contained in the MLX environment; they do not add a
second model stack.

This pair was selected because MLX-LM 0.31.3 declares `mlx>=0.31.2` and its
release is paired with MLX 0.31.2. A local import/Metal matrix-compute smoke
test and `mlx_lm.lora --help` passed on the Apple GPU. This is only a framework
mechanism check: it is not the required 8B synthetic memory/throughput dry run,
does not pin the model conversion artifacts, and does not close P5.1.

Apple Silicon bitwise identity is not promised. Reproducibility means pinned
inputs, code, configuration, seeds, artifact hashes, and tolerance-based output
checks.

## Fair baseline and runtime contract

The primary SFT comparison is an untuned Llama-3.1-8B-Instruct run using the
same prompt, context policy, output contract, decoding route, and evaluation
split as the tuned adapter. Existing Ollama Llama-3.1-8B structured and
long-context runs use a different model artifact/runtime/input policy and
remain reference lines, not the causal estimate of SFT gain.

Because the old Ollama reference and the new primary chain share the Llama 3.1
8B Instruct family, the already-required untuned validation run provides a
useful conversion-health diagnostic at no additional inference run. Broadly
consistent behavior is expected, but numerical equivalence is not: the old
artifact is Ollama Q4_K_M, while the new source conversion, runtime, prompt
rendering, and patient-question input policy may differ. A material discrepancy
triggers inspection of conversion, quantization, tokenizer/chat template,
runtime, prompt, and input-policy provenance before interpreting model quality.
This is diagnostic only, has no retrospective pass threshold, and may not
replace the matched untuned-8B versus tuned-8B primary comparison.

Deployment is decided by an ordered compatibility test:

1. test adapter fusion and a pinned GGUF/Ollama path on synthetic fixtures,
   including numerical agreement checks;
2. if that route fails a recorded compatibility gate, use a pinned MLX
   inference path and record runtime as a known comparison confounder.

No route may be described as equivalent until schema features, including
array uniqueness and allowed evidence identifiers, have been tested. The
untuned and tuned 8B runs must use the same selected route.

## Canonical training source and split boundary

One owner-only canonical training record is the source of both:

- the actual MLX-LM training representation; and
- a MedicalGPT-compatible SFT JSONL compatibility export used only with
  synthetic data outside the local environment.

The exporter must prove patient-level membership, stable ordering, source and
split hashes, label distribution, JSON-schema validity, round-trip integrity,
and chat-template rendering consistency.

Before export, a deterministic patient-hash rule reserves exactly 15
calibration-only patients from the 70 frozen training patients, leaving 55
train-fit patients. Its salt/algorithm version, patient-list hash, and derived
manifest must be frozen before any fitting. These patients may not contribute
examples, silver labels, early stopping, or training-loss summaries.

Reservation implementation `1.0.0` is available through
`clinical-matcher-reserve-apixaban-calibration`. It accepts only a frozen split,
has no default patient count, uses the fixed SHA-256 policy documented in
`docs/APIXABAN_BENCHMARK.md`, validates source-bound membership, and writes a
new owner-only manifest without overwrite. Synthetic tests cover deterministic
repeat, train-only selection, boundary counts, source/membership tampering, and
private output. The owner approved the count of 15 before generation. The real
reservation is an owner-only restricted artifact and must remain outside Git
and ordinary online services.

The training unit is one patient-question pair. P5.1 must select one primary
input policy before export and record its visible chunk set, context/truncation
rule, and policy hash. The untuned and tuned 8B runs use that same policy. A
silver citation must be a subset of the chunks visible to the student for that
row; the exporter fails rather than training an unreachable citation.

The owner froze the following input and length contract on 2026-08-23, before
the train-fit length report was generated:

- input policy `all-complete-evidence-v1` exposes every complete evidence chunk
  in source order, with no retriever, label-based selection, partial-chunk
  slicing, or truncation;
- prompt `apixaban-single-fact-sft-1.0.0` includes injection resistance, the
  source-defined boolean and numeric rules, and the explicit instruction
  `For numeric facts with no value in the note, return unknown.`;
- every row reserves 512 output tokens, and an actual rendered target above
  512 stops export rather than being truncated;
- candidate context tiers are 2,048, 4,096, 8,192, and 16,384 tokens. The
  selected tier is the smallest tier for which the rendered system and user
  prompt, chat-template generation overhead, and 512-token reserve fit all
  55-by-23 train-fit rows.

The machine-readable contract is version `1.0.0`. The aggregate length report
contains only distribution summaries and tier counts, is hash-bound to the
source, tokenizer, chat template, prompt, and code commit, and is owner-only.
It contains no patient text, patient IDs, or row-level lengths. Validation and
test rows do not participate in selection. A future holdout row that exceeds
the frozen tier is recorded as a measured failure/abstention and is never
silently truncated.

Context selection uses token-length statistics from train-fit patients only.
Calibration-only, validation, and test patients do not influence the selected
length tier. Only owner-only aggregates may be written. Every candidate
`max_seq_len` covers the complete rendered sequence: chat-template and special
tokens, instruction, question, schema/format overhead, visible evidence, and a
fixed schema-derived output reserve. Synthetic memory dry-runs use that total
sequence length, not the bare note length. The tokenizer, chat template,
length tier, and complete-chunk truncation rule are frozen before citation
visibility and D/E coverage are computed.

The synthetic memory/throughput gate must exercise the exact context tier
selected by that owner-only complete-sequence report, including the same chat
template, instruction, question, schema overhead, visible complete chunks, and
fixed output reserve. Passing a shorter tier does not close the gate. If the
length-selected tier fails on the 24 GB machine, the 8B route fails as a whole
and enters the separately reviewed fallback procedure; the length report and
memory report may not be declared independently successful while their joint
configuration is infeasible. The failure cannot be hidden by post-hoc chunk
slicing, label-informed selection, or a silent context reduction.

## Evidence-ID supervision

### Semantics

The released labels provide typed facts, not independently adjudicated
evidence relevance. Rule-triggered or teacher-produced evidence IDs are
therefore **silver citation proxies**, never evidence gold. They may train the
output contract and support an auxiliary audit, but may not be used as the gold
for Evidence Recall@k, MRR, nDCG, or a claim of citation fidelity.

P4.3 verifies that known outputs cite an identifier belonging to the same
patient. It does not prove that the cited text supports the answer. P5.4 must
preserve that distinction.

### Source hierarchy

For train-fit patients only:

1. **D — deterministic-rule silver.** Retain a citation candidate only when
   the frozen deterministic typed result equals the released typed label.
   Store rule-set hash, rule IDs, prediction hash, question ID, and source
   category. Correct agreement does not itself establish citation relevance.
2. **E — frozen-teacher backoff.** Consider only gold-known rows not covered by
   an accepted D candidate. Require teacher typed agreement with gold and store
   model/tokenizer revisions, prompt and contract hashes, seed/decoding
   settings, runtime, prediction hash, and evidence IDs.
3. Lexical answer occurrence is a diagnostic within D, not a separate source.
4. Patient-local ownership, non-empty IDs, or teacher correctness alone is not
   a citation-quality gate.

Unknown labels do not need an evidence citation to satisfy P4.3 and are not
used to inflate D/E known-row coverage. Policy `1.1.0` explicitly permits the
source-defined `med_decisions=absent, value=false` result to remain known with
an empty citation. Those rows enter SFT with the contract-valid empty list and
remain in row and label-distribution reports, but they are excluded from the
citation-required coverage denominator and the per-question silver gate.
`med_decisions=present` remains a normal citation-required known row. No other
known empty-citation result receives this exception.

The deterministic rule artifact records development on both train and
validation. It is not described as train-only. Test labels remain unused. Its
validation influence must be carried into P5 provenance and into the limits of
validation-based model selection.

### Coverage and quality gates

The owner approved these numerical gates before generated coverage was
inspected:

- accepted silver must cover at least 60% of all citation-required gold-known
  train-fit rows;
- every citation-required question must independently reach both 30% coverage
  and at least five accepted rows;
- each D/E source audit reviews all candidates when it has at most 100, or a
  deterministic hash-stratified sample capped at 100 otherwise;
- each source must achieve at least 90% `support`; `ambiguous` is counted as a
  failure in that rate.

The two approved zero-tolerance audit categories are:

1. `cross_patient_citation`: a silver row cites any evidence ID that is not in
   that patient's own evidence inventory in the frozen staging corpus.
2. `student_invisible_citation`: a silver row cites any evidence ID that is not
   in the visible chunk set for that patient-question row under the frozen
   input policy and input plan.

Both are machine-checkable invariants, not sampling estimates. The silver
generator must assert them over the complete candidate set and fail closed on
any violation; the manual audit re-confirms zero occurrences in its reviewed
sample. Any observed instance at any stage is a pipeline defect: fix the
generator, regenerate the entire silver artifact, and perform fresh sampling
and a fresh audit. Removing only the offending row and continuing is
prohibited. Because `student_invisible_citation` depends on the visible chunk
set, real candidate generation and both coverage gates remain blocked until
the input policy is frozen.

The audit sampling protocol is
`sha256_stratified_silver_audit_sampling/1.0.0`. Its selection salt is
`clinicalmatcher-p5-silver-audit-v1`. For each source (`D` or `E`), a row's
sampling digest is SHA-256 over the UTF-8 encoding of the `"\0"`-joined tuple:

1. exact algorithm identifier
   `sha256_stratified_silver_audit_sampling/1.0.0`;
2. selection salt;
3. the **pre-audit silver candidate artifact** `artifact_sha256`;
4. patient ID;
5. question ID.

Every component must be non-empty and contain no NUL character; validation
fails closed before hashing otherwise.

The pre-audit artifact distinction is mandatory: an artifact cannot be called
accepted silver until its quality audit passes. Binding the digest to its
candidate-artifact hash makes a sample unique and reproducible for that
artifact. Complete regeneration after a zero-tolerance defect changes the
artifact hash and therefore requires a fresh sample structurally, rather than
by operator memory.

Per-source allocation protocol `1.0.0` has review budget `N=100`. Review every
candidate when a source has at most 100. Otherwise:

1. define the non-empty strata as question ID by fact status (`present` or
   `absent`); record the question-derived answer type (`numeric` or `boolean`)
   for every stratum;
2. assign one row to every non-empty stratum, which also guarantees at least
   one row from every question that produced candidates eligible for audit;
3. distribute the remaining budget by largest remainder in proportion to each
   stratum's remaining capacity (`candidate_count - 1`), not its original
   count, so no stratum can receive more rows than it contains; break equal
   remainders by ascending `(question_id, fact_status)`;
4. select the lowest sampling digests within each stratum.

Review rubric `1.0.0` presents the source question, released gold typed answer,
cited chunk text with evidence IDs, and D rule or E teacher provenance. The
reviewer records exactly one judgment: `support`, `not_support`, or
`ambiguous`. A numeric row is `support` only when the cited text supports the
exact extracted value in the question-required clinical context. Catalog
`1.0.0` defines `canonical_unit = null`, so this audit must not infer or claim
a gold unit; a unit visible in source text may be retained as citation
metadata but cannot be retroactively attributed to the released label. A
boolean row is `support` only after explicitly checking negation, personal
versus family history, and uncertain or hedged wording. Every reviewed row
also re-confirms zero occurrences of both zero-tolerance categories.

Exactly one reviewer, the data owner, performs this training-signal quality
check. It is not independent evidence gold. The audit package and judgment
record contain restricted note text, remain owner-only, and are hash-bound to
the pre-audit candidate artifact and this protocol version.

Reports must show, at minimum:

- all gold-known train-fit rows as the reported population;
- citation-required gold-known rows as the silver-gate denominator;
- citation-covered rows overall and by D/E source;
- coverage by question ID, numeric/boolean type, and present/absent status;
- default-absent and other structurally non-citable rows separately;
- rows rejected for typed disagreement, missing evidence, invalid ownership,
  student invisibility, ambiguity, or failed manual citation review.

An aggregate percentage alone cannot pass the gate if a citation-required
question or answer class has collapsed coverage. Every silver source must pass
a deterministic, stratified, owner-only manual review before any of its rows
enter training, including a D-only path. Reviewed `not_support` and `ambiguous`
candidates are removed, then coverage is recomputed and all overall/per-question
gates are applied again. Only audited D coverage decides whether E is needed;
if E is generated, E is audited and D+E coverage is recomputed once more. Any
protocol revision after candidate inspection invalidates that audit and
requires a newly versioned protocol, fresh sampling, and fresh review.

### First-run row policy

The first SFT run uses stock MLX-LM whole-completion loss and a coherent row
filter rather than a custom field-level trainer:

- include gold-known rows only when an accepted D/E silver citation exists and
  every cited ID is visible in the student's input;
- include `med_decisions=absent, value=false` with the contract-valid empty
  evidence list under the sole known-fact exception;
- include gold-unknown rows with the contract-valid empty evidence list;
- exclude gold-known rows without accepted silver from the first run;
- publish owner-only counts and proportions for every exclusion, question,
  answer status, and silver source.

This filtering can bias the fitted distribution toward questions and facts
that rules or the teacher can cite. The predeclared coverage gate must therefore
apply both overall and per question/class. If the observed coverage fails that
gate or empties a required question/class, P5.2 stops. Field-selective evidence
loss may then be proposed as a separately reviewed fallback; it is not part of
the first dry run or baseline implementation.

Exporter contract `1.0.0` implements this row policy without choosing the
still-open thresholds. It accepts only a frozen split, its source-bound
calibration reservation, a self-hashed exact train-fit input plan, and D/E
artifacts that explicitly attest that predeclared audit thresholds passed.
It fails on holdout membership, typed disagreement, cross-patient or invisible
citations, D/E overlap, incomplete input grids, and provenance tampering. One
canonical restricted record is then rendered losslessly to MLX `messages` and
MedicalGPT ShareGPT `conversations`; the exporter verifies message equality,
file hashes, owner-only permissions, and exclusion counts. Passing synthetic
tests is mechanism evidence only. It is not approval of the real input policy,
the audit thresholds, or any silver citation as evidence gold.

Mechanically attaching retrieval top-k IDs after inference is prohibited when
presented as model-generated grounding. A future architecture may expose
pipeline-attributed citations only through an explicit provenance field and a
new reviewed contract; that is outside the current P5 decision.

## P5.4 retention decision

Training uses a fixed budget and evaluates the endpoint checkpoint. Intermediate
checkpoints exist for recovery only and are not searched with task metrics. A
future switch to MLX-native validation-loss selection would require a recorded
revision before training, not a post-hoc checkpoint search.

The primary validation comparison is matched untuned 8B versus tuned 8B under
the same input policy and runtime. Existing rules, prior Ollama 8B, and RAG
results are descriptive context. P4.3/verifier output is the deterministic
post-processing view of a prediction, not an additional experimental arm.
Validation reports must
include:

- typed metrics before P4.3 projection;
- the same metrics after P4.3 projection;
- known-answer citation presence and patient-ownership validity;
- unknown handling and gold-known abstention;
- per-question D/E training coverage and manual silver-audit results;
- patient-cluster bootstrap intervals, latency, peak memory, schema validity,
  and comparison-runtime limitations.

Agreement with D/E is a weak auxiliary diagnostic, not evidence relevance.
The adapter must not be retained merely because it emits more patient-local
IDs. Exact primary metric, non-inferiority safety thresholds, training budget,
and stop/keep rule remain P5.1 closure gates and must be approved before the
first real training run.

P5.4 makes the validation-stage keep/drop decision and freezes the final
configuration. The locked test is exposed once, in one final batch under P7.2,
after all configuration decisions. P5 and P7 reuse the same immutable test
artifacts; no second test inference, checkpoint swap, threshold change, or
component replacement is allowed after results are visible.

## Closure checklist

P5.1 stays unchecked until all of the following are recorded and verified:

- complete MLX conversion and training pins;
- synthetic memory/throughput feasibility result;
- deterministic calibration-only patient reservation;
- untuned 8B baseline contract and runtime route;
- approved evidence coverage and manual-audit thresholds;
- approved primary metric, safety thresholds, training budget, and stop rule;
- one frozen patient-question input policy and visible-citation assertion;
- a train-fit-only length report selecting one approved tier, followed by a
  synthetic memory/throughput pass at that exact tier;
- synthetic tests for row filtering, exclusion accounting, and empty-unknown
  targets;
- an exact-version `requirements-mlx.txt` training environment record;
- a manifest-based guard for the exact synthetic Colab upload bundle;
- public license and attribution update.
