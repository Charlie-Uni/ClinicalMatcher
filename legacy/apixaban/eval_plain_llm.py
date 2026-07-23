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
- You may use the retrieved EHR examples ONLY as secondary reference (for calibration),
  NOT as a vote or majority label.

Clinical criteria (rule-based intuition, but NOT rigid exclusion rules):
1. AFib Safe:
   - Non-valvular atrial fibrillation.
   - No clear evidence of major bleeding risk (no recent major stroke, no uncontrolled hemorrhage, etc.).
2. Stroke Risk / Heart Function:
   - CHADS2 score <= 2 is low risk; CHADS2 >= 3 is higher risk but does NOT automatically exclude the patient.
   - Left ventricular ejection fraction (LVEF) >= 30% is generally acceptable.
   - LVEF < 30% is a limitation and often pushes the patient to SEMI-IDEAL.
   - If CHADS2 or LVEF are missing, treat them as UNKNOWN (do not automatically downgrade).
3. Lab Safety (approximate thresholds; think in three levels):

   Major red-flag abnormalities (strongly suggest NON-IDEAL):
   - Creatinine > 3.0 mg/dL
   - Hemoglobin < 9.0 g/dL
   - Platelets < 50 x10^9/L
   - Total bilirubin >= 3.0 mg/dL
   - AST >= 200 U/L

   Moderate abnormalities (borderline → often SEMI-IDEAL, unless combined with other severe issues):
   - Creatinine between 2.0–3.0 mg/dL
   - Hemoglobin between 9.0–10.0 g/dL
   - Platelets between 50–100 x10^9/L
   - Total bilirubin between 1.5–3.0 mg/dL
   - AST between 80–200 U/L

   Normal / mildly abnormal (generally safe):
   - Values within the usual trial-like thresholds:
     Creatinine <= 2.5 mg/dL,
     Hemoglobin >= 10 g/dL,
     Platelets >= 100 x10^9/L,
     Total bilirubin <= 1.5 mg/dL,
     AST <= 80 U/L.

4. Mental Health:
   - If the question clearly mentions severe bipolar disorder, schizophrenia, or major depression
     that impairs medical decision-making, this is a negative factor.
   - If mental health is NOT mentioned, treat it as UNKNOWN (do NOT downgrade by default).

5. Metabolic / Comorbidity Control:
   - Diabetes and hypertension reasonably controlled is acceptable.
   - Uncontrolled comorbidities that make anticoagulation clearly unsafe are a negative factor.
   - If control status is not described, treat it as UNKNOWN (do NOT automatically downgrade).

IMPORTANT:
- In this task, you usually receive ONLY basic numeric values and very brief clinical context.
  Missing information (e.g. unknown CHADS2, unknown LVEF, no mention of mental health)
  should NOT by itself push the patient to NON-IDEAL.
- Focus on obvious, clinically significant problems in the CURRENT PATIENT’s labs and heart function.

How to reason (follow these steps):

1. First, summarise the key clinical features of the CURRENT PATIENT from the question:
   - AFib status (if mentioned), stroke risk (CHADS2), heart function (LVEF),
     lab values (creatinine, hemoglobin, platelets, bilirubin, AST),
     and any comorbidities.

2. Then, using ONLY the CURRENT PATIENT’s data, classify each domain:
   - AFib safety: satisfied / violated / unknown.
   - Stroke risk / heart function: satisfied / limited / clearly problematic / unknown.
   - Labs: mostly normal, moderately abnormal, OR severely abnormal (use the thresholds above).
   - Mental health: OK / clearly problematic / unknown.
   - Metabolic & comorbidities: reasonably controlled / clearly uncontrolled / unknown.

3. Use the retrieved EHR examples and their labels ONLY as supporting context:
   - You may mention them briefly to justify that similar patients were treated as acceptable or risky.
   - DO NOT simply copy the majority label from retrieved notes.

4. Decide ONE final label based on the overall pattern:

   - "ideal":
     - No major red-flag lab abnormalities.
     - At most mild or moderate issues, but nothing that clearly makes anticoagulation unsafe.
     - It is acceptable that CHADS2, LVEF, or mental health are UNKNOWN if nothing dangerous is described.

   - "semi-ideal":
     - Mostly safe, but with at least one meaningful limitation or moderate abnormality
       (e.g. LVEF < 30%, moderately abnormal labs, or comorbidities that require caution),
     - AND no single very severe red-flag abnormality.
     - Use this when the patient looks "borderline" or "suboptimal" but not clearly unsafe.

   - "non-ideal":
     - Clear, major safety problems:
       - Any of the major red-flag lab abnormalities (e.g. creatinine > 3.0, very low hemoglobin,
         very low platelets, very high bilirubin, very high AST),
       - OR multiple moderate abnormalities combined that make anticoagulation clearly unsafe,
       - OR explicit strong contraindications (e.g. active major bleeding, severe hepatic failure).

Answer format:
1. Provide a short, step-by-step explanation in English:
   - Explicitly state for each domain (AFib safety, heart/stroke risk, labs, mental health, comorbidities)
     whether it is satisfied, limited, clearly unsafe, or unknown.
   - Optionally mention retrieved examples as supporting context.
2. At the very end, output exactly one line in the format (all lower-case label):
   Final label: ideal
   or
   Final label: semi-ideal
   or
   Final label: non-ideal
"""

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "{question}"),
    ]
)

llm = ChatOllama(model="llama3.1", temperature=0.2)
parser = StrOutputParser()

chain = prompt | llm | parser


def extract_final_label(answer: str) -> str:
    pattern = r"Final label:\s*(ideal|semi-ideal|non-ideal)"
    m = re.search(pattern, answer, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    return "unknown"


def main():
    if not os.path.exists(EVAL_CSV):
        raise FileNotFoundError(f"{EVAL_CSV} missing.")

    eval_df = pd.read_csv(EVAL_CSV)
    required_cols = {"case_id", "question", "GT_label"}
    if not required_cols.issubset(eval_df.columns):
        raise ValueError(f"{EVAL_CSV} must contain columns: {required_cols}")

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

        answer = chain.invoke({"question": question})
        print("LLM Answer:\n", answer)

        pred_label = extract_final_label(answer)
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
    print(f"[PLAIN LLM] Total cases: {total}, Correct: {correct}, Accuracy: {acc:.3f}")
    print("=" * 80)

    out_path = "plain_eval_results.csv"
    pd.DataFrame(results).to_csv(out_path, index=False)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
