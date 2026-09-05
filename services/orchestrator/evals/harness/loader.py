"""Load an EvalSuite from disk: ``<suite>/manifest.yaml`` + ``<suite>/cases/*.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import Budget, EvalCase, EvalSuite, Gate

DATASETS_DIR = Path(__file__).resolve().parents[1] / "datasets"


def resolve_suite_dir(suite: str | Path) -> Path:
    """Accept either a suite name (under ``datasets/``) or an explicit directory path."""
    path = Path(suite)
    return path if path.exists() else DATASETS_DIR / str(suite)


def load_suite(suite: str | Path) -> EvalSuite:
    suite_dir = resolve_suite_dir(suite)
    manifest = yaml.safe_load((suite_dir / "manifest.yaml").read_text())
    cases = [
        _load_case(case_file)
        for case_file in sorted((suite_dir / "cases").glob("*.yaml"))
    ]
    return EvalSuite(
        id=manifest["id"],
        version=int(manifest["version"]),
        target=manifest.get("target", {}),
        budget=Budget(**manifest.get("budget", {})),
        gate=Gate(**manifest.get("gate", {})),
        cases=cases,
    )


def _load_case(case_file: Path) -> EvalCase:
    raw = yaml.safe_load(case_file.read_text())
    return EvalCase(
        id=str(raw["id"]),
        input=raw.get("input", {}),
        expected=raw.get("expected", {}),
        labels=raw.get("labels", {}),
    )
