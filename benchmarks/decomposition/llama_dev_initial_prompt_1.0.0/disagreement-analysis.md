# Initial-prompt decomposition disagreement analysis (dev)

This is descriptive agreement with Codex-drafted, owner-accepted assisted silver; it is not accuracy against independent human gold.

- Scope: Under the initial frozen prompt v1.0.0, zero-shot and with no few-shot examples, local Llama 3.1 8B produced a negative descriptive-agreement result against the assisted silver for strict typed-schema decomposition (atom F1 = 0); this result is limited to that configuration and does not evaluate the ceiling after prompt iteration.
- Owner review outcome: 40/40 accepted unchanged, 0 edited, 0 review notes
- Reference draft model: `openai-codex-conversational-assistant`
- Evaluated model: `ollama/llama3.1:8b-instruct-q4_k_m@sha256:46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`
- Decision: retain as an initial-prompt dev-only negative baseline; test entry gate not met and locked test remains closed

## Frozen descriptive result

- Criteria / schema-valid / semantic-valid: 40 / 37 / 26
- Exact atom matches: 0

## Mutually exclusive primary attribution

Every dev item belongs to exactly one category; counts sum to 40. These are observable diagnostics, not causal proof.

| Category | Count |
|---|---:|
| `runtime_error` | 1 |
| `schema_invalid_output_budget_reached` | 2 |
| `semantic_invalid_boolean_operator` | 5 |
| `semantic_invalid_negation_encoding` | 5 |
| `semantic_invalid_string_operator` | 1 |
| `valid_atom_count_mismatch` | 15 |
| `valid_field_mismatch` | 1 |
| `valid_value_type_mismatch` | 2 |
| `valid_unit_mismatch` | 5 |
| `valid_fact_selection_mismatch` | 3 |

## Non-primary component overlap

These marginal multiset diagnostics use only the 26 semantic-valid outputs. They may overlap and do not change the zero exact-atom primary result.

| Component | Matched / predicted / reference | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| `field` | 29 / 33 / 68 | 0.8788 | 0.4265 | 0.5743 |
| `polarity` | 31 / 33 / 68 | 0.9394 | 0.4559 | 0.6139 |
| `operator` | 20 / 33 / 68 | 0.6061 | 0.2941 | 0.3960 |
| `value_type` | 15 / 33 / 68 | 0.4545 | 0.2206 | 0.2970 |
| `value` | 14 / 33 / 68 | 0.4242 | 0.2059 | 0.2772 |
| `unit` | 26 / 33 / 68 | 0.7879 | 0.3824 | 0.5149 |
| `time_window` | 30 / 33 / 68 | 0.9091 | 0.4412 | 0.5941 |
| `fact_selection` | 1 / 33 / 68 | 0.0303 | 0.0147 | 0.0198 |

## Interpretation boundaries

- Span alignment is `not_evaluable_without_identity_matched_atoms`: The reported zero span score has a zero matched-identity denominator; it does not show that every predicted span was wrong.
- The eight-item information-asymmetry subgroup had semantic-valid count 4 and topology agreement 0.0000; no causal effect is claimed.
- Runtime: mean 140.333 seconds/item, P95 552.965 seconds/item, total 5613.335 seconds on Apple M3.
- A stronger prompt, a larger-model contract, or truly independent decomposition gold would require a new, separately versioned decision; none changes this retained run.

The unanimous no-note owner-review distribution remains disclosed as a rubber-stamp risk. The assisted silver is observation-locked and was not revised after model disagreements were observed.
