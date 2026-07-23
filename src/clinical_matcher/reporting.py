import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from .evaluation import BootstrapInterval, CoverageRiskPoint
from .splits import canonical_sha256
from .validation import validate_document


REPORT_VERSION = "1.0.0"
REPORT_SCHEMA_RESOURCE = "schemas/run-report-1.0.0.schema.json"


def report_fingerprint(run_specification: Mapping[str, Any]) -> str:
    return canonical_sha256(run_specification)


def interval_document(interval: BootstrapInterval) -> Dict[str, Any]:
    return asdict(interval)


def curve_document(
    points: Iterable[CoverageRiskPoint],
) -> Tuple[Dict[str, Any], ...]:
    return tuple(asdict(point) for point in points)


def validate_report(report: Dict[str, Any]) -> None:
    validate_document(report, REPORT_SCHEMA_RESOURCE)


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    provenance = report["provenance"]
    split = provenance["split"]
    metrics = report["metrics"]
    lines = [
        "# ClinicalMatcher run report",
        "",
        "> Research evaluation only. This is not a clinical decision or "
        "enrollment recommendation.",
        "",
        "## Reproducibility",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Run ID | `{report['run_id']}` |",
        f"| Generated | `{report['generated_at']}` |",
        f"| Code commit | `{provenance['code_commit']}` |",
        f"| Dataset | `{provenance['dataset_id']}` |",
        f"| Dataset SHA-256 | `{provenance['dataset_sha256']}` |",
        f"| Split strategy | `{split['strategy']}` |",
        f"| Split name | `{split['name']}` |",
        f"| Split manifest SHA-256 | `{split['manifest_sha256']}` |",
        f"| Split seed | `{split['seed']}` |",
        f"| Bootstrap unit | `{report['configuration']['bootstrap_unit']}` |",
        "",
        "## Metrics",
        "",
        "| Layer | Metric | Value |",
        "| --- | --- | ---: |",
    ]
    for layer in ("retrieval", "decision", "ranking", "latency"):
        for name, value in metrics[layer].items():
            if isinstance(value, (int, float)):
                lines.append(
                    f"| {layer} | `{name}` | {_format_metric(value)} |"
                )

    lines.extend(
        [
            "",
            "## Bootstrap confidence intervals",
            "",
            "| Metric | Estimate | Lower | Upper | Clusters |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, interval in metrics["bootstrap"].items():
        lines.append(
            f"| `{name}` | {_format_metric(interval['estimate'])} | "
            f"{_format_metric(interval['lower'])} | "
            f"{_format_metric(interval['upper'])} | "
            f"{interval['cluster_count']} |"
        )

    lines.extend(
        [
            "",
            "## Coverage–risk",
            "",
            "| Selection threshold | Coverage | Risk | Answered |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for point in metrics["coverage_risk"]:
        threshold = (
            "abstain-all"
            if point["threshold"] is None
            else _format_metric(point["threshold"])
        )
        risk = (
            "n/a"
            if point["risk"] is None
            else _format_metric(point["risk"])
        )
        lines.append(
            f"| {threshold} | {_format_metric(point['coverage'])} | "
            f"{risk} | {point['answered']}/{point['total']} |"
        )

    lines.extend(
        [
            "",
            "## Error attribution",
            "",
            "| Category | Count |",
            "| --- | ---: |",
        ]
    )
    for category, count in report["errors"]["counts"].items():
        lines.append(f"| `{category}` | {count} |")
    lines.extend(
        [
            "",
            "The retrieval and decision layers are reported separately. "
            "Confidence intervals use cluster resampling, never independent "
            "criterion-row resampling.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(
    report: Dict[str, Any], output_directory: Path
) -> Tuple[Path, Path]:
    validate_report(report)
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "report.json"
    markdown_path = output_directory / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path
