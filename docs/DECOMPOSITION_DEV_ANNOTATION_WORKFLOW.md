# Dev decomposition annotation workflow

Status: **manual dev annotation workflow; no completed annotations or gold yet**

This workflow lets the owner annotate the 40 frozen public dev criteria without
changing the immutable source package. It never reads the locked-test source
directory and never generates, recommends, repairs, or completes a tree.

## Bound inputs

- selection: `benchmarks/decomposition/af_decomposition_selection_1.2.0.json`
- dev sources: `benchmarks/decomposition/dev_sources_1.2.0/`
- catalog: `benchmarks/decomposition/dev_concept_catalog_1.1.0.json`
- issue log: `benchmarks/decomposition/dev_annotation_issue_log_1.0.0.json`
- package: `benchmarks/decomposition/dev_single_annotator_package_1.0.0.json`
- guide: `decomposition-guide/1.1.0`

The mutable work file belongs under `artifacts/decomposition/`, which is
already excluded from Git by the repository-wide `artifacts/` rule. It is
written with mode `0600`. The frozen package must never be edited.

## Start once

Run from the repository root after reinstalling the current working tree:

```bash
uv pip install --python .venv/bin/python --reinstall .

.venv/bin/clinical-matcher-decomposition-dev-annotate start \
  --output artifacts/decomposition/dev_owner_annotation_work.json
```

`start` refuses to overwrite an existing work file. It creates 40 null
expressions and reports the first pending criterion ID.

## Inspect one dev item

Use `next` to display only the next pending dev criterion, its frozen source,
resolution flags, and any owner-approved issue resolution:

```bash
.venv/bin/clinical-matcher-decomposition-dev-annotate next \
  --work artifacts/decomposition/dev_owner_annotation_work.json
```

`show --criterion-id ID` revisits a chosen dev item. Neither command displays
another annotation, model output, or locked-test text.

## Look up the frozen catalog

Catalog search is a literal lookup chosen by the annotator, not an automatic
recommendation:

```bash
.venv/bin/clinical-matcher-decomposition-dev-annotate catalog --query age
```

The owner writes one expression JSON file manually using the frozen expression
schema in
`src/clinical_matcher/schemas/decomposition-annotation-1.0.0.schema.json`.
The validator does not propose a field, operator, value, span, or repair.

## Save or clear one expression

```bash
.venv/bin/clinical-matcher-decomposition-dev-annotate set-expression \
  --work artifacts/decomposition/dev_owner_annotation_work.json \
  --criterion-id CRITERION_ID \
  --expression-json artifacts/decomposition/current_expression.json
```

The command checks the expression schema, catalog membership, source ID,
source-span bounds, positive-boolean/NOT convention, and left-to-right
condition-ID order before atomically replacing the mutable work file. Invalid
input leaves the previous work file unchanged.

Use `clear-expression --work ... --criterion-id ...` to remove a saved tree.
Use `progress --work ...` or `validate --work ...` at any time.

## Completion gate

Finalization fails unless all 40 expressions are present and valid. It also
requires two explicit attestations:

```bash
.venv/bin/clinical-matcher-decomposition-dev-annotate finalize \
  --work artifacts/decomposition/dev_owner_annotation_work.json \
  --output artifacts/decomposition/dev_owner_annotation_completed.json \
  --attest-human-no-model-output \
  --attest-test-source-not-inspected
```

The completed artifact is still owner work pending final reference-gold
packaging and audit. It is not automatically committed or described as dual
annotation, adjudicated gold, or clinical ground truth.

## Independence boundary

The owner may use the frozen guide, catalog, issue log, and mechanical
validator. The owner must not ask an LLM to propose, review, repair, or complete
any of the 40 trees before the dev reference is frozen. Assistance may explain
the mechanics of the CLI or schema without looking at a selected criterion's
intended tree.
