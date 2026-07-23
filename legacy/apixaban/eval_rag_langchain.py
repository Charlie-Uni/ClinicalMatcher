import os
import re
import pandas as pd

from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

EVAL_CSV = "data/eval_cases.csv"
CHROMA_DIR = "chroma_apixaban"
EMBED_MODEL = "llama3.1"
TOP_K = 5

SYSTEM_PROMPT = """You are an AI assistant for clinical trial eligibility matching.

Task:
- Decide whether the CURRENT PATIENT is an IDEAL, SEMI-IDEAL, or NON-IDEAL candidate for an Apixaban trial.
- Apply the official decomposed criteria. Information-bottleneck analysis shows that comorbidity/bleeding-risk chunks and labs carry most signal, so ALWAYS read the retrieved snippets in this priority order:
  1) hi_risk_core + comorb_only (bleeding risks, psychiatric history, decision capacity)
  2) labs_only (creatinine, HGB, PLT, bilirubin, AST)
  3) full_text or random_drop (general narrative fallback)
- Use retrieved examples only for calibration; the final judgement must rely on the CURRENT PATIENT’S facts.

Clinical criteria (rule table):
1. Non-valvular AFib & no bleeding risk:
   - IMPORTANT: Having atrial fibrillation (AFib) is the ENTRY CONDITION for this trial, not a disqualifier.
   - “Not attributable to a reversible cause” means the AFib is NOT caused by a temporary curable condition such as hyperthyroidism, acute post-surgical state, or electrolyte imbalance. Chronic persistent or paroxysmal AFib managed with rate-control medications IS eligible.
   - RVR (rapid ventricular response) means the ventricular rate is fast during AFib — this is a rate-control issue only. It does NOT make AFib reversible. A patient with “AFib with RVR on diltiazem/metoprolol” is still a valid AFib candidate for this trial.
   - Being already on apixaban for AFib is STRONG positive evidence — mark this domain TRUE unless a specific exclusion applies.
   - Exclude ONLY: documented active/major bleeding, hemorrhagic tendency, blood dyscrasia, peptic ulcer disease within 6 months, recent hemorrhagic stroke, prior AFib ablation, or valvular stenosis requiring surgery.
   - Default to TRUE if none of the above exclusions are present.

2. Controlled stroke risk & good heart function:
   - LVEF >= 30% PASSES. Examples: LVEF 31%, 35%, 39%, 45%, 60% all PASS. Only LVEF < 30% (e.g., 25%, 20%) is a concern.
   - CHADS2 <= 2 is preferred; CHADS2 3-4 is elevated but does NOT automatically exclude.
   - Exclusion: stroke/TIA during this admission or within the past month, or prior stroke/TIA history.
   - Missing CHADS2/LVEF: infer from stable vitals and absence of stroke history → default TRUE.

3. Lab safety:
   - Normal thresholds: Creatinine <= 2.5, HGB >= 10, PLT >= 100, Bilirubin <= 1.8, AST <= 80.
   - Severe red flags (FORCE non-ideal immediately): Creatinine > 3.0, HGB < 9.0, PLT < 50, Bilirubin >= 3.0, AST >= 200.
   - IMPORTANT threshold examples for PLT: PLT=57 is mildly abnormal (50 < 57, NOT auto-fail). PLT=45 IS auto-fail (45 < 50). Read the number carefully before comparing.
   - Mildly abnormal values (e.g. creatinine 1.5-2.5, PLT 50-99, AST 80-150, bilirubin 1.8-2.9) do NOT auto-fail — mark TRUE unless a severe red flag is present.
   - If a severe red flag IS present, mark criterion_3_labs: false AND the final label MUST be non-ideal regardless of other criteria.
   - If labs are missing or all normal, mark TRUE.

4. Mental status & decision capacity:
   - Mark FALSE only when the note explicitly states: severe bipolar disorder, schizophrenia, major depression with impaired decision-making, or documented loss of decision capacity.
   - Absence of psychiatric mention → TRUE.

5. Metabolic / comorbidity control:
   - Fail ONLY when comorbidities are clearly uncontrolled: brittle diabetes (glucose >> 180), or decompensated CHF making anticoagulation unsafe.
   - Stable heart failure managed with medications → TRUE.
   - Hypertension or diabetes that is medically managed → TRUE.

Mandatory reasoning contract:
1. For each criterion, cite the specific evidence and state true or false.
2. Final label logic:
   - ideal: all five booleans True.
   - semi-ideal: exactly one or two False criteria AND no severe red flags (no auto-fail lab, no active bleeding).
   - non-ideal: three or more False criteria, OR any severe lab red flag (Creatinine > 3.0 / HGB < 9.0 / PLT < 50 / Bilirubin >= 3.0 / AST >= 200), OR documented active bleeding/hemorrhage.
   - CRITICAL: If ANY severe lab red flag is present, the final label is ALWAYS non-ideal — even if only criterion_3_labs is False.
   - When unsure between ideal and semi-ideal, choose semi-ideal.
   - When unsure between semi-ideal and non-ideal, choose semi-ideal unless a severe red flag is present.

Output format (STRICT — follow this exactly):
1. Brief explanation covering each criterion.
2. Domain summary block — each value MUST be exactly `true` or `false`, nothing else:
   criterion_1_afib_safe: true
   criterion_2_stroke_heart: false
   criterion_3_labs: true
   criterion_4_mental_health: true
   criterion_5_metabolic_comorbidities: true
3. Final label on its own line — exactly one of these three:
   Final label: ideal
   Final label: semi-ideal
   Final label: non-ideal
"""

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            "Question:\n{question}\n\nRelevant documents (from EHR):\n{retrieved_docs}\n",
        ),
    ]
)

llm = ChatOllama(model="llama3.1", temperature=0)
parser = StrOutputParser()
chain = prompt | llm | parser


def extract_final_label(answer: str) -> str:
    clean = re.sub(r"\*+", "", answer).lower().strip()
    # Pass 1: label immediately after colon
    m = re.search(r"final label\s*:\s*(non-ideal|semi-ideal|ideal)", clean)
    if m:
        return m.group(1).strip()
    # Pass 2: label embedded in sentence after "final label:" (e.g. "The patient is a NON-IDEAL candidate")
    m = re.search(r"final label\s*:.{0,120}?(non-ideal|semi-ideal|ideal)", clean, re.DOTALL)
    if m:
        return m.group(1).strip()
    print("Warning: final label missing; will use criterion fallback.")
    return None


def extract_criteria_flags(answer: str):
    text = re.sub(r"\*+", "", answer).lower()

    # positive signals → True (checked first)
    TRUE_PATTERNS  = r"true|satisfied|yes|pass|met|inferred.{0,20}true|true.{0,20}inferred|false.{0,40}inferred.{0,20}true|false.{0,40}absence"
    # negative signals → False (only if no positive signal)
    FALSE_PATTERNS = r"\bfalse\b|not.{0,10}satisfied|fail|not.{0,10}met|problematic|inferred.{0,20}false"

    def grab(key: str):
        # find the line that starts with this criterion key
        m = re.search(rf"{re.escape(key)}\s*:\s*(.{{0,120}})", text)
        if not m:
            return None
        value = m.group(1).split("\n")[0].strip()
        # Check TRUE first — prevents "false (inferred from absence → TRUE)" being parsed as False
        if re.search(TRUE_PATTERNS, value):
            return True
        if re.search(FALSE_PATTERNS, value):
            return False
        return None

    flags = {
        "crit1": grab("criterion_1_afib_safe"),
        "crit2": grab("criterion_2_stroke_heart"),
        "crit3": grab("criterion_3_labs"),
        "crit4": grab("criterion_4_mental_health"),
        "crit5": grab("criterion_5_metabolic_comorbidities"),
    }
    if all(v is None for v in flags.values()):
        print("Warning: domain summary missing; cannot parse booleans.")
    return flags


def derive_label_from_criteria(bools):
    if all(bools):
        return "ideal"
    false_count = sum(1 for b in bools if not b)
    if false_count >= 3:
        return "non-ideal"
    return "semi-ideal"


def build_retriever():
    if not os.path.exists(CHROMA_DIR):
        raise FileNotFoundError(
            f"Chroma directory '{CHROMA_DIR}' not found. Please run build_rag_index.py first."
        )

    embeddings = OllamaEmbeddings(model=EMBED_MODEL)
    vectordb = Chroma(
        persist_directory=CHROMA_DIR,
        embedding_function=embeddings,
        collection_name="apixaban_rag",
    )

    retriever = vectordb.as_retriever(
        search_type="similarity",
        search_kwargs={"k": TOP_K},
    )
    return retriever


def format_retrieved_docs(docs) -> str:
    buckets = {
        "comorb": [],
        "labs": [],
        "general": [],
    }

    for doc in docs:
        chunk_type = (doc.metadata or {}).get("chunk_type", "general")
        if chunk_type in {"comorb_only", "hi_risk_core"}:
            buckets["comorb"].append(doc)
        elif chunk_type == "labs_only":
            buckets["labs"].append(doc)
        else:
            buckets["general"].append(doc)

    sections = []
    ordered_sections = [
        ("[High-priority] Comorbidity / contraindication snippets (read first)", buckets["comorb"]),
        ("[High-priority] Laboratory / objective measurements (read second)", buckets["labs"]),
        ("[Fallback] General narrative/context snippets (read last)", buckets["general"]),
    ]

    doc_counter = 1
    for header, bucket_docs in ordered_sections:
        if not bucket_docs:
            continue
        sections.append(header)
        for doc in bucket_docs:
            meta = doc.metadata or {}
            meta_str = ", ".join(
                f"{k}={v}" for k, v in meta.items() if k not in ("source",) and v not in (None, "")
            )
            sections.append(f"[Doc {doc_counter}] chunk_type={meta.get('chunk_type', 'general')}")
            if meta_str:
                sections.append(f"  Meta: {meta_str}")
            sections.append(doc.page_content.strip())
            sections.append("-" * 40)
            doc_counter += 1

    return "\n".join(sections)


def main():
    if not os.path.exists(EVAL_CSV):
        raise FileNotFoundError(f"{EVAL_CSV} missing.")

    eval_df = pd.read_csv(EVAL_CSV)
    required_cols = {"case_id", "question", "GT_label"}
    if not required_cols.issubset(eval_df.columns):
        raise ValueError(f"{EVAL_CSV} must contain columns: {required_cols}")

    retriever = build_retriever()

    results = []
    correct = 0

    for _, row in eval_df.iterrows():
        case_id = row["case_id"]
        question = str(row["question"])
        gt_label = str(row["GT_label"]).strip().lower()

        print("\n" + "=" * 80)
        print(f"Case {case_id}:")
        print(f"GT_label = {gt_label}")
        print("-" * 80)

        docs = retriever.get_relevant_documents(question)
        retrieved_docs = format_retrieved_docs(docs)

        answer = chain.invoke(
            {
                "question": question,
                "retrieved_docs": retrieved_docs,
            }
        )
        print("LLM Answer:\n", answer)

        pred_label = extract_final_label(answer)
        crit_flags = extract_criteria_flags(answer)
        crit_booleans = [
            crit_flags.get("crit1") if crit_flags.get("crit1") is not None else False,
            crit_flags.get("crit2") if crit_flags.get("crit2") is not None else False,
            crit_flags.get("crit3") if crit_flags.get("crit3") is not None else False,
            crit_flags.get("crit4") if crit_flags.get("crit4") is not None else False,
            crit_flags.get("crit5") if crit_flags.get("crit5") is not None else False,
        ]
        fallback_label = derive_label_from_criteria(crit_booleans)
        pred_label = pred_label or fallback_label
        is_correct = pred_label == gt_label
        print(f"Predicted label = {pred_label} | Correct? {is_correct}")

        if is_correct:
            correct += 1

        results.append(
            {
                "case_id": case_id,
                "question": question,
                "GT_label": gt_label,
                "LLM_answer": answer,
                "pred_label": pred_label,
                "correct": is_correct,
            }
        )

    total = len(results)
    acc = correct / total if total else 0.0
    print("\n" + "=" * 80)
    print(f"[LANGCHAIN RAG] Total cases: {total}, Correct: {correct}, Accuracy: {acc:.3f}")
    print("=" * 80)

    out_path = "langchain_eval_results.csv"
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
