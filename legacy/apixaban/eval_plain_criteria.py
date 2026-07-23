import os
import re
import pandas as pd

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

EVAL_CSV = "data/eval_cases.csv"

SYSTEM_PROMPT = """You are an AI assistant for clinical trial eligibility matching.

Task:
- Your goal is to decide whether the CURRENT PATIENT is an IDEAL, SEMI-IDEAL, or NON-IDEAL candidate
  for an Apixaban clinical trial.
- You must base your judgement PRIMARILY on:
  (1) the clinical features of the CURRENT PATIENT described in the question
      (e.g. CHADS2, LVEF, creatinine, hemoglobin, platelets, bilirubin, AST, comorbidities),
  (2) the high-level trial criteria listed below.
- You may use any retrieved EHR examples ONLY as secondary reference (for calibration),
  NOT as a vote or majority label.

Clinical criteria (5 domains; rule-based intuition, but NOT rigid exclusion rules):

1. AFib Safe (criterion 1):
   - Target: Non-valvular atrial fibrillation AND no clear evidence of major bleeding risk.
   - Major bleeding risk examples:
     * recent major stroke or hemorrhagic stroke
     * active major bleeding
     * uncontrolled peptic ulcer disease, etc.

2. Stroke Risk / Heart Function (criterion 2):
   - CHADS2 score <= 2 → low stroke risk.
   - CHADS2 >= 3 → higher stroke risk but this ALONE does NOT automatically exclude the patient.
   - LVEF >= 30% is generally acceptable.
   - LVEF < 30% = limited heart function and usually a negative factor.
   - If CHADS2 or LVEF are missing, treat them as UNKNOWN (do NOT automatically fail this domain).

3. Lab Safety (criterion 3, numeric thresholds):

   Major red-flag abnormalities (strongly suggest NON-IDEAL):
   - Creatinine > 3.0 mg/dL
   - Hemoglobin < 9.0 g/dL
   - Platelets < 50 x10^9/L
   - Total bilirubin >= 3.0 mg/dL
   - AST >= 200 U/L

   Moderate abnormalities (borderline; caution):
   - Creatinine between 2.0–3.0 mg/dL
   - Hemoglobin between 9.0–10.0 g/dL
   - Platelets between 50–100 x10^9/L
   - Total bilirubin between 1.5–3.0 mg/dL
   - AST between 80–200 U/L

   Normal / mildly abnormal (generally safe):
   - Creatinine <= 2.5 mg/dL
   - Hemoglobin >= 10 g/dL
   - Platelets >= 100 x10^9/L
   - Total bilirubin <= 1.5 mg/dL
   - AST <= 80 U/L

   For the FINAL True/False decision of this domain:
   - If ALL available lab values are within the “normal / mildly abnormal” thresholds
     AND no important lab is clearly missing → criterion_3 = True.
   - If ANY lab is in the major red-flag range OR clearly very abnormal,
     OR key labs are missing in a way that makes safety unclear → criterion_3 = False.
   - If values are in the “moderate abnormal” range but not in the red-flag range,
     you may treat the domain as “limited” in the explanation, but the final boolean should be:
       * True if overall still reasonably safe,
       * False if, in your clinical judgement, anticoagulation looks unsafe or very high risk.

4. Mental Health (criterion 4):
   - Negative factors: severe bipolar disorder, schizophrenia, or major depression
     that impairs medical decision-making, OR inability to participate in decisions.
   - If mental health is NOT mentioned at all, treat it as UNKNOWN.
   - For the FINAL boolean:
     * If mental health is clearly OK or not mentioned → criterion_4 = True.
     * If there is clear evidence of severe psychiatric disease with impaired decision-making
       → criterion_4 = False.

5. Metabolic / Comorbidity Control (criterion 5):
   - Positive: diabetes and hypertension reasonably controlled, comorbidities stable.
   - Negative: clearly uncontrolled comorbidities that make anticoagulation unsafe
     (e.g. severe uncontrolled heart failure, decompensated liver disease, etc.).
   - If control status is not described, treat it as UNKNOWN.
   - For the FINAL boolean:
     * If comorbidities look reasonably controlled OR status is unknown with no red-flag signs
       → criterion_5 = True.
     * If comorbidities are clearly uncontrolled and make anticoagulation unsafe
       → criterion_5 = False.

IMPORTANT:
- In this task, you usually receive ONLY basic numeric values and very brief clinical context.
- Missing information (e.g. unknown CHADS2, unknown LVEF, no mention of mental health)
  should NOT by itself push the FINAL patient label to NON-IDEAL.
- Focus on obvious, clinically significant problems in the CURRENT PATIENT’s labs and heart function
  when deciding the FINAL label.

How to reason (you MUST follow these steps):

1. First, summarise the key clinical features of the CURRENT PATIENT from the question:
   - AFib status (if mentioned), stroke risk (CHADS2), heart function (LVEF),
     lab values (creatinine, hemoglobin, platelets, bilirubin, AST),
     and any comorbidities or bleeding history.

2. Then, using ONLY the CURRENT PATIENT’s data, classify each domain with a short textual status:
   - AFib safety: “satisfied” / “violated” / “unknown”.
   - Stroke risk / heart function: “satisfied” / “limited” / “clearly problematic” / “unknown”.
   - Labs: “mostly normal”, “moderately abnormal”, OR “severely abnormal”.
   - Mental health: “OK” / “clearly problematic” / “unknown”.
   - Metabolic & comorbidities: “reasonably controlled” / “clearly uncontrolled” / “unknown”.

3. AFTER that, convert each domain into a STRICT boolean (True/False) decision:
   - criterion_1_pass (AFib safety): True or False
   - criterion_2_pass (Stroke/Heart): True or False
   - criterion_3_pass (Labs): True or False
   - criterion_4_pass (Mental health): True or False
   - criterion_5_pass (Metabolic/Comorbidities): True or False

   Use these mapping rules:
   - Any domain you judged as “clearly problematic” or “severely abnormal”
     → corresponding criterion_pass = False.
   - Any domain you judged as clearly “satisfied”, “OK”, “mostly normal”, or “reasonably controlled”
     → criterion_pass = True.
   - “limited” or “moderately abnormal” or “unknown”:
     → you MUST force yourself to choose True or False,
       based on whether, overall, this looks acceptable (True) or unsafe (False)
       for anticoagulation in the apixaban trial.

4. Decide ONE final label based on the overall pattern of the 5 booleans and the clinical reasoning:

   - "ideal":
     - All 5 criteria reasonably safe:
       * criterion_1_pass, 2_pass, 3_pass, 4_pass, 5_pass are all True,
       * and no domain is clearly problematic.
   - "semi-ideal":
     - Patient is mostly safe but with at least one limitation:
       * some criteria may be False or borderline,
       * but there is NO obvious major lab red-flag or absolute contraindication.
   - "non-ideal":
     - Clear major safety problems:
       * one or more criteria are strongly unsafe (e.g. very abnormal labs,
         major bleeding, clearly uncontrolled comorbidities),
       * OR overall anticoagulation looks clearly too risky.

Answer format (you MUST follow this EXACT format):

1. A short, step-by-step explanation in English, where you:
   - For each domain, state the textual status (e.g. “AFib safety: satisfied”, “Labs: moderately abnormal”),
     and briefly justify it from the patient data.

2. Then output a separate “Domain summary” block with EXACTLY 5 lines, ALL in lower case:
   criterion_1_afib_safe: true/false
   criterion_2_stroke_heart: true/false
   criterion_3_labs: true/false
   criterion_4_mental_health: true/false
   criterion_5_metabolic_comorbidities: true/false

3. Finally, on the LAST line, output EXACTLY one of:
   Final label: ideal
   Final label: semi-ideal
   Final label: non-ideal

- Do NOT output any other variants (no extra words, no “Final label: Final label:”).
- Do NOT invent additional labels or formats.
"""

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "Question:\n{question}"),
    ]
)


def get_llm():
    return ChatOllama(model="llama3.1", temperature=0.0)


parser = StrOutputParser()


def extract_final_label(answer: str) -> str:
    """
    Extract the final label from the response. Expected format:
    Final label: ideal / semi-ideal / non-ideal
    """
    text = answer.lower()
    matches = list(re.finditer(r"final label:\s*(ideal|semi-ideal|non-ideal)", text))
    if not matches:
        return "unknown"
    return matches[-1].group(1).strip()


def extract_criteria_flags(answer: str):
    """
    Parse the 5 lines in the 'Domain summary' block:
      criterion_1_afib_safe: true/false
      ...
    Return dict with booleans or None if not found.
    """
    text = answer.lower()

    def grab(key: str):
        m = re.search(rf"{key}\s*:\s*(true|false)", text)
        if not m:
            return None
        return True if m.group(1) == "true" else False

    return {
        "crit1": grab("criterion_1_afib_safe"),
        "crit2": grab("criterion_2_stroke_heart"),
        "crit3": grab("criterion_3_labs"),
        "crit4": grab("criterion_4_mental_health"),
        "crit5": grab("criterion_5_metabolic_comorbidities"),
    }


def to_bool_or_none(val):
    """Convert GT value (0/1, True/False, etc.) to bool or None."""
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


def main():
    if not os.path.exists(EVAL_CSV):
        raise FileNotFoundError(f"{EVAL_CSV} not found.")

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

    llm = get_llm()

    results = []

    total_cases = 0
    correct_label = 0

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

        chain = prompt | llm | parser
        answer = chain.invoke({"question": question})
        print("LLM Answer:\n", answer)

        pred_label = extract_final_label(answer)
        crit_flags = extract_criteria_flags(answer)

        pred_c1 = crit_flags["crit1"]
        pred_c2 = crit_flags["crit2"]
        pred_c3 = crit_flags["crit3"]
        pred_c4 = crit_flags["crit4"]
        pred_c5 = crit_flags["crit5"]

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
            }
        )

    label_acc = correct_label / total_cases if total_cases else 0.0

    print("\n" + "=" * 80)
    print(f"[PLAIN LLM] Total cases: {total_cases}, Label correct: {correct_label}, Label Accuracy: {label_acc:.3f}")

    for i in range(1, 6):
        if crit_totals[i] > 0:
            acc_i = crit_correct[i] / crit_totals[i]
        else:
            acc_i = 0.0
        print(
            f"Criterion {i} accuracy: {crit_correct[i]}/{crit_totals[i]} = {acc_i:.3f}"
        )
    print("=" * 80)

    out_path = "plain_criteria_eval_results.csv"
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
