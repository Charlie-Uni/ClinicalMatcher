import pandas as pd
from sklearn.feature_selection import mutual_info_classif

# ============================================================
# Configuration: update the CSV path to match your file layout
# ============================================================
CSV_PATH = "data/apixaban_processed.csv"

# Feature columns to evaluate via mutual information
FEATURE_COLS = [
    # Lab values
    "AST",
    "BILI",
    "CREAT",
    "HGB",
    "PLT",
    "blood_glucose",
    "chads2",
    "lvef",
    # Binary comorbidities / risk factors
    "afib",
    "arterial_hypertension",
    "heart_failure",
    "t2d",
    "prior_stroke",
    "recent_stroke",
    "peptic_ulcer_disease",
    "bleeding",
    "surgical_valvular_disease",
    "bipolar",
    "mdd",
    "schizophrenia",
    "med_decisions",
]


def load_data(path: str) -> pd.DataFrame:
    """Load the cleaned apixaban_processed.csv file."""
    df = pd.read_csv(path)
    print("Loaded CSV:", path)
    print("Shape:", df.shape)
    print("Columns:", df.columns.tolist())
    return df


def build_trial_label(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build a three-class label using ideal_candidate and semi_ideal_candidate:
      - ideal
      - semi-ideal
      - non-ideal
    """
    for col in ["ideal_candidate", "semi_ideal_candidate"]:
        if col not in df.columns:
            raise ValueError(f"Missing column in CSV: {col}")

    def label_row(row):
        if row["ideal_candidate"] == 1:
            return "ideal"
        if row["semi_ideal_candidate"] == 1:
            return "semi-ideal"
        return "non-ideal"

    df["trial_label"] = df.apply(label_row, axis=1)
    print("\n[Label counts]")
    print(df["trial_label"].value_counts())
    return df


def encode_label(df: pd.DataFrame):
    """Map the textual trial_label into 0/1/2 for mutual_info_classif."""
    label_map = {"non-ideal": 0, "semi-ideal": 1, "ideal": 2}
    if "trial_label" not in df.columns:
        raise ValueError("trial_label column not found, please run build_trial_label first.")
    df["trial_label_id"] = df["trial_label"].map(label_map)
    if df["trial_label_id"].isna().any():
        raise ValueError("Found unmapped labels in trial_label.")
    return df, label_map


def select_features(df: pd.DataFrame) -> pd.DataFrame:
    """Select the feature columns and ensure they are numeric."""
    missing = [c for c in FEATURE_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing feature columns in CSV: {missing}")

    X = df[FEATURE_COLS].copy()
    for col in X.columns:
        X[col] = pd.to_numeric(X[col], errors="coerce")

    print("\n[Non-null counts per feature]")
    print(X.notna().sum().sort_values(ascending=False))
    return X


def compute_mi_per_feature(X: pd.DataFrame, y: pd.Series, min_samples: int = 5) -> pd.DataFrame:
    """
    Compute mutual information feature-by-feature:
      - For each feature, keep rows where both the feature and y are non-null.
      - If the subset has fewer than min_samples rows, mark MI as NaN.
      - If the feature or the label is constant within the subset, mark MI as NaN.
    """
    rows = []

    for col in X.columns:
        feat = pd.to_numeric(X[col], errors="coerce")
        mask = feat.notna() & y.notna()
        n = int(mask.sum())

        if n < min_samples:
            mi = float("nan")
            note = f"skipped (n={n} < min_samples={min_samples})"
        else:
            feat_sub = feat[mask]
            y_sub = y[mask]

            if feat_sub.nunique() <= 1 or y_sub.nunique() <= 1:
                mi = float("nan")
                note = "skipped (constant feature or label in subset)"
            else:
                x_sub = feat_sub.to_numpy().reshape(-1, 1)
                y_arr = y_sub.to_numpy()

                mi = mutual_info_classif(
                    x_sub,
                    y_arr,
                    discrete_features="auto",
                    random_state=42,
                )[0]
                note = "ok"

        rows.append(
            {
                "feature": col,
                "n_samples_used": n,
                "mi_score": mi,
                "note": note,
            }
        )

    mi_df = pd.DataFrame(rows).sort_values("mi_score", ascending=False)
    return mi_df


def main():
    # 1. Load data
    df = load_data(CSV_PATH)

    # 2. Build the three-class label
    df = build_trial_label(df)

    # 3. Encode labels as 0/1/2
    df, label_map = encode_label(df)

    # 4. Select features
    X = select_features(df)
    y = df["trial_label_id"]

    # 5. Compute mutual information feature-by-feature
    mi_df = compute_mi_per_feature(X, y, min_samples=5)

    print("\n=== Mutual Information ranking (per feature) ===")
    print(mi_df)

    out_path = "mi_feature_ranking_per_feature.csv"
    mi_df.to_csv(out_path, index=False)
    print(f"\nSaved MI scores to {out_path}")
    print("\nLabel mapping used:", label_map)


if __name__ == "__main__":
    main()
