import json
import math
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Mapping, Sequence, Tuple

from .base import RankedEvidence


TOKEN_PATTERN = re.compile(r"[^\W_]+(?:[./-][^\W_]+)*", re.UNICODE)


def tokenize(text: str) -> Tuple[str, ...]:
    return tuple(match.group(0) for match in TOKEN_PATTERN.finditer(text.casefold()))


class BM25PatientRetriever:
    """Deterministic BM25 ranking over evidence isolated within each patient."""

    def __init__(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if not math.isfinite(k1) or k1 <= 0:
            raise ValueError("BM25 k1 must be finite and positive")
        if not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("BM25 b must be finite and between zero and one")
        if not records:
            raise ValueError("BM25 requires evidence records")
        self.k1 = float(k1)
        self.b = float(b)
        documents: Dict[str, list] = defaultdict(list)
        seen = set()
        for record in records:
            evidence_id = record["evidence_id"]
            if evidence_id in seen:
                raise ValueError("BM25 evidence IDs must be globally unique")
            seen.add(evidence_id)
            terms = tokenize(record["text"])
            documents[record["patient_id"]].append(
                {
                    "evidence_id": evidence_id,
                    "source_start": record["source_span"]["start"],
                    "term_frequencies": Counter(terms),
                    "length": len(terms),
                }
            )
        self._documents = {
            patient_id: tuple(
                sorted(
                    patient_documents,
                    key=lambda item: (item["source_start"], item["evidence_id"]),
                )
            )
            for patient_id, patient_documents in documents.items()
        }
        self._average_lengths = {}
        self._document_frequencies = {}
        for patient_id, patient_documents in self._documents.items():
            self._average_lengths[patient_id] = sum(
                item["length"] for item in patient_documents
            ) / len(patient_documents)
            frequencies = Counter()
            for item in patient_documents:
                frequencies.update(item["term_frequencies"].keys())
            self._document_frequencies[patient_id] = frequencies

    @property
    def patient_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._documents))

    @property
    def document_count(self) -> int:
        return sum(len(items) for items in self._documents.values())

    def deterministic_index_size_bytes(self) -> int:
        payload = {
            "k1": self.k1,
            "b": self.b,
            "patients": [
                {
                    "patient_id": patient_id,
                    "documents": [
                        {
                            "evidence_id": item["evidence_id"],
                            "source_start": item["source_start"],
                            "length": item["length"],
                            "term_frequencies": dict(
                                sorted(item["term_frequencies"].items())
                            ),
                        }
                        for item in self._documents[patient_id]
                    ],
                }
                for patient_id in self.patient_ids
            ],
        }
        return len(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    def rank(self, patient_id: str, query: str) -> Tuple[RankedEvidence, ...]:
        documents = self._documents.get(patient_id)
        if documents is None:
            raise KeyError(f"Unknown BM25 patient: {patient_id}")
        query_terms = tuple(dict.fromkeys(tokenize(query)))
        average_length = self._average_lengths[patient_id]
        document_count = len(documents)
        frequencies = self._document_frequencies[patient_id]
        scored = []
        for document in documents:
            score = 0.0
            for term in query_terms:
                term_frequency = document["term_frequencies"].get(term, 0)
                if not term_frequency:
                    continue
                document_frequency = frequencies[term]
                inverse_document_frequency = math.log(
                    1
                    + (document_count - document_frequency + 0.5)
                    / (document_frequency + 0.5)
                )
                denominator = term_frequency + self.k1 * (
                    1 - self.b
                    + self.b * document["length"] / average_length
                )
                score += inverse_document_frequency * (
                    term_frequency * (self.k1 + 1) / denominator
                )
            scored.append((score, document["source_start"], document["evidence_id"]))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return tuple(
            RankedEvidence(patient_id, evidence_id, score, rank)
            for rank, (score, _, evidence_id) in enumerate(scored, start=1)
        )

    def retrieve(
        self, patient_id: str, query: str, k: int
    ) -> Tuple[RankedEvidence, ...]:
        if k < 1:
            raise ValueError("Retrieval k must be positive")
        return tuple(
            item for item in self.rank(patient_id, query) if item.score > 0
        )[:k]
