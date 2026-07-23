import hashlib
import json
import random
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .fixture import SyntheticFixture, load_fixture
from .validation import validate_document


MANIFEST_VERSION = "1.0.0"
MANIFEST_SCHEMA_RESOURCE = "schemas/split-manifest-1.0.0.schema.json"
SUPPORTED_STRATEGIES = ("patient_holdout", "trial_holdout", "joint_holdout")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_identifiers(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_identifiers(item)
            for key, item in value.items()
            if not key.endswith("_id") and not key.endswith("_ids")
        }
    if isinstance(value, list):
        return [_without_identifiers(item) for item in value]
    return value


def _content_hash(value: Any) -> str:
    return canonical_sha256(_without_identifiers(value))


def current_git_commit(repo_root: Path = Path(".")) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked_changes = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--"],
        cwd=repo_root,
        check=False,
    )
    staged_changes = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
        check=False,
    )
    if tracked_changes.returncode or staged_changes.returncode:
        raise ValueError(
            "Tracked Git changes are present; commit them before creating "
            "a reproducible manifest or report"
        )
    return result.stdout.strip()


@dataclass(frozen=True)
class SplitPartition:
    entity_ids: Mapping[str, Tuple[str, ...]]
    content_sha256: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class SplitManifest:
    manifest_version: str
    manifest_sha256: str
    dataset_id: str
    dataset_schema_version: str
    dataset_sha256: str
    parent_dataset_sha256: Tuple[str, ...]
    strategy: str
    isolated_dimensions: Tuple[str, ...]
    semantic_similarity_threshold: float
    seed: int
    generated_at: str
    code_commit: str
    generation_command: str
    splits: Mapping[str, SplitPartition]

    def partition(self, name: str) -> SplitPartition:
        try:
            return self.splits[name]
        except KeyError as error:
            raise ValueError(f"Unknown split: {name}") from error


@dataclass(frozen=True)
class SemanticNearDuplicate:
    dimension: str
    left_id: str
    right_id: str
    similarity: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.similarity <= 1.0:
            raise ValueError("Semantic similarity must be between 0 and 1")
        if not self.dimension or not self.left_id or not self.right_id:
            raise ValueError("Semantic pair fields must be non-empty")


def semantic_pairs_from_embeddings(
    dimension: str,
    embeddings: Mapping[str, Sequence[float]],
    threshold: float,
) -> Tuple[SemanticNearDuplicate, ...]:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("Semantic threshold must be between 0 and 1")
    if not dimension:
        raise ValueError("Semantic dimension must be non-empty")

    normalized: Dict[str, Tuple[float, ...]] = {}
    vector_length = None
    for entity_id, vector in embeddings.items():
        values = tuple(float(value) for value in vector)
        if not entity_id or not values:
            raise ValueError("Embedding IDs and vectors must be non-empty")
        if vector_length is None:
            vector_length = len(values)
        elif len(values) != vector_length:
            raise ValueError("All embedding vectors must have equal length")
        norm = sum(value * value for value in values) ** 0.5
        if norm == 0:
            raise ValueError("Embedding vectors must have non-zero norm")
        normalized[entity_id] = tuple(value / norm for value in values)

    ids = tuple(sorted(normalized))
    pairs = []
    for left_index, left_id in enumerate(ids):
        for right_id in ids[left_index + 1 :]:
            similarity = sum(
                left * right
                for left, right in zip(
                    normalized[left_id],
                    normalized[right_id],
                )
            )
            if similarity >= threshold:
                pairs.append(
                    SemanticNearDuplicate(
                        dimension=dimension,
                        left_id=left_id,
                        right_id=right_id,
                        similarity=similarity,
                    )
                )
    return tuple(pairs)


def _partition(values: Sequence[str], seed: int, test_fraction: float) -> Tuple:
    if len(values) < 2:
        raise ValueError("A holdout dimension requires at least two entities")
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between 0 and 1")
    shuffled = list(values)
    random.Random(seed).shuffle(shuffled)
    test_count = max(1, min(len(shuffled) - 1, round(len(shuffled) * test_fraction)))
    test = tuple(sorted(shuffled[:test_count]))
    train = tuple(sorted(shuffled[test_count:]))
    return train, test


def _criterion_ids(
    fixture: SyntheticFixture, trial_ids: Iterable[str]
) -> Tuple[str, ...]:
    selected = set(trial_ids)
    return tuple(
        sorted(
            criterion.criterion_id
            for trial in fixture.trials
            if trial.trial_id in selected
            for criterion in trial.criteria
        )
    )


def _entity_records(document: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    patients = {
        item["patient_id"]: item
        for item in document["patients"]
    }
    trials = {item["trial_id"]: item for item in document["trials"]}
    criteria = {
        criterion["criterion_id"]: criterion
        for trial in document["trials"]
        for criterion in trial["criteria"]
    }
    return {
        "patient": patients,
        "trial": trials,
        "criterion": criteria,
    }


def _partition_document(
    entity_ids: Mapping[str, Sequence[str]],
    entity_records: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    ids = {
        dimension: sorted(values)
        for dimension, values in sorted(entity_ids.items())
    }
    hashes = {
        dimension: {
            entity_id: _content_hash(entity_records[dimension][entity_id])
            for entity_id in values
        }
        for dimension, values in ids.items()
    }
    return {"entity_ids": ids, "content_sha256": hashes}


def _manifest_hash(document: Dict[str, Any]) -> str:
    unsigned = dict(document)
    unsigned.pop("manifest_sha256", None)
    return canonical_sha256(unsigned)


def generate_split_manifest(
    fixture_path: Path,
    strategy: str,
    seed: int,
    test_fraction: float,
    dataset_id: str,
    code_commit: Optional[str] = None,
    generated_at: Optional[str] = None,
    generation_command: Optional[str] = None,
    parent_dataset_sha256: Sequence[str] = (),
) -> Dict[str, Any]:
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(f"Unsupported split strategy: {strategy}")
    raw: Dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture = load_fixture(fixture_path)
    patient_ids = tuple(sorted(item.patient_id for item in fixture.patients))
    trial_ids = tuple(sorted(item.trial_id for item in fixture.trials))

    if strategy in {"patient_holdout", "joint_holdout"}:
        train_patients, test_patients = _partition(
            patient_ids, seed, test_fraction
        )
    else:
        train_patients = test_patients = patient_ids

    if strategy in {"trial_holdout", "joint_holdout"}:
        train_trials, test_trials = _partition(
            trial_ids, seed + 1, test_fraction
        )
    else:
        train_trials = test_trials = trial_ids

    isolated = {
        "patient_holdout": ["patient"],
        "trial_holdout": ["trial", "criterion"],
        "joint_holdout": ["patient", "trial", "criterion"],
    }[strategy]
    entity_records = _entity_records(raw)
    split_ids = {
        "train": {
            "patient": train_patients,
            "trial": train_trials,
            "criterion": _criterion_ids(fixture, train_trials),
        },
        "test": {
            "patient": test_patients,
            "trial": test_trials,
            "criterion": _criterion_ids(fixture, test_trials),
        },
    }
    document: Dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_sha256": "pending",
        "dataset": {
            "dataset_id": dataset_id,
            "schema_version": fixture.schema_version,
            "content_sha256": canonical_sha256(raw),
            "parent_content_sha256": list(parent_dataset_sha256),
        },
        "strategy": strategy,
        "isolated_dimensions": isolated,
        "semantic_similarity_threshold": 0.95,
        "seed": seed,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "code_commit": code_commit or current_git_commit(),
        "generation_command": generation_command
        or (
            "clinical-matcher-split --strategy "
            f"{strategy} --seed {seed} --test-fraction {test_fraction}"
        ),
        "splits": {
            name: _partition_document(ids, entity_records)
            for name, ids in split_ids.items()
        },
    }
    document["manifest_sha256"] = _manifest_hash(document)
    validate_document(document, MANIFEST_SCHEMA_RESOURCE)
    return document


def load_split_manifest(path: Path) -> SplitManifest:
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    validate_document(document, MANIFEST_SCHEMA_RESOURCE)
    expected_hash = _manifest_hash(document)
    if document["manifest_sha256"] != expected_hash:
        raise ValueError(
            "Split manifest hash mismatch: the manifest was modified"
        )
    manifest = SplitManifest(
        manifest_version=document["manifest_version"],
        manifest_sha256=document["manifest_sha256"],
        dataset_id=document["dataset"]["dataset_id"],
        dataset_schema_version=document["dataset"]["schema_version"],
        dataset_sha256=document["dataset"]["content_sha256"],
        parent_dataset_sha256=tuple(
            document["dataset"]["parent_content_sha256"]
        ),
        strategy=document["strategy"],
        isolated_dimensions=tuple(document["isolated_dimensions"]),
        semantic_similarity_threshold=document[
            "semantic_similarity_threshold"
        ],
        seed=document["seed"],
        generated_at=document["generated_at"],
        code_commit=document["code_commit"],
        generation_command=document["generation_command"],
        splits={
            name: SplitPartition(
                entity_ids={
                    dimension: tuple(ids)
                    for dimension, ids in item["entity_ids"].items()
                },
                content_sha256=item["content_sha256"],
            )
            for name, item in document["splits"].items()
        },
    )
    assert_no_split_leakage(manifest)
    return manifest


def assert_dataset_matches(
    manifest: SplitManifest, fixture_path: Path
) -> None:
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    actual = canonical_sha256(raw)
    if actual != manifest.dataset_sha256:
        raise ValueError(
            "Dataset fingerprint does not match the split manifest"
        )


def assert_no_split_leakage(
    manifest: SplitManifest,
    semantic_pairs: Iterable[SemanticNearDuplicate] = (),
) -> None:
    split_names = tuple(manifest.splits)
    problems = []
    for dimension in manifest.isolated_dimensions:
        for split_name in split_names:
            if dimension not in manifest.splits[split_name].entity_ids:
                problems.append(
                    f"{dimension}: missing membership in {split_name}"
                )
        for left_index, left_name in enumerate(split_names):
            left = manifest.splits[left_name]
            for right_name in split_names[left_index + 1 :]:
                right = manifest.splits[right_name]
                repeated_ids = set(left.entity_ids.get(dimension, ())) & set(
                    right.entity_ids.get(dimension, ())
                )
                if repeated_ids:
                    problems.append(
                        f"{dimension}: IDs cross {left_name}/{right_name}: "
                        f"{sorted(repeated_ids)}"
                    )
                left_hashes = set(
                    left.content_sha256.get(dimension, {}).values()
                )
                right_hashes = set(
                    right.content_sha256.get(dimension, {}).values()
                )
                repeated_hashes = left_hashes & right_hashes
                if repeated_hashes:
                    problems.append(
                        f"{dimension}: exact content duplicates cross "
                        f"{left_name}/{right_name}"
                    )

    memberships = {
        dimension: {
            entity_id: split_name
            for split_name, split in manifest.splits.items()
            for entity_id in split.entity_ids.get(dimension, ())
        }
        for dimension in manifest.isolated_dimensions
    }
    for pair in semantic_pairs:
        if pair.dimension not in manifest.isolated_dimensions:
            continue
        known = memberships[pair.dimension]
        unknown = {
            item_id
            for item_id in (pair.left_id, pair.right_id)
            if item_id not in known
        }
        if unknown:
            problems.append(
                f"{pair.dimension}: semantic scan contains unknown IDs "
                f"{sorted(unknown)}"
            )
        elif (
            known[pair.left_id] != known[pair.right_id]
            and pair.similarity >= manifest.semantic_similarity_threshold
        ):
            problems.append(
                f"{pair.dimension}: semantic near-duplicate crosses "
                f"{known[pair.left_id]}/{known[pair.right_id]} at "
                f"{pair.similarity:.4f}"
            )

    if problems:
        raise ValueError("Split leakage detected:\n- " + "\n- ".join(problems))
