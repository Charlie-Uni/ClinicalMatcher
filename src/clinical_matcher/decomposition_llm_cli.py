import argparse
from pathlib import Path
from typing import Optional, Sequence

from .decomposition_llm import (
    render_comparison_markdown,
    run_decomposition_llm_dev,
    write_decomposition_llm_run,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the frozen dev-only local Llama decomposition comparison."
    )
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--acknowledge-dev-only-assisted-silver-comparison",
        action="store_true",
        help="Acknowledge that this is descriptive dev agreement, not gold accuracy.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.acknowledge_dev_only_assisted_silver_comparison:
        raise ValueError(
            "--acknowledge-dev-only-assisted-silver-comparison is required"
        )
    expected_outputs = (
        args.output_dir / "predictions.json",
        args.output_dir / "comparison-report.json",
        args.output_dir / "comparison-report.md",
    )
    if any(path.exists() for path in expected_outputs):
        raise FileExistsError("Refusing to overwrite decomposition-model output")

    def progress(index: int, total: int) -> None:
        print(f"Completed local decomposition request {index}/{total}", flush=True)

    prediction, report = run_decomposition_llm_dev(
        args.repo_root.resolve(), progress=progress
    )
    markdown = render_comparison_markdown(report)
    prediction_path = report_path = None
    markdown_path = args.output_dir / "comparison-report.md"
    try:
        prediction_path, report_path = write_decomposition_llm_run(
            prediction, report, args.output_dir
        )
        with markdown_path.open("x", encoding="utf-8") as handle:
            handle.write(markdown)
    except BaseException:
        for path in (prediction_path, report_path, markdown_path):
            if path is not None:
                path.unlink(missing_ok=True)
        raise
    print(f"Wrote predictions: {prediction_path}")
    print(f"Wrote comparison report: {report_path}")
    print(f"Wrote Markdown summary: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
