import pandas as pd

EVAL_CSV = "data/eval_cases.csv"
PROC_CSV = "data/apixaban_processed.csv"
OUT_CSV = "data/eval_cases_with_crit.csv"

eval_df = pd.read_csv(EVAL_CSV)
proc_df = pd.read_csv(PROC_CSV)

print("eval_cases columns:", eval_df.columns.tolist())
print("apixaban_processed columns:", proc_df.columns.tolist())

cols_from_proc = [
    "patient_id",
    "crit1_pass",
    "crit2_pass",
    "crit3_pass",
    "crit4_pass",
    "crit5_pass",
]

missing = [c for c in cols_from_proc if c not in proc_df.columns]
if missing:
    raise ValueError(f"apixaban_processed.csv missing columns: {missing}")

proc_small = proc_df[cols_from_proc].copy()

if "patient_id" not in eval_df.columns:
    raise ValueError("eval_cases.csv is missing patient_id column.")

merged = eval_df.merge(proc_small, on="patient_id", how="left")

nan_rows = merged[merged["crit1_pass"].isna()]
if not nan_rows.empty:
    print("⚠️ Warning: the following case_id entries have missing patient_id matches:")
    print(nan_rows[["case_id", "patient_id"]])

merged.to_csv(OUT_CSV, index=False)
print(f"✅ Saved merged CSV to {OUT_CSV}")
