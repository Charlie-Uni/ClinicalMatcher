import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .apixaban_benchmark import (
    EXPECTED_OFFICIAL_COUNTS,
    OFFICIAL_SOURCE_SHA256,
    file_sha256,
    validate_apixaban_benchmark,
    validate_apixaban_benchmark_manifest,
    verify_apixaban_benchmark_files,
)
from .apixaban_contract import load_question_catalog
from .apixaban_quality import validate_restricted_quality_report
from .ingestion.apixaban import (
    validate_apixaban_id_map,
    validate_apixaban_import_manifest,
    validate_apixaban_staging_corpus,
)
from .ingestion.patients import assert_restricted_local_path
from .splits import (
    SplitManifest,
    SplitPartition,
    canonical_sha256,
    current_git_commit,
)
from .validation import validate_document


SPLIT_VERSION = "1.0.0"
SPLIT_SCHEMA = "schemas/apixaban-split-manifest-1.0.0.schema.json"
SEMANTIC_SCHEMA = "schemas/semantic-scan-summary-1.0.0.schema.json"
SPLIT_NAMES = ("train", "validation", "test")
ALGORITHM_NAME = "grouped_multilabel_greedy"
ALGORITHM_VERSION = "1.1.0"


class ApixabanSplitError(ValueError):
    """Raised when split generation, isolation, or freezing is invalid."""


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def _serialized(document: Dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _source_status(assessment: Mapping[str, Any]) -> str:
    if not assessment["abstained"]:
        return "answered"
    if assessment["abstention_reason"] == "source_not_specified":
        return "not_specified"
    if assessment["abstention_reason"] == "source_anomaly":
        return "source_anomaly"
    raise ApixabanSplitError("Unsupported released-label abstention reason")


def _patient_content_hash(patient: Mapping[str, Any]) -> str:
    content = [
        {
            "source_span": evidence["source_span"],
            "text": evidence["text"],
        }
        for evidence in patient["evidence"]
    ]
    return canonical_sha256(content)


def _target_counts(
    patient_count: int, fractions: Mapping[str, float]
) -> Dict[str, int]:
    if set(fractions) != set(SPLIT_NAMES):
        raise ApixabanSplitError(
            "Fractions must define train, validation, and test"
        )
    if any(value <= 0 or value >= 1 for value in fractions.values()):
        raise ApixabanSplitError("Every split fraction must be between 0 and 1")
    if not math.isclose(sum(fractions.values()), 1.0, abs_tol=1e-12):
        raise ApixabanSplitError("Split fractions must sum to 1")
    raw = {name: patient_count * fractions[name] for name in SPLIT_NAMES}
    counts = {name: math.floor(raw[name]) for name in SPLIT_NAMES}
    remainder = patient_count - sum(counts.values())
    order = sorted(
        SPLIT_NAMES,
        key=lambda name: (-(raw[name] - counts[name]), SPLIT_NAMES.index(name)),
    )
    for name in order[:remainder]:
        counts[name] += 1
    if any(value < 1 for value in counts.values()):
        raise ApixabanSplitError("Every split must contain at least one patient")
    return counts


def _stable_tie(seed: int, value: str) -> str:
    return hashlib.sha256(
        f"{ALGORITHM_VERSION}\0{seed}\0{value}".encode("utf-8")
    ).hexdigest()


def _patient_tokens(
    assessments: Iterable[Mapping[str, Any]],
) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for item in assessments:
        question_id = item["question_id"]
        tokens[f"{question_id}|fact={item['fact_status']}"] += 1
        tokens[f"{question_id}|source={_source_status(item)}"] += 1
    return tokens


def _group_patients(
    patient_ids: Sequence[str],
    admission_by_patient: Mapping[str, str],
    content_hash_by_patient: Mapping[str, str],
) -> Tuple[Tuple[str, ...], ...]:
    union = _UnionFind(patient_ids)
    for values in (admission_by_patient, content_hash_by_patient):
        grouped: Dict[str, List[str]] = defaultdict(list)
        for patient_id, value in values.items():
            grouped[value].append(patient_id)
        for members in grouped.values():
            for patient_id in members[1:]:
                union.union(members[0], patient_id)
    groups: Dict[str, List[str]] = defaultdict(list)
    for patient_id in patient_ids:
        groups[union.find(patient_id)].append(patient_id)
    return tuple(
        sorted((tuple(sorted(members)) for members in groups.values()))
    )


def _assignment_cost(
    candidate: str,
    group_tokens: Counter[str],
    current_tokens: Mapping[str, Counter[str]],
    global_tokens: Counter[str],
    fractions: Mapping[str, float],
) -> float:
    cost = 0.0
    for split_name in SPLIT_NAMES:
        values = current_tokens[split_name]
        for token, total in global_tokens.items():
            observed = values[token]
            if split_name == candidate:
                observed += group_tokens[token]
            cost += _token_cost(
                observed, total, fractions[split_name]
            )
    return cost


def _token_cost(observed: int, total: int, fraction: float) -> float:
    target = total * fraction
    return (
        ((observed - target) ** 2) / max(total, 1)
        + (25.0 if observed == 0 else 0.0)
    )


def _improve_group_assignment(
    assigned_groups: Dict[str, List[Tuple[str, ...]]],
    group_tokens: Mapping[Tuple[str, ...], Counter[str]],
    current_tokens: Dict[str, Counter[str]],
    global_tokens: Counter[str],
    fractions: Mapping[str, float],
    seed: int,
) -> None:
    for _ in range(1000):
        best = None
        for left_index, left_name in enumerate(SPLIT_NAMES):
            for right_name in SPLIT_NAMES[left_index + 1 :]:
                for left_group in assigned_groups[left_name]:
                    for right_group in assigned_groups[right_name]:
                        if len(left_group) != len(right_group):
                            continue
                        affected = set(group_tokens[left_group]) | set(
                            group_tokens[right_group]
                        )
                        delta = 0.0
                        for token in affected:
                            total = global_tokens[token]
                            left_before = current_tokens[left_name][token]
                            right_before = current_tokens[right_name][token]
                            left_after = (
                                left_before
                                - group_tokens[left_group][token]
                                + group_tokens[right_group][token]
                            )
                            right_after = (
                                right_before
                                - group_tokens[right_group][token]
                                + group_tokens[left_group][token]
                            )
                            delta += _token_cost(
                                left_after, total, fractions[left_name]
                            )
                            delta += _token_cost(
                                right_after, total, fractions[right_name]
                            )
                            delta -= _token_cost(
                                left_before, total, fractions[left_name]
                            )
                            delta -= _token_cost(
                                right_before, total, fractions[right_name]
                            )
                        tie = _stable_tie(
                            seed,
                            f"swap|{left_name}|{'|'.join(left_group)}|"
                            f"{right_name}|{'|'.join(right_group)}",
                        )
                        candidate = (
                            delta,
                            tie,
                            left_name,
                            right_name,
                            left_group,
                            right_group,
                        )
                        if best is None or candidate < best:
                            best = candidate
        if best is None or best[0] >= -1e-12:
            return
        _, _, left_name, right_name, left_group, right_group = best
        assigned_groups[left_name].remove(left_group)
        assigned_groups[right_name].remove(right_group)
        assigned_groups[left_name].append(right_group)
        assigned_groups[right_name].append(left_group)
        current_tokens[left_name].subtract(group_tokens[left_group])
        current_tokens[left_name].update(group_tokens[right_group])
        current_tokens[right_name].subtract(group_tokens[right_group])
        current_tokens[right_name].update(group_tokens[left_group])
    raise ApixabanSplitError("Label-balance swap optimization did not converge")


def _assign_groups(
    groups: Sequence[Tuple[str, ...]],
    tokens_by_patient: Mapping[str, Counter[str]],
    fractions: Mapping[str, float],
    targets: Mapping[str, int],
    seed: int,
) -> Dict[str, Tuple[str, ...]]:
    global_tokens: Counter[str] = Counter()
    for tokens in tokens_by_patient.values():
        global_tokens.update(tokens)
    group_tokens = {
        group: sum(
            (tokens_by_patient[patient_id] for patient_id in group),
            Counter(),
        )
        for group in groups
    }

    def rarity(group: Tuple[str, ...]) -> float:
        return sum(
            count / global_tokens[token]
            for token, count in group_tokens[group].items()
        )

    ordered = sorted(
        groups,
        key=lambda group: (
            -len(group),
            -rarity(group),
            _stable_tie(seed, "|".join(group)),
        ),
    )
    assigned_groups: Dict[str, List[Tuple[str, ...]]] = {
        name: [] for name in SPLIT_NAMES
    }
    current_tokens = {name: Counter() for name in SPLIT_NAMES}
    for group in ordered:
        candidates = [
            name
            for name in SPLIT_NAMES
            if sum(len(item) for item in assigned_groups[name]) + len(group)
            <= targets[name]
        ]
        if not candidates:
            raise ApixabanSplitError(
                "Grouped constraints cannot satisfy requested split sizes"
            )
        selected = min(
            candidates,
            key=lambda name: (
                _assignment_cost(
                    name,
                    group_tokens[group],
                    current_tokens,
                    global_tokens,
                    fractions,
                ),
                _stable_tie(seed, f"{name}|{'|'.join(group)}"),
            ),
        )
        assigned_groups[selected].append(group)
        current_tokens[selected].update(group_tokens[group])
    _improve_group_assignment(
        assigned_groups,
        group_tokens,
        current_tokens,
        global_tokens,
        fractions,
        seed,
    )
    assigned = {
        name: [
            patient_id
            for group in assigned_groups[name]
            for patient_id in group
        ]
        for name in SPLIT_NAMES
    }
    if {name: len(values) for name, values in assigned.items()} != dict(targets):
        raise ApixabanSplitError("Generated split sizes do not match targets")
    return {name: tuple(sorted(values)) for name, values in assigned.items()}


def _status_counts(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    items = list(rows)
    fact = Counter(item["fact_status"] for item in items)
    source = Counter(_source_status(item) for item in items)
    return {
        "fact_status_counts": {
            name: fact[name] for name in ("present", "absent", "unknown")
        },
        "source_status_counts": {
            name: source[name]
            for name in ("answered", "not_specified", "source_anomaly")
        },
    }


def _balance_report(
    assignments: Mapping[str, Sequence[str]],
    assessments: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    split_by_patient = {
        patient_id: split_name
        for split_name, patient_ids in assignments.items()
        for patient_id in patient_ids
    }
    by_question: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    by_question_split: Dict[Tuple[str, str], List[Mapping[str, Any]]] = (
        defaultdict(list)
    )
    for item in assessments:
        by_question[item["question_id"]].append(item)
        by_question_split[
            (item["question_id"], split_by_patient[item["patient_id"]])
        ].append(item)
    questions = []
    deviations = []
    zero_support = 0
    unavoidable_zero_support = 0
    for question_id in sorted(by_question):
        overall = _status_counts(by_question[question_id])
        split_counts = {
            name: _status_counts(by_question_split[(question_id, name)])
            for name in SPLIT_NAMES
        }
        for group_name in ("fact_status_counts", "source_status_counts"):
            for label, overall_count in overall[group_name].items():
                if overall_count == 0:
                    continue
                unavoidable_zero_support += max(
                    0, len(SPLIT_NAMES) - min(overall_count, len(SPLIT_NAMES))
                )
                overall_rate = overall_count / len(by_question[question_id])
                for split_name in SPLIT_NAMES:
                    split_count = split_counts[split_name][group_name][label]
                    if split_count == 0:
                        zero_support += 1
                    deviations.append(
                        abs(
                            split_count / len(assignments[split_name])
                            - overall_rate
                        )
                    )
        questions.append(
            {
                "question_id": question_id,
                "overall": overall,
                "splits": split_counts,
            }
        )
    return {
        "status": "reported_not_thresholded",
        "maximum_absolute_prevalence_deviation": max(deviations, default=0.0),
        "mean_absolute_prevalence_deviation": (
            sum(deviations) / len(deviations) if deviations else 0.0
        ),
        "zero_support_split_label_cell_count": zero_support,
        "minimum_unavoidable_zero_support_cell_count": (
            unavoidable_zero_support
        ),
        "excess_zero_support_cell_count": (
            zero_support - unavoidable_zero_support
        ),
        "questions": questions,
    }


def validate_apixaban_split_manifest(
    document: Dict[str, Any],
    *,
    expected_patient_ids: Optional[Iterable[str]] = None,
) -> None:
    validate_document(document, SPLIT_SCHEMA)
    if _manifest_hash(document) != document["manifest_sha256"]:
        raise ApixabanSplitError("Apixaban split manifest hash mismatch")
    fractions = document["policy"]["requested_fractions"]
    if not math.isclose(sum(fractions.values()), 1.0, abs_tol=1e-12):
        raise ApixabanSplitError("Split fractions do not sum to 1")
    targets = document["policy"]["target_patient_counts"]
    memberships = {
        name: partition["patient_ids"]
        for name, partition in document["splits"].items()
    }
    all_ids = [patient_id for values in memberships.values() for patient_id in values]
    if len(all_ids) != len(set(all_ids)):
        raise ApixabanSplitError("Patient IDs cross split boundaries")
    if expected_patient_ids is not None and set(all_ids) != set(
        expected_patient_ids
    ):
        raise ApixabanSplitError("Split membership does not cover benchmark")
    if targets != _target_counts(len(all_ids), fractions):
        raise ApixabanSplitError(
            "Target patient counts do not match requested fractions"
        )
    for name, partition in document["splits"].items():
        if partition["patient_ids"] != sorted(partition["patient_ids"]):
            raise ApixabanSplitError("Split patient IDs must be sorted")
        if partition["patient_count"] != len(partition["patient_ids"]):
            raise ApixabanSplitError("Partition patient count is incorrect")
        if partition["patient_count"] != targets[name]:
            raise ApixabanSplitError("Partition size does not match target")
        if set(partition["patient_content_sha256"]) != set(
            partition["patient_ids"]
        ):
            raise ApixabanSplitError("Patient content hashes are incomplete")
    content_memberships: Dict[str, set] = defaultdict(set)
    for split_name, partition in document["splits"].items():
        for content_hash in partition["patient_content_sha256"].values():
            content_memberships[content_hash].add(split_name)
    if any(len(values) > 1 for values in content_memberships.values()):
        raise ApixabanSplitError("Exact note content duplicates cross splits")
    catalog_ids = sorted(
        question["question_id"] for question in load_question_catalog()["questions"]
    )
    balance = document["balance"]
    if [item["question_id"] for item in balance["questions"]] != catalog_ids:
        raise ApixabanSplitError("Balance report does not cover frozen catalog")
    deviations = []
    zero_support = 0
    unavoidable_zero_support = 0
    total_patients = len(all_ids)
    for question in balance["questions"]:
        for group_name in ("fact_status_counts", "source_status_counts"):
            overall = question["overall"][group_name]
            if sum(overall.values()) != total_patients:
                raise ApixabanSplitError(
                    "Overall question balance does not reconcile"
                )
            for split_name in SPLIT_NAMES:
                split_values = question["splits"][split_name][group_name]
                if sum(split_values.values()) != targets[split_name]:
                    raise ApixabanSplitError(
                        "Split question balance does not reconcile"
                    )
            for label, overall_count in overall.items():
                if sum(
                    question["splits"][split_name][group_name][label]
                    for split_name in SPLIT_NAMES
                ) != overall_count:
                    raise ApixabanSplitError(
                        "Split label counts do not sum to overall"
                    )
                if overall_count == 0:
                    continue
                unavoidable_zero_support += max(
                    0, len(SPLIT_NAMES) - min(overall_count, len(SPLIT_NAMES))
                )
                overall_rate = overall_count / total_patients
                for split_name in SPLIT_NAMES:
                    split_count = question["splits"][split_name][group_name][
                        label
                    ]
                    if split_count == 0:
                        zero_support += 1
                    deviations.append(
                        abs(split_count / targets[split_name] - overall_rate)
                    )
    expected_max = max(deviations, default=0.0)
    expected_mean = sum(deviations) / len(deviations) if deviations else 0.0
    if not math.isclose(
        balance["maximum_absolute_prevalence_deviation"], expected_max
    ) or not math.isclose(
        balance["mean_absolute_prevalence_deviation"], expected_mean
    ):
        raise ApixabanSplitError("Balance deviation metrics are incorrect")
    if balance["zero_support_split_label_cell_count"] != zero_support:
        raise ApixabanSplitError("Zero-support balance count is incorrect")
    if balance["minimum_unavoidable_zero_support_cell_count"] != (
        unavoidable_zero_support
    ) or balance["excess_zero_support_cell_count"] != (
        zero_support - unavoidable_zero_support
    ):
        raise ApixabanSplitError("Zero-support feasibility counts are incorrect")
    status = document["status"]
    freeze = document["freeze"]
    if status == "candidate":
        if freeze != {
            "test_locked": False,
            "decision_reference": None,
            "audited_candidate_manifest_sha256": None,
            "semantic_scan_summary_sha256": None,
        } or document["isolation"]["semantic_scan_status"] != "pending":
            raise ApixabanSplitError("Candidate freeze state is invalid")
    else:
        if (
            not freeze["test_locked"]
            or not freeze["decision_reference"]
            or not freeze["audited_candidate_manifest_sha256"]
            or not freeze["semantic_scan_summary_sha256"]
            or document["isolation"]["semantic_scan_status"] != "passed"
        ):
            raise ApixabanSplitError("Frozen manifest lacks audit provenance")


def split_manifest_view(document: Dict[str, Any]) -> SplitManifest:
    validate_apixaban_split_manifest(document)
    return SplitManifest(
        manifest_version="1.0.0",
        manifest_sha256=document["manifest_sha256"],
        dataset_id=document["dataset"]["dataset_id"],
        dataset_schema_version=document["dataset"]["dataset_version"],
        dataset_sha256=document["dataset"]["benchmark_sha256"],
        parent_dataset_sha256=(
            document["dataset"]["staging_corpus_sha256"],
        ),
        strategy="patient_holdout",
        isolated_dimensions=("patient",),
        semantic_similarity_threshold=document["policy"][
            "semantic_similarity_threshold"
        ],
        seed=document["policy"]["seed"],
        generated_at=document["generated_at"],
        code_commit=document["code_commit"],
        generation_command=document["generation_command"],
        splits={
            name: SplitPartition(
                entity_ids={"patient": tuple(partition["patient_ids"])},
                content_sha256={
                    "patient": partition["patient_content_sha256"]
                },
            )
            for name, partition in document["splits"].items()
        },
    )


def build_apixaban_split_candidate(
    benchmark: Dict[str, Any],
    benchmark_manifest: Dict[str, Any],
    staging_corpus: Dict[str, Any],
    import_manifest: Dict[str, Any],
    id_map: Dict[str, Any],
    quality_report: Dict[str, Any],
    *,
    fractions: Mapping[str, float],
    seed: int,
    semantic_similarity_threshold: float = 0.95,
    generated_at: Optional[str] = None,
    code_commit: Optional[str] = None,
    generation_command: Optional[str] = None,
    required_source_sha256: Optional[str] = OFFICIAL_SOURCE_SHA256,
    required_counts: Optional[Dict[str, int]] = EXPECTED_OFFICIAL_COUNTS,
) -> Dict[str, Any]:
    if seed < 0:
        raise ApixabanSplitError("Seed must be non-negative")
    if not 0 <= semantic_similarity_threshold <= 1:
        raise ApixabanSplitError("Semantic threshold must be between 0 and 1")
    validate_apixaban_benchmark(
        benchmark,
        required_source_sha256=required_source_sha256,
        required_counts=required_counts,
    )
    validate_apixaban_benchmark_manifest(benchmark_manifest)
    validate_restricted_quality_report(
        quality_report, required_counts=required_counts
    )
    validate_apixaban_staging_corpus(staging_corpus)
    validate_apixaban_import_manifest(import_manifest)
    validate_apixaban_id_map(id_map)
    if benchmark_manifest["output"]["benchmark_sha256"] != quality_report[
        "source"
    ]["benchmark_sha256"]:
        raise ApixabanSplitError("Quality report references another benchmark")
    if benchmark["source"]["staging_corpus_sha256"] != import_manifest[
        "outputs"
    ]["corpus_sha256"]:
        raise ApixabanSplitError("Staging provenance does not match benchmark")
    if import_manifest["outputs"]["id_map_sha256"] != hashlib.sha256(
        _serialized(id_map)
    ).hexdigest():
        raise ApixabanSplitError("ID map does not match import manifest")
    patient_ids = tuple(benchmark["patient_ids"])
    sets = [
        set(patient_ids),
        {item["patient_id"] for item in staging_corpus["patients"]},
        {item["patient_id"] for item in id_map["records"]},
    ]
    if not all(value == sets[0] for value in sets[1:]):
        raise ApixabanSplitError("Benchmark, staging, and ID map patients differ")

    assessments_by_patient: Dict[str, List[Mapping[str, Any]]] = defaultdict(
        list
    )
    for assessment in benchmark["assessments"]:
        assessments_by_patient[assessment["patient_id"]].append(assessment)
    tokens_by_patient = {
        patient_id: _patient_tokens(assessments_by_patient[patient_id])
        for patient_id in patient_ids
    }
    content_hash_by_patient = {
        patient["patient_id"]: _patient_content_hash(patient)
        for patient in staging_corpus["patients"]
    }
    admission_by_patient = {
        record["patient_id"]: record["hadm_id"]
        for record in id_map["records"]
    }
    groups = _group_patients(
        patient_ids, admission_by_patient, content_hash_by_patient
    )
    targets = _target_counts(len(patient_ids), fractions)
    assignments = _assign_groups(
        groups, tokens_by_patient, fractions, targets, seed
    )

    def group_stats(values: Mapping[str, str]) -> Tuple[int, int]:
        counts = Counter(values.values())
        return len(counts), sum(count > 1 for count in counts.values())

    admission_groups, multi_admission = group_stats(admission_by_patient)
    content_groups, multi_content = group_stats(content_hash_by_patient)
    document: Dict[str, Any] = {
        "apixaban_split_manifest_version": SPLIT_VERSION,
        "manifest_sha256": "pending",
        "status": "candidate",
        "generated_at": generated_at or _now(),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command
        or (
            "clinical-matcher-apixaban-split candidate --seed "
            f"{seed} --train-fraction {fractions['train']} "
            f"--validation-fraction {fractions['validation']} "
            f"--test-fraction {fractions['test']}"
        ),
        "dataset": {
            "dataset_id": benchmark["source"]["dataset_id"],
            "dataset_version": benchmark["source"]["dataset_version"],
            "benchmark_sha256": benchmark_manifest["output"][
                "benchmark_sha256"
            ],
            "benchmark_manifest_sha256": benchmark_manifest[
                "manifest_sha256"
            ],
            "staging_corpus_sha256": import_manifest["outputs"][
                "corpus_sha256"
            ],
            "import_manifest_sha256": import_manifest["manifest_sha256"],
            "id_map_sha256": import_manifest["outputs"]["id_map_sha256"],
            "quality_report_sha256": quality_report["report_sha256"],
            "question_catalog_sha256": benchmark["contract"][
                "question_catalog_sha256"
            ],
        },
        "policy": {
            "algorithm": ALGORITHM_NAME,
            "algorithm_version": ALGORITHM_VERSION,
            "seed": seed,
            "requested_fractions": {
                name: fractions[name] for name in SPLIT_NAMES
            },
            "target_patient_counts": targets,
            "grouping_dimensions": [
                "patient_id",
                "admission_id",
                "exact_note_content",
            ],
            "label_features": [
                "question_fact_status",
                "question_source_status",
            ],
            "seed_selection_status": "predeclared_not_searched",
            "semantic_similarity_threshold": semantic_similarity_threshold,
        },
        "splits": {
            name: {
                "patient_count": len(assignments[name]),
                "patient_ids": list(assignments[name]),
                "patient_content_sha256": {
                    patient_id: content_hash_by_patient[patient_id]
                    for patient_id in assignments[name]
                },
            }
            for name in SPLIT_NAMES
        },
        "balance": _balance_report(
            assignments, benchmark["assessments"]
        ),
        "isolation": {
            "all_patients_assigned_once": True,
            "cross_split_patient_overlap_count": 0,
            "cross_split_admission_overlap_count": 0,
            "cross_split_exact_note_duplicate_count": 0,
            "admission_group_count": admission_groups,
            "multi_patient_admission_group_count": multi_admission,
            "exact_content_group_count": content_groups,
            "multi_patient_exact_content_group_count": multi_content,
            "semantic_scan_status": "pending",
        },
        "freeze": {
            "test_locked": False,
            "decision_reference": None,
            "audited_candidate_manifest_sha256": None,
            "semantic_scan_summary_sha256": None,
        },
        "disclosure_note": (
            "This manifest contains pseudonymous patient membership and note "
            "content fingerprints derived from restricted MIMIC data. Keep it "
            "local; only separately reviewed aggregate diagnostics may leave "
            "the authorized environment."
        ),
    }
    document["manifest_sha256"] = _manifest_hash(document)
    validate_apixaban_split_manifest(
        document, expected_patient_ids=patient_ids
    )
    return document


def build_apixaban_split_candidate_from_paths(
    benchmark_path: Path,
    benchmark_manifest_path: Path,
    staging_corpus_path: Path,
    import_manifest_path: Path,
    id_map_path: Path,
    quality_report_path: Path,
    **kwargs: Any,
) -> Dict[str, Any]:
    paths = (
        benchmark_path,
        benchmark_manifest_path,
        staging_corpus_path,
        import_manifest_path,
        id_map_path,
        quality_report_path,
    )
    for path in paths:
        assert_restricted_local_path(path)
        if path.stat().st_mode & 0o077:
            raise ApixabanSplitError(f"Restricted input is not owner-only: {path}")
    verify_apixaban_benchmark_files(
        benchmark_path, benchmark_manifest_path
    )
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    benchmark, benchmark_manifest, staging, import_manifest, id_map, quality = (
        documents
    )
    if file_sha256(staging_corpus_path) != import_manifest["outputs"][
        "corpus_sha256"
    ]:
        raise ApixabanSplitError("Staging file hash mismatch")
    if file_sha256(id_map_path) != import_manifest["outputs"]["id_map_sha256"]:
        raise ApixabanSplitError("ID-map file hash mismatch")
    return build_apixaban_split_candidate(
        benchmark,
        benchmark_manifest,
        staging,
        import_manifest,
        id_map,
        quality,
        **kwargs,
    )


def freeze_apixaban_split(
    candidate: Dict[str, Any],
    semantic_summary: Dict[str, Any],
    decision_reference: str,
) -> Dict[str, Any]:
    validate_apixaban_split_manifest(candidate)
    if candidate["status"] != "candidate":
        raise ApixabanSplitError("Only a candidate manifest can be frozen")
    if not decision_reference.strip():
        raise ApixabanSplitError("Freeze decision reference is required")
    validate_document(semantic_summary, SEMANTIC_SCHEMA)
    if semantic_summary["split_manifest_sha256"] != candidate[
        "manifest_sha256"
    ]:
        raise ApixabanSplitError("Semantic audit references another candidate")
    if semantic_summary["dataset_sha256"] != candidate["dataset"][
        "benchmark_sha256"
    ]:
        raise ApixabanSplitError("Semantic audit references another dataset")
    if semantic_summary["dimension"] != "patient" or not semantic_summary[
        "results"
    ]["leakage_assertion_passed"]:
        raise ApixabanSplitError("Patient semantic leakage audit did not pass")
    if semantic_summary["threshold"] != candidate["policy"][
        "semantic_similarity_threshold"
    ]:
        raise ApixabanSplitError("Semantic threshold differs from candidate")
    frozen = json.loads(json.dumps(candidate))
    audited_hash = candidate["manifest_sha256"]
    frozen["status"] = "frozen"
    frozen["isolation"]["semantic_scan_status"] = "passed"
    frozen["freeze"] = {
        "test_locked": True,
        "decision_reference": decision_reference,
        "audited_candidate_manifest_sha256": audited_hash,
        "semantic_scan_summary_sha256": canonical_sha256(semantic_summary),
    }
    frozen["manifest_sha256"] = _manifest_hash(frozen)
    validate_apixaban_split_manifest(frozen)
    for name in SPLIT_NAMES:
        if frozen["splits"][name] != candidate["splits"][name]:
            raise ApixabanSplitError("Freeze operation changed split membership")
    return frozen


def load_apixaban_split_manifest(path: Path) -> Dict[str, Any]:
    assert_restricted_local_path(path)
    if path.stat().st_mode & 0o077:
        raise ApixabanSplitError(f"Split manifest is not owner-only: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_apixaban_split_manifest(document)
    return document


def write_apixaban_split_document(
    document: Dict[str, Any], output_path: Path
) -> Path:
    assert_restricted_local_path(output_path)
    validate_apixaban_split_manifest(document)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite restricted split output: {output_path}"
        ) from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_serialized(document))
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def write_private_json(document: Dict[str, Any], output_path: Path) -> Path:
    assert_restricted_local_path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite restricted output: {output_path}"
        ) from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_serialized(document))
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    return output_path
