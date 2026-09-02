# LLM-assisted decomposition review workflow

Status: **owner-approved active engineering workflow**

The owner changed the unexecuted single-annotator route on 2026-09-02, while
the prior work file still contained zero saved expressions. This workflow
retains the frozen source selection, concept catalog, issue resolutions, and
all semantic rules from decomposition guide `1.1.0`. It replaces only the
independence/model-output rule and the resulting claim.

The output label is always:

```text
llm_assisted_owner_reviewed_silver
```

It is not independent human gold, clinical ground truth, an IAA artifact, or
an independent semantic oracle for preference/RL training. Any comparison to
it is descriptive reference agreement, not decomposition accuracy.

## Bound assistance

- decision: `decomposition-llm-assisted-decision/1.0.0`
- decision SHA-256: `15e268461f4708ffb11bafd8712913ee622ace80c380e5a0aee7915825de6dd1`
- draft model label: `openai-codex-conversational-assistant`
- model revision: session-managed and unpinned
- draft prompt: `clinicalmatcher-assisted-decomposition-draft/1.0.0`
- source package: the frozen, still-unannotated dev package; its former
  independence/gold metadata is not inherited by assisted output

Every atom in both the draft and owner-reviewed expression retains
`method=llm`, the bound model ID, and prompt version. Owner edits are recorded
by an item-level review decision and note; they never rewrite provenance to
`human`.

## Start a separate work file

Do not reuse or overwrite `dev_owner_annotation_work.json`.

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist start \
  --output artifacts/decomposition/dev_llm_assisted_work.json
```

The new file is mode `0600`, write-once at creation, and starts with 40 null
drafts and 40 pending owner reviews.

## Inspect the next item

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist next \
  --work artifacts/decomposition/dev_llm_assisted_work.json
```

`next_action=llm_draft` means the assistant must produce a draft.
`next_action=owner_review` means the owner must inspect the already saved draft
before another item is drafted. This enforces one draft followed by one owner
decision rather than batch rubber-stamping.

The assistant may use the frozen catalog and approved issue resolution. It must
not inspect test source before the runtime configuration is frozen.

## Save the LLM draft

The assistant-generated expression is saved to a scratch JSON file, for
example:

```text
artifacts/decomposition/current_llm_draft.json
```

Every atom must use this provenance shape:

```json
{
  "method": "llm",
  "model_id": "openai-codex-conversational-assistant",
  "prompt_version": "clinicalmatcher-assisted-decomposition-draft/1.0.0"
}
```

Then run:

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist set-draft \
  --work artifacts/decomposition/dev_llm_assisted_work.json \
  --criterion-id CRITERION_ID \
  --expression-json artifacts/decomposition/current_llm_draft.json
```

Schema, catalog membership, source identity/span, positive-boolean convention,
and condition ordering are checked before the work file changes.

## Owner review

The owner reads the source, approved issue resolution, draft tree, catalog
definitions, and source spans. There are two permitted outcomes.

Accept without edits:

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist review \
  --work artifacts/decomposition/dev_llm_assisted_work.json \
  --criterion-id CRITERION_ID \
  --decision accepted_unchanged
```

Accept after edits:

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist review \
  --work artifacts/decomposition/dev_llm_assisted_work.json \
  --criterion-id CRITERION_ID \
  --decision accepted_with_edits \
  --expression-json artifacts/decomposition/current_owner_edit.json \
  --note "Concise description of what the owner changed and why."
```

An edited expression must differ from the draft and requires a non-empty note.
It still retains LLM atom provenance because the review was model-assisted.

If the draft is unusable, clear it and request a new one rather than accepting
it:

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist clear-item \
  --work artifacts/decomposition/dev_llm_assisted_work.json \
  --criterion-id CRITERION_ID
```

## Progress and validation

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist progress \
  --work artifacts/decomposition/dev_llm_assisted_work.json

.venv/bin/clinical-matcher-decomposition-dev-assist validate \
  --work artifacts/decomposition/dev_llm_assisted_work.json
```

Progress reports drafted and reviewed counts separately. One accepted item is
not represented as independent human annotation.

## Finalization

Finalization fails until all 40 drafts have an owner review:

```bash
.venv/bin/clinical-matcher-decomposition-dev-assist finalize \
  --work artifacts/decomposition/dev_llm_assisted_work.json \
  --output artifacts/decomposition/dev_llm_assisted_completed.json \
  --attest-llm-assistance-disclosed \
  --attest-owner-reviewed-every-item \
  --attest-test-source-not-inspected
```

The completed artifact permanently records both the original LLM draft and the
owner-reviewed expression, plus whether the owner changed it. It permanently
sets `independent_gold_claimed=false` and
`grpo_semantic_oracle_claimed=false`.
