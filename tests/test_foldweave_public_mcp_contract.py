"""Freshness checks for the generated public gateway output schemas."""

from __future__ import annotations

import json

from name_atlas.foldweave_public_mcp_contract import (
    EXPECTED_OUTPUT_SCHEMA_FAMILY_COUNT,
    EXPECTED_PUBLIC_TOOL_COUNT,
    OUTPUT_CONTRACT_SCHEMA_VERSION,
    PUBLIC_MCP_OUTPUT_CONTRACT_PATH,
    render_public_mcp_output_contract,
)


def test_checked_in_public_mcp_output_contract_matches_fastmcp() -> None:
    checked_in = PUBLIC_MCP_OUTPUT_CONTRACT_PATH.read_text(encoding="utf-8")
    assert checked_in == render_public_mcp_output_contract(), (
        "The public MCP output contract is stale. Run "
        "`python -m name_atlas.foldweave_public_mcp_contract --write`."
    )

    contract = json.loads(checked_in)
    assert set(contract) == {
        "outputSchemaFamilies",
        "schemaVersion",
        "toolOutputFamily",
        "widgetResourceUri",
    }
    assert contract["schemaVersion"] == OUTPUT_CONTRACT_SCHEMA_VERSION
    assert len(contract["toolOutputFamily"]) == EXPECTED_PUBLIC_TOOL_COUNT
    assert len(contract["outputSchemaFamilies"]) == EXPECTED_OUTPUT_SCHEMA_FAMILY_COUNT
    assert set(contract["toolOutputFamily"].values()) == set(
        contract["outputSchemaFamilies"]
    )
    for family, schema in contract["outputSchemaFamilies"].items():
        assert schema["title"] == family
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
