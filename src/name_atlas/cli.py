"""Command-line entry point for the local workbench."""

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence

import uvicorn

from name_atlas.app import create_app
from name_atlas.config import DEFAULT_PORT, LOOPBACK_HOST, RuntimeConfig
from name_atlas.domain import RunMode

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the stable judge-facing command parser."""

    parser = argparse.ArgumentParser(prog="name-atlas")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser(
        "demo",
        help="Run the loopback-only Reversible Name Atlas application.",
    )
    demo.add_argument(
        "--mode",
        choices=[mode.value for mode in RunMode],
        required=True,
        help="Use a recorded response or the live gpt-5.6 provider.",
    )
    demo.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Loopback port (default: {DEFAULT_PORT}).",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run the CLI and return a process exit code."""

    args = build_parser().parse_args(argv)
    mode = RunMode(args.mode)
    config = RuntimeConfig.from_environment(
        mode=mode,
        port=args.port,
        environ=os.environ if environ is None else environ,
    )

    if mode is RunMode.LIVE and not config.api_key_configured:
        print(
            "Live mode is blocked: configure OPENAI_API_KEY locally, then rerun. "
            "Do not paste the key into chat.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(level=logging.INFO)
    LOGGER.info("Starting Reversible Name Atlas: %s", config.safe_diagnostics())
    print(f"Reversible Name Atlas: http://{LOOPBACK_HOST}:{config.port}")
    print(config.provider_status)
    uvicorn.run(
        create_app(config),
        host=LOOPBACK_HOST,
        port=config.port,
        log_level="info",
    )
    return 0


def main() -> None:
    """Console-script entry point."""

    raise SystemExit(run())


if __name__ == "__main__":
    main()
