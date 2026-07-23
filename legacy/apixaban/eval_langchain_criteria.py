import os
import re
import pandas as pd

from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableParallel, RunnablePassthrough

DATA_CSV = "data/apixaban_processed.csv"
EVAL_CSV = "data/eval_cases.csv"

SYSTEM_PROMPT = """You are an AI assistant for clinical trial eligibility matching.

Task:
- Decide whether the CURRENT PATIENT is an IDEAL, SEMI-IDEAL, or NON-IDEAL candidate for an Apixaban trial.
- Use the structured data in the question as the primary evidence. Retrieved examples are only calibration hints; never copy their labels.
- Based on our information-bottleneck analysis, focus first on comorbidity/bleeding-risk facts (AFib status, psychiatric history, contraindications), then laboratories, then remaining narrative.

Clinical criteria (exact protocol definitions):
1. Non-valvular AFib & no bleeding risk (criterion_1):
   - Require AFib not attributable to a reversible cause AND no AFib ablation or valvular stenosis requiring surgery.
   - Exclude active/major bleeding, blood dyscrasias, peptic ulcer disease within 6 months, or serious hemorrhagic events.
   - If the question states the patient is already receiving apixaban without bleeding issues, you may infer this domain as satisfied.

2. Controlled stroke risk & good heart function (criterion_2):
   - CHADS2 <= 2 and LVEF >= 30% when those values exist.
   - No stroke/TIA during this admission or within the past month, no prior stroke/TIA history, no symptomatic heart failure.
   - Missing CHADS2 or LVEF is not an excuse to skip the boolean. Use the narrative to infer the safest choice and mention that it was inferred.

3. Lab safety (criterion_3):
   - All thresholds must be met unless a severe red flag is explicitly present: Creatinine <= 2.5 mg/dL, Hemoglobin >= 10 g/dL, Platelets >= 100 ×10^3/µL, Total bilirubin <= 1.8 mg/dL (~1.5×ULN), AST <= 80 U/L.
   - Severe red flags: creatinine > 3.0, HGB < 9.0, PLT < 50 ×10^3/µL, bilirubin >= 3.0, AST >= 200 → automatically False.
   - If all reported labs are normal, mark this domain True even if some values are missing.

4. Mental health & decision capacity (criterion_4):
   - Only set False when the note clearly states severe bipolar disorder, schizophrenia/schizoaffective disorder, major depression that prevents medical decision-making, OR explicit loss of capacity (e.g., med_decisions=1).
   - When none of those conditions are mentioned, you must output True. Do not skip this line or return “unknown”.

5. Metabolic / comorbidity control (criterion_5):
   - Either no diabetes and no hypertension, OR (if diabetes is present) glucose <= 180 mg/dL with reasonably controlled hypertension and no uncontrolled comorbidities making anticoagulation unsafe.

Reasoning workflow (STRICT):
1. Summarise the patient’s AFib status, CHADS2/LVEF, labs, mental health clues, and comorbidities.
2. For each criterion, describe whether it is satisfied / limited / problematic / inferred and cite the evidence.
3. Convert each criterion into a boolean (`criterion_1_pass` … `criterion_5_pass`). Missing data must be handled via best-effort inference; skipping a line is forbidden.
4. Final label logic:
   - ideal: all booleans True.
   - semi-ideal: mostly True with at most one limitation and no severe red flag.
   - non-ideal: clear safety problems (any severe lab abnormality, active bleeding, uncontrolled comorbidity, or multiple False domains). When uncertain, choose the safer label and explain why.

Answer format (responses missing any element are INVALID):
1. Step-by-step explanation (~5 bullets) referencing each criterion and the supporting evidence.
2. Domain summary block with EXACTLY these five lines (lowercase). Each value must be `true` or `false` (never “unknown”):
   criterion_1_afib_safe: true/false
   criterion_2_stroke_heart: true/false
   criterion_3_labs: true/false
   criterion_4_mental_health: true/false
   criterion_5_metabolic_comorbidities: true/false
3. Final line on its own: `Final label: ideal` OR `Final label: semi-ideal` OR `Final label: non-ideal`.
- Any extra text after the final label counts as an incorrect answer.
"""


def build_vectorstore():
    if not os.path.exists(DATA_CSV):
        raise FileNotFoundError(f"{DATA_CSV} not found.")

    df = pd.read_csv(DATA_CSV)

    if "text" not in df.columns:
        raise ValueError("`text` column missing from apixaban_processed.csv.")

    texts = df["text"].fillna("").astype(str).tolist()

    metadatas = []
    for _, row in df.iterrows():
        metadatas.append(
            {
                "note_id": row.get("note_id", ""),
                "hadm_id": row.get("hadm_id", ""),
                "crit1_pass": row.get("crit1_pass", ""),
                "crit2_pass": row.get("crit2_pass", ""),
                "crit3_pass": row.get("crit3_pass", ""),
                "crit4_pass": row.get("crit4_pass", ""),
                "crit5_pass": row.get("crit5_pass", ""),
                "semi_ideal_candidate": row.get("semi_ideal_candidate", ""),
                "ideal_candidate": row.get("ideal_candidate", ""),
            }
        )

    if os.path.exists("faiss_pid.index"):
        print("Loading cached FAISS index...")
        embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")
        return FAISS.load_local(
            "faiss_pid.index",
            embeddings,
            allow_dangerous_deserialization=True,
        )

    embeddings = HuggingFaceEmbeddings(model_name="intfloat/multilingual-e5-small")

    print("Building FAISS vector store with multilingual-e5-small embeddings...")
    vectorstore = FAISS.from_texts(
        texts=texts,
        embedding=embeddings,
        metadatas=metadatas,
    )
    vectorstore.save_local("faiss_pid.index")

    return vectorstore


def get_llm():
    return ChatOllama(model="llama3.1", temperature=0.0)


prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "human",
            "Question:\n{question}\n\nRelevant documents (from EHR):\n{context}",
        ),
    ]
)

parser = StrOutputParser()


def build_rag_chain(vectorstore):
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

    def format_docs(docs):
        parts = []
        for i, d in enumerate(docs, start=1):
            md = d.metadata or {}
            header = (
                f"[Doc {i}] note_id={md.get('note_id','')}, hadm_id={md.get('hadm_id','')}, "
                f"crit1={md.get('crit1_pass','')}, crit2={md.get('crit2_pass','')}, "
                f"crit3={md.get('crit3_pass','')}, crit4={md.get('crit4_pass','')}, "
                f"crit5={md.get('crit5_pass','')}, "
                f"semi_ideal={md.get('semi_ideal_candidate','')}, ideal={md.get('ideal_candidate','')}"
            )
            parts.append(header)
            parts.append(d.page_content[:1200])
            parts.append("\n---\n")
        return "\n".join(parts)

    llm = get_llm()

    rag_chain = (
        RunnableParallel(
            {
                "context": retriever | format_docs,
                "question": RunnablePassthrough(),
            }
        )
        | prompt
        | llm
        | parser
    )

    return rag_chain


def extract_final_label(answer: str) -> str:
    text = answer.lower().replace("**", "")
    matches = list(re.finditer(r"final label\s*:\s*(ideal|semi-ideal|non-ideal)", text))
    if not matches:
        print("Warning: final label missing from answer.")
        return None
    return matches[-1].group(1).strip()


def extract_criteria_flags(answer: str):
    text = answer.lower()

    def grab(key: str):
        m = re.search(rf"{key}\s*:\s*(true|false)", text)
        if not m:
            return None
        return True if m.group(1) == "true" else False

    flags = {
        "crit1": grab("criterion_1_afib_safe"),
        "crit2": grab("criterion_2_stroke_heart"),
        "crit3": grab("criterion_3_labs"),
        "crit4": grab("criterion_4_mental_health"),
        "crit5": grab("criterion_5_metabolic_comorbidities"),
    }
    if all(v is None for v in flags.values()):
        print("Warning: domain summary block missing or unparsable in answer.")
    return flags


def to_bool_or_none(val):
    if pd.isna(val):
        return None
    if isinstance(val, (int, float)):
        return bool(int(val))
    s = str(val).strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    return None


def combine_domain_flags(flags):
    normalized = [bool(flag) for flag in flags]
    if all(normalized):
        return "ideal"
    if not normalized[0] or not normalized[2]:
        return "non-ideal"
    return "semi-ideal"


def main():
    if not os.path.exists(EVAL_CSV):
        raise FileNotFoundError(f"{EVAL_CSV} not found.")

    vectorstore = build_vectorstore()
    rag_chain = build_rag_chain(vectorstore)

    eval_df = pd.read_csv(EVAL_CSV)
    required_cols = {
        "case_id",
        "question",
        "GT_label",
        "crit1_pass",
        "crit2_pass",
        "crit3_pass",
        "crit4_pass",
        "crit5_pass",
    }
    if not required_cols.issubset(eval_df.columns):
        raise ValueError(f"{EVAL_CSV} must contain columns: {required_cols}")

    results = []

    total_cases = 0
    correct_label = 0
    combined_correct = 0

    crit_totals = {i: 0 for i in range(1, 6)}
    crit_correct = {i: 0 for i in range(1, 6)}

    for _, row in eval_df.iterrows():
        case_id = row["case_id"]
        question = str(row["question"])
        gt_label = str(row["GT_label"]).strip().lower()

        gt_c1 = to_bool_or_none(row["crit1_pass"])
        gt_c2 = to_bool_or_none(row["crit2_pass"])
        gt_c3 = to_bool_or_none(row["crit3_pass"])
        gt_c4 = to_bool_or_none(row["crit4_pass"])
        gt_c5 = to_bool_or_none(row["crit5_pass"])

        print("\n" + "=" * 80)
        print(f"Case {case_id}:")
        print(f"GT_label = {gt_label}")
        print(f"GT crits: {gt_c1}, {gt_c2}, {gt_c3}, {gt_c4}, {gt_c5}")
        print("-" * 80)

        answer = rag_chain.invoke(question)
        print("LLM Answer:\n", answer)

        pred_label = extract_final_label(answer)
        crit_flags = extract_criteria_flags(answer)

        pred_c1 = crit_flags["crit1"]
        pred_c2 = crit_flags["crit2"]
        pred_c3 = crit_flags["crit3"]
        pred_c4 = crit_flags["crit4"]
        pred_c5 = crit_flags["crit5"]

        combined_pred = combine_domain_flags(
            [
                pred_c1 if pred_c1 is not None else False,
                pred_c2 if pred_c2 is not None else False,
                pred_c3 if pred_c3 is not None else False,
                pred_c4 if pred_c4 is not None else False,
                pred_c5 if pred_c5 is not None else False,
            ]
        )
        combined_is_correct = combined_pred == gt_label
        if combined_is_correct:
            combined_correct += 1

        if pred_label is None or pred_label == "unknown":
            print("Info: final label missing; using combined verdict.")
            pred_label = combined_pred

        is_label_correct = pred_label == gt_label
        print(f"Predicted label = {pred_label} | Correct? {is_label_correct}")

        total_cases += 1
        if is_label_correct:
            correct_label += 1

        crit_preds = [pred_c1, pred_c2, pred_c3, pred_c4, pred_c5]
        crit_gts = [gt_c1, gt_c2, gt_c3, gt_c4, gt_c5]

        crit_correct_flags = []
        for i in range(5):
            gt_val = crit_gts[i]
            pred_val = crit_preds[i]
            if gt_val is not None and pred_val is not None:
                crit_totals[i + 1] += 1
                if gt_val == pred_val:
                    crit_correct[i + 1] += 1
                    crit_correct_flags.append(True)
                else:
                    crit_correct_flags.append(False)
            else:
                crit_correct_flags.append(None)

        print(
            f"Pred crits: {pred_c1}, {pred_c2}, {pred_c3}, {pred_c4}, {pred_c5}"
        )

        results.append(
            {
                "case_id": case_id,
                "question": question,
                "GT_label": gt_label,
                "gt_crit1_pass": gt_c1,
                "gt_crit2_pass": gt_c2,
                "gt_crit3_pass": gt_c3,
                "gt_crit4_pass": gt_c4,
                "gt_crit5_pass": gt_c5,
                "LLM_answer": answer,
                "pred_label": pred_label,
                "pred_crit1_pass": pred_c1,
                "pred_crit2_pass": pred_c2,
                "pred_crit3_pass": pred_c3,
                "pred_crit4_pass": pred_c4,
                "pred_crit5_pass": pred_c5,
                "label_correct": is_label_correct,
                "crit1_correct": crit_correct_flags[0],
                "crit2_correct": crit_correct_flags[1],
                "crit3_correct": crit_correct_flags[2],
                "crit4_correct": crit_correct_flags[3],
                "crit5_correct": crit_correct_flags[4],
                "pred_label_combined": combined_pred,
                "combined_correct": combined_is_correct,
            }
        )

    label_acc = correct_label / total_cases if total_cases else 0.0
    combined_acc = combined_correct / total_cases if total_cases else 0.0

    print("\n" + "=" * 80)
    print(f"[LANGCHAIN RAG] Total cases: {total_cases}, Label correct: {correct_label}, Label Accuracy: {label_acc:.3f}")
    print(f"[LANGCHAIN RAG] Combined label accuracy (trial-rule): {combined_acc:.3f}")

    for i in range(1, 6):
        if crit_totals[i] > 0:
            acc_i = crit_correct[i] / crit_totals[i]
        else:
            acc_i = 0.0
        print(
            f"Criterion {i} accuracy: {crit_correct[i]}/{crit_totals[i]} = {acc_i:.3f}"
        )
    print("=" * 80)

    out_path = "langchain_criteria_eval_results.csv"
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
