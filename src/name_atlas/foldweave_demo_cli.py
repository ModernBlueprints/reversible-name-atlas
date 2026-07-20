"""Bundled keyless replay entry point for Foldweave review."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

from name_atlas.connected_planner_runtime import PROJECT_ROOT
from name_atlas.folder_refactor.demo_fixtures import materialize_hero_fixture
from name_atlas.foldweave_review_cli import run_prepare_origin


def run_foldweave_demo(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Materialize the hero and prepare its exact replay through v3 review."""

    parser = argparse.ArgumentParser(prog="foldweave demo")
    parser.add_argument("--mode", choices=("replay",), required=True)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = (
        PROJECT_ROOT / ".name-atlas" / "foldweave-demo"
        if args.root is None
        else args.root.expanduser().resolve(strict=False)
    )
    fixture = root / "fixture"
    source = fixture / "sofia-apollo"
    if not source.is_dir():
        materialize_hero_fixture(fixture)
    output = root / "results"
    output.mkdir(parents=True, exist_ok=True)
    return run_prepare_origin(
        [
            "--mode",
            "replay",
            "--source",
            str(source),
            "--output",
            str(output),
            "--job",
            str(root / "jobs" / "active.json"),
        ],
        environ=environ,
    )
