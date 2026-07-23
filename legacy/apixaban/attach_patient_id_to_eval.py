import pandas as pd

EVAL_CSV = "data/eval_cases.csv"
MAPPING_CSV = "data/eval_case_mapping.csv"
OUT_CSV = "data/eval_cases_with_pid.csv"

eval_df = pd.read_csv(EVAL_CSV)
map_df = pd.read_csv(MAPPING_CSV)

required = {"case_id", "patient_id"}
if not required.issubset(map_df.columns):
    raise ValueError("eval_case_mapping.csv must contain case_id and patient_id columns.")

merged = eval_df.merge(map_df, on="case_id", how="left")

missing = merged[merged["patient_id"].isna()]
if not missing.empty:
    print("⚠️ Missing patient_id for case_ids:", missing["case_id"].tolist())

merged.to_csv(OUT_CSV, index=False)
print(f"✅ Wrote merged CSV to {OUT_CSV}")
