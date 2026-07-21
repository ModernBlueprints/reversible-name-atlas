"""Generate the public gateway's MCP output-schema contract from FastMCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue

from name_atlas.foldweave_chatgpt_mcp import (
    WIDGET_RESOURCE_URI,
    build_foldweave_chatgpt_server,
)

OUTPUT_CONTRACT_SCHEMA_VERSION = "foldweave-public-mcp-output-contract.v1"
EXPECTED_PUBLIC_TOOL_COUNT = 22
EXPECTED_OUTPUT_SCHEMA_FAMILY_COUNT = 9
PUBLIC_MCP_OUTPUT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "gateway"
    / "src"
    / "generated"
    / "foldweave-public-mcp-output-contract.v1.json"
)

JsonObject = dict[str, JsonValue]


class PublicMcpOutputContractError(RuntimeError):
    """The live FastMCP surface cannot produce one safe public contract."""


async def build_public_mcp_output_contract() -> JsonObject:
    """Return the deterministic public output contract from live tool schemas."""

    tools = sorted(
        await build_foldweave_chatgpt_server().list_tools(),
        key=lambda tool: tool.name,
    )
    if len(tools) != EXPECTED_PUBLIC_TOOL_COUNT:
        raise PublicMcpOutputContractError(
            "The public Foldweave MCP tool count changed; review the gateway "
            "contract before regenerating it."
        )

    output_schema_families: dict[str, JsonObject] = {}
    tool_output_family: dict[str, JsonValue] = {}
    for tool in tools:
        schema = _json_object(tool.outputSchema, label=f"{tool.name}.outputSchema")
        family = schema.get("title")
        if not isinstance(family, str) or not family:
            raise PublicMcpOutputContractError(
                f"The {tool.name} output schema has no stable family title."
            )
        existing = output_schema_families.get(family)
        if existing is not None and existing != schema:
            raise PublicMcpOutputContractError(
                f"Output schema family {family} has conflicting definitions."
            )
        output_schema_families[family] = schema
        tool_output_family[tool.name] = family

    if len(output_schema_families) != EXPECTED_OUTPUT_SCHEMA_FAMILY_COUNT:
        raise PublicMcpOutputContractError(
            "The public Foldweave MCP output-family count changed; review the "
            "gateway contract before regenerating it."
        )

    return {
        "schemaVersion": OUTPUT_CONTRACT_SCHEMA_VERSION,
        "widgetResourceUri": WIDGET_RESOURCE_URI,
        "outputSchemaFamilies": dict(sorted(output_schema_families.items())),
        "toolOutputFamily": dict(sorted(tool_output_family.items())),
    }


def render_public_mcp_output_contract() -> str:
    """Render canonical, reviewable UTF-8 JSON for the checked-in snapshot."""

    contract = asyncio.run(build_public_mcp_output_contract())
    return (
        json.dumps(
            contract,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Write or verify the checked-in gateway output-contract snapshot."""

    parser = argparse.ArgumentParser(
        prog="python -m name_atlas.foldweave_public_mcp_contract",
        description=(
            "Generate the public Foldweave gateway output schemas from the "
            "authoritative FastMCP server."
        ),
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--write",
        action="store_true",
        help="Replace the checked-in snapshot with the live generated contract.",
    )
    action.add_argument(
        "--check",
        action="store_true",
        help="Fail unless the checked-in snapshot matches the live contract.",
    )
    options = parser.parse_args(list(argv) if argv is not None else None)
    rendered = render_public_mcp_output_contract()

    if options.write:
        PUBLIC_MCP_OUTPUT_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PUBLIC_MCP_OUTPUT_CONTRACT_PATH.write_text(rendered, encoding="utf-8")
        return 0

    try:
        checked_in = PUBLIC_MCP_OUTPUT_CONTRACT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(
            "The checked-in public MCP output-contract snapshot is missing.",
            file=sys.stderr,
        )
        return 1
    if checked_in != rendered:
        print(
            "The public MCP output-contract snapshot is stale. Run "
            "`python -m name_atlas.foldweave_public_mcp_contract --write`.",
            file=sys.stderr,
        )
        return 1
    return 0


def _json_object(value: Any, *, label: str) -> JsonObject:
    if not isinstance(value, dict):
        raise PublicMcpOutputContractError(f"{label} is not a JSON object.")
    try:
        normalized = json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        )
    except (TypeError, ValueError) as exc:
        raise PublicMcpOutputContractError(f"{label} is not strict JSON.") from exc
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        raise PublicMcpOutputContractError(f"{label} is not a JSON object.")
    return cast(JsonObject, normalized)


if __name__ == "__main__":
    raise SystemExit(main())
