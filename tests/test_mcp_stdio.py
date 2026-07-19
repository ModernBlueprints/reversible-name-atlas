"""Protocol-level acceptance for the real Name Atlas STDIO server."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from name_atlas.mcp_contracts import McpJobStatus
from name_atlas.mcp_server import SERVER_INSTRUCTIONS

EXPECTED_TOOLS = {
    "plan_and_create_copy",
    "job_status",
    "answer_clarification",
    "get_change_file",
    "apply_change_file",
    "verify_result",
    "recreate_original",
}
READ_ONLY_TOOLS = {"job_status", "get_change_file", "verify_result"}
OPEN_WORLD_TOOLS = {"plan_and_create_copy", "answer_clarification"}


@pytest.mark.anyio
async def test_real_stdio_server_exposes_only_the_bounded_tool_surface(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    repository = Path(__file__).resolve().parents[1]
    stderr_path = tmp_path / "mcp-stderr.log"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "name_atlas.launcher", "mcp"],
        cwd=str(repository),
        env=environment,
    )

    with stderr_path.open("w", encoding="utf-8") as stderr:
        async with stdio_client(parameters, errlog=stderr) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.instructions == SERVER_INSTRUCTIONS
                assert (
                    initialized.instructions[:512]
                    .rstrip()
                    .endswith("makes no external network request.")
                )

                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS
                assert len(listed.tools) == 7
                for tool in listed.tools:
                    assert tool.outputSchema is not None
                    assert tool.outputSchema["additionalProperties"] is False
                    request_reference = tool.inputSchema["properties"]["request"][
                        "$ref"
                    ]
                    request_definition = request_reference.rsplit("/", 1)[-1]
                    assert (
                        tool.inputSchema["$defs"][request_definition][
                            "additionalProperties"
                        ]
                        is False
                    )
                    assert tool.annotations is not None
                    assert tool.annotations.readOnlyHint is (
                        tool.name in READ_ONLY_TOOLS
                    )
                    assert tool.annotations.destructiveHint is False
                    assert tool.annotations.idempotentHint is True
                    assert tool.annotations.openWorldHint is (
                        tool.name in OPEN_WORLD_TOOLS
                    )

                result = await session.call_tool(
                    "job_status",
                    {"request": {"job_handle": "0" * 32}},
                )
                assert result.isError is False
                assert result.structuredContent is not None
                projected = McpJobStatus.model_validate(
                    result.structuredContent,
                    strict=True,
                )
                assert projected.status == "blocked"
                assert projected.blocker_code == "job_not_found"

    assert stderr_path.read_text(encoding="utf-8") == ""
