"""CLI: ``python -m evals.harness --suite <name> [--samples N] [--mode gate|full] [--junit path]``.

Prints a Markdown report and exits non-zero when the gate fails, so CI can block a merge
(plan 12 §5.7). ``--suite all`` runs every dataset and gates on the worst.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .loader import DATASETS_DIR
from .report import to_junit, to_markdown
from .runner import run_suite


def _suite_names(arg: str) -> list[str]:
    if arg != "all":
        return [arg]
    return sorted(p.name for p in DATASETS_DIR.iterdir() if (p / "manifest.yaml").exists())


def _write_junit(dest: str, run) -> None:
    out = Path(dest)
    path = out / f"{run.suite_id}.xml" if out.is_dir() else out
    path.write_text(to_junit(run))


async def _main(args: argparse.Namespace) -> int:
    all_passed = True
    for name in _suite_names(args.suite):
        run = await run_suite(name, samples=args.samples, mode=args.mode)
        print(to_markdown(run))
        if args.junit:
            _write_junit(args.junit, run)
        all_passed = all_passed and run.passed
    return 0 if all_passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="evals.harness", description="Run Shipwright AI evals.")
    parser.add_argument("--suite", default="all", help="suite name under datasets/, or 'all'")
    parser.add_argument("--samples", type=int, default=None, help="samples/case (default: manifest gate.samples)")
    parser.add_argument("--mode", default="gate", choices=["gate", "full"])
    parser.add_argument("--junit", default=None, help="write JUnit XML here (file or directory)")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
