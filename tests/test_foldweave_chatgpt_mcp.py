"""F0c protocol and boundary acceptance for hosted Foldweave MCP Apps."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from connected_change_fixtures import make_connected_change_fixture, tree_state
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult

from name_atlas.folder_refactor.connected_change.job_v3 import (
    GptHostedJobAuthorityV3,
)
from name_atlas.folder_refactor.contracts import FolderPlan, FolderPlanEntry
from name_atlas.folder_refactor.foldweave_host_contracts import (
    FolderHostPlanRevisionEntryV1,
    FolderHostPlanRevisionV1,
)
from name_atlas.folder_refactor.serialization import canonical_sha256
from name_atlas.foldweave_chatgpt_mcp import (
    SERVER_INSTRUCTIONS,
    WIDGET_MIME_TYPE,
    WIDGET_RESOURCE_URI,
    build_foldweave_chatgpt_server,
    build_foldweave_mcp_parser,
    run_foldweave_mcp_server,
)
from name_atlas.foldweave_host_service import FoldweaveHostPlanningService
from name_atlas.foldweave_launcher import run as run_foldweave
from name_atlas.foldweave_local_handles import FoldweaveLocalHandleStore
from name_atlas.foldweave_paths import FoldweavePaths
from name_atlas.native_bridge import (
    NativeOpenResult,
    NativeOpenStatus,
    NativePathRole,
    NativePathSelection,
    NativeSelectionStatus,
)

oslo_tz = ZoneInfo("Europe/Oslo")

EXPECTED_TOOLS = {
    "choose_local_item",
    "create_or_resume_planning_job",
    "plan_change",
    "list_inventory_page",
    "read_text_excerpt",
    "inspect_markdown_links",
    "request_clarification",
    "answer_clarification",
    "submit_plan",
    "get_compiler_failures",
    "revise_plan",
    "submit_plan_revision",
    "get_plan_preview",
    "job_status",
    "keep_previous_proposal",
    "accept_plan_and_create_copy",
    "verify_result",
}
READ_ONLY_TOOLS = {
    "get_compiler_failures",
    "get_plan_preview",
    "job_status",
    "verify_result",
}
WIDGET_CALLABLE_TOOLS = {
    "get_plan_preview",
    "keep_previous_proposal",
    "accept_plan_and_create_copy",
    "verify_result",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class HostedHarness:
    service: FoldweaveHostPlanningService
    server: Any
    source_handle: str
    output_handle: str
    fixture: Any
    output_root: Path


def _harness(tmp_path: Path) -> HostedHarness:
    fixture = make_connected_change_fixture(tmp_path / "projects")
    output = tmp_path / "output"
    output.mkdir()
    tokens = iter(("A" * 43, "B" * 43, "C" * 43, "D" * 43))
    handles = FoldweaveLocalHandleStore(
        clock=lambda: datetime(2026, 7, 19, 20, 0, tzinfo=oslo_tz),
        token_factory=lambda: next(tokens),
    )
    source_handle = handles.register(
        role=NativePathRole.SOURCE_FOLDER,
        path=fixture.sofia_root,
        channel="chatgpt_hosted",
    )
    output_handle = handles.register(
        role=NativePathRole.OUTPUT_PARENT,
        path=output,
        channel="chatgpt_hosted",
    )
    service = FoldweaveHostPlanningService(
        paths=FoldweavePaths(state_root=tmp_path / "state"),
        handle_store=handles,
        clock=lambda: datetime(2026, 7, 19, 20, 0, tzinfo=oslo_tz),
    )
    return HostedHarness(
        service=service,
        server=build_foldweave_chatgpt_server(service),
        source_handle=source_handle.handle,
        output_handle=output_handle.handle,
        fixture=fixture,
        output_root=output,
    )


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await server.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    assert result.isError is False
    assert result.structuredContent is not None
    return result.structuredContent


def _plan_for(harness: HostedHarness, job_id: str) -> FolderPlan:
    job = harness.service.status(job_id)
    assert isinstance(job.authority, GptHostedJobAuthorityV3)
    return FolderPlan(
        source_commitment=job.source_inventory.source_commitment,
        request_fingerprint=job.authority.planning_state.request_fingerprint,
        request_scope="rename_and_move_every_file",
        evidence_fingerprint=(
            job.authority.planning_state.evidence_state.evidence_fingerprint
        ),
        result_folder_name=harness.fixture.result_name,
        entries=tuple(
            FolderPlanEntry(
                file_id=item.file_id,
                original_path=item.relative_path,
                proposed_target=harness.fixture.target_paths[item.relative_path],
                rationale="Organize the connected project for handoff.",
                evidence_ids=("initial_inventory",),
            )
            for item in job.source_inventory.files
            if not item.protected
        ),
        exclusions=(),
    )


async def _create_review(harness: HostedHarness, *, key: str) -> dict[str, Any]:
    started = await _call(
        harness.server,
        "plan_change",
        {
            "source_handle": harness.source_handle,
            "output_handle": harness.output_handle,
            "request": harness.fixture.request,
            "evidence_disclosure_acknowledged": True,
            "idempotency_key": key,
        },
    )
    plan = _plan_for(harness, started["job_id"])
    submitted = await _call(
        harness.server,
        "submit_plan",
        {
            "job_id": started["job_id"],
            "call_id": f"{key}-plan",
            "plan": plan.model_dump(mode="json"),
        },
    )
    assert submitted["lifecycle"] == "reviewing"
    return submitted


@pytest.mark.anyio
async def test_server_metadata_widget_resource_and_tool_bounds() -> None:
    server = build_foldweave_chatgpt_server()
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == len(EXPECTED_TOOLS)
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is (tool.name in READ_ONLY_TOOLS)
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is (tool.name != "choose_local_item")
        assert tool.annotations.openWorldHint is False
        assert tool.outputSchema is not None
        assert tool.outputSchema["additionalProperties"] is False
        assert tool.inputSchema["type"] == "object"
        if tool.name == "get_plan_preview":
            assert tool.meta is not None
            assert tool.meta["openai/outputTemplate"] == WIDGET_RESOURCE_URI
            assert tool.meta["ui"] == {
                "resourceUri": WIDGET_RESOURCE_URI,
                "visibility": ["model", "app"],
            }
        else:
            assert not tool.meta or "openai/outputTemplate" not in tool.meta
        if tool.name in WIDGET_CALLABLE_TOOLS:
            assert tool.meta is not None
            if tool.name != "get_plan_preview":
                assert tool.meta["ui"] == {"visibility": ["app"]}
            assert tool.meta["openai/widgetAccessible"] is True

    resources = await server.list_resources()
    assert len(resources) == 1
    resource = resources[0]
    assert str(resource.uri) == WIDGET_RESOURCE_URI
    assert resource.mimeType == WIDGET_MIME_TYPE
    assert resource.meta["ui"] == {
        "csp": {"connectDomains": [], "resourceDomains": []},
        "prefersBorder": True,
    }
    assert resource.meta is not None
    assert resource.meta["openai/widgetCSP"] == {
        "connect_domains": [],
        "resource_domains": [],
    }
    contents = list(await server.read_resource(WIDGET_RESOURCE_URI))
    assert len(contents) == 1
    html = contents[0].content
    assert contents[0].mime_type == WIDGET_MIME_TYPE
    assert '<div id="foldweave-chatgpt-widget-root"></div>' in html
    assert '<script type="module">' in html
    assert "fw-chatgpt-widget" in html
    assert "ui/initialize" in html
    assert "ui/notifications/tool-result" in html
    assert '<script type="module" src=' not in html
    assert '<link rel="stylesheet"' not in html

    assert SERVER_INSTRUCTIONS.startswith("Foldweave never changes a selected source.")
    assert "Responses API" in SERVER_INSTRUCTIONS[:512]
    assert "Receiver preparation" in SERVER_INSTRUCTIONS


@pytest.mark.anyio
async def test_complete_hosted_origin_review_revision_accept_and_verify(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    source_before = tree_state(harness.fixture.sofia_root)
    start_arguments = {
        "source_handle": harness.source_handle,
        "output_handle": harness.output_handle,
        "request": harness.fixture.request,
        "evidence_disclosure_acknowledged": True,
        "idempotency_key": "f0c-origin",
    }
    started = await _call(harness.server, "plan_change", start_arguments)
    repeated = await _call(
        harness.server,
        "create_or_resume_planning_job",
        start_arguments,
    )
    assert repeated == started
    job_id = started["job_id"]
    assert started["lifecycle"] == "planning"
    assert started["direct_api_used"] is False
    assert started["direct_budget_reserved"] is False

    inventory = await _call(
        harness.server,
        "list_inventory_page",
        {"job_id": job_id, "call_id": "inventory-1", "page_size": 100},
    )
    assert inventory["result"]["items"]
    assert all("sha256" not in item for item in inventory["result"]["items"])
    assert inventory["evidence_fingerprint"] == (
        harness.service.status(
            job_id
        ).authority.planning_state.evidence_state.evidence_fingerprint
    )
    inventory_state = harness.service.status(
        job_id
    ).authority.planning_state.evidence_state
    assert inventory["permitted_evidence_ids"] == (
        "initial_inventory",
        *(record.fingerprint for record in inventory_state.records),
    )

    durable = harness.service.status(job_id)
    markdown = next(
        item
        for item in durable.source_inventory.files
        if item.relative_path == "notes/client-brief.md"
    )
    excerpt = await _call(
        harness.server,
        "read_text_excerpt",
        {
            "job_id": job_id,
            "call_id": "excerpt-1",
            "file_id": markdown.file_id,
            "start_byte": 0,
            "max_bytes": 16_384,
        },
    )
    assert excerpt["result"] is not None
    assert excerpt["evidence_fingerprint"] == (
        harness.service.status(
            job_id
        ).authority.planning_state.evidence_state.evidence_fingerprint
    )
    excerpt_state = harness.service.status(
        job_id
    ).authority.planning_state.evidence_state
    assert excerpt["permitted_evidence_ids"] == (
        "initial_inventory",
        *(record.fingerprint for record in excerpt_state.records),
    )
    links = await _call(
        harness.server,
        "inspect_markdown_links",
        {
            "job_id": job_id,
            "call_id": "links-1",
            "file_id": markdown.file_id,
            "page_size": 100,
        },
    )
    assert links["result"]["references"]
    assert links["evidence_fingerprint"] == (
        harness.service.status(
            job_id
        ).authority.planning_state.evidence_state.evidence_fingerprint
    )
    links_state = harness.service.status(job_id).authority.planning_state.evidence_state
    assert links["permitted_evidence_ids"] == (
        "initial_inventory",
        *(record.fingerprint for record in links_state.records),
    )

    clarification = await _call(
        harness.server,
        "request_clarification",
        {
            "job_id": job_id,
            "expected_revision": links["job_revision"],
            "question": "Should the approved material remain separate?",
            "idempotency_key": "clarification-question-1",
        },
    )
    assert clarification["lifecycle"] == "awaiting_clarification"
    clarification_again = await _call(
        harness.server,
        "request_clarification",
        {
            "job_id": job_id,
            "expected_revision": links["job_revision"],
            "question": "Should the approved material remain separate?",
            "idempotency_key": "clarification-question-1",
        },
    )
    assert clarification_again == clarification
    with pytest.raises(ToolError) as question_conflict:
        await harness.server.call_tool(
            "request_clarification",
            {
                "job_id": job_id,
                "expected_revision": links["job_revision"],
                "question": "Should all material be combined?",
                "idempotency_key": "clarification-question-1",
            },
        )
    assert "clarification_idempotency_conflict" in str(question_conflict.value)
    with pytest.raises(ToolError) as stale_answer:
        await harness.server.call_tool(
            "answer_clarification",
            {
                "job_id": job_id,
                "expected_revision": links["job_revision"],
                "question_fingerprint": clarification[
                    "clarification_question_fingerprint"
                ],
                "answer": "Yes, keep approved and research material separate.",
                "idempotency_key": "clarification-answer-stale",
            },
        )
    assert "clarification_binding_mismatch" in str(stale_answer.value)
    with pytest.raises(ToolError) as wrong_question:
        await harness.server.call_tool(
            "answer_clarification",
            {
                "job_id": job_id,
                "expected_revision": clarification["job_revision"],
                "question_fingerprint": "0" * 64,
                "answer": "Yes, keep approved and research material separate.",
                "idempotency_key": "clarification-answer-wrong-question",
            },
        )
    assert "clarification_binding_mismatch" in str(wrong_question.value)
    answered = await _call(
        harness.server,
        "answer_clarification",
        {
            "job_id": job_id,
            "expected_revision": clarification["job_revision"],
            "question_fingerprint": clarification["clarification_question_fingerprint"],
            "answer": "Yes, keep approved and research material separate.",
            "idempotency_key": "clarification-answer-1",
        },
    )
    assert answered["lifecycle"] == "planning"
    answered_again = await _call(
        harness.server,
        "answer_clarification",
        {
            "job_id": job_id,
            "expected_revision": clarification["job_revision"],
            "question_fingerprint": clarification["clarification_question_fingerprint"],
            "answer": "Yes, keep approved and research material separate.",
            "idempotency_key": "clarification-answer-1",
        },
    )
    assert answered_again == answered
    with pytest.raises(ToolError) as answer_conflict:
        await harness.server.call_tool(
            "answer_clarification",
            {
                "job_id": job_id,
                "expected_revision": clarification["job_revision"],
                "question_fingerprint": clarification[
                    "clarification_question_fingerprint"
                ],
                "answer": "No, combine all material.",
                "idempotency_key": "clarification-answer-1",
            },
        )
    assert "clarification_idempotency_conflict" in str(answer_conflict.value)

    status = await _call(harness.server, "job_status", {"job_id": job_id})
    assert status == answered
    plan = _plan_for(harness, job_id)
    submitted = await _call(
        harness.server,
        "submit_plan",
        {
            "job_id": job_id,
            "call_id": "plan-1",
            "plan": plan.model_dump(mode="json"),
        },
    )
    assert submitted["lifecycle"] == "reviewing"
    submitted_again = await _call(
        harness.server,
        "submit_plan",
        {
            "job_id": job_id,
            "call_id": "plan-1",
            "plan": plan.model_dump(mode="json"),
        },
    )
    assert submitted_again == submitted
    failures = await _call(
        harness.server,
        "get_compiler_failures",
        {"job_id": job_id},
    )
    assert failures["failures"] == ()

    review = await _call(
        harness.server,
        "get_plan_preview",
        {
            "job_id": job_id,
            "expected_revision": submitted["job_revision"],
            "preview_fingerprint": submitted["preview_fingerprint"],
            "channel": "chatgpt_hosted",
        },
    )
    assert review["schema_version"] == "foldweave-chatgpt-review.v1"
    assert review["status"]["lifecycle"] == "reviewing"
    assert review["status"]["direct_api_used"] is False
    assert review["status"]["direct_budget_reserved"] is False
    assert not tuple(harness.output_root.iterdir())

    reviewed_job = harness.service.status(job_id)
    assert reviewed_job.candidate_plan is not None
    first = next(
        item for item in reviewed_job.candidate_plan.file_mappings if not item.protected
    )
    revision_reserved = await _call(
        harness.server,
        "revise_plan",
        {
            "job_id": job_id,
            "expected_revision": review["status"]["job_revision"],
            "candidate_fingerprint": review["status"]["candidate_fingerprint"],
            "preview_fingerprint": review["status"]["preview_fingerprint"],
            "instruction": "Place the first reviewed file in a revised folder.",
            "idempotency_key": "revision-1",
        },
    )
    assert revision_reserved["lifecycle"] == "revising"
    sparse = FolderHostPlanRevisionV1(
        base_candidate_fingerprint=canonical_sha256(reviewed_job.candidate_plan),
        entries=(
            FolderHostPlanRevisionEntryV1(
                file_id=first.file_id,
                replacement_target_path=f"revised/{Path(first.target_path).name}",
                rationale="Apply the user's exact revision.",
                evidence_ids=("initial_inventory",),
            ),
        ),
    )
    revised = await _call(
        harness.server,
        "submit_plan_revision",
        {
            "job_id": job_id,
            "call_id": "revision-submit-1",
            "revision": sparse.model_dump(mode="json"),
        },
    )
    assert revised["lifecycle"] == "reviewing"
    assert revised["proposal_revision"] == 1
    revised_review = await _call(
        harness.server,
        "get_plan_preview",
        {
            "job_id": job_id,
            "expected_revision": revised["job_revision"],
            "preview_fingerprint": revised["preview_fingerprint"],
            "channel": "chatgpt_hosted",
        },
    )
    assert revised_review["preview"]["proposal_revision"] == 1

    exact = revised_review["status"]
    preview = revised_review["preview"]
    acceptance_arguments = {
        "job_id": job_id,
        "proposal_revision": preview["proposal_revision"],
        "source_commitment": preview["source_commitment"],
        "imported_change_file_fingerprint": preview["imported_change_file_fingerprint"],
        "match_report_fingerprint": preview["match_report_fingerprint"],
        "authorization_context_fingerprint": exact["authorization_context_fingerprint"],
        "expected_revision": exact["job_revision"],
        "preview_fingerprint": exact["preview_fingerprint"],
        "candidate_fingerprint": exact["candidate_fingerprint"],
        "idempotency_key": "accept-1",
        "channel": "chatgpt_hosted",
    }
    accepted = await _call(
        harness.server,
        "accept_plan_and_create_copy",
        acceptance_arguments,
    )
    assert accepted["status"]["lifecycle"] == "verified"
    assert accepted["result"]["source_unchanged"] is True
    accepted_retry = await _call(
        harness.server,
        "accept_plan_and_create_copy",
        acceptance_arguments,
    )
    assert accepted_retry == accepted
    conflicting_acceptance = {
        **acceptance_arguments,
        "candidate_fingerprint": "0" * 64,
    }
    with pytest.raises(ToolError):
        await harness.server.call_tool(
            "accept_plan_and_create_copy",
            conflicting_acceptance,
        )
    verification = await _call(
        harness.server,
        "verify_result",
        {
            "job_id": job_id,
            "organized_tree_commitment": accepted["result"][
                "organized_tree_commitment"
            ],
            "channel": "chatgpt_hosted",
        },
    )
    assert verification["verification"] == "verified"
    assert verification["failed_check_ids"] == ()
    assert tree_state(harness.fixture.sofia_root) == source_before

    encoded = json.dumps(accepted, sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "/Users/" not in encoded
    assert "api_key" not in encoded.lower()


@pytest.mark.anyio
async def test_fastmcp_polling_is_byte_read_only_after_source_mutation(
    tmp_path: Path,
) -> None:
    """All declared read-only hosted polling tools preserve durable job bytes."""

    harness = _harness(tmp_path)
    reviewed = await _create_review(harness, key="polling-read-only")
    job_id = reviewed["job_id"]
    job = harness.service.status(job_id)
    assert job.preview is not None
    job_bytes = job.job_path.read_bytes()

    changed_relative = next(
        item.relative_path for item in job.source_inventory.files if not item.protected
    )
    changed = harness.fixture.sofia_root / changed_relative
    changed.write_bytes(changed.read_bytes() + b"changed after review\n")

    status = await _call(harness.server, "job_status", {"job_id": job_id})
    assert status["lifecycle"] == "reviewing"
    preview = await _call(
        harness.server,
        "get_plan_preview",
        {
            "job_id": job_id,
            "expected_revision": reviewed["job_revision"],
            "preview_fingerprint": reviewed["preview_fingerprint"],
            "channel": "chatgpt_hosted",
        },
    )
    assert preview["status"]["job_id"] == status["job_id"]
    assert preview["status"]["job_revision"] == status["job_revision"]
    assert preview["status"]["preview_fingerprint"] == status["preview_fingerprint"]
    failures = await _call(
        harness.server,
        "get_compiler_failures",
        {"job_id": job_id},
    )
    assert failures["failures"] == ()
    assert job.job_path.read_bytes() == job_bytes

    with pytest.raises(ToolError):
        await harness.server.call_tool(
            "revise_plan",
            {
                "job_id": job_id,
                "expected_revision": reviewed["job_revision"],
                "candidate_fingerprint": reviewed["candidate_fingerprint"],
                "preview_fingerprint": reviewed["preview_fingerprint"],
                "instruction": "Move the first file into a new folder.",
                "idempotency_key": "stale-mutation",
            },
        )
    stale = harness.service.status(job_id)
    assert stale.lifecycle.value == "stale"
    assert stale.staleness is not None
    assert stale.staleness.code == "source_changed"
    assert job.job_path.read_bytes() != job_bytes


@pytest.mark.anyio
async def test_failed_revision_keeps_and_rebinds_previous_preview(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    submitted = await _create_review(harness, key="failed-revision")
    job = harness.service.status(submitted["job_id"])
    assert job.candidate_plan is not None
    mutable = tuple(
        item for item in job.candidate_plan.file_mappings if not item.protected
    )
    first, second = mutable[:2]
    reserved = await _call(
        harness.server,
        "revise_plan",
        {
            "job_id": job.job_id,
            "expected_revision": submitted["job_revision"],
            "candidate_fingerprint": submitted["candidate_fingerprint"],
            "preview_fingerprint": submitted["preview_fingerprint"],
            "instruction": "Move the first file onto the second file's target.",
            "idempotency_key": "failed-revision-reserve",
        },
    )
    assert reserved["lifecycle"] == "revising"
    collision = FolderHostPlanRevisionV1(
        base_candidate_fingerprint=canonical_sha256(job.candidate_plan),
        entries=(
            FolderHostPlanRevisionEntryV1(
                file_id=first.file_id,
                replacement_target_path=second.target_path,
                rationale="Apply the requested target.",
                evidence_ids=("initial_inventory",),
            ),
        ),
    )
    failed = await _call(
        harness.server,
        "submit_plan_revision",
        {
            "job_id": job.job_id,
            "call_id": "failed-revision-submit",
            "revision": collision.model_dump(mode="json"),
        },
    )
    assert failed["lifecycle"] == "revision_failed"
    assert failed["revision_failure_code"] is not None
    failed_review = await _call(
        harness.server,
        "get_plan_preview",
        {
            "job_id": job.job_id,
            "expected_revision": failed["job_revision"],
            "preview_fingerprint": failed["preview_fingerprint"],
            "channel": "chatgpt_hosted",
        },
    )
    assert failed_review["status"]["lifecycle"] == "revision_failed"
    assert failed_review["status"]["revision_failure"] is not None
    preview = failed_review["preview"]
    status = failed_review["status"]
    keep_arguments = {
        "job_id": job.job_id,
        "proposal_revision": preview["proposal_revision"],
        "source_commitment": preview["source_commitment"],
        "imported_change_file_fingerprint": preview["imported_change_file_fingerprint"],
        "match_report_fingerprint": preview["match_report_fingerprint"],
        "authorization_context_fingerprint": status[
            "authorization_context_fingerprint"
        ],
        "expected_revision": status["job_revision"],
        "preview_fingerprint": status["preview_fingerprint"],
        "candidate_fingerprint": status["candidate_fingerprint"],
        "idempotency_key": "keep-previous-1",
        "channel": "chatgpt_hosted",
    }
    kept = await _call(
        harness.server,
        "keep_previous_proposal",
        keep_arguments,
    )
    assert kept["status"]["lifecycle"] == "reviewing"
    assert kept["status"]["revision_failure"] is None
    kept_retry = await _call(
        harness.server,
        "keep_previous_proposal",
        keep_arguments,
    )
    assert kept_retry == kept
    assert not tuple(harness.output_root.iterdir())


class _FixedNativeBridge:
    def __init__(self, selected: Path) -> None:
        self._selected = selected

    async def choose_path(self, role: NativePathRole) -> NativePathSelection:
        assert role is NativePathRole.SOURCE_FOLDER
        return NativePathSelection(
            status=NativeSelectionStatus.SELECTED,
            path=self._selected,
        )

    async def show_in_finder(self, path: Path) -> NativeOpenResult:
        del path
        return NativeOpenResult(status=NativeOpenStatus.OPENED)


@pytest.mark.anyio
async def test_local_selection_returns_only_an_opaque_handle(tmp_path: Path) -> None:
    selected = tmp_path / "private-project"
    selected.mkdir()
    handles = FoldweaveLocalHandleStore(
        clock=lambda: datetime(2026, 7, 19, 20, 0, tzinfo=oslo_tz),
        token_factory=lambda: "Z" * 43,
    )
    service = FoldweaveHostPlanningService(
        paths=FoldweavePaths(state_root=tmp_path / "state"),
        handle_store=handles,
        native_bridge=_FixedNativeBridge(selected),
        clock=lambda: datetime(2026, 7, 19, 20, 0, tzinfo=oslo_tz),
    )
    result = await _call(
        build_foldweave_chatgpt_server(service),
        "choose_local_item",
        {"role": "source_folder"},
    )
    assert result["status"] == "selected"
    assert result["item"]["handle"] == f"fw_{'Z' * 43}"
    assert result["item"]["display_name"] == selected.name
    assert str(selected) not in json.dumps(result, default=str)


@pytest.mark.anyio
async def test_sensitive_excerpt_is_persisted_locally_but_not_returned(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    fake_local_path = "/".join(("", "Users", "alice", "private.txt"))
    fake_project_key = "sk-proj-" + ("X" * 32)
    sensitive = f"Local {fake_local_path} and {fake_project_key}"
    (source / "note.md").write_text(sensitive, encoding="utf-8")
    tokens = iter(("X" * 43, "Y" * 43))
    handles = FoldweaveLocalHandleStore(
        clock=lambda: datetime(2026, 7, 19, 20, 0, tzinfo=oslo_tz),
        token_factory=lambda: next(tokens),
    )
    source_handle = handles.register(
        role=NativePathRole.SOURCE_FOLDER,
        path=source,
        channel="chatgpt_hosted",
    )
    output_handle = handles.register(
        role=NativePathRole.OUTPUT_PARENT,
        path=output,
        channel="chatgpt_hosted",
    )
    service = FoldweaveHostPlanningService(
        paths=FoldweavePaths(state_root=tmp_path / "state"),
        handle_store=handles,
        clock=lambda: datetime(2026, 7, 19, 20, 0, tzinfo=oslo_tz),
    )
    server = build_foldweave_chatgpt_server(service)
    started = await _call(
        server,
        "plan_change",
        {
            "source_handle": source_handle.handle,
            "output_handle": output_handle.handle,
            "request": "Organize the note.",
            "evidence_disclosure_acknowledged": True,
            "idempotency_key": "sensitive-boundary",
        },
    )
    file_id = service.status(started["job_id"]).source_inventory.files[0].file_id
    with pytest.raises(ToolError) as error:
        await server.call_tool(
            "read_text_excerpt",
            {
                "job_id": started["job_id"],
                "call_id": "sensitive-excerpt",
                "file_id": file_id,
                "start_byte": 0,
                "max_bytes": 16_384,
            },
        )
    rendered = str(error.value)
    assert "local_path_disclosure_blocked" in rendered
    assert "/Users/alice" not in rendered
    assert "SUPERSECRETVALUE" not in rendered
    durable = service.status(started["job_id"])
    assert len(durable.authority.planning_state.evidence_state.records) == 1


def test_mcp_launcher_dispatch_and_loopback_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import name_atlas.foldweave_chatgpt_mcp as module

    observed: dict[str, Any] = {}

    class _FakeServer:
        def run(self, *, transport: str) -> None:
            observed["transport"] = transport

    def build_fake(*, host: str, port: int) -> _FakeServer:
        observed["host"] = host
        observed["port"] = port
        return _FakeServer()

    monkeypatch.setattr(module, "build_foldweave_chatgpt_server", build_fake)
    assert (
        run_foldweave_mcp_server(
            [
                "--transport",
                "streamable-http",
                "--host",
                "localhost",
                "--port",
                "8765",
            ]
        )
        == 0
    )
    assert observed == {
        "host": "localhost",
        "port": 8765,
        "transport": "streamable-http",
    }
    monkeypatch.setattr(module, "run_foldweave_mcp_server", lambda argv: len(argv))
    assert run_foldweave(["mcp", "--transport", "stdio"]) == 2
    with pytest.raises(SystemExit):
        build_foldweave_mcp_parser().parse_args(["--host", "0.0.0.0"])


@pytest.mark.anyio
async def test_real_stdio_protocol_is_provider_free_and_path_safe(
    tmp_path: Path,
) -> None:
    environment = dict(os.environ)
    environment.pop("OPENAI_API_KEY", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    repository = Path(__file__).resolve().parents[1]
    stderr_path = tmp_path / "foldweave-mcp-stderr.log"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "name_atlas.foldweave_launcher", "mcp"],
        cwd=str(repository),
        env=environment,
    )
    with stderr_path.open("w", encoding="utf-8") as stderr:
        async with stdio_client(parameters, errlog=stderr) as streams:
            read_stream, write_stream = streams
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.instructions == SERVER_INSTRUCTIONS
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS
                resources = await session.list_resources()
                assert len(resources.resources) == 1
                assert str(resources.resources[0].uri) == WIDGET_RESOURCE_URI
                widget = await session.read_resource(WIDGET_RESOURCE_URI)
                assert widget.contents[0].mimeType == WIDGET_MIME_TYPE
                assert "foldweave-chatgpt-widget-root" in widget.contents[0].text
                result = await session.call_tool(
                    "job_status",
                    {"job_id": "0" * 32},
                )
                assert result.isError is True
                text = " ".join(
                    item.text for item in result.content if hasattr(item, "text")
                )
                assert "job_handle_invalid" in text
                assert "/Users/" not in text
                assert "OPENAI_API_KEY" not in text

    assert stderr_path.read_text(encoding="utf-8") == ""


def test_mcp_module_has_no_direct_provider_or_budget_import() -> None:
    import name_atlas.foldweave_chatgpt_mcp as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    forbidden = (
        "foldweave_provider_factory",
        "api_budget",
        "OpenAI",
        "from openai",
        "import openai",
    )
    assert not tuple(item for item in forbidden if item in source)
