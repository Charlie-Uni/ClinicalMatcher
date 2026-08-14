import math
import struct
from importlib.metadata import version
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Tuple

from .base import RankedEvidence


Vector = Tuple[float, ...]


class DenseEncoder(Protocol):
    def encode_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...

    def encode_queries(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...


def _vector(values: Sequence[float], dimension: Optional[int] = None) -> Vector:
    resolved = tuple(float(value) for value in values)
    if not resolved or any(not math.isfinite(value) for value in resolved):
        raise ValueError("Dense vectors must be finite and non-empty")
    if dimension is not None and len(resolved) != dimension:
        raise ValueError("Dense vector dimension mismatch")
    return resolved


def serialize_float32_vectors(vectors: Sequence[Sequence[float]]) -> bytes:
    if not vectors:
        raise ValueError("Dense index requires vectors")
    dimension = len(vectors[0])
    resolved = tuple(_vector(vector, dimension) for vector in vectors)
    return b"".join(
        struct.pack(f"<{dimension}f", *vector) for vector in resolved
    )


class DensePatientRetriever:
    """Exact dot-product retrieval isolated within each patient."""

    def __init__(
        self,
        records: Sequence[Mapping[str, Any]],
        document_vectors: Sequence[Sequence[float]],
        *,
        query_encoder: Optional[
            Callable[[Sequence[str]], Sequence[Sequence[float]]]
        ] = None,
    ) -> None:
        if not records or len(records) != len(document_vectors):
            raise ValueError("Dense document/vector count mismatch")
        dimension = len(document_vectors[0])
        if dimension < 1:
            raise ValueError("Dense vector dimension must be positive")
        self.dimension = dimension
        self._query_encoder = query_encoder
        documents = {}
        seen = set()
        for record, raw_vector in zip(records, document_vectors):
            evidence_id = record["evidence_id"]
            if evidence_id in seen:
                raise ValueError("Dense evidence IDs must be globally unique")
            seen.add(evidence_id)
            documents.setdefault(record["patient_id"], []).append(
                (
                    evidence_id,
                    int(record["source_span"]["start"]),
                    _vector(raw_vector, dimension),
                )
            )
        self._documents = {
            patient_id: tuple(
                sorted(items, key=lambda item: (item[1], item[0]))
            )
            for patient_id, items in documents.items()
        }

    @property
    def patient_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._documents))

    @property
    def document_count(self) -> int:
        return sum(len(items) for items in self._documents.values())

    def rank_vector(
        self, patient_id: str, query_vector: Sequence[float]
    ) -> Tuple[RankedEvidence, ...]:
        documents = self._documents.get(patient_id)
        if documents is None:
            raise KeyError(f"Unknown dense patient: {patient_id}")
        query = _vector(query_vector, self.dimension)
        scored = [
            (
                sum(left * right for left, right in zip(vector, query)),
                source_start,
                evidence_id,
            )
            for evidence_id, source_start, vector in documents
        ]
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return tuple(
            RankedEvidence(patient_id, evidence_id, score, rank)
            for rank, (score, _, evidence_id) in enumerate(scored, start=1)
        )

    def rank(self, patient_id: str, query: str) -> Tuple[RankedEvidence, ...]:
        if self._query_encoder is None:
            raise RuntimeError("Dense query encoder is unavailable")
        encoded = self._query_encoder([query])
        if len(encoded) != 1:
            raise ValueError("Dense query encoder returned an unexpected count")
        return self.rank_vector(patient_id, encoded[0])

    def retrieve_vector(
        self, patient_id: str, query_vector: Sequence[float], k: int
    ) -> Tuple[RankedEvidence, ...]:
        if k < 1:
            raise ValueError("Retrieval k must be positive")
        return self.rank_vector(patient_id, query_vector)[:k]

    def retrieve(
        self, patient_id: str, query: str, k: int
    ) -> Tuple[RankedEvidence, ...]:
        if k < 1:
            raise ValueError("Retrieval k must be positive")
        return self.rank(patient_id, query)[:k]


class MedCPTEncoder:
    """Pinned, local-only MedCPT dual encoder using official CLS pooling."""

    def __init__(self, contract: Mapping[str, Any]) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as error:
            raise RuntimeError(
                "Dense retrieval dependencies are missing; install the pinned "
                "requirements in an isolated local environment"
            ) from error

        runtime = contract["runtime"]
        if runtime["device"] != "cpu" or not runtime["local_files_only"]:
            raise ValueError("MedCPT runtime must remain deterministic and local-only")
        if version("torch") != runtime["torch_version"]:
            raise RuntimeError("Installed torch version differs from dense contract")
        if version("transformers") != runtime["transformers_version"]:
            raise RuntimeError(
                "Installed transformers version differs from dense contract"
            )
        torch.use_deterministic_algorithms(runtime["deterministic_algorithms"])
        self._torch = torch
        self._batch_size = runtime["batch_size"]
        self._query_max_length = contract["query_input"]["max_length"]
        self._document_max_length = contract["document_input"]["max_length"]
        query = contract["models"]["query_encoder"]
        document = contract["models"]["document_encoder"]
        common = {"local_files_only": True, "trust_remote_code": False}
        self._query_tokenizer = AutoTokenizer.from_pretrained(
            query["model_id"], revision=query["revision"], **common
        )
        self._query_model = AutoModel.from_pretrained(
            query["model_id"], revision=query["revision"], **common
        ).to("cpu")
        self._document_tokenizer = AutoTokenizer.from_pretrained(
            document["model_id"], revision=document["revision"], **common
        )
        self._document_model = AutoModel.from_pretrained(
            document["model_id"], revision=document["revision"], **common
        ).to("cpu")
        self._query_model.eval()
        self._document_model.eval()

    def _encode_batches(
        self,
        texts: Sequence[str],
        *,
        tokenizer: Any,
        model: Any,
        max_length: int,
        paired: bool,
    ) -> Tuple[Vector, ...]:
        vectors = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            if paired:
                encoded = tokenizer(
                    [""] * len(batch),
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                )
            else:
                encoded = tokenizer(
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                )
            encoded = {key: value.to("cpu") for key, value in encoded.items()}
            with self._torch.inference_mode():
                output = model(**encoded).last_hidden_state[:, 0, :]
            vectors.extend(output.detach().to(self._torch.float32).cpu().tolist())
        return tuple(_vector(vector) for vector in vectors)

    def encode_documents(self, texts: Sequence[str]) -> Tuple[Vector, ...]:
        return self._encode_batches(
            texts,
            tokenizer=self._document_tokenizer,
            model=self._document_model,
            max_length=self._document_max_length,
            paired=True,
        )

    def encode_queries(self, texts: Sequence[str]) -> Tuple[Vector, ...]:
        return self._encode_batches(
            texts,
            tokenizer=self._query_tokenizer,
            model=self._query_model,
            max_length=self._query_max_length,
            paired=False,
        )
