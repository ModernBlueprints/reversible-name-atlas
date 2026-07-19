"""Official STDIO MCP transport for the shared Name Atlas domain services."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from name_atlas.mcp_contracts import (
    AnswerClarificationRequest,
    ApplyChangeFileRequest,
    JobHandleRequest,
    McpChangeFileResult,
    McpJobStatus,
    McpReconstructionResult,
    McpVerificationResult,
    PlanAndCreateCopyRequest,
    RecreateOriginalRequest,
    VerifyResultRequest,
)
from name_atlas.mcp_service import NameAtlasMcpService

_FIRST_INSTRUCTION_BLOCK = (
    "Name Atlas never edits a selected source; it creates a separate verified "
    "result. Origin: call plan_and_create_copy with "
    "evidence_disclosure_acknowledged=true, poll job_status, answer at most one "
    "question with answer_clarification using the returned revision and question "
    "fingerprint, then poll until verified or blocked. Receiver: call "
    "apply_change_file, then poll job_status. Never invent handles or bypass "
    "blockers. Change File application is keyless, makes no GPT call, and makes "
    "no external network request."
)
if len(_FIRST_INSTRUCTION_BLOCK) > 512:
    raise AssertionError("The MCP workflow instruction block exceeds 512 characters.")
SERVER_INSTRUCTIONS = (
    _FIRST_INSTRUCTION_BLOCK.ljust(512)
    + "The start key is reused for that job's reconstruction; each mutation "
    "binds its exact request inside the durable job. A clarification may use a "
    "new caller key and also binds its exact answer, expected revision, and "
    "question fingerprint. Never reuse one operation key for different "
    "arguments. Server startup schedules unfinished durable work; status polling "
    "itself never resumes work or contacts a provider. "
    "get_change_file is available only after a GPT-planned origin verifies. "
    "Blocked results must be reported, never bypassed."
)


def build_mcp_server(
    service: NameAtlasMcpService | None = None,
) -> FastMCP[None]:
    """Build the exact seven-tool server around one shared service adapter."""

    coordinator = service or NameAtlasMcpService()

    @asynccontextmanager
    async def lifespan(_server: FastMCP[None]) -> AsyncIterator[None]:
        await coordinator.recover_nonterminal_jobs()
        try:
            yield None
        finally:
            await coordinator.wait_for_operations()

    server: FastMCP[None] = FastMCP(
        name="Reversible Name Atlas",
        instructions=SERVER_INSTRUCTIONS,
        log_level="WARNING",
        lifespan=lifespan,
    )

    @server.tool(
        name="plan_and_create_copy",
        title="Plan and create a separate verified copy",
        description=(
            "Complete the bounded local inventory preflight, persist one "
            "consented GPT-5.6 planning job, and return its durable handle "
            "before provider planning or result creation finishes."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def plan_and_create_copy(
        request: PlanAndCreateCopyRequest,
    ) -> McpJobStatus:
        return await coordinator.plan_and_create_copy(request)

    @server.tool(
        name="job_status",
        title="Read durable Name Atlas job status",
        description=(
            "Read one persisted job without resuming work, contacting GPT, or "
            "creating output."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def job_status(request: JobHandleRequest) -> McpJobStatus:
        return await coordinator.job_status(request)

    @server.tool(
        name="answer_clarification",
        title="Answer the sole waiting clarification",
        description=(
            "Persist one answer bound to the exact job revision and question, "
            "then continue its existing planner origin."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        structured_output=True,
    )
    async def answer_clarification(
        request: AnswerClarificationRequest,
    ) -> McpJobStatus:
        return await coordinator.answer_clarification(request)

    @server.tool(
        name="get_change_file",
        title="Get the verified Name Atlas Change File",
        description=(
            "Return the verified local Change File path and its receipt-bound "
            "identities without returning project payload bytes."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def get_change_file(
        request: JobHandleRequest,
    ) -> McpChangeFileResult:
        return await coordinator.get_change_file(request)

    @server.tool(
        name="apply_change_file",
        title="Apply a shared Change File",
        description=(
            "Persist a keyless receiver job, deterministically match the local "
            "project, and create a separate verified result."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def apply_change_file(
        request: ApplyChangeFileRequest,
    ) -> McpJobStatus:
        return await coordinator.apply_change_file(request)

    @server.tool(
        name="verify_result",
        title="Independently verify a Name Atlas result",
        description=(
            "Run the source-free, keyless, network-independent receipt verifier "
            "against one candidate result."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def verify_result(
        request: VerifyResultRequest,
    ) -> McpVerificationResult:
        return await coordinator.verify_result(request)

    @server.tool(
        name="recreate_original",
        title="Recreate the selected job's original layout",
        description=(
            "Create and verify one fixed absent sibling reconstruction without "
            "changing the source or organized result."
        ),
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        structured_output=True,
    )
    async def recreate_original(
        request: RecreateOriginalRequest,
    ) -> McpReconstructionResult:
        return await coordinator.recreate_original(request)

    return server


def run_mcp_server(argv: Sequence[str] | None = None) -> int:
    """Run only the STDIO protocol; all diagnostics remain on STDERR."""

    arguments = list(argv or ())
    if arguments:
        logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
        logging.getLogger(__name__).error(
            "name-atlas mcp accepts no command-line arguments"
        )
        return 2
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    build_mcp_server().run(transport="stdio")
    return 0
