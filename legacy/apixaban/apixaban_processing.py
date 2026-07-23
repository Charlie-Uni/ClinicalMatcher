import zipfile
from pathlib import Path

import pandas as pd


# 1) Path to the raw zip file
ZIP_PATH = Path("mimic-iv-ext-apixaban-trial-criteria-questions-1.0.0.zip")

# 2) Relative path to the CSV inside the zip archive
CSV_INSIDE_ZIP = (
    "mimic-iv-ext-apixaban-trial-criteria-questions-1.0.0/annotated_apixaban_combined.csv"
)

# 3) IdealCandidates CSV generated from the JSON summaries
IDEAL_CSV_PATH = Path("IdealCandidates/ideal_candidates.csv")

# 4) Output file
OUTPUT_PATH = Path("apixaban_processed.csv")


def load_raw_annotations():
    """Read the raw long-format annotations from the zip archive."""
    print("Reading raw annotations from the zip file...")
    with zipfile.ZipFile(ZIP_PATH) as z:
        with z.open(CSV_INSIDE_ZIP) as f:
            df = pd.read_csv(f)
    print("Finished reading. Sample rows:")
    print(df.head())
    return df


def clean_answers(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the answer column: yes/no -> 1/0, numeric -> float, not_specified -> NaN."""
    print("Cleaning answer column...")

    df = df.copy()
    df["answer_clean"] = df["answer"]

    # Normalize to lowercase strings for consistent processing.
    df["answer_clean"] = df["answer_clean"].astype(str).str.strip().str.lower()

    # Map yes/no answers to 1/0.
    yes_no_map = {"yes": 1, "no": 0}
    yes_no_mask = df["question_type"] == "yes"
    df.loc[yes_no_mask, "answer_clean"] = df.loc[yes_no_mask, "answer_clean"].map(
        yes_no_map
    )

    # Convert numeric questions to floats.
    numeric_mask = df["question_type"] == "numeric"
    df.loc[numeric_mask, "answer_clean"] = pd.to_numeric(
        df.loc[numeric_mask, "answer_clean"], errors="coerce"
    )

    # Treat not_specified == 1 as missing data.
    df.loc[df["not_specified"] == 1, "answer_clean"] = pd.NA

    print("Cleaned answers. Sample rows:")
    print(df[["note_id", "criterion", "question_type", "answer", "answer_clean"]].head(20))
    return df


def pivot_long_to_wide(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert the long table (one row per criterion) into a wide table
    (one row per note with columns for each criterion).
    """

    print("Pivoting to wide format...")
    wide = df.pivot_table(
        index=["note_id", "hadm_id", "text"],
        columns="criterion",
        values="answer_clean",
        aggfunc="first",
    )
    wide = wide.reset_index()

    print("Wide table shape:", wide.shape)
    print("Wide table columns (partial):", list(wide.columns)[:30])
    return wide


def add_criteria_columns(wide: pd.DataFrame) -> pd.DataFrame:
    """
    Add crit1_pass ~ crit5_pass columns based on the criteria rules described in the docx.
    Update the logic here if the document changes.
    """

    wide = wide.copy()
    print("Computing pass/fail flags for the 5 criteria...")

    # ======== Criteria 1: Non-valvular AFib and low bleeding risk ========
    inc1 = (
        (wide.get("afib") == 1)
        & (wide.get("surgical_valvular_disease") == 0)
        & (wide.get("afib_ablation") == 0)
    )

    exc1 = (
        (wide.get("bleeding") == 1)
        | (wide.get("recent_stroke") == 1)
        | (wide.get("peptic_ulcer_disease") == 1)
        | (wide.get("hemorrhagic") == 1)
    )

    wide["crit1_pass"] = inc1 & (~exc1)

    # ======== Criteria 2: Controlled stroke risk and preserved heart function ========
    chads2 = wide.get("chads2")
    lvef = wide.get("lvef")
    recent_stroke = wide.get("recent_stroke")
    prior_stroke = wide.get("prior_stroke")
    heart_failure = wide.get("heart_failure")

    chads_ok = chads2.isna() | (chads2 <= 2)
    lvef_ok = lvef.isna() | (lvef >= 30)
    inc2 = chads_ok & lvef_ok

    exc2 = (recent_stroke == 1) | (prior_stroke == 1) | (heart_failure == 1)
    wide["crit2_pass"] = inc2 & (~exc2)

    # ======== Criteria 3: Lab thresholds ========
    creat = wide.get("CREAT")
    hgb = wide.get("HGB")
    plt = wide.get("PLT")
    bili = wide.get("BILI")
    ast = wide.get("AST")

    crit3_cond = (
        (creat.notna())
        & (creat <= 2.5)
        & (hgb.notna())
        & (hgb >= 10)
        & (plt.notna())
        & (plt >= 100)
        & (bili.notna())
        & (bili <= 1.5)
        & (ast.notna())
        & (ast <= 80)
    )
    wide["crit3_pass"] = crit3_cond

    # ======== Criteria 4: Mental health and decision capacity ========
    bipolar = wide.get("bipolar")
    schiz = wide.get("schizophrenia")
    mdd = wide.get("mdd")
    med_decisions = wide.get("med_decisions")

    mental_disorder = (bipolar == 1) | (schiz == 1) | (mdd == 1)
    no_decision_capacity = med_decisions == 0

    wide["crit4_pass"] = ~(mental_disorder | no_decision_capacity)

    # ======== Criteria 5: Metabolic and comorbidity control ========
    t2d = wide.get("t2d")
    hypertension = wide.get("arterial_hypertension")
    glucose = wide.get("blood_glucose")

    case1 = (t2d == 0) & (hypertension == 0)
    case2 = (t2d == 1) & glucose.notna() & (glucose <= 180)
    wide["crit5_pass"] = case1 | case2

    print("Added columns:", [c for c in wide.columns if c.startswith("crit")])
    return wide


def merge_ideal_candidates(wide: pd.DataFrame) -> pd.DataFrame:
    """
    Merge the IdealCandidates table to add the final labels.
    """

    print("Reading IdealCandidates table...")
    ideal = pd.read_csv(IDEAL_CSV_PATH)

    print("IdealCandidates sample:")
    print(ideal.head())

    merged = wide.merge(
        ideal,
        on=["note_id", "hadm_id"],
        how="left",
    )

    print("Merge complete. Shape:", merged.shape)
    return merged


def main():
    # 1. Read raw annotations
    df_raw = load_raw_annotations()

    # 2. Clean the answer column
    df_clean = clean_answers(df_raw)

    # 3. Convert long table to wide table
    wide = pivot_long_to_wide(df_clean)

    # 4. Add the five criteria pass/fail columns
    wide_with_criteria = add_criteria_columns(wide)

    # 5. Merge IdealCandidates labels
    final_df = merge_ideal_candidates(wide_with_criteria)

    # 6. Export the final CSV
    print(f"Writing output to {OUTPUT_PATH}...")
    final_df.to_csv(OUTPUT_PATH, index=False)
    print("All done!")


if __name__ == "__main__":
    main()
