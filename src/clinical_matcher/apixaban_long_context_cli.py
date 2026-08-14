from typing import Optional, Sequence

from .apixaban_structured_llm import load_long_context_contract
from .apixaban_structured_llm_cli import run_cli


def main(argv: Optional[Sequence[str]] = None) -> int:
    return run_cli(
        argv,
        contract=load_long_context_contract(),
        long_context=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
