"""Provider-free MCP Apps surface for ChatGPT-hosted Foldweave planning."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, cast

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BeforeValidator, Field, JsonValue, model_validator

from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderJobLifecycleV3,
    FolderRefactorJobV3,
    GptHostedJobAuthorityV3,
    build_keep_previous_action,
)
from name_atlas.folder_refactor.connected_change.preview import FolderPlanPreviewV1
from name_atlas.folder_refactor.contracts import (
    SHA256_PATTERN,
    FolderPlan,
    StrictFrozenModel,
)
from name_atlas.folder_refactor.foldweave_host_contracts import (
    FolderHostPlanRevisionV1,
)
from name_atlas.folder_refactor.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)
from name_atlas.foldweave_host_service import (
    FoldweaveHostPlanningService,
    FoldweaveHostServiceError,
)
from name_atlas.foldweave_local_handles import OpaqueLocalItemHandle
from name_atlas.native_bridge import NativePathRole, NativeSelectionStatus

WIDGET_RESOURCE_URI = "ui://foldweave/review-v10.html"
WIDGET_MIME_TYPE = "text/html;profile=mcp-app"
WIDGET_JS_NAME = "foldweave-chatgpt-widget.js"
WIDGET_CSS_NAME = "foldweave-chatgpt-widget.css"

_FIRST_INSTRUCTION_BLOCK = (
    "Foldweave never changes a selected source. Origin workflow: choose source "
    "and output handles, call plan_change, inspect only bounded evidence, submit "
    "one complete plan, then call get_plan_preview. The user may revise through "
    "revise_plan and submit_plan_revision or accept the exact preview. Poll "
    "job_status and verify_result. ChatGPT supplies model inference; this server "
    "never calls the Foldweave Responses API or its direct budget ledger."
)
if len(_FIRST_INSTRUCTION_BLOCK) > 512:
    raise AssertionError("The hosted MCP workflow instruction exceeds 512 characters.")

SERVER_INSTRUCTIONS = (
    _FIRST_INSTRUCTION_BLOCK.ljust(512)
    + "Every mutation is bound to an opaque local handle, durable job, exact "
    "fingerprints, and expected revision. Mutations that accept a caller retry "
    "key bind it durably; clarification retries bind the exact question or "
    "answer. Never invent a handle, local path, proof result, or approval. The "
    "model proposes; fixed "
    "Foldweave code scans, compiles, renders, executes, receipts, and verifies; "
    "only the user accepts. Current F0c qualification exposes the complete hosted "
    "origin review/revision/accept/verify path. Receiver preparation, Change File "
    "retrieval, and reconstruction are later surfaces and are intentionally not "
    "advertised by this server. Every successful bounded-evidence result returns "
    "the authoritative current evidence_fingerprint and permitted_evidence_ids. "
    "Use those exact values in submit_plan. Every plan entry must cite only a "
    "permitted ID; initial_inventory is valid for inventory-based moves. Never "
    "put a call ID, file ID, or fingerprint into evidence_ids, and never submit "
    "an empty or placeholder probe plan."
)

WIDGET_RESOURCE_META: dict[str, Any] = {
    "ui": {
        "csp": {
            "connectDomains": [],
            "resourceDomains": [],
        },
        "prefersBorder": True,
    },
    "openai/widgetDescription": (
        "Foldweave's exact current-versus-proposed folder review, revision, "
        "acceptance, and verification surface."
    ),
    "openai/widgetCSP": {
        "connect_domains": [],
        "resource_domains": [],
    },
    "openai/widgetPrefersBorder": True,
}
_WIDGET_TOOL_META: dict[str, Any] = {
    "ui": {
        "resourceUri": WIDGET_RESOURCE_URI,
        "visibility": ["model", "app"],
    },
    "openai/outputTemplate": WIDGET_RESOURCE_URI,
    "openai/widgetAccessible": True,
    "openai/toolInvocation/invoking": "Loading the exact Foldweave preview",
    "openai/toolInvocation/invoked": "Foldweave preview ready",
}
_WIDGET_CALLABLE_META: dict[str, Any] = {
    "ui": {"visibility": ["app"]},
    "openai/widgetAccessible": True,
}

JobId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
CallId = Annotated[str, Field(min_length=1, max_length=128)]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=200)]
OpaqueHandle = Annotated[str, Field(pattern=r"^fw_[A-Za-z0-9_-]{43}$")]


def _parse_folder_plan(value: Any) -> FolderPlan:
    if isinstance(value, FolderPlan):
        return value
    return FolderPlan.model_validate_json(canonical_json_bytes(value), strict=True)


def _parse_host_revision(value: Any) -> FolderHostPlanRevisionV1:
    if isinstance(value, FolderHostPlanRevisionV1):
        return value
    return FolderHostPlanRevisionV1.model_validate_json(
        canonical_json_bytes(value),
        strict=True,
    )


McpFolderPlan = Annotated[FolderPlan, BeforeValidator(_parse_folder_plan)]
McpHostPlanRevision = Annotated[
    FolderHostPlanRevisionV1,
    BeforeValidator(_parse_host_revision),
]


class FoldweaveHostedJobStatusV1(StrictFrozenModel):
    """Path-free durable status for a ChatGPT-hosted planning job."""

    schema_version: Literal["foldweave-hosted-job-status.v1"] = (
        "foldweave-hosted-job-status.v1"
    )
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    lifecycle: Literal[
        "matching",
        "planning",
        "awaiting_clarification",
        "reviewing",
        "revising",
        "revision_failed",
        "executing",
        "verified",
        "stale",
        "blocked",
    ]
    job_revision: int = Field(ge=0)
    proposal_revision: int = Field(ge=0, le=2)
    source_commitment: str = Field(pattern=SHA256_PATTERN)
    request_fingerprint: str = Field(pattern=SHA256_PATTERN)
    model_transport: Literal["chatgpt_hosted"] = "chatgpt_hosted"
    direct_api_used: Literal[False] = False
    direct_budget_reserved: Literal[False] = False
    has_preview: bool
    candidate_fingerprint: str | None = Field(default=None, pattern=SHA256_PATTERN)
    preview_fingerprint: str | None = Field(default=None, pattern=SHA256_PATTERN)
    clarification_question: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )
    clarification_question_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    revision_attempts_remaining: int = Field(ge=0, le=2)
    revision_failure_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )
    blocker_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )

    @model_validator(mode="after")
    def require_consistent_public_state(self):
        if self.has_preview != (self.preview_fingerprint is not None):
            raise ValueError("Hosted status preview fields disagree.")
        if self.has_preview != (self.candidate_fingerprint is not None):
            raise ValueError("Hosted status candidate fields disagree.")
        if (self.clarification_question is None) != (
            self.clarification_question_fingerprint is None
        ):
            raise ValueError("Hosted clarification fields disagree.")
        return self


class FoldweaveEvidenceResultV1(StrictFrozenModel):
    """One bounded host-visible evidence response and its durable checkpoint."""

    schema_version: Literal["foldweave-host-evidence-result.v1"] = (
        "foldweave-host-evidence-result.v1"
    )
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    job_revision: int = Field(ge=0)
    evidence_fingerprint: str = Field(pattern=SHA256_PATTERN)
    permitted_evidence_ids: tuple[str, ...] = Field(min_length=1)
    tool_name: Literal[
        "list_inventory_page",
        "read_text_excerpt",
        "inspect_markdown_links",
    ]
    call_id: str = Field(min_length=1, max_length=128)
    result: JsonValue | None = None
    error_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )

    @model_validator(mode="after")
    def require_one_outcome(self):
        if (self.result is None) == (self.error_code is None):
            raise ValueError("Evidence output requires exactly one outcome.")
        return self


class FoldweaveCompilerFailurePublicV1(StrictFrozenModel):
    """One bounded deterministic compiler rejection visible to the host model."""

    submission_index: int = Field(ge=1, le=3)
    call_id: str = Field(min_length=1, max_length=128)
    plan_fingerprint: str = Field(pattern=SHA256_PATTERN)
    code: str = Field(pattern=r"^[a-z0-9_:-]{1,128}$")
    detail: str = Field(min_length=1, max_length=2_000)
    failure_fingerprint: str = Field(pattern=SHA256_PATTERN)


class FoldweaveCompilerFailuresV1(StrictFrozenModel):
    """Complete bounded deterministic failures for the current planning job."""

    schema_version: Literal["foldweave-host-compiler-failures.v1"] = (
        "foldweave-host-compiler-failures.v1"
    )
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    failures: tuple[FoldweaveCompilerFailurePublicV1, ...] = Field(max_length=3)


class FoldweaveHostedReviewStatusV1(StrictFrozenModel):
    """Exact renderer-facing review status with truthful hosted provenance."""

    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    lifecycle: Literal["reviewing", "revision_failed", "executing", "verified"]
    job_revision: int = Field(ge=0)
    proposal_revision: int = Field(ge=0, le=2)
    candidate_fingerprint: str = Field(pattern=SHA256_PATTERN)
    preview_fingerprint: str = Field(pattern=SHA256_PATTERN)
    authorization_context_fingerprint: str = Field(pattern=SHA256_PATTERN)
    model_transport: Literal["chatgpt_hosted"] = "chatgpt_hosted"
    direct_api_used: Literal[False] = False
    direct_budget_reserved: Literal[False] = False
    revision_available: bool
    revision_attempts_remaining: int = Field(ge=0, le=2)
    revision_failure: str | None = Field(default=None, min_length=1, max_length=200)


class FoldweaveHostedVerifiedResultV1(StrictFrozenModel):
    """Minimal independently verified result summary for the widget."""

    verification: Literal["verified"] = "verified"
    source_unchanged: Literal[True] = True
    complete_file_count: int = Field(ge=1, le=500)
    changed_path_count: int = Field(ge=0, le=500)
    organized_tree_commitment: str = Field(pattern=SHA256_PATTERN)
    change_file_fingerprint: str | None = Field(default=None, pattern=SHA256_PATTERN)


class FoldweaveChatGptReviewV1(StrictFrozenModel):
    """The sole complete DTO mounted in the ChatGPT Foldweave widget."""

    schema_version: Literal["foldweave-chatgpt-review.v1"] = (
        "foldweave-chatgpt-review.v1"
    )
    state_version: int = Field(ge=0)
    journey: Literal["organize", "apply"]
    preview: FolderPlanPreviewV1
    status: FoldweaveHostedReviewStatusV1
    result: FoldweaveHostedVerifiedResultV1 | None = None

    @model_validator(mode="after")
    def require_complete_binding(self):
        if not (
            self.status.job_id == self.preview.job_id
            and self.status.proposal_revision == self.preview.proposal_revision
            and self.status.candidate_fingerprint
            == self.preview.compiled_candidate_fingerprint
            and self.status.preview_fingerprint == self.preview.preview_fingerprint
        ):
            raise ValueError("Hosted review status targets another preview.")
        if self.status.lifecycle == "verified":
            if self.result is None:
                raise ValueError("A verified hosted review requires a result summary.")
            if not (
                self.result.complete_file_count == self.preview.counts.file_count
                and self.result.changed_path_count
                == self.preview.counts.changed_path_count
            ):
                raise ValueError("Hosted result counts differ from the preview.")
        elif self.result is not None:
            raise ValueError("Only a verified hosted review may expose a result.")
        return self


class FoldweaveLocalSelectionResultV1(StrictFrozenModel):
    """One path-free native selection outcome for the paired local app."""

    schema_version: Literal["foldweave-local-selection-result.v1"] = (
        "foldweave-local-selection-result.v1"
    )
    status: Literal["selected", "cancelled", "unavailable", "timeout", "failed"]
    item: OpaqueLocalItemHandle | None = None
    reason_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )

    @model_validator(mode="after")
    def require_selection_shape(self):
        if self.status == "selected":
            if self.item is None or self.reason_code is not None:
                raise ValueError("A selected item requires only its opaque handle.")
        elif self.item is not None:
            raise ValueError("A failed or cancelled selection cannot expose an item.")
        return self


class FoldweaveVerificationResultV1(StrictFrozenModel):
    """Path-free independent result-verification evidence."""

    schema_version: Literal["foldweave-verification-result.v1"] = (
        "foldweave-verification-result.v1"
    )
    verification: Literal["verified", "blocked"]
    job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    receipt_fingerprint: str | None = Field(default=None, pattern=SHA256_PATTERN)
    organized_tree_commitment: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    failed_check_ids: tuple[str, ...]


def build_foldweave_chatgpt_server(
    service: FoldweaveHostPlanningService | None = None,
    *,
    asset_root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> FastMCP[None]:
    """Build one MCP Apps server over the shared durable host-planning service."""

    coordinator = service or FoldweaveHostPlanningService()
    server: FastMCP[None] = FastMCP(
        name="Foldweave",
        instructions=SERVER_INSTRUCTIONS,
        log_level="WARNING",
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=False,
    )

    @server.resource(
        WIDGET_RESOURCE_URI,
        name="foldweave_review",
        title="Foldweave structure review",
        description=(
            "Render one exact current-versus-proposed Foldweave plan before "
            "the user authorizes a separate copy."
        ),
        mime_type=WIDGET_MIME_TYPE,
        meta=WIDGET_RESOURCE_META,
    )
    def foldweave_review_widget() -> str:
        return _load_widget_html(asset_root)

    @server.tool(
        name="choose_local_item",
        title="Choose a local Foldweave item",
        description=(
            "Ask the paired local app to select one fixed-role item and return "
            "only a short-lived opaque handle, never a local path."
        ),
        annotations=_annotations(read_only=False, idempotent=False),
        structured_output=True,
    )
    async def choose_local_item(
        role: NativePathRole,
    ) -> FoldweaveLocalSelectionResultV1:
        try:
            status, item, reason_code = await coordinator.choose_local_item(
                role=role,
                channel="chatgpt_hosted",
            )
            output = FoldweaveLocalSelectionResultV1(
                status=cast(NativeSelectionStatus, status).value,
                item=item,
                reason_code=reason_code,
            )
            return _success(output, "Foldweave returned a path-free selection.")
        except Exception as exc:  # pragma: no branch - stable public conversion
            return _failure(exc, "local_selection_failed")

    def start_job(
        *,
        source_handle: str,
        output_handle: str,
        request: str,
        evidence_disclosure_acknowledged: bool,
        idempotency_key: str,
    ) -> CallToolResult:
        try:
            job = coordinator.create_or_resume_planning_job(
                source_handle=source_handle,
                output_handle=output_handle,
                request=request,
                disclosure_acknowledged=evidence_disclosure_acknowledged,
                idempotency_key=idempotency_key,
                model_transport="chatgpt_hosted",
            )
            return _success(
                _project_job_status(job),
                "Foldweave created or resumed the hosted planning job.",
            )
        except Exception as exc:
            return _failure(exc, "planning_job_failed")

    @server.tool(
        name="create_or_resume_planning_job",
        title="Create or resume hosted Foldweave planning",
        description=(
            "Create one consented durable ChatGPT-hosted planning job from "
            "opaque local handles without calling the direct Responses API."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def create_or_resume_planning_job(
        source_handle: OpaqueHandle,
        output_handle: OpaqueHandle,
        request: Annotated[str, Field(min_length=1, max_length=20_000)],
        evidence_disclosure_acknowledged: bool,
        idempotency_key: IdempotencyKey,
    ) -> FoldweaveHostedJobStatusV1:
        return start_job(
            source_handle=source_handle,
            output_handle=output_handle,
            request=request,
            evidence_disclosure_acknowledged=evidence_disclosure_acknowledged,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="plan_change",
        title="Start a hosted Foldweave origin review",
        description=(
            "High-level alias that starts the same durable hosted planning "
            "workflow; the host must still inspect evidence and submit a plan."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def plan_change(
        source_handle: OpaqueHandle,
        output_handle: OpaqueHandle,
        request: Annotated[str, Field(min_length=1, max_length=20_000)],
        evidence_disclosure_acknowledged: bool,
        idempotency_key: IdempotencyKey,
    ) -> FoldweaveHostedJobStatusV1:
        return start_job(
            source_handle=source_handle,
            output_handle=output_handle,
            request=request,
            evidence_disclosure_acknowledged=evidence_disclosure_acknowledged,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="list_inventory_page",
        title="List bounded Foldweave inventory evidence",
        description=(
            "Read one deterministic page of path-relative file metadata from "
            "the exact durable hosted job. The result includes the authoritative "
            "current evidence_fingerprint and permitted_evidence_ids to use "
            "verbatim in submit_plan. Use initial_inventory for ordinary "
            "inventory-based moves; never invent an evidence ID."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def list_inventory_page(
        job_id: JobId,
        call_id: CallId,
        cursor: Annotated[
            str | None,
            Field(pattern=r"^inv:[a-f0-9]{16}:[0-9]+$"),
        ] = None,
        page_size: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> FoldweaveEvidenceResultV1:
        return _run_evidence(
            coordinator,
            tool_name="list_inventory_page",
            job_id=job_id,
            call_id=call_id,
            cursor=cursor,
            page_size=page_size,
        )

    @server.tool(
        name="read_text_excerpt",
        title="Read a bounded Foldweave text excerpt",
        description=(
            "Read only a counted UTF-8 excerpt for one eligible stable file ID "
            "in the exact hosted job."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def read_text_excerpt(
        job_id: JobId,
        call_id: CallId,
        file_id: Sha256,
        start_byte: Annotated[int, Field(ge=0)],
        max_bytes: Annotated[int, Field(ge=1, le=16_384)],
    ) -> FoldweaveEvidenceResultV1:
        return _run_evidence(
            coordinator,
            tool_name="read_text_excerpt",
            job_id=job_id,
            call_id=call_id,
            file_id=file_id,
            start_byte=start_byte,
            max_bytes=max_bytes,
        )

    @server.tool(
        name="inspect_markdown_links",
        title="Inspect bounded supported-link evidence",
        description=(
            "Read one deterministic page of supported relative Markdown-link "
            "relationships for an eligible file ID."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def inspect_markdown_links(
        job_id: JobId,
        call_id: CallId,
        file_id: Sha256,
        cursor: Annotated[
            str | None,
            Field(pattern=r"^links:[a-f0-9]{16}:[0-9]+$"),
        ] = None,
        page_size: Annotated[int, Field(ge=1, le=100)] = 50,
    ) -> FoldweaveEvidenceResultV1:
        return _run_evidence(
            coordinator,
            tool_name="inspect_markdown_links",
            job_id=job_id,
            call_id=call_id,
            file_id=file_id,
            cursor=cursor,
            page_size=page_size,
        )

    @server.tool(
        name="request_clarification",
        title="Request the sole Foldweave clarification",
        description=(
            "Persist the one model-originated question for a missing user intent; "
            "mechanical compiler failures are not clarifications."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def request_clarification(
        job_id: JobId,
        expected_revision: Annotated[int, Field(ge=0)],
        question: Annotated[str, Field(min_length=1, max_length=1_000)],
        idempotency_key: IdempotencyKey,
    ) -> FoldweaveHostedJobStatusV1:
        try:
            job = coordinator.request_clarification(
                job_id=job_id,
                expected_revision=expected_revision,
                question=question,
                idempotency_key=idempotency_key,
            )
            return _success(
                _project_job_status(job),
                "Foldweave persisted the sole clarification question.",
            )
        except Exception as exc:
            return _failure(exc, "clarification_failed")

    @server.tool(
        name="answer_clarification",
        title="Answer the waiting Foldweave clarification",
        description=(
            "Persist the user's exact answer only when the expected revision and "
            "question fingerprint still match."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def answer_clarification(
        job_id: JobId,
        expected_revision: Annotated[int, Field(ge=0)],
        question_fingerprint: Sha256,
        answer: Annotated[str, Field(min_length=1, max_length=2_000)],
        idempotency_key: IdempotencyKey,
    ) -> FoldweaveHostedJobStatusV1:
        try:
            job = coordinator.answer_clarification(
                job_id=job_id,
                expected_revision=expected_revision,
                question_fingerprint=question_fingerprint,
                answer=answer,
                idempotency_key=idempotency_key,
            )
            return _success(
                _project_job_status(job),
                "Foldweave persisted the exact clarification answer.",
            )
        except Exception as exc:
            return _failure(exc, "clarification_answer_failed")

    @server.tool(
        name="submit_plan",
        title="Submit a complete Foldweave plan",
        description=(
            "Compile one complete host-model plan deterministically and stop at "
            "review without creating any output."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def submit_plan(
        job_id: JobId,
        call_id: CallId,
        plan: McpFolderPlan,
    ) -> FoldweaveHostedJobStatusV1:
        try:
            job = coordinator.submit_plan(job_id=job_id, call_id=call_id, plan=plan)
            return _success(
                _project_job_status(job),
                "Foldweave checked the complete plan; inspect its durable status.",
            )
        except Exception as exc:
            return _failure(exc, "plan_submission_failed")

    @server.tool(
        name="get_compiler_failures",
        title="Get deterministic Foldweave compiler failures",
        description=(
            "Read all bounded deterministic plan-submission failures for the "
            "exact hosted job without changing it."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
        structured_output=True,
    )
    def get_compiler_failures(
        job_id: JobId,
    ) -> FoldweaveCompilerFailuresV1:
        try:
            failures = tuple(
                FoldweaveCompilerFailurePublicV1(
                    submission_index=item.submission_index,
                    call_id=item.call_id,
                    plan_fingerprint=item.plan_fingerprint,
                    code=item.code,
                    detail=item.detail,
                    failure_fingerprint=item.failure_fingerprint,
                )
                for item in coordinator.get_compiler_failures(job_id)
            )
            return _success(
                FoldweaveCompilerFailuresV1(job_id=job_id, failures=failures),
                "Foldweave returned the deterministic compiler failures.",
            )
        except Exception as exc:
            return _failure(exc, "compiler_failures_unavailable")

    @server.tool(
        name="revise_plan",
        title="Reserve a Foldweave proposal revision",
        description=(
            "Bind the user's exact revision instruction to the visible candidate "
            "before the ChatGPT host model submits one sparse replacement."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def revise_plan(
        job_id: JobId,
        expected_revision: Annotated[int, Field(ge=0)],
        candidate_fingerprint: Sha256,
        preview_fingerprint: Sha256,
        instruction: Annotated[str, Field(min_length=1, max_length=2_000)],
        idempotency_key: IdempotencyKey,
    ) -> FoldweaveHostedJobStatusV1:
        try:
            job = coordinator.begin_revision(
                job_id=job_id,
                expected_revision=expected_revision,
                candidate_fingerprint=candidate_fingerprint,
                preview_fingerprint=preview_fingerprint,
                instruction=instruction,
                idempotency_key=idempotency_key,
            )
            return _success(
                _project_job_status(job),
                "Foldweave reserved the exact revision for hosted planning.",
            )
        except Exception as exc:
            return _failure(exc, "revision_reservation_failed")

    @server.tool(
        name="submit_plan_revision",
        title="Submit a sparse Foldweave plan revision",
        description=(
            "Compile a strict sparse hosted revision into one complete immutable "
            "replacement preview while preserving the prior valid proposal."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        structured_output=True,
    )
    def submit_plan_revision(
        job_id: JobId,
        call_id: CallId,
        revision: McpHostPlanRevision,
    ) -> FoldweaveHostedJobStatusV1:
        try:
            job = coordinator.submit_plan_revision(
                job_id=job_id,
                call_id=call_id,
                revision=revision,
            )
            return _success(
                _project_job_status(job),
                "Foldweave checked the sparse revision; inspect its replacement "
                "preview.",
            )
        except Exception as exc:
            return _failure(exc, "plan_revision_failed")

    @server.tool(
        name="get_plan_preview",
        title="Render the exact Foldweave plan preview",
        description=(
            "Return the sole complete current-versus-proposed preview DTO and "
            "mount the Foldweave review widget."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
        meta=_WIDGET_TOOL_META,
        structured_output=True,
    )
    def get_plan_preview(
        job_id: JobId,
        expected_revision: Annotated[int, Field(ge=0)],
        preview_fingerprint: Sha256,
        channel: Literal["chatgpt_hosted"] = "chatgpt_hosted",
    ) -> FoldweaveChatGptReviewV1:
        del channel
        try:
            job = coordinator.status(job_id)
            if job.revision != expected_revision:
                return _failure(
                    RuntimeError("preview_revision_mismatch"),
                    "preview_revision_mismatch",
                )
            if job.preview is None or (
                job.preview.preview_fingerprint != preview_fingerprint
            ):
                return _failure(
                    RuntimeError("preview_fingerprint_mismatch"),
                    "preview_fingerprint_mismatch",
                )
            return _success(
                _project_review(job),
                "Foldweave returned the exact review snapshot.",
            )
        except Exception as exc:
            return _failure(exc, "preview_unavailable")

    @server.tool(
        name="job_status",
        title="Read durable Foldweave hosted status",
        description=(
            "Read one path-free durable job checkpoint without resuming work, "
            "calling a model, or creating output."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
        structured_output=True,
    )
    def job_status(job_id: JobId) -> FoldweaveHostedJobStatusV1:
        try:
            return _success(
                _project_job_status(coordinator.status(job_id)),
                "Foldweave returned the durable hosted job status.",
            )
        except Exception as exc:
            return _failure(exc, "job_status_unavailable")

    @server.tool(
        name="keep_previous_proposal",
        title="Keep the previous valid Foldweave proposal",
        description=(
            "Dismiss one failed revision and rebind the preserved complete "
            "proposal to a fresh exact review checkpoint."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        meta=_WIDGET_CALLABLE_META,
        structured_output=True,
    )
    def keep_previous_proposal(
        job_id: JobId,
        proposal_revision: Annotated[int, Field(ge=0, le=2)],
        source_commitment: Sha256,
        imported_change_file_fingerprint: Sha256 | None,
        match_report_fingerprint: Sha256 | None,
        authorization_context_fingerprint: Sha256,
        expected_revision: Annotated[int, Field(ge=0)],
        preview_fingerprint: Sha256,
        candidate_fingerprint: Sha256,
        idempotency_key: IdempotencyKey,
        channel: Literal["chatgpt_hosted"] = "chatgpt_hosted",
    ) -> FoldweaveChatGptReviewV1:
        del channel
        try:
            current = coordinator.status(job_id)
            retry = build_keep_previous_action(
                base_job_revision=expected_revision,
                candidate_fingerprint=candidate_fingerprint,
                preview_fingerprint=preview_fingerprint,
                idempotency_key=idempotency_key,
            )
            matching_retry_key = tuple(
                action
                for action in current.keep_previous_actions
                if action.idempotency_key_sha256 == retry.idempotency_key_sha256
            )
            if matching_retry_key:
                _require_static_preview_binding(
                    current,
                    proposal_revision=proposal_revision,
                    source_commitment=source_commitment,
                    imported_change_file_fingerprint=(imported_change_file_fingerprint),
                    match_report_fingerprint=match_report_fingerprint,
                    authorization_context_fingerprint=(
                        authorization_context_fingerprint
                    ),
                    expected_revision=expected_revision,
                    preview_fingerprint=preview_fingerprint,
                    candidate_fingerprint=candidate_fingerprint,
                )
                job = coordinator.keep_previous_proposal(
                    job_id=job_id,
                    expected_revision=expected_revision,
                    preview_fingerprint=preview_fingerprint,
                    candidate_fingerprint=candidate_fingerprint,
                    idempotency_key=idempotency_key,
                )
                return _success(
                    _project_review(job),
                    "Foldweave returned the already preserved proposal.",
                )
            _require_exact_preview_binding(
                current,
                proposal_revision=proposal_revision,
                source_commitment=source_commitment,
                imported_change_file_fingerprint=imported_change_file_fingerprint,
                match_report_fingerprint=match_report_fingerprint,
                authorization_context_fingerprint=(authorization_context_fingerprint),
                expected_revision=expected_revision,
                preview_fingerprint=preview_fingerprint,
                candidate_fingerprint=candidate_fingerprint,
            )
            job = coordinator.keep_previous_proposal(
                job_id=job_id,
                expected_revision=expected_revision,
                preview_fingerprint=preview_fingerprint,
                candidate_fingerprint=candidate_fingerprint,
                idempotency_key=idempotency_key,
            )
            return _success(
                _project_review(job),
                "Foldweave restored the previous valid proposal.",
            )
        except Exception as exc:
            return _failure(exc, "keep_proposal_failed")

    @server.tool(
        name="accept_plan_and_create_copy",
        title="Accept the exact Foldweave preview and create a copy",
        description=(
            "Persist exact fingerprint-bound user authorization, create a "
            "separate copy, and independently verify it without direct API use."
        ),
        annotations=_annotations(read_only=False, idempotent=True),
        meta=_WIDGET_CALLABLE_META,
        structured_output=True,
    )
    def accept_plan_and_create_copy(
        job_id: JobId,
        proposal_revision: Annotated[int, Field(ge=0, le=2)],
        source_commitment: Sha256,
        imported_change_file_fingerprint: Sha256 | None,
        match_report_fingerprint: Sha256 | None,
        authorization_context_fingerprint: Sha256,
        expected_revision: Annotated[int, Field(ge=0)],
        preview_fingerprint: Sha256,
        candidate_fingerprint: Sha256,
        idempotency_key: IdempotencyKey,
        channel: Literal["chatgpt_hosted"] = "chatgpt_hosted",
    ) -> FoldweaveChatGptReviewV1:
        try:
            current = coordinator.status(job_id)
            _require_exact_preview_binding(
                current,
                proposal_revision=proposal_revision,
                source_commitment=source_commitment,
                imported_change_file_fingerprint=imported_change_file_fingerprint,
                match_report_fingerprint=match_report_fingerprint,
                authorization_context_fingerprint=(authorization_context_fingerprint),
                expected_revision=expected_revision,
                preview_fingerprint=preview_fingerprint,
                candidate_fingerprint=candidate_fingerprint,
            )
            assert current.candidate_plan is not None
            job = coordinator.accept_plan_and_create_copy(
                job_id=job_id,
                expected_revision=expected_revision,
                preview_fingerprint=preview_fingerprint,
                candidate_fingerprint=candidate_fingerprint,
                result_folder_name=current.candidate_plan.result_folder_name,
                idempotency_key=idempotency_key,
                channel=channel,
            )
            return _success(
                _project_review(job),
                "Foldweave created and independently verified the separate copy.",
            )
        except Exception as exc:
            return _failure(exc, "acceptance_failed")

    @server.tool(
        name="verify_result",
        title="Independently verify the Foldweave result",
        description=(
            "Run the source-free deterministic receipt verifier for the exact "
            "durable result without model or direct-budget use."
        ),
        annotations=_annotations(read_only=True, idempotent=True),
        meta=_WIDGET_CALLABLE_META,
        structured_output=True,
    )
    def verify_result(
        job_id: JobId,
        organized_tree_commitment: Sha256,
        channel: Literal["chatgpt_hosted"] = "chatgpt_hosted",
    ) -> FoldweaveVerificationResultV1:
        del channel
        try:
            verification = coordinator.verify_result(job_id)
            if verification.organized_tree_commitment != organized_tree_commitment:
                return _failure(
                    RuntimeError("verification_commitment_mismatch"),
                    "verification_commitment_mismatch",
                )
            output = FoldweaveVerificationResultV1(
                verification=verification.status.value,
                job_id=verification.job_id,
                receipt_fingerprint=verification.receipt_fingerprint,
                organized_tree_commitment=verification.organized_tree_commitment,
                failed_check_ids=verification.failed_check_ids,
            )
            return _success(output, "Foldweave independent verification completed.")
        except Exception as exc:
            return _failure(exc, "verification_failed")

    return server


def build_foldweave_mcp_parser() -> argparse.ArgumentParser:
    """Build the bounded local transport parser for ``foldweave mcp``."""

    parser = argparse.ArgumentParser(
        prog="foldweave mcp",
        description=(
            "Run the provider-free Foldweave MCP Apps server over STDIO or a "
            "loopback-only Streamable HTTP endpoint."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument(
        "--host",
        choices=("127.0.0.1", "::1", "localhost"),
        default="127.0.0.1",
        help="Loopback bind host for Streamable HTTP.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Loopback Streamable HTTP port (1-65535).",
    )
    return parser


def run_foldweave_mcp_server(argv: Sequence[str] | None = None) -> int:
    """Run STDIO by default or an explicitly loopback Streamable HTTP server."""

    options = build_foldweave_mcp_parser().parse_args(list(argv or ()))
    if not 1 <= options.port <= 65_535:
        build_foldweave_mcp_parser().error("--port must be between 1 and 65535")
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    server = build_foldweave_chatgpt_server(host=options.host, port=options.port)
    server.run(transport=options.transport)
    return 0


def _run_evidence(
    service: FoldweaveHostPlanningService,
    *,
    tool_name: Literal[
        "list_inventory_page",
        "read_text_excerpt",
        "inspect_markdown_links",
    ],
    job_id: str,
    call_id: str,
    **arguments: Any,
) -> CallToolResult:
    try:
        method = getattr(service, tool_name)
        job, result, error_code = method(
            job_id=job_id,
            call_id=call_id,
            **arguments,
        )
        output = FoldweaveEvidenceResultV1(
            job_id=job.job_id,
            job_revision=job.revision,
            evidence_fingerprint=(
                _require_chatgpt_authority(
                    job
                ).planning_state.evidence_state.evidence_fingerprint
            ),
            permitted_evidence_ids=(
                "initial_inventory",
                *(
                    record.fingerprint
                    for record in _require_chatgpt_authority(
                        job
                    ).planning_state.evidence_state.records
                ),
            ),
            tool_name=tool_name,
            call_id=call_id,
            result=result,
            error_code=error_code,
        )
        return _success(output, "Foldweave returned bounded hosted evidence.")
    except Exception as exc:
        return _failure(exc, "evidence_call_failed")


def _project_job_status(job: FolderRefactorJobV3) -> FoldweaveHostedJobStatusV1:
    authority = _require_chatgpt_authority(job)
    preview = job.preview
    state = authority.planning_state
    question = state.clarification_question
    output = FoldweaveHostedJobStatusV1(
        job_id=job.job_id,
        lifecycle=job.lifecycle.value,
        job_revision=job.revision,
        proposal_revision=job.proposal_revision,
        source_commitment=job.source_inventory.source_commitment,
        request_fingerprint=state.request_fingerprint,
        has_preview=preview is not None,
        candidate_fingerprint=(
            preview.compiled_candidate_fingerprint if preview is not None else None
        ),
        preview_fingerprint=(
            preview.preview_fingerprint if preview is not None else None
        ),
        clarification_question=question,
        clarification_question_fingerprint=_question_fingerprint(question),
        revision_attempts_remaining=max(0, 2 - job.revision_attempt_count),
        revision_failure_code=(
            job.revision_failure.code if job.revision_failure is not None else None
        ),
        blocker_code=job.blocker_code,
    )
    _assert_safe_boundary(output)
    return output


def _project_review(job: FolderRefactorJobV3) -> FoldweaveChatGptReviewV1:
    _require_chatgpt_authority(job)
    preview = job.preview
    candidate = job.candidate_plan
    if (
        preview is None
        or candidate is None
        or job.lifecycle
        not in {
            FolderJobLifecycleV3.REVIEWING,
            FolderJobLifecycleV3.REVISION_FAILED,
            FolderJobLifecycleV3.EXECUTING,
            FolderJobLifecycleV3.VERIFIED,
        }
    ):
        raise FoldweaveHostServiceError(
            "preview_unavailable",
            "The hosted job does not have a reviewable preview.",
        )
    revision_available = (
        job.lifecycle
        in {FolderJobLifecycleV3.REVIEWING, FolderJobLifecycleV3.REVISION_FAILED}
        and job.revision_attempt_count < 2
    )
    result = None
    if job.lifecycle is FolderJobLifecycleV3.VERIFIED:
        artifacts = job.verified_artifacts
        if artifacts is None:
            raise FoldweaveHostServiceError(
                "verified_artifacts_unavailable",
                "The verified hosted job has no proof identities.",
            )
        result = FoldweaveHostedVerifiedResultV1(
            complete_file_count=preview.counts.file_count,
            changed_path_count=preview.counts.changed_path_count,
            organized_tree_commitment=artifacts.organized_tree_commitment,
            change_file_fingerprint=artifacts.change_file_fingerprint,
        )
    output = FoldweaveChatGptReviewV1(
        state_version=job.revision,
        journey=(
            "apply"
            if preview.imported_change_file_fingerprint is not None
            else "organize"
        ),
        preview=preview,
        status=FoldweaveHostedReviewStatusV1(
            job_id=job.job_id,
            lifecycle=job.lifecycle.value,
            job_revision=job.revision,
            proposal_revision=job.proposal_revision,
            candidate_fingerprint=preview.compiled_candidate_fingerprint,
            preview_fingerprint=preview.preview_fingerprint,
            authorization_context_fingerprint=_authorization_context(job),
            revision_available=revision_available,
            revision_attempts_remaining=max(0, 2 - job.revision_attempt_count),
            revision_failure=(
                f"Revision failed: {job.revision_failure.code}."
                if job.revision_failure is not None
                else None
            ),
        ),
        result=result,
    )
    _assert_safe_boundary(output)
    return output


def _require_exact_preview_binding(
    job: FolderRefactorJobV3,
    *,
    proposal_revision: int,
    source_commitment: str,
    imported_change_file_fingerprint: str | None,
    match_report_fingerprint: str | None,
    authorization_context_fingerprint: str,
    expected_revision: int,
    preview_fingerprint: str,
    candidate_fingerprint: str,
) -> None:
    preview = job.preview
    candidate = job.candidate_plan
    if (
        preview is None
        or candidate is None
        or not (
            preview.expected_job_revision == expected_revision
            and job.proposal_revision == proposal_revision
            and job.source_inventory.source_commitment == source_commitment
            and preview.imported_change_file_fingerprint
            == imported_change_file_fingerprint
            and preview.match_report_fingerprint == match_report_fingerprint
            and preview.preview_fingerprint == preview_fingerprint
            and preview.compiled_candidate_fingerprint == candidate_fingerprint
            and canonical_sha256(candidate) == candidate_fingerprint
            and _authorization_context(job) == authorization_context_fingerprint
        )
    ):
        raise FoldweaveHostServiceError(
            "review_binding_mismatch",
            "The action targets a stale, changed, or unseen Foldweave preview.",
        )


def _authorization_context(job: FolderRefactorJobV3) -> str:
    preview = job.preview
    candidate = job.candidate_plan
    if preview is None or candidate is None:
        raise FoldweaveHostServiceError(
            "preview_unavailable",
            "Authorization requires a complete Foldweave preview.",
        )
    return _authorization_context_values(
        job,
        expected_job_revision=preview.expected_job_revision,
        preview_fingerprint=preview.preview_fingerprint,
        candidate_fingerprint=preview.compiled_candidate_fingerprint,
    )


def _require_static_preview_binding(
    job: FolderRefactorJobV3,
    *,
    proposal_revision: int,
    source_commitment: str,
    imported_change_file_fingerprint: str | None,
    match_report_fingerprint: str | None,
    authorization_context_fingerprint: str,
    expected_revision: int,
    preview_fingerprint: str,
    candidate_fingerprint: str,
) -> None:
    preview = job.preview
    candidate = job.candidate_plan
    if (
        preview is None
        or candidate is None
        or not (
            job.proposal_revision == proposal_revision
            and job.source_inventory.source_commitment == source_commitment
            and preview.imported_change_file_fingerprint
            == imported_change_file_fingerprint
            and preview.match_report_fingerprint == match_report_fingerprint
            and canonical_sha256(candidate) == candidate_fingerprint
            and _authorization_context_values(
                job,
                expected_job_revision=expected_revision,
                preview_fingerprint=preview_fingerprint,
                candidate_fingerprint=candidate_fingerprint,
            )
            == authorization_context_fingerprint
        )
    ):
        raise FoldweaveHostServiceError(
            "review_binding_mismatch",
            "The action targets a stale, changed, or unseen Foldweave preview.",
        )


def _authorization_context_values(
    job: FolderRefactorJobV3,
    *,
    expected_job_revision: int,
    preview_fingerprint: str,
    candidate_fingerprint: str,
) -> str:
    preview = job.preview
    candidate = job.candidate_plan
    if preview is None or candidate is None:
        raise FoldweaveHostServiceError(
            "preview_unavailable",
            "Authorization requires a complete Foldweave preview.",
        )
    return canonical_sha256(
        {
            "domain": "foldweave:chatgpt-authorization-context:v1",
            "job_id": job.job_id,
            "expected_job_revision": expected_job_revision,
            "proposal_revision": job.proposal_revision,
            "source_commitment": preview.source_commitment,
            "imported_change_file_fingerprint": (
                preview.imported_change_file_fingerprint
            ),
            "match_report_fingerprint": preview.match_report_fingerprint,
            "candidate_fingerprint": candidate_fingerprint,
            "preview_fingerprint": preview_fingerprint,
            "output_parent": job.output_parent.resolve(strict=False).as_posix(),
            "result_folder_name": candidate.result_folder_name,
        }
    )


def _require_chatgpt_authority(
    job: FolderRefactorJobV3,
) -> GptHostedJobAuthorityV3:
    authority = job.authority
    if not isinstance(authority, GptHostedJobAuthorityV3) or (
        authority.model_transport != "chatgpt_hosted"
    ):
        raise FoldweaveHostServiceError(
            "host_authority_mismatch",
            "The job does not use ChatGPT-hosted Foldweave planning.",
        )
    return authority


def _question_fingerprint(question: str | None) -> str | None:
    if question is None:
        return None
    return canonical_sha256(
        {
            "domain": "foldweave:host-clarification-question:v1",
            "text": question,
        }
    )


def _annotations(*, read_only: bool, idempotent: bool) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=False,
        idempotentHint=idempotent,
        openWorldHint=False,
    )


def _success(model: StrictFrozenModel, narration: str) -> Any:
    # FastMCP first validates a manual CallToolResult against the Python return
    # annotation. Preserve strict tuples and datetimes until MCP serialization.
    payload = model.model_dump(mode="python")
    _assert_safe_boundary(payload)
    _assert_safe_boundary(narration)
    return CallToolResult(
        content=[TextContent(type="text", text=narration)],
        structuredContent=payload,
        isError=False,
    )


def _failure(exc: Exception, fallback_code: str) -> NoReturn:
    code = fallback_code
    if isinstance(exc, FoldweaveHostServiceError):
        code = exc.code
    elif isinstance(exc, RuntimeError) and re.fullmatch(
        r"[a-z0-9_:-]{1,128}",
        str(exc),
    ):
        code = str(exc)
    if not re.fullmatch(r"[a-z0-9_:-]{1,128}", code):
        code = "foldweave_tool_failed"
    message = _PUBLIC_ERROR_MESSAGES.get(
        code,
        "Foldweave could not complete this hosted action.",
    )
    _assert_safe_boundary(message)
    raise ToolError(f"{code}: {message}")


_PUBLIC_ERROR_MESSAGES: Mapping[str, str] = {
    "evidence_disclosure_required": (
        "Foldweave requires acceptance of the bounded evidence disclosure."
    ),
    "clarification_conflict": (
        "Foldweave already bound a different clarification question."
    ),
    "clarification_answer_conflict": (
        "Foldweave already bound a different clarification answer."
    ),
    "clarification_binding_mismatch": (
        "The clarification answer targets a stale or different question."
    ),
    "clarification_idempotency_conflict": (
        "The clarification retry key is bound to another exact request."
    ),
    "preview_revision_mismatch": (
        "The requested Foldweave preview revision is no longer current."
    ),
    "preview_fingerprint_mismatch": (
        "The requested Foldweave preview fingerprint is no longer current."
    ),
    "review_binding_mismatch": (
        "The action targets a stale, changed, or unseen Foldweave preview."
    ),
    "verification_commitment_mismatch": (
        "Independent verification returned a different organized-tree identity."
    ),
}

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "accesstoken",
        "refreshtoken",
        "password",
        "clientsecret",
        "credential",
        "authorizationheader",
    }
)
_POSIX_ABSOLUTE = re.compile(r"(?:^|[\s\"'(])/(?!/)[^\s\"')]+")
_HOME_ABSOLUTE = re.compile(r"(?:^|[\s\"'(])~/")
_WINDOWS_ABSOLUTE = re.compile(r"(?:^|[\s\"'(])[A-Za-z]:[\\/]")
_UNC_ABSOLUTE = re.compile(r"(?:^|[\s\"'(])\\\\[^\\\s]+[\\/]")
_COMMON_LOCAL_PATH = re.compile(r"/(?:Users|Volumes|private|tmp|home)/")
_FILE_URL = re.compile(r"\bfile:/{1,3}", re.IGNORECASE)
_SECRET_VALUE = re.compile(
    r"(?:\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}|\bBearer\s+[A-Za-z0-9._~-]{8,})"
)


def _assert_safe_boundary(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        raise FoldweaveHostServiceError(
            "host_boundary_too_deep",
            "Hosted output exceeds the supported nesting depth.",
        )
    if isinstance(value, Path):
        raise FoldweaveHostServiceError(
            "local_path_disclosure_blocked",
            "Hosted output cannot contain local filesystem paths.",
        )
    if isinstance(value, str):
        if any(
            pattern.search(value)
            for pattern in (
                _POSIX_ABSOLUTE,
                _HOME_ABSOLUTE,
                _WINDOWS_ABSOLUTE,
                _UNC_ABSOLUTE,
                _COMMON_LOCAL_PATH,
                _FILE_URL,
            )
        ):
            raise FoldweaveHostServiceError(
                "local_path_disclosure_blocked",
                "Hosted output cannot contain local filesystem paths.",
            )
        if _SECRET_VALUE.search(value):
            raise FoldweaveHostServiceError(
                "credential_disclosure_blocked",
                "Hosted output cannot contain credential-like text.",
            )
        return
    if isinstance(value, StrictFrozenModel):
        _assert_safe_boundary(value.model_dump(mode="python"), depth=depth + 1)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _SECRET_KEYS:
                raise FoldweaveHostServiceError(
                    "credential_field_disclosure_blocked",
                    "Hosted output cannot contain credential fields.",
                )
            _assert_safe_boundary(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _assert_safe_boundary(item, depth=depth + 1)


def _load_widget_html(asset_root: Path | None) -> str:
    root = asset_root or _default_widget_asset_root()
    javascript_path = root / WIDGET_JS_NAME
    stylesheet_path = root / WIDGET_CSS_NAME
    if not javascript_path.is_file() or not stylesheet_path.is_file():
        raise RuntimeError(
            "Foldweave ChatGPT widget assets are unavailable; run the approved "
            "frontend production build before starting the MCP Apps server."
        )
    javascript = javascript_path.read_text(encoding="utf-8")
    stylesheet = stylesheet_path.read_text(encoding="utf-8")
    safe_javascript = re.sub(r"</script", r"<\\/script", javascript, flags=re.I)
    safe_stylesheet = re.sub(r"</style", r"<\\/style", stylesheet, flags=re.I)
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8"><meta name="viewport" '
        'content="width=device-width,initial-scale=1">'
        "<title>Foldweave structure review</title>"
        f"<style>{safe_stylesheet}</style></head><body>"
        '<div id="foldweave-chatgpt-widget-root"></div>'
        f'<script type="module">{safe_javascript}</script>'
        "</body></html>"
    )


def _default_widget_asset_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    candidates = []
    if frozen_root is not None:
        candidates.append(Path(frozen_root) / "web" / "dist" / "chatgpt-widget")
    module_path = Path(__file__).resolve()
    candidates.extend(
        (
            module_path.parent / "assets" / "chatgpt-widget",
            module_path.parents[2] / "web" / "dist" / "chatgpt-widget",
        )
    )
    for candidate in candidates:
        if (candidate / WIDGET_JS_NAME).is_file() and (
            candidate / WIDGET_CSS_NAME
        ).is_file():
            return candidate
    return candidates[-1]


if __name__ == "__main__":
    raise SystemExit(run_foldweave_mcp_server())
