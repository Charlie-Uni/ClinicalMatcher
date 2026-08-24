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

The pinned Hugging Face source weights are downloaded into the ignored,
task-specific model directory rather than the shared Hugging Face cache. The
small tokenizer, configuration, license, and use-policy files remain protected.
After the converted 4-bit artifact has been hashed, load-checked, recorded in a
durably re-read manifest, and shown to be recoverable from the pinned revision,
only the manifest-listed source weight shards and their index may be deleted.
Deleting a whole shared cache, an unresolved glob, or the protected files is
prohibited. If a tool nevertheless uses the shared cache, only its exact pinned
repository revision may be removed through a cache-aware command that preserves
other repositories and revisions; an indiscriminate cache purge remains
prohibited.

The public CPU package and local MLX training environment remain separate, but
P5 does not introduce a second lock system. A reviewed `requirements-mlx.txt`
with exact versions, together with the model/conversion/run manifest, is the
training-environment record for the first experiment.

The local mechanism environment was frozen on 2026-08-23 with CPython
3.11.16 on ARM64 macOS 26.5.2:

- `mlx==0.31.2`, release commit
  `68cf2fddd8de5edd8ab3d926391772b2e2cedad8`;
- `mlx-lm==0.31.3`, release commit
  `ed1fca4cef15a824c5f1702c80f70b4cffc8e4dd`;
- the complete 40-package environment is recorded in
  `requirements-mlx.txt`.

The five project-validation dependencies (`attrs`, `jsonschema`,
`jsonschema-specifications`, `referencing`, and `rpds-py`) reuse the exact
versions from the frozen public lock. They make the owner-only length and SFT
validation CLIs self-contained in the MLX environment; they do not add a
second model stack. The exact `setuptools==84.0.0` pin permits first-party
wheel reinstall without an unpinned build-isolation download; it is build
plumbing, not a model dependency.

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

The owner-only real report was generated locally on 2026-08-23 from the frozen
55-by-23 train-fit grid using code commit
`2d938bb03eadb791c12b88bdc17ab234729456c4`. The smallest tier fitting every
rendered prompt plus the 512-token reserve was **16,384 tokens**. The report,
input plan, patient-level rows, and restricted aggregate statistics remain
outside Git. This selects the configuration to test; it does not pass the
joint gate. The 8B route remains blocked until a synthetic 16,384-token QLoRA
memory/throughput run succeeds on the 24 GB machine.

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

Gate contract `p5-mlx-qlora-16k-gate/1.0.0` freezes 4-bit affine quantization
with group size 64; LoRA rank 8, scale 20, dropout 0 over the last 16 layers;
micro-batch 1 with four-step gradient accumulation; prompt-loss masking;
gradient checkpointing; and seed 17. The intended Llama target keys are
`q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj`, and `down_proj`
under their attention/MLP paths. The run manifest must record the complete
resolved module-name list from the loaded converted model and fail if its
suffixes differ from that contract.

The optimizer is specifically `mlx.optimizers.Adam`, learning rate `1e-5`,
betas `[0.9, 0.999]`, epsilon `1e-8`, `bias_correction=false`, no weight decay,
and a constant schedule with no warmup. The synthetic gate runs eight
micro-iterations, producing two four-step measurement windows. This is long
enough to cross two optimizer-update boundaries without pretending to be a
training-quality experiment. At least one rendered synthetic sequence must be
exactly 16,384 tokens. The gate records seconds/step, tokens/second, peak memory,
and the peak stage. Pinned MLX-LM `0.31.3` uses its default loss, which
materializes full vocabulary logits and does not use chunked cross-entropy; this
fact is recorded before execution so an OOM is not misdiagnosed. Any later
memory-relevant parameter change requires a fresh gate.

The frozen gate was executed locally on 2026-08-23 from implementation commit
`1b4cabf`. Conversion manifest
`577a5de3af1f21753202e2949f37e76292a6cf03a1413eb9001ac3da63dc096e`
records 16,069,739,560 source bytes and a 4,534,758,247-byte converted artifact,
plus a successful Metal load and exact converted/source token-ID equivalence on
the 16,384-token probe. The converted model resolved exactly 112 LoRA modules:
the seven approved projection suffixes in each of layers 16 through 31.

The stock-loss 16,384-token training gate **failed before its first completed
micro-iteration**. MLX attempted one 17,177,772,096-byte allocation, exceeding
the Metal maximum buffer size of 14,302,248,960 bytes. This is consistent with
the predeclared unchunked full-vocabulary-logits risk. The owner-only failed-run
manifest hash is
`43937dea18fe54609c549edfd69ff8bedacfebd2e9131b5b0d8d2d79d080c2d5`;
it contains no patient data. No seconds/step or tokens/second estimate exists
because no step completed. The approximately 5.16 GB observed peak excludes the
rejected 17.18 GB allocation and must not be reported as the required memory.

**Revision note (2026-08-24):** the preceding consistency statement is retained
as the historical pre-diagnostic interpretation, but it is incorrect. The
completed allocation-source diagnostic below identifies the requested buffer as
the explicit grouped-query attention-score matrix, not the vocabulary-logit
tensor. This additive correction does not rewrite the original gate record.

Therefore the frozen **8B + 16K + stock MLX-LM default-loss** route is not
feasible on this machine and does not pass P5.1. No shorter context, different
loss, or different base model is selected by this failure. Any fallback is a
new owner-reviewed contract and requires a fresh exact-configuration gate.

### Approved completion-only projection revision

On 2026-08-24 the owner separately approved gate contract
`p5-mlx-qlora-16k-gate/1.1.0`, triggered only by failed-run manifest
`43937dea18fe54609c549edfd69ff8bedacfebd2e9131b5b0d8d2d79d080c2d5`.
The model, full 16,384-token input, whole-completion targets, prompt mask,
optimizer, LoRA configuration, gradient accumulation, and seed remain
unchanged. The revision changes only how the identical masked loss is
calculated: the complete sequence still produces hidden states, while the
vocabulary projection is limited to the 544-position tail that provably covers
the 512-token output reserve plus the pinned iterator's 32-token padding slack.

This is not the rejected field-selective-loss proposal. No JSON field or token
inside the assistant completion receives a different weight or mask, and the
earlier proposal to omit evidence-ID loss on selected completion spans remains
rejected. The stock `mlx_lm.tuner.trainer.train` loop and exact Adam
configuration remain in use; only its documented `loss` argument receives
`clinical_matcher.p5_mlx_completion_loss.completion_only_projection_loss`.
Implementation `1.0.0` is pinned by source SHA-256
`d95ca72cacbb63ec027c83324e015331dccbcdddcc8b9ffbe6e8f42cad518d60`.

Before the revised 8B gate, public synthetic boundary tests and an Apple-MLX
test against pinned `mlx_lm.tuner.trainer.default_loss` must establish: the
first completion target is predicted from hidden position
`prompt_offset - 1`; completion length one, prompt length one, and completion
length 512 are covered; loss values match under tight tolerance; and gradients
match for every trainable parameter. Passing those tests authorizes only a
fresh synthetic 16K gate. If another allocation or runtime stage fails, work
stops for another owner review; no chained parameter change is permitted.

The prerequisites passed on 2026-08-24: the public suite completed 292 tests
(two Apple-only cases skipped there), the pinned Apple-MLX loss/gradient and
stock-trainer injection cases passed locally, public-data checks passed, and CI
run `32645060172` was green for commit `ef3018c`. The ensuing exact gate still
failed before its first completed micro-iteration. Its self-hashed result
manifest is
`1d8b751d2608a7c74f8410474fb96d99af9ba50dc1a7bf629b06347de977b720`.
The run verified a 16,384-token untruncated row, a 544-position projected tail,
39 supervised positions, all 112 intended LoRA modules, and the pinned loss
module hash, then requested the identical 17,177,772,096-byte buffer against
Metal's 14,302,248,960-byte limit. No throughput window completed.

This result disproves the prior attribution of that exact allocation to the
full-sequence vocabulary-logit tensor: restricting vocabulary projection did
not alter the request. The size is numerically compatible with a quadratic
16K attention-like intermediate, but the Metal error contains no allocation
stack, so that remains an inference rather than a recorded cause. Contract
`1.1.0` therefore does not pass the mechanism gate. P5.1 remains open, and no
additional fallback or parameter change is authorized by this failure.

### Approved attention-allocation diagnostic

The owner approved diagnostic contract
`p5-mlx-attention-diagnostic/1.0.0` on 2026-08-24. It uses synthetic arrays
only, cannot read restricted data, cannot run the full model or trainer, and
cannot authorize a training fallback. Before execution it freezes the exact
prediction `2 * 32 * (L - 1)^2` bytes for the grouped-query attention score
shape `[1, 8, 4, L - 1, L - 1]` in bfloat16: 1,073,217,600 bytes at 4K,
4,293,918,784 bytes at 8K, and 17,177,772,096 bytes at 16K.

The diagnostic has three outputs: process-isolated allocation probes for all
three tiers in pinned MLX; a small causal SDPA query-gradient probe in the
pinned and latest-stable MLX environments; and a hash-bound audit of official
MLX source for the pinned release, latest stable release, and current main
commit. Successful allocation-probe tensors must match the predicted shape and
byte count exactly. A failed allocation counts as confirming evidence only if
the allocator-reported request equals that same predeclared byte count. Peak
memory remains an aggregate secondary observation and is never substituted for
the single-buffer byte prediction.

After execution, the historical sentence describing the first failure as
consistent with full-vocabulary logits must remain visible but receive an
additive correction that cites the new diagnostic artifact hash. Regardless of
the result, work returns to owner review; no package upgrade, shorter context,
different model, or execution-environment change follows automatically.

### Attention-allocation diagnostic result

The approved diagnostic completed on 2026-08-24 without reading restricted
data or changing the training configuration. The three isolated pinned-MLX
allocation probes matched the frozen quadratic prediction exactly:

- 4,096-token tier: shape `[1, 8, 4, 4095, 4095]`, predicted and observed
  1,073,217,600 bytes; result manifest
  `c4efb33ca479dc1bc1d3b5d05fc66bc276667770ac264124d5acc1d07ff4d470`;
- 8,192-token tier: shape `[1, 8, 4, 8191, 8191]`, predicted and observed
  4,293,918,784 bytes; result manifest
  `3b7d81a550da8351510884f672ea5c6a0fdb8e65890705cd2270c6ce7b44243c`;
- 16,384-token tier: shape `[1, 8, 4, 16383, 16383]`, predicted
  17,177,772,096 bytes; Metal rejected exactly 17,177,772,096 bytes against its
  14,302,248,960-byte single-buffer limit; result manifest
  `d15c756df1a48d95734ea8c66ceeeca98655cfdce520efb146eb7def977cb128`.

Therefore the failed buffer is the explicit bfloat16 grouped-query
attention-score tensor: `2 * 32 * (L - 1)^2` bytes. This is direct
three-tier allocation evidence, not an inference from the 16K byte count alone.
The small causal SDPA query-gradient probes also executed in pinned MLX 0.31.2
and latest-stable MLX 0.32.1; their result manifests are respectively
`28ea04bb054fde47a562887a921668f7c49fd0bdf6824505b747274e3a288b62`
and
`49e601bf113e649c56d315d4f4feec9647f1028025d0f1e80daa47976fc85b72`.
Those small probes establish executable gradient behavior only; they do not by
themselves establish a fused or memory-efficient backward route.

The hash-bound official-source audit in diagnostic contract
`912d3bf67ed491a86b7f03f0815152cdcfad174c0bf4359eaa4cb125d7741a8f`
finds the same decisive limitation in pinned MLX 0.31.2, latest-stable MLX
0.32.1, and current main commit
`d9077d8316ad7305497a3ecf2296bd0e0e99a627`: Metal training forces the
unfused fallback, the SDPA VJP selects fallback, and its Metal GPU evaluator is
not implemented. Consequently, upgrading to the currently released MLX or the
audited current main does not provide a memory-efficient Metal SDPA backward
path for this gate.

The completion-only vocabulary projection remains a tested, semantically
equivalent optimization and is not reverted, but it cannot remove this earlier
attention allocation. Under the audited official MLX implementations, local
8B + 16K training remains infeasible on this machine. P5.1 remains open and
returns to owner review; this result selects no shorter context, new execution
environment, different model, or postponement policy.

### Owner-selected staged 8K investigation

On 2026-08-24 the owner selected the local 8K path for investigation without
yet revising the frozen input policy. Contract
`p5-mlx-qlora-8k-probe/1.0.0` first authorizes a synthetic-only 8,192-token
probe using the same converted 8B model, completion-only loss, LoRA targets,
optimizer, batch/accumulation configuration, checkpointing, and seed as the
failed 16K gate. It records peak memory, both throughput windows, and a
conservative one-epoch projection using the slower reported seconds/step times
the original 1,265-row grid. Passing this probe is feasibility evidence only.

Before reading the owner-only report's 8K coverage count, the following
length-only screen is frozen: at most 5% of the original 1,265 rows may overflow
(therefore at most 63 rows), and every question must retain at least 30% of its
original train-fit rows and at least five rows. All questions are screened
without labels, which conservatively covers the later citation-required
subset. This necessary screen does not replace the already frozen silver
coverage gate. Both the synthetic probe and the length screen must pass before
an 8K policy revision may be drafted; neither result automatically changes the
policy or authorizes restricted training.

The exact 8K probe was executed locally on 2026-08-24 from implementation
commit `68c9ef8`. It did **not** pass. After starting adapter setup and training,
the native Metal process terminated with `SIGABRT` / exit code 134 and reported
command-buffer `Insufficient Memory`
(`kIOGPUCommandBufferCallbackErrorOutOfMemory`). This was not the earlier
14,302,248,960-byte single-buffer-limit error. The native abort occurred before
either four-step reporting window completed, so no seconds/step, epoch wall
clock, or reliable peak-memory measurement exists and none may be inferred.

Because a C++ native abort cannot be caught by the Python trainer, a separate
post-failure recorder bound the preflight, exact synthetic JSONL, and partial
adapter configuration without rerunning the probe. The owner-only failure
manifest is
`926b074c4901e9ff394d2791454a436651b8fc44ac610200cee9e42f4ef22799`;
it records probe commit `68c9ef8`, recorder commit `6944730`, the frozen
contract/model/loss hashes, the empty throughput-report list, and the terminal
observation source. It contains no patient data.

Under the predeclared staged decision, this failed step 1 stops the 8K route
before inspecting the owner-only length report. The 5%/63-row and per-question
thresholds remain frozen but unevaluated, the input policy remains unchanged,
and no 8K input-plan version or silver-grid revision is authorized. P5.1 returns
to owner review; neither a retry nor postponement follows automatically.

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

Canonical-row contract `1.0.0` and exporter contract `1.1.0` implement this
row policy under the approved thresholds. The exporter accepts only a frozen
split, its source-bound
calibration reservation, a self-hashed exact train-fit input plan, and D/E
artifacts that explicitly attest that predeclared audit thresholds passed.
It fails on holdout membership, typed disagreement, cross-patient or invisible
citations, D/E overlap, incomplete input grids, and provenance tampering. One
canonical restricted record is then rendered losslessly to MLX `messages` and
MedicalGPT ShareGPT `conversations`; the exporter verifies message equality,
file hashes, owner-only permissions, exclusion counts, and frozen-tokenizer
sequence limits. Passing synthetic tests is mechanism evidence only. It is not
evidence that a real silver artifact passed its audit, or that any silver
citation is evidence gold.

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
