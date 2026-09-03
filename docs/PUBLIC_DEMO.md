# Synthetic public demo

Status: executable P7.3 public interface

Demo contract: `public-demo-report/1.0.0`

This demo is a deterministic, offline view of ClinicalMatcher's implemented
core. It ranks two fictional trials for two fictional patients and exposes the
intermediate records needed to audit the result:

- patient-local BM25 evidence candidates and scores;
- typed facts and their evidence IDs;
- inclusion/exclusion polarity and hard/soft behavior;
- atom-level truth values, units, reasons, and issues;
- trial decision, score, coverage, and abstention reasons; and
- derived synthetic safety probes for a missing required fact and a unit
  conflict.

It is research software, not a medical device. It must not be used for medical
advice, clinical eligibility decisions, autonomous enrollment, or patient
exclusion.

## Run from a clean clone

Create the pinned public CPU environment as described in the README, reinstall
the current worktree, then run:

```bash
uv run --no-sync clinical-matcher-demo \
  --fixture fixtures/synthetic/trial_matching.json \
  --format markdown
```

For the complete machine-readable audit trace:

```bash
uv run --no-sync clinical-matcher-demo \
  --fixture fixtures/synthetic/trial_matching.json \
  --format json
```

Neither command requires a model server, accelerator, network connection, API
key, or restricted dataset. The report contains no generated timestamp, so the
same fixture bytes and code produce the same JSON. The field
`fixture_file_sha256` explicitly means SHA-256 over the file bytes; it is not a
canonical document self-hash.

## What the trace means

The fixture supplies already typed, evidence-linked facts. The demo runs real
patient-isolated BM25 retrieval over the fictional evidence and displays that
ranking beside deterministic criterion evaluation. BM25 does not extract or
create the typed facts in this public path. A retrieved hit is therefore a
candidate text record, not independently annotated relevance gold.

The normal path shows how facts, evidence IDs, atomic conditions, criterion
polarity, and hard/soft aggregation produce a stable multi-trial ranking. The
two safety probes are explicitly derived from the fictional fixture:

1. `missing_required_fact` removes one required numeric fact and must return
   `unknown` with an abstention reason;
2. `typed_unit_conflict` changes only that fictional fact's unit and must
   return `unknown` with a unit-mismatch verifier issue.

These probes demonstrate fail-closed software behavior. They do not estimate
clinical safety, calibration, or real-world error rates.

## Fail-safe input boundary

The command accepts only a document with the exact independently authored
synthetic-fixture declaration and then applies the frozen JSON Schema plus
semantic-link validation. Invalid JSON, an altered declaration, an invalid
schema, or a broken patient/evidence relationship produces exit code `2`, a
generic error on stderr, and no partial report. The generic error deliberately
does not echo input content.

The declaration and static checks reduce accidental misuse; they are not proof
that arbitrary user-supplied data is synthetic. Do not point this command at
clinical records.

## Deliberate omissions

- No Llama, online API, MedicalGPT, or LightRAG component runs in this demo.
- No real patient, MIMIC row, patient-level label, model prediction, embedding,
  or index is packaged.
- No synthetic result is reported as clinical accuracy.
- No autonomous enrollment recommendation is emitted.
