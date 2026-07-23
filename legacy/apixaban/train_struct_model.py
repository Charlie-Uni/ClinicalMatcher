import os
import json
import joblib
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report


MI_CSV = "mi_feature_ranking_per_feature.csv"
DATA_CSV = "data/apixaban_processed.csv"
MODELS_DIR = "models"


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
    return df


def encode_label(df: pd.DataFrame):
    """Map textual labels to integers 0/1/2."""
    label_map = {"non-ideal": 0, "semi-ideal": 1, "ideal": 2}
    df["trial_label_id"] = df["trial_label"].map(label_map)
    if df["trial_label_id"].isna().any():
        raise ValueError("Found unmapped labels in trial_label.")
    return df, label_map


def select_features_from_mi(mi_path: str, top_k: int = 10):
    """
    Select the top-k features (with note == 'ok') from the MI ranking CSV.
    Adjust top_k as needed.
    """
    mi_df = pd.read_csv(mi_path)
    mi_df = mi_df[mi_df["note"] == "ok"].copy()
    mi_df = mi_df.sort_values("mi_score", ascending=False)
    selected = mi_df["feature"].head(top_k).tolist()
    print(f"[MI] Selected features (top {top_k}):", selected)
    return selected


def main():
    os.makedirs(MODELS_DIR, exist_ok=True)

    df = pd.read_csv(DATA_CSV)
    print("Loaded:", DATA_CSV, "Shape:", df.shape)

    df = build_trial_label(df)
    df, label_map = encode_label(df)

    feature_cols = select_features_from_mi(MI_CSV, top_k=10)

    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in data for selected features: {missing}")

    X = df[feature_cols].copy()
    for col in feature_cols:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    y = df["trial_label_id"]

    mask_non_all_nan = ~X.isna().all(axis=1)
    X = X[mask_non_all_nan]
    y = y[mask_non_all_nan]
    print("After dropping all-NaN rows:", X.shape)

    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                RandomForestClassifier(
                    n_estimators=200,
                    random_state=42,
                    class_weight="balanced",
                ),
            ),
        ]
    )

    pipe.fit(X_train, y_train)

    y_pred = pipe.predict(X_val)
    print("\n[Validation classification_report]")
    print(classification_report(y_val, y_pred, target_names=list(label_map.keys())))

    model_path = os.path.join(MODELS_DIR, "structured_model.pkl")
    joblib.dump(pipe, model_path)
    print("Saved structured model to:", model_path)

    feat_path = os.path.join(MODELS_DIR, "structured_features.json")
    with open(feat_path, "w") as f:
        json.dump(feature_cols, f, indent=2)
    print("Saved feature list to:", feat_path)

    label_map_path = os.path.join(MODELS_DIR, "label_map.json")
    with open(label_map_path, "w") as f:
        json.dump(label_map, f, indent=2)
    print("Saved label map to:", label_map_path)


if __name__ == "__main__":
    main()
