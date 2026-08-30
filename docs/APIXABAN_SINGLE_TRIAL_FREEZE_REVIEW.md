# Apixaban single-trial freeze review

Status: intended mentor-rule scoring contract `1.0.0` implemented before
validation; owner approval of the exact contract and unit adapter remains
required before any validation run

Review date: 2026-08-30

## Scope boundary

This review precedes P4.7 implementation. It compares the available mentor-side
five-rule descriptions with the legacy implementation and the frozen official
fact catalog. It does not inspect validation or locked-test labels, calculate
agreement, or establish a clinical trial protocol.

The official Apixaban release provides 23 human-reviewed fact targets. It does
not define the five patient-screening rules. Any P4.7 tree must therefore name
its mentor-side source and remain a legacy-rule diagnostic unless a separately
versioned clinical protocol is supplied and reviewed.

## Reviewed inputs

| Input | SHA-256 | Role |
|---|---|---|
| Frozen public fact catalog `1.0.0` | file `b23480b02df923d169c9d24487fb72c6c386e252d5c7c65cd11587a71ccbbf98`; canonical catalog content `c51e07b98c6c380545685ae0585644fcb8eb5a5b5a2e2fee936f2e0dca15bc8f` | Defines the 23 fact fields and their extraction semantics; explicitly leaves eligibility mapping to a separate step. |
| Mentor criteria document | `be872d1f2e6baa4883b3dbcdc53895a8ff3d0362e27a13193ecee1181e9a14ec` | Contains the decomposed questions and several alternative natural-language, symbolic, and code-like formulations of the five rules. |
| Mentor `criteria.json` | `bd4e4ae54a2b59e5202c2eded4c1dd4e25c86d74361a331c188435230e6cec6e` | Contains one prose formulation for each rule section. |
| Mentor `screening_results.json` | `f358d18feb47997d87d27b104b0c3490d08bba913e64b33b17b75ab2c65c59d3` | Defines the mentor-designated project reference result. It is rule-derived and is not independent patient-level clinical gold. |
| Official release README | `88b77606cbcead4a263d3eb1d3e58ca16ed9098b082b498672f39ebd3ffdab30` | Preserves the full annotation intent behind the shortened released question strings. |
| Legacy `apixaban_processing.py` | `23028ae0fd2d7ae3998ae06d47746ff8164735e5f5c8eff011633819b1d20576` | Contains one executable five-rule candidate. The repository copy is byte-identical to the mentor-folder copy. It is not the missing generator named by the screening README. |

The mentor criteria document contains no patient rows or note text. Its hash is
recorded for provenance; the document itself remains outside the repository.

## Coverage finding

The five rule groups collectively reference all 23 official fact fields:

- rule 1: atrial fibrillation, ablation, valvular surgery, bleeding, peptic
  ulcer disease, and hemorrhagic tendency;
- rule 2: CHADS2, LVEF, recent stroke, prior stroke/TIA, and heart failure;
- rule 3: creatinine, hemoglobin, platelets, bilirubin, and AST;
- rule 4: depression, schizophrenia, bipolar disorder, and inability to make
  medical decisions;
- rule 5: diabetes, treated arterial hypertension, and blood glucose.

Complete field coverage does not resolve the rule semantics below.

## Owner source and scoring resolution

On 2026-08-30 the owner supplied and selected the following hierarchy before
any validation agreement was inspected:

1. `criteria.json` defines the intended five criteria identities;
2. the explicit arrow scoring formulas in the mentor DOCX define executable
   rule logic when the same document contains conflicting prose or code-like
   examples;
3. the official release README and mentor DOCX define the full annotation
   intent, while the released CSV/catalog provide stable fact identifiers;
4. `screening_results.json` is the mentor-designated project reference result;
   and
5. the missing named generator cannot be supplied, so the reference remains
   explicitly rule-derived and non-independent.

Formula omissions use the already frozen Kleene three-valued policy: missing or
incompatible required facts become `UNKNOWN` and never pass automatically. This
resolution identifies the intended project diagnostic. It is not a qualified
clinical review and does not establish that the five rules reproduce a trial
protocol or clinically correct eligibility.

The frozen executable resolutions are:

- Rule 1 uses `AFib AND (NOT ablation OR NOT valvular surgery)` and requires all
  three scored bleeding-risk facts to be absent.
- Rule 2 uses `CHADS2 <= 3`, `LVEF >= 50`, and requires recent stroke, prior
  stroke/TIA, and heart failure all to be absent.
- Rule 3 requires all five thresholds: platelet `>=100`, bilirubin `<=1.8`, AST
  `<=80`, creatinine `<=2.5`, and hemoglobin `>=10`, with the units stated by
  the DOCX.
- Rule 4 requires all three mental-health exclusions and inability to make
  medical decisions to be false. This intentionally corrects the opposite
  polarity in the legacy code according to the selected scoring formula.
- Rule 5 is `(no diabetes AND no treated hypertension) OR (diabetes AND glucose
  <=180)`.
- Rules 1--5 are hard. Rules 1--4 plus Rule 5 project to `ideal`; Rules 1--4
  plus an explicit Rule 5 failure project to `semi-ideal`; an explicit failure
  in Rules 1--4 projects to `non-ideal`; unresolved required distinctions
  project to `unknown`.

## Historical semantic conflicts resolved for this diagnostic

| Area | Conflicting available formulations | Why owner review is required |
|---|---|---|
| Rule 1 connective | One symbolic line permits either no ablation or no valvular surgery; the prose and legacy code require both to be absent. | These trees accept different patients. |
| Rule 2 thresholds | The mentor document contains both `CHADS2 <= 3` with `LVEF >= 50` and `CHADS2 <= 2` with `LVEF >= 30`; the legacy code uses the latter pair. | Threshold choice cannot be inferred from the official fact questions. |
| Rule 2 missing values | The legacy code treats missing CHADS2 or LVEF as passing; a three-valued verifier would normally return unknown. | Passing and abstaining are different safety semantics. |
| Rule 2 exclusion connective | The exclusion prose and legacy code require recent stroke, prior stroke/TIA, and heart failure all to be absent; another code-like line permits a patient when either the stroke pair is absent or heart failure is absent. | The latter is materially less restrictive. |
| Rule 3 connective | The heading, detailed symbolic line, and legacy code require all five laboratory thresholds; another prose/code-like formulation mixes an initial pair with alternatives. | `ALL` and `ANY` cannot be reconciled by normalization. |
| Rule 3 bilirubin threshold | One formulation uses `1.5 * ULN` with an explicit ULN of `1.2`; the legacy code compares the raw value to `1.5`. | These imply different numeric cutoffs. |
| Rule 3 units | The rule text names clinical units, but the official question contract does not define canonical units and the legacy code compares raw numbers. | A typed clinical comparison cannot honestly assert unit compatibility. |
| Rule 4 polarity | The official fact asks whether the patient is **unable** to make medical decisions, so `true` is adverse. The legacy code excludes when the cleaned value equals `0`, which corresponds to the opposite polarity. | This is a likely implementation defect, but changing it would cease to reproduce that code. |
| General unknown handling | The five legacy expressions do not apply one consistent missing-value policy across boolean and numeric fields. | Unknown propagation must be frozen before any agreement is observed. |
| Fact-target adequacy | The mentor rule and official catalog differ in whether AFib reversibility, the breadth of hypertension history, and discharge-time decision capacity are represented. | Similar field names do not establish that the released fact is sufficient for the intended atom. |

The recent-stroke and recent-bleeding windows are already part of the official
fact-question targets. P4.7 must not add a second date filter when no reliable
index-date trace exists in the released labels.

## Owner route selection

On 2026-08-30, the data owner selected option 2, the intended mentor-rule
diagnostic, then supplied the source hierarchy above. That hierarchy authorizes
the pre-validation scoring-contract implementation, not a claim of clinical
correctness and not a validation run. The original itemized questions remain in
`docs/APIXABAN_CRITERIA_REVIEW_CHECKLIST_ZH.md` as an audit template for any
future qualified clinical review.

The considered routes were:

1. **Legacy-code reproduction diagnostic.**
   Freeze the byte-identical legacy Python semantics, including documented
   oddities, and label every result as agreement with an executable legacy
   reference. This does not claim that the rules are clinically correct or
   that they generated the supplied three-class flags. Its position-dependent
   missing-value defaults and reversed decision-capacity polarity cannot be
   represented faithfully by the current three-valued expression tree without
   a dedicated compatibility layer. That layer would be a separately approved
   scope change, not the default P4.7 implementation.
2. **Intended mentor-rule diagnostic.** Resolve every conflict above with an
   explicit decision from a qualified content reviewer, issue a new rule
   version, and treat comparison with the old flags as descriptive only. This
   is the recommended P4.7 route when that reviewer is available; the assistant
   must not select the resolutions.
3. **Pause for provenance clarification.** Obtain the missing
   `notebooks/preprocess.ipynb`, `data_processing/screener.py`, or a mentor
   clarification identifying the authoritative formulation, then repeat this
   freeze review.

No option upgrades the three-class labels to independent human gold. The
current contract records the mentor-intended project diagnostic without a
qualified clinical review. Any future clinical-validity claim still requires
qualified review or an independently versioned protocol; project-reference
agreement cannot supply that evidence.

## Implemented contract and remaining gate

`src/clinical_matcher/resources/apixaban-intended-rule-contract-1.0.0.json`
freezes the source hashes, exact 23-field mapping, 24 atom occurrences, five
expression topologies, thresholds, polarity, hard-rule semantics, unknown
policy, and class projection. Its canonical self-hash is
`eca82d50f45727830b8a8443bfddf7e16e30cbd801b3403bee7f6ca2f970bde1`.
`src/clinical_matcher/apixaban_single_trial.py` validates those invariants and
constructs the typed expression tree. Synthetic tests cover all thresholds,
the two disjunctions, unit mismatch, missing facts, polarity, tamper rejection,
and both class projections.

The official numeric fact labels store no units. Contract units are therefore
documented DOCX-based mapping assumptions, not observed label metadata. No real
validation may run until the owner reviews this exact contract and separately
approves a versioned adapter that assigns or verifies those units without
claiming clinical unit safety. Validation remains a single locked run on the
validation split; the locked test remains untouched.
