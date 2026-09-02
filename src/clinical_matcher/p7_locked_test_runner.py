"""Single-purpose P7 locked-test runner.

The public entry point checks the frozen authorization contract before it
resolves any restricted input. It has no split selector and no arm selector.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from .apixaban_abstention import run_deterministic_abstention
from .apixaban_benchmark import (
    file_sha256,
    validate_apixaban_benchmark,
    verify_apixaban_benchmark_files,
)
from .apixaban_deterministic import (
    build_deterministic_prediction_set,
    write_deterministic_prediction_set,
)
from .apixaban_error_attribution import run_error_attribution
from .apixaban_evaluation import (
    evaluate_apixaban_predictions,
    validate_prediction_set,
    write_apixaban_evaluation_report,
)
from .apixaban_single_trial_evaluation import (
    build_single_trial_evaluation_v1_1,
    load_mentor_reference,
    write_single_trial_evaluation,
)
from .apixaban_split import load_apixaban_split_manifest, write_private_json
from .apixaban_structured_llm import (
    load_long_context_contract,
    load_structured_llm_contract,
    run_structured_llm_baseline,
    write_structured_llm_run,
)
from .ingestion.apixaban import validate_apixaban_staging_corpus
from .ingestion.patients import assert_restricted_local_path
from .p7_locked_test import (
    BASE_ARM_IDS,
    VIEW_IDS,
    P7LockedTestError,
    build_batch_manifest,
    build_public_release_candidate,
    build_representative_case_package,
    build_request_latency_trace,
    build_state_event,
    load_p7_contract,
    require_locked_test_authorization,
    validate_p7_contract,
    validate_state_event,
    write_batch_manifest,
    write_private_text,
    write_request_latency_trace,
    write_state_event,
)


RawPhase = Callable[..., Dict[str, Any]]
GoldPhase = Callable[..., Dict[str, Any]]


def _private_directory(path: Path) -> Path:
    assert_restricted_local_path(path)
    if path.is_symlink():
        raise P7LockedTestError("P7 private directory must not be a symbolic link")
    if path.exists() and not path.is_dir():
        raise P7LockedTestError("P7 private directory path is not a directory")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise P7LockedTestError("P7 output escaped the batch root") from error
    return relative.as_posix()


def _artifact(root: Path, path: Path) -> Dict[str, str]:
    return {"path": _relative(root, path), "sha256": file_sha256(path)}


def _write_event(
    root: Path,
    *,
    sequence: int,
    event: str,
    contract_sha256: str,
    attempt: int,
    gold_backed_phase_started: bool,
    reason_code: Optional[str] = None,
    artifact_count: int = 0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    document = build_state_event(
        contract_sha256=contract_sha256,
        event=event,
        attempt=attempt,
        gold_backed_phase_started=gold_backed_phase_started,
        reason_code=reason_code,
        artifact_count=artifact_count,
    )
    path = root / "events" / f"{sequence:02d}-{event}.json"
    _private_directory(path.parent)
    write_state_event(document, path)
    artifact = {
        "event": event,
        "attempt": attempt,
        **_artifact(root, path),
    }
    return document, artifact


def _load_event_artifacts(root: Path, contract_sha256: str) -> list[Dict[str, Any]]:
    event_dir = root / "events"
    if not event_dir.exists():
        return []
    children = list(event_dir.iterdir())
    if any(
        child.is_symlink() or not child.is_file() or child.suffix != ".json"
        for child in children
    ):
        raise P7LockedTestError("P7 event directory contains an unknown entry")
    artifacts = []
    for path in sorted(children):
        document = json.loads(path.read_text(encoding="utf-8"))
        validate_state_event(document)
        if document["contract_sha256"] != contract_sha256:
            raise P7LockedTestError("P7 state belongs to a different contract")
        artifacts.append(
            {
                "event": document["event"],
                "attempt": document["attempt"],
                **_artifact(root, path),
            }
        )
    return artifacts


def _select_attempt(
    events: Sequence[Mapping[str, Any]], *, allow_pre_gold_retry: bool
) -> Tuple[int, int]:
    names = tuple(item["event"] for item in events)
    if not names:
        return 1, 1
    if names == ("attempt_started", "pre_gold_failed"):
        if not allow_pre_gold_retry:
            raise P7LockedTestError(
                "One pre-gold attempt failed; the single retry needs explicit approval"
            )
        return 2, 3
    if any(
        name in {"gold_phase_started", "terminal_failed", "batch_complete"}
        for name in names
    ):
        raise P7LockedTestError(
            "P7 gold-backed phase already started; rerun is permanently forbidden"
        )
    raise P7LockedTestError(
        "P7 has an incomplete pre-gold state; automatic resume or retry is forbidden"
    )


def _assert_input_file(path: Path, expected_sha256: str, name: str) -> None:
    assert_restricted_local_path(path)
    if path.is_symlink() or not path.is_file():
        raise P7LockedTestError(f"Missing restricted P7 {name} input")
    if path.stat().st_mode & 0o077:
        raise P7LockedTestError(f"Restricted P7 {name} input is not owner-only")
    if file_sha256(path) != expected_sha256:
        raise P7LockedTestError(f"Restricted P7 {name} hash mismatch")


def _prediction_population(
    prediction_set: Mapping[str, Any], contract: Mapping[str, Any]
) -> None:
    validate_prediction_set(dict(prediction_set))
    rows = prediction_set["predictions"]
    if len(rows) != contract["execution"]["expected_row_count"]:
        raise P7LockedTestError("P7 prediction row count changed")
    if len({item["patient_id"] for item in rows}) != contract["execution"][
        "expected_patient_count"
    ]:
        raise P7LockedTestError("P7 prediction patient count changed")
    if prediction_set["split_name"] != "test":
        raise P7LockedTestError("P7 prediction is not from the locked test split")


def run_raw_phase(
    *,
    contract: Dict[str, Any],
    batch_root: Path,
    attempt_directory: Path,
    frozen_split_path: Path,
    staging_corpus_path: Path,
    clients: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run the three frozen arms without opening benchmark labels."""

    del batch_root
    clients = clients or {}
    predictions: Dict[str, Dict[str, Any]] = {}
    prediction_paths: Dict[str, Path] = {}
    latency_traces: Dict[str, Dict[str, Any]] = {}
    base_artifacts = []

    arm_directory = _private_directory(attempt_directory / BASE_ARM_IDS[0])
    observed: list[Mapping[str, Any]] = []
    prediction = build_deterministic_prediction_set(
        frozen_split_path=frozen_split_path,
        staging_corpus_path=staging_corpus_path,
        split_name="test",
        request_observer=observed.append,
    )
    _prediction_population(prediction, contract)
    if prediction["rule_set_sha256"] != contract["base_arms"][0]["config_sha256"]:
        raise P7LockedTestError("P7 deterministic configuration changed")
    prediction_path = write_deterministic_prediction_set(
        prediction, arm_directory / "predictions.json"
    )
    latency_trace = build_request_latency_trace(
        arm_id=BASE_ARM_IDS[0], split_name="test", requests=observed
    )
    latency_path = write_request_latency_trace(
        latency_trace, arm_directory / "request-latency.json"
    )
    predictions[f"{BASE_ARM_IDS[0]}.raw"] = prediction
    prediction_paths[f"{BASE_ARM_IDS[0]}.raw"] = prediction_path
    latency_traces[BASE_ARM_IDS[0]] = latency_trace
    base_artifacts.append(
        {
            "arm_id": BASE_ARM_IDS[0],
            "prediction_path": prediction_path,
            "run_report_path": None,
            "latency_trace_path": latency_path,
        }
    )

    llm_specs = (
        (BASE_ARM_IDS[1], load_structured_llm_contract()),
        (BASE_ARM_IDS[2], load_long_context_contract()),
    )
    for arm_index, (arm_id, arm_contract) in enumerate(llm_specs, start=1):
        arm_directory = _private_directory(attempt_directory / arm_id)
        observed = []
        prediction, run_report = run_structured_llm_baseline(
            frozen_split_path=frozen_split_path,
            staging_corpus_path=staging_corpus_path,
            split_name="test",
            client=clients.get(arm_id),
            contract=arm_contract,
            request_observer=observed.append,
        )
        _prediction_population(prediction, contract)
        if prediction["inference_config_sha256"] != contract["base_arms"][
            arm_index
        ]["config_sha256"]:
            raise P7LockedTestError(f"P7 {arm_id} configuration changed")
        prediction_path, run_report_path = write_structured_llm_run(
            prediction, run_report, arm_directory
        )
        latency_trace = build_request_latency_trace(
            arm_id=arm_id, split_name="test", requests=observed
        )
        latency_path = write_request_latency_trace(
            latency_trace, arm_directory / "request-latency.json"
        )
        predictions[f"{arm_id}.raw"] = prediction
        prediction_paths[f"{arm_id}.raw"] = prediction_path
        latency_traces[arm_id] = latency_trace
        base_artifacts.append(
            {
                "arm_id": arm_id,
                "prediction_path": prediction_path,
                "run_report_path": run_report_path,
                "latency_trace_path": latency_path,
            }
        )

    if tuple(predictions) != tuple(f"{arm_id}.raw" for arm_id in BASE_ARM_IDS):
        raise P7LockedTestError("P7 raw-arm execution order changed")
    return {
        "predictions": predictions,
        "prediction_paths": prediction_paths,
        "latency_traces": latency_traces,
        "base_artifacts": base_artifacts,
    }


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluate_view(
    *,
    prediction_path: Path,
    benchmark_path: Path,
    split_path: Path,
    staging_path: Path,
    output_directory: Path,
) -> Dict[str, Path]:
    p1_5 = evaluate_apixaban_predictions(
        benchmark_path,
        split_path,
        prediction_path,
        "test",
    )
    p1_5_directory = _private_directory(output_directory / "p1-5")
    p1_5_path, _ = write_apixaban_evaluation_report(p1_5, p1_5_directory)
    p4_5_directory = _private_directory(output_directory / "p4-5")
    p4_5_path = run_error_attribution(
        prediction_path=prediction_path,
        benchmark_path=benchmark_path,
        staging_corpus_path=staging_path,
        frozen_split_path=split_path,
        output_path=p4_5_directory / "report.json",
    )
    return {
        "p1_5_report_path": p1_5_path,
        "p4_5_report_path": p4_5_path,
    }


def run_gold_phase(
    *,
    contract: Dict[str, Any],
    batch_root: Path,
    attempt_directory: Path,
    frozen_split_path: Path,
    staging_corpus_path: Path,
    benchmark_path: Path,
    benchmark_manifest_path: Path,
    mentor_results_path: Path,
    mentor_candidate_csv_path: Path,
    id_map_path: Path,
    raw_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Create every frozen gold-backed view and report, without selection."""

    del batch_root
    verify_apixaban_benchmark_files(benchmark_path, benchmark_manifest_path)
    predictions = dict(raw_result["predictions"])
    prediction_paths = dict(raw_result["prediction_paths"])
    p4_3_report_paths: Dict[str, Optional[Path]] = {
        f"{arm_id}.raw": None for arm_id in BASE_ARM_IDS
    }
    projection_directory = _private_directory(attempt_directory / "p4-3")
    for arm_id in BASE_ARM_IDS:
        raw_id = f"{arm_id}.raw"
        projected_id = f"{arm_id}.p4_3"
        view_directory = _private_directory(projection_directory / arm_id)
        projected_path, projection_report_path = run_deterministic_abstention(
            prediction_path=prediction_paths[raw_id],
            benchmark_path=benchmark_path,
            staging_corpus_path=staging_corpus_path,
            frozen_split_path=frozen_split_path,
            projection_output_path=view_directory / "predictions.json",
            report_output_path=view_directory / "report.json",
        )
        projected = _load_json(projected_path)
        _prediction_population(projected, contract)
        predictions[projected_id] = projected
        prediction_paths[projected_id] = projected_path
        p4_3_report_paths[projected_id] = projection_report_path

    ordered_predictions = {view_id: predictions[view_id] for view_id in VIEW_IDS}
    ordered_paths = {view_id: prediction_paths[view_id] for view_id in VIEW_IDS}
    evaluation_reports: Dict[str, Dict[str, Any]] = {}
    view_artifacts = []
    evaluation_root = _private_directory(attempt_directory / "evaluations")
    for view_id in VIEW_IDS:
        view_directory = _private_directory(evaluation_root / view_id)
        report_paths = _evaluate_view(
            prediction_path=ordered_paths[view_id],
            benchmark_path=benchmark_path,
            split_path=frozen_split_path,
            staging_path=staging_corpus_path,
            output_directory=view_directory,
        )
        p1_5_report = _load_json(report_paths["p1_5_report_path"])
        evaluation_reports[view_id] = p1_5_report
        view_artifacts.append(
            {
                "view_id": view_id,
                "prediction_path": ordered_paths[view_id],
                **report_paths,
                "p4_3_report_path": p4_3_report_paths.get(view_id),
            }
        )

    benchmark = _load_json(benchmark_path)
    validate_apixaban_benchmark(benchmark)
    split = load_apixaban_split_manifest(frozen_split_path)
    mentor_reference = load_mentor_reference(
        mentor_results_path, mentor_candidate_csv_path, id_map_path
    )
    final_view = "llama31_long_context_1_0_0.p4_3"
    p4_7_report, p4_7_trace = build_single_trial_evaluation_v1_1(
        benchmark,
        split,
        ordered_predictions[final_view],
        mentor_reference,
        split_name="test",
        benchmark_sha256=file_sha256(benchmark_path),
        prediction_set_sha256=file_sha256(ordered_paths[final_view]),
        mentor_results_sha256=file_sha256(mentor_results_path),
        candidate_csv_sha256=file_sha256(mentor_candidate_csv_path),
        id_map_sha256=file_sha256(id_map_path),
        p7_contract_sha256=contract["contract_sha256"],
    )
    p4_7_directory = _private_directory(attempt_directory / "p4-7")
    p4_7_paths = write_single_trial_evaluation(
        p4_7_report, p4_7_trace, p4_7_directory
    )

    case_package = build_representative_case_package(
        contract=contract,
        view_id=final_view,
        split_name="test",
        prediction_set=ordered_predictions[final_view],
        benchmark=benchmark,
        staging_corpus=_load_json(staging_corpus_path),
        expected_patient_ids=split["splits"]["test"]["patient_ids"],
    )
    case_path = write_private_json(
        case_package, attempt_directory / "p4-5-representative-cases.json"
    )
    public_candidate = build_public_release_candidate(
        contract=contract,
        evaluation_reports=evaluation_reports,
        predictions=ordered_predictions,
        latency_traces=raw_result["latency_traces"],
    )
    public_path = write_private_json(
        public_candidate,
        attempt_directory / "public-release-candidate.owner-review-required.json",
    )
    return {
        "predictions": ordered_predictions,
        "prediction_paths": ordered_paths,
        "view_artifacts": view_artifacts,
        "p4_7_paths": p4_7_paths,
        "case_path": case_path,
        "public_path": public_path,
    }


def _manifest_records(
    *,
    root: Path,
    raw_result: Mapping[str, Any],
    gold_result: Mapping[str, Any],
) -> Tuple[list[Dict[str, Any]], list[Dict[str, Any]], Dict[str, Any]]:
    base_records = []
    for item in raw_result["base_artifacts"]:
        record = {
            "arm_id": item["arm_id"],
            "prediction_path": _relative(root, item["prediction_path"]),
            "prediction_sha256": file_sha256(item["prediction_path"]),
            "run_report_path": None,
            "run_report_sha256": None,
            "latency_trace_path": _relative(root, item["latency_trace_path"]),
            "latency_trace_sha256": file_sha256(item["latency_trace_path"]),
        }
        if item["run_report_path"] is not None:
            record["run_report_path"] = _relative(root, item["run_report_path"])
            record["run_report_sha256"] = file_sha256(item["run_report_path"])
        base_records.append(record)

    view_records = []
    for item in gold_result["view_artifacts"]:
        record = {
            "view_id": item["view_id"],
            "prediction_path": _relative(root, item["prediction_path"]),
            "prediction_sha256": file_sha256(item["prediction_path"]),
            "p1_5_report_path": _relative(root, item["p1_5_report_path"]),
            "p1_5_report_sha256": file_sha256(item["p1_5_report_path"]),
            "p4_5_report_path": _relative(root, item["p4_5_report_path"]),
            "p4_5_report_sha256": file_sha256(item["p4_5_report_path"]),
            "p4_3_report_path": None,
            "p4_3_report_sha256": None,
        }
        if item["p4_3_report_path"] is not None:
            record["p4_3_report_path"] = _relative(
                root, item["p4_3_report_path"]
            )
            record["p4_3_report_sha256"] = file_sha256(
                item["p4_3_report_path"]
            )
        view_records.append(record)

    report_path, trace_path, summary_path = gold_result["p4_7_paths"]
    p4_7 = {
        "view_id": "llama31_long_context_1_0_0.p4_3",
        "report_path": _relative(root, report_path),
        "report_sha256": file_sha256(report_path),
        "trace_path": _relative(root, trace_path),
        "trace_sha256": file_sha256(trace_path),
        "summary_path": _relative(root, summary_path),
        "summary_sha256": file_sha256(summary_path),
    }
    return base_records, view_records, p4_7


def _execute_locked_test_batch(
    *,
    repository_root: Path,
    output_root: Path,
    frozen_split_path: Path,
    staging_corpus_path: Path,
    benchmark_path: Path,
    benchmark_manifest_path: Path,
    mentor_results_path: Path,
    mentor_candidate_csv_path: Path,
    id_map_path: Path,
    allow_pre_gold_retry: bool = False,
    clients: Optional[Mapping[str, Any]],
    contract: Dict[str, Any],
    raw_phase: RawPhase,
    gold_phase: GoldPhase,
) -> Path:
    """Testable state machine; the public wrapper fixes both phase functions."""

    frozen_contract = dict(contract)
    require_locked_test_authorization(frozen_contract)
    validate_p7_contract(frozen_contract, repository_root=repository_root)

    assert_restricted_local_path(output_root)
    if output_root.exists() and not output_root.is_dir():
        raise P7LockedTestError("P7 output root is not a directory")
    if output_root.exists() and any(output_root.iterdir()) and not (
        output_root / "events"
    ).is_dir():
        raise P7LockedTestError("P7 output root has unrecognized prior content")
    root = _private_directory(output_root)
    prior_events = _load_event_artifacts(root, frozen_contract["contract_sha256"])
    top_level_names = {path.name for path in root.iterdir()}
    if not prior_events and top_level_names:
        raise P7LockedTestError("P7 output root has unrecognized prior content")
    if tuple(item["event"] for item in prior_events) == (
        "attempt_started",
        "pre_gold_failed",
    ) and not top_level_names.issubset({"events", "attempt-1"}):
        raise P7LockedTestError("P7 retry root contains unrecognized prior content")
    attempt, next_sequence = _select_attempt(
        prior_events, allow_pre_gold_retry=allow_pre_gold_retry
    )

    dataset = frozen_contract["dataset"]
    _assert_input_file(
        frozen_split_path, dataset["split_file_sha256"], "frozen split"
    )
    _assert_input_file(
        staging_corpus_path, dataset["staging_corpus_sha256"], "staging corpus"
    )
    attempt_directory = _private_directory(root / f"attempt-{attempt}")
    _, started_artifact = _write_event(
        root,
        sequence=next_sequence,
        event="attempt_started",
        contract_sha256=frozen_contract["contract_sha256"],
        attempt=attempt,
        gold_backed_phase_started=False,
    )
    events = [*prior_events, started_artifact]
    try:
        raw_result = raw_phase(
            contract=frozen_contract,
            batch_root=root,
            attempt_directory=attempt_directory,
            frozen_split_path=frozen_split_path,
            staging_corpus_path=staging_corpus_path,
            clients=clients,
        )
    except BaseException as error:
        _, failed_artifact = _write_event(
            root,
            sequence=next_sequence + 1,
            event="pre_gold_failed",
            contract_sha256=frozen_contract["contract_sha256"],
            attempt=attempt,
            gold_backed_phase_started=False,
            reason_code=type(error).__name__,
        )
        events.append(failed_artifact)
        raise P7LockedTestError(
            "P7 pre-gold phase failed; no result was evaluated or printed"
        ) from None

    _, raw_artifact = _write_event(
        root,
        sequence=next_sequence + 1,
        event="raw_complete",
        contract_sha256=frozen_contract["contract_sha256"],
        attempt=attempt,
        gold_backed_phase_started=False,
        artifact_count=len(raw_result.get("base_artifacts", [])),
    )
    events.append(raw_artifact)
    _, gold_started_artifact = _write_event(
        root,
        sequence=next_sequence + 2,
        event="gold_phase_started",
        contract_sha256=frozen_contract["contract_sha256"],
        attempt=attempt,
        gold_backed_phase_started=True,
    )
    events.append(gold_started_artifact)

    gold_inputs = (
        (benchmark_path, dataset["benchmark_sha256"], "benchmark"),
        (
            benchmark_manifest_path,
            dataset["benchmark_manifest_sha256"],
            "benchmark manifest",
        ),
        (mentor_results_path, dataset["mentor_results_sha256"], "mentor results"),
        (
            mentor_candidate_csv_path,
            dataset["mentor_candidate_csv_sha256"],
            "mentor candidate CSV",
        ),
        (id_map_path, dataset["id_map_sha256"], "ID map"),
    )
    try:
        for path, expected_sha256, name in gold_inputs:
            _assert_input_file(path, expected_sha256, name)
        gold_result = gold_phase(
            contract=frozen_contract,
            batch_root=root,
            attempt_directory=attempt_directory,
            frozen_split_path=frozen_split_path,
            staging_corpus_path=staging_corpus_path,
            benchmark_path=benchmark_path,
            benchmark_manifest_path=benchmark_manifest_path,
            mentor_results_path=mentor_results_path,
            mentor_candidate_csv_path=mentor_candidate_csv_path,
            id_map_path=id_map_path,
            raw_result=raw_result,
        )
        base_records, view_records, p4_7 = _manifest_records(
            root=root, raw_result=raw_result, gold_result=gold_result
        )
        complete_event = build_state_event(
            contract_sha256=frozen_contract["contract_sha256"],
            event="batch_complete",
            attempt=attempt,
            gold_backed_phase_started=True,
            artifact_count=len(base_records) + len(view_records) + 4,
        )
        complete_event_path = root / "events" / (
            f"{next_sequence + 3:02d}-batch_complete.json"
        )
        complete_artifact = {
            "event": "batch_complete",
            "attempt": attempt,
            "path": _relative(root, complete_event_path),
            "sha256": "pending",
        }
        # The canonical event document is written with stable JSON formatting;
        # compute its raw file hash before creating the manifest that binds it.
        event_payload = json.dumps(complete_event, indent=2, sort_keys=True) + "\n"
        complete_artifact["sha256"] = hashlib.sha256(
            event_payload.encode("utf-8")
        ).hexdigest()
        manifest = build_batch_manifest(
            contract=frozen_contract,
            attempt=attempt,
            base_arms=base_records,
            views=view_records,
            p4_7=p4_7,
            representative_case_package=_artifact(root, gold_result["case_path"]),
            public_candidate=_artifact(root, gold_result["public_path"]),
            events=[*events, complete_artifact],
        )
        manifest_path = write_batch_manifest(manifest, root / "batch-manifest.json")
        write_private_text(event_payload, complete_event_path)
        return manifest_path
    except BaseException as error:
        terminal_sequence = next_sequence + 3
        terminal_path = root / "events" / f"{terminal_sequence:02d}-terminal_failed.json"
        if not terminal_path.exists():
            _write_event(
                root,
                sequence=terminal_sequence,
                event="terminal_failed",
                contract_sha256=frozen_contract["contract_sha256"],
                attempt=attempt,
                gold_backed_phase_started=True,
                reason_code=type(error).__name__,
            )
        raise P7LockedTestError(
            "P7 gold-backed phase failed terminally; rerun is forbidden"
        ) from None


def execute_locked_test_batch(
    *,
    repository_root: Path,
    output_root: Path,
    frozen_split_path: Path,
    staging_corpus_path: Path,
    benchmark_path: Path,
    benchmark_manifest_path: Path,
    mentor_results_path: Path,
    mentor_candidate_csv_path: Path,
    id_map_path: Path,
    allow_pre_gold_retry: bool = False,
) -> Path:
    """Execute the exact checked-in arms and evaluators, with no injection API."""

    return _execute_locked_test_batch(
        repository_root=repository_root,
        output_root=output_root,
        frozen_split_path=frozen_split_path,
        staging_corpus_path=staging_corpus_path,
        benchmark_path=benchmark_path,
        benchmark_manifest_path=benchmark_manifest_path,
        mentor_results_path=mentor_results_path,
        mentor_candidate_csv_path=mentor_candidate_csv_path,
        id_map_path=id_map_path,
        allow_pre_gold_retry=allow_pre_gold_retry,
        clients=None,
        contract=load_p7_contract(),
        raw_phase=run_raw_phase,
        gold_phase=run_gold_phase,
    )
