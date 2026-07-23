import os
import shutil
import pandas as pd

from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter

COMORB_KEYWORDS = [
    "comorbidity",
    "comorbid",
    "diabetes",
    "hypertension",
    "heart failure",
    "copd",
    "schizophrenia",
    "bipolar",
    "depression",
    "stroke",
    "tia",
    "psych",
]

LAB_KEYWORDS = [
    "creatinine",
    "creat ",
    "bun",
    "plt",
    "platelet",
    "platelets",
    "hemoglobin",
    "hgb",
    "hematocrit",
    "bilirubin",
    "bili",
    "ast",
    "alt",
    "alk phos",
    "lvef",
    "ef ",
    "lab ",
    "labs",
    "laboratory",
    "cbc",
    "cmp",
    "glucose",
    "mg/dl",
]

HI_RISK_KEYWORDS = [
    "bleeding",
    "hemorrhage",
    "peptic ulcer",
    "intracranial",
    "gastrointestinal",
    "contraindication",
    "valvular",
    "high risk",
]

DB_DIR = "chroma_apixaban"
CSV_PATH = "data/apixaban_processed.csv"
EMBED_MODEL = "llama3.1"


def detect_chunk_type(text: str) -> str:
    lowered = text.lower()

    def matches(keywords):
        return any(k in lowered for k in keywords)

    if matches(COMORB_KEYWORDS):
        return "comorb_only"
    if matches(HI_RISK_KEYWORDS):
        return "hi_risk_core"
    if matches(LAB_KEYWORDS):
        return "labs_only"
    return "general"


def load_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    print(f"Loaded: {path} Shape: {df.shape}")
    return df


def make_docs_from_df(df: pd.DataFrame):
    base_docs = []
    for _, row in df.iterrows():
        text = str(row.get("text", ""))
        if not text or text.lower() == "nan":
            continue

        metadata = {
            "note_id": row.get("note_id"),
            "hadm_id": row.get("hadm_id"),
            "trial_label": row.get("trial_label") if "trial_label" in df.columns else None,
            "ideal_candidate": row.get("ideal_candidate"),
            "semi_ideal_candidate": row.get("semi_ideal_candidate"),
        }
        base_docs.append(Document(page_content=text, metadata=metadata))

    print(f"Base documents (before splitting): {len(base_docs)}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=100,
        length_function=len,
        separators=["\n\n", "\n", ". ", " "],
    )

    split_docs = splitter.split_documents(base_docs)
    print(f"Documents after splitting: {len(split_docs)}")

    max_len = max(len(d.page_content) for d in split_docs) if split_docs else 0
    print(f"Max chunk length: {max_len} characters")

    for doc in split_docs:
        chunk_type = detect_chunk_type(doc.page_content)
        doc.metadata = doc.metadata or {}
        doc.metadata["chunk_type"] = chunk_type

    return split_docs


def main():
    df = load_data(CSV_PATH)

    if "trial_label" not in df.columns:
        def label_row(row):
            if row["ideal_candidate"] == 1:
                return "ideal"
            if row["semi_ideal_candidate"] == 1:
                return "semi-ideal"
            return "non-ideal"

        df["trial_label"] = df.apply(label_row, axis=1)

    docs = make_docs_from_df(df)

    print(f"Using OllamaEmbeddings model='{EMBED_MODEL}'")
    embeddings = OllamaEmbeddings(model=EMBED_MODEL)

    if os.path.exists(DB_DIR):
        print(f"Removing existing DB dir: {DB_DIR}")
        shutil.rmtree(DB_DIR)

    print(f"Building Chroma index in '{DB_DIR}' on {len(docs)} chunks...")
    vectordb = Chroma(
        embedding_function=embeddings,
        persist_directory=DB_DIR,
        collection_name="apixaban_rag",
    )

    batch_size = 10
    total = len(docs)
    for start in range(0, total, batch_size):
        batch = docs[start : start + batch_size]
        vectordb.add_documents(batch)
        processed = min(start + batch_size, total)
        print(f"Embedded {processed}/{total} chunks")

    vectordb.persist()
    print("Chroma index built and persisted.")


if __name__ == "__main__":
    main()
