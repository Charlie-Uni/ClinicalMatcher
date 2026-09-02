# Local Llama decomposition comparison

This is the P5D.5 dev-only execution guide. It compares a pinned local Llama
3.1 8B output with the frozen Codex-drafted, owner-accepted assisted silver.
It does **not** measure accuracy against independent human gold.

## Frozen contract

`decomposition-llama-dev-comparison/1.0.0` fixes:

- Ollama `0.32.6` on unauthenticated loopback only;
- `llama3.1:latest` Q4_K_M manifest SHA-256
  `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`;
- temperature `0`, seed `17`, context `16384`, and output cap `4096`;
- one criterion per request and no retries or manual output repair;
- criterion source text, the complete 85-entry dev catalog, and only the
  common annotation-guide sections as model input;
- no assisted tree, owner review, item-specific issue resolution, few-shot
  example, patient data, or external inference endpoint.

The item-bound JSON Schema constrains catalog fields, condition-ID shape,
source-span bounds, source ID, method, model ID, and prompt version. The
semantic validator then enforces exact source slicing, condition-ID order,
typed operators, and typed-verifier loading. A failure stays in the fixed
40-item denominator with no hand correction.

## Required disclosure

Every report names both systems: the reference was drafted by
`openai-codex-conversational-assistant` and accepted unchanged by the owner;
the evaluated system is the pinned local Llama baseline. The owner-review
distribution is 40/40 accepted unchanged, zero edited items, and zero notes.
That unanimous no-note distribution is a rubber-stamp risk.

Eight reference items contain pre-model owner resolutions that are hidden from
the evaluated model. Their results are reported as a separate
information-asymmetry subgroup. Neither subgroup nor overall metrics may be
called decomposition accuracy, clinical ground truth performance, IAA, or a
GRPO semantic-oracle result.

The silver was frozen before this run. Observed disagreements may not be used
to change it. Any later correction requires a retained post-observation
exploratory version.

## Run dev once

Start the already-installed local Ollama service, verify `ollama --version` and
`ollama list`, then run:

```bash
.venv/bin/clinical-matcher-decomposition-llama-dev \
  --repo-root . \
  --output-dir artifacts/decomposition/llama_dev_1_0_0 \
  --acknowledge-dev-only-assisted-silver-comparison
```

The write is non-overwriting. It emits `predictions.json`,
`comparison-report.json`, and `comparison-report.md`. JSON artifacts are
self-hashed and bind the contract, public source package, catalog, model,
prompt, code commit, and silver manifest. The report contains overall atom,
structure, and span agreement; the eight-item subgroup; trial-cluster
bootstrap intervals; failure counts; latency and token totals; and the required
claim limitations.

This command has no test-split option. Locked-test text remains outside P5D.5.
