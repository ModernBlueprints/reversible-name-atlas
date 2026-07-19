"""Provider-lazy console entry for the Connected Change browser product."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from name_atlas.config import DEFAULT_PORT, LOOPBACK_HOST
from name_atlas.connected_web_service import ConnectedBrowserRunService
from name_atlas.folder_app import create_folder_app
from name_atlas.folder_refactor.connected_change.job_service import (
    default_connected_change_job_path,
)

LOGGER = logging.getLogger(__name__)


def build_connected_browser_parser() -> argparse.ArgumentParser:
    """Build the C2 browser parser without importing a planner or budget ledger."""

    parser = argparse.ArgumentParser(
        prog="name-atlas run",
        description="Run the local Connected Change browser application.",
    )
    parser.add_argument(
        "--mode",
        choices=("development",),
        required=True,
        help="Use truthful deterministic development planning with no API call.",
    )
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--job", type=Path, default=None)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser


def run_connected_browser(argv: Sequence[str] | None = None) -> int:
    """Start one loopback C2 browser without provider initialization."""

    args = build_connected_browser_parser().parse_args(argv)
    if not 1 <= args.port <= 65_535:
        print("Startup blocked: port must be between 1 and 65535.", file=sys.stderr)
        return 2
    job_path = (
        args.job.expanduser().resolve(strict=False)
        if args.job is not None
        else default_connected_change_job_path()
    )
    initial_source: Path | None = None
    initial_output_parent: Path | None = None
    try:
        if not os.path.lexists(job_path):
            if args.source is not None:
                initial_source = args.source.expanduser().resolve(strict=True)
                if not initial_source.is_dir():
                    raise NotADirectoryError("source must be a readable directory")
            if args.output is not None:
                initial_output_parent = args.output.expanduser().resolve(strict=True)
            elif initial_source is not None:
                initial_output_parent = initial_source.parent
            if initial_output_parent is not None and not initial_output_parent.is_dir():
                raise NotADirectoryError("output must be an existing directory")
        service = ConnectedBrowserRunService(job_path=job_path)
        app = create_folder_app(
            service,
            initial_source=initial_source,
            initial_output_parent=initial_output_parent,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            f"Startup blocked: Connected Change job cannot be opened: {exc}",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(level=logging.INFO)
    LOGGER.info(
        "Starting Reversible Name Atlas C2 on loopback with provider-lazy routing."
    )
    print(f"Reversible Name Atlas: http://{LOOPBACK_HOST}:{args.port}")
    print(ConnectedBrowserRunService.planner_label)
    print(f"FolderRefactorJob: {job_path}")
    uvicorn.run(app, host=LOOPBACK_HOST, port=args.port, log_level="info")
    return 0
