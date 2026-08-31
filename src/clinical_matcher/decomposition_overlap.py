import hashlib
import json
import math
import re
import unicodedata
from fractions import Fraction
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .decomposition_benchmark import (
    DecompositionBenchmarkError,
    validate_decomposition_selection_document,
)
from .splits import current_git_commit
from .validation import validate_document


REPORT_VERSION = "decomposition-overlap-diagnostic/1.0.0"
METHOD = "unicode_word_set_jaccard/1.0.0"
REPORT_SCHEMA = (
    "schemas/decomposition-overlap-diagnostic-1.0.0.schema.json"
)
TOP_K = 20
PERCENTILES = (50, 90, 95, 99)
TOKEN_PATTERN = re.compile(r"[^\W_]+", flags=re.UNICODE)


class DecompositionOverlapError(ValueError):
    """Raised when the disclosure-only overlap report is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lexical_tokens(text: str) -> frozenset[str]:
    if not isinstance(text, str) or not text.strip():
        raise DecompositionOverlapError("Criterion text must be non-empty")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = frozenset(TOKEN_PATTERN.findall(normalized))
    if not tokens:
        raise DecompositionOverlapError(
            "Criterion text must contain at least one Unicode word token"
        )
    return tokens


def _criterion_reference(record: Dict[str, Any]) -> Dict[str, str]:
    return {
        "nct_id": record["nct_id"],
        "criterion_id": record["criterion_id"],
        "normalized_text_sha256": record["normalized_text_sha256"],
    }


def _nearest_rank(values: Sequence[Fraction], percentile: int) -> Fraction:
    if not values:
        raise DecompositionOverlapError("Overlap distribution is empty")
    if not 0 < percentile <= 100:
        raise DecompositionOverlapError("Percentile must be in (0, 100]")
    ordered = sorted(values)
    index = math.ceil(percentile * len(ordered) / 100) - 1
    return ordered[index]


def _as_float(value: Fraction) -> float:
    return round(float(value), 12)


def build_overlap_diagnostic(
    selection: Dict[str, Any],
    selection_file_sha256: str,
    builder_code_commit: str | None = None,
) -> Dict[str, Any]:
    """Build a report-only exhaustive lexical overlap diagnostic."""
    validate_decomposition_selection_document(selection)
    if not re.fullmatch(r"[0-9a-f]{64}", selection_file_sha256):
        raise DecompositionOverlapError("Invalid selection file SHA-256")
    commit = builder_code_commit or current_git_commit()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise DecompositionOverlapError("Invalid builder Git commit")

    selected = [record for record in selection["records"] if record["selected"]]
    by_split = {
        split: sorted(
            (
                record
                for record in selected
                if record["assigned_split"] == split
            ),
            key=lambda record: (record["nct_id"], record["criterion_id"]),
        )
        for split in ("dev", "test")
    }
    if any(len(by_split[split]) != 40 for split in by_split):
        raise DecompositionOverlapError(
            "Diagnostic requires exactly 40 selected criteria per split"
        )

    token_sets = {
        (record["nct_id"], record["criterion_id"]): lexical_tokens(
            record["source_text"]
        )
        for record in selected
    }
    pairs: List[Tuple[Fraction, Dict[str, Any]]] = []
    for left in by_split["dev"]:
        left_tokens = token_sets[(left["nct_id"], left["criterion_id"])]
        for right in by_split["test"]:
            right_tokens = token_sets[(right["nct_id"], right["criterion_id"])]
            shared = len(left_tokens & right_tokens)
            union = len(left_tokens | right_tokens)
            score = Fraction(shared, union)
            pairs.append(
                (
                    score,
                    {
                        "dev": _criterion_reference(left),
                        "test": _criterion_reference(right),
                        "shared_token_count": shared,
                        "union_token_count": union,
                        "jaccard_similarity": _as_float(score),
                    },
                )
            )

    ranked = sorted(
        pairs,
        key=lambda item: (
            -item[0],
            item[1]["dev"]["nct_id"],
            item[1]["dev"]["criterion_id"],
            item[1]["test"]["nct_id"],
            item[1]["test"]["criterion_id"],
        ),
    )
    scores = [score for score, _ in pairs]
    document: Dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "status": "disclosure_only_no_selection_gate",
        "builder_code_commit": commit,
        "source_selection": {
            "selection_manifest_id": selection["selection_manifest_id"],
            "selection_manifest_sha256": selection[
                "selection_manifest_sha256"
            ],
            "selection_file_sha256": selection_file_sha256,
        },
        "method": {
            "name": METHOD,
            "comparison_scope": "all_dev_by_test_selected_pairs",
            "normalization": "Unicode NFKC then casefold",
            "tokenization": "unique Unicode alphanumeric word tokens; underscores excluded",
            "similarity": "set intersection size divided by set union size",
            "distribution_quantile": "nearest_rank",
            "top_k": TOP_K,
            "selection_effect": "none",
            "limitations": [
                "Lexical overlap is not semantic equivalence.",
                "Paraphrases with different vocabulary may receive low overlap.",
                "The diagnostic does not change split membership or benchmark eligibility.",
            ],
        },
        "counts": {
            "dev_criteria": len(by_split["dev"]),
            "test_criteria": len(by_split["test"]),
            "cross_split_pairs_evaluated": len(pairs),
            "reported_top_pairs": min(TOP_K, len(pairs)),
        },
        "distribution": {
            "maximum": _as_float(max(scores)),
            **{
                f"p{percentile}": _as_float(
                    _nearest_rank(scores, percentile)
                )
                for percentile in PERCENTILES
            },
        },
        "top_pairs": [record for _, record in ranked[:TOP_K]],
    }
    report_sha256 = _canonical_hash(document)
    document["report_id"] = f"decomposition-overlap-{report_sha256[:16]}"
    document["report_sha256"] = report_sha256
    validate_document(document, REPORT_SCHEMA)
    return document


def build_overlap_diagnostic_from_path(
    selection_path: Path,
) -> Dict[str, Any]:
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    return build_overlap_diagnostic(selection, _file_sha256(selection_path))


def validate_overlap_diagnostic(
    selection_path: Path,
    report: Dict[str, Any],
) -> None:
    validate_document(report, REPORT_SCHEMA)
    unsigned = dict(report)
    report_id = unsigned.pop("report_id")
    report_sha256 = unsigned.pop("report_sha256")
    expected_sha256 = _canonical_hash(unsigned)
    if report_sha256 != expected_sha256:
        raise DecompositionOverlapError("Overlap report hash mismatch")
    if report_id != f"decomposition-overlap-{expected_sha256[:16]}":
        raise DecompositionOverlapError("Overlap report ID mismatch")

    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    expected = build_overlap_diagnostic(
        selection,
        _file_sha256(selection_path),
        builder_code_commit=report["builder_code_commit"],
    )
    if report != expected:
        raise DecompositionOverlapError(
            "Overlap report does not reproduce from the selection manifest"
        )

