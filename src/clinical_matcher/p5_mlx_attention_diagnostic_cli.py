import argparse
from pathlib import Path
from typing import Optional, Sequence

from .p5_mlx_attention_diagnostic import (
    run_allocation_probe,
    run_gradient_probe,
    write_diagnostic_result,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the approved synthetic-only MLX attention diagnostic"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    allocation = subparsers.add_parser("allocation")
    allocation.add_argument(
        "--context-tier", type=int, choices=(4096, 8192, 16384), required=True
    )
    allocation.add_argument("--output", type=Path, required=True)

    gradient = subparsers.add_parser("gradient")
    gradient.add_argument("--input-length", type=int, default=256)
    gradient.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "allocation":
        result = run_allocation_probe(args.context_tier)
    else:
        result = run_gradient_probe(args.input_length)
    write_diagnostic_result(result, args.output)
    print(f"Diagnostic result written: {args.output} ({result['manifest_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
