# P8.5 single-exposure implementation review

Date: 2026-10-19. Status: implementation prepared; exposure prerequisites pending.

The owner requested "先跑 P8.5 holdout" in the current session. This is a new,
explicit execution instruction for the previously selected H + V1 candidate.
The final private authorization record must quote this instruction and bind
the completed final plan; it must not invent a later owner review.

## Candidate and runtime reconciliation

The sealed `reader-contract-H-rep-mac.json` records **Ollama 0.34.4**, despite
the development narrative naming 0.34.0. Its self SHA-256 is
`4337ae51193f07a387233bdfaf20af4a0634449b83116b61ce2e665b0bb09ae4`.
The sealed configuration takes precedence. The original contract is retained
unchanged. P8.5 uses its original H grouping, unpatched v1 prompt, schema,
Llama 3.1 8B Q4_K_M manifest, decoding parameters, request budget and request
retry policy, on the same Apple M3 hardware.

The currently installed application has upgraded to 0.40.3. An isolated 0.34.4
runtime therefore uses loopback port 11435. The port is the only transport
deviation; request bodies and inference settings do not change. The official
Darwin archive SHA-256 is
`e9c8fddaab5f48f47f2c4ae3d23d0732f5182417125353faeed2188e34a22799`.
The final private plan pins the actual runtime files, encoder files, model
files, environment versions and installed source bytes.

## Frozen batch and reporting scope

The fixed dependency arms are `rules`, `H`, then `H.V1`. Every arm receives raw
and P4.3 safety views. No historical v1 control is added, so the batch reports
absolute held-out fact-extraction results and makes no historical improvement
claim. H versus H.V1 paired patient-bootstrap differences are predeclared in
the restricted report; validation values are descriptive references only.

The public projection has exactly six numeric fields per arm/view: typed
exact match, boolean macro-F1, numeric-status macro-F1, unknown count, and
request-latency P50/P95. H and H.V1 share the same underlying inference latency.
Rule-only request latency is not applicable. Intervals, question-level results,
patient details, raw rows and examples remain owner-only.

## Producer/consumer and failure review

- Preparation checks partition membership metadata and file permissions, but
  never opens or byte-hashes the two protected holdout files.
- The existing project-wide exclusive consumption marker is written and
  durably synced before either protected file is opened. A second process,
  changed output directory, new plan or partial marker cannot reset it.
- An in-process reader verifies the persistent event, final-plan self pin,
  original reservation, exact file-byte pin, canonical-content pin, partition
  seal and population. Evidence and gold each have one read slot.
- Patient-local BM25 uses strictly positive candidates; MedCPT and RRF reuse
  the frozen P3 settings, source-question queries, revisions and tie breaks.
  No new chunking, query expansion, reranking or dense-window fix is included.
- Both H request groups are individually persisted. All rules, H predictions,
  V1 decisions, safety projections and provenance are persisted before gold
  can open. Arbitration never receives gold.
- A malformed/failed request follows the original measured-abstention policy.
  An infrastructure or integrity failure after consumption is terminal. There
  is no batch retry, reset, resumption or replacement population.
- Metrics and paired patient-bootstrap intervals are computed only in the
  original batch. A final manifest pins the complete output/event chain.
  Later presentation reads the sealed public projection, without reopening
  gold or recalculating metrics.

## Exposure entry conditions

The final plan is not prepared until the complete public test suite, public
data guard, synthetic full-batch tests, live synthetic replay, installed-source
comparison and hosted CI on the exact execution commit pass. All preparation
artifacts stay owner-only. The former P7 test remains permanently closed.

Local file-provider reads stalled in the original workspace and virtual
environment. A clean public checkout and isolated environment outside that
provider are used for execution; exact source-byte comparison and commit pins
remain mandatory. The original restricted inputs and lifetime vault do not
move. No holdout has been read during this implementation review.
