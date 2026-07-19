"""Semantic service checks for the seven-tool MCP adapter."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from connected_change_fixtures import make_connected_change_fixture

from name_atlas.folder_refactor.connected_change.job_service import (
    ConnectedChangeJobService,
    default_connected_change_job_path,
)
from name_atlas.folder_refactor.connected_change.job_v2 import (
    FolderRefactorJobV2,
    FolderRefactorJobV2Store,
    GptPlannedJobAuthorityV2,
)
from name_atlas.folder_refactor.connected_change.planning import (
    ConnectedOriginPlanningService,
)
from name_atlas.folder_refactor.connected_change.service import (
    create_connected_change_origin,
)
from name_atlas.folder_refactor.demo_fixtures import (
    AMBIGUITY_ANSWER,
    AMBIGUITY_REQUEST,
    HERO_REQUEST,
    materialize_ambiguity_fixture,
    materialize_hero_fixture,
)
from name_atlas.folder_refactor.inventory import scan_folder
from name_atlas.mcp_contracts import (
    AnswerClarificationRequest,
    ApplyChangeFileRequest,
    JobHandleRequest,
    PlanAndCreateCopyRequest,
    RecreateOriginalRequest,
    VerifyResultRequest,
)
from name_atlas.mcp_service import NameAtlasMcpService


async def _wait_for_lifecycle(
    service: NameAtlasMcpService,
    handle: str,
    *lifecycles: str,
) -> object:
    for _attempt in range(500):
        status = await service.job_status(JobHandleRequest(job_handle=handle))
        if status.lifecycle in lifecycles:
            return status
        await asyncio.sleep(0.02)
    raise AssertionError(f"Durable job did not reach {lifecycles}: {status}")


def _origin_request(
    *,
    source: Path,
    output: Path,
    key: str,
    request: str = HERO_REQUEST,
    mode: str = "replay",
) -> PlanAndCreateCopyRequest:
    return PlanAndCreateCopyRequest(
        source_root=str(source),
        output_parent=str(output),
        user_request=request,
        mode=mode,
        idempotency_key=key,
        evidence_disclosure_acknowledged=True,
    )


@pytest.mark.anyio
async def test_consent_and_missing_live_key_are_nonmutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    budget = runtime / ".name-atlas" / "api_budget.json"
    budget.parent.mkdir(parents=True)
    budget.write_bytes(b"budget-sentinel\n")
    service = NameAtlasMcpService(project_root=runtime)

    refused = await service.plan_and_create_copy(
        PlanAndCreateCopyRequest(
            source_root="/does/not/exist",
            output_parent="/also/absent",
            user_request="Organize this project.",
            mode="replay",
            idempotency_key="mcp-consent-refusal-0001",
            evidence_disclosure_acknowledged=False,
        )
    )
    assert refused.status == "consent_required"
    assert refused.job_handle is None
    assert not (runtime / ".name-atlas" / "jobs").exists()
    assert budget.read_bytes() == b"budget-sentinel\n"

    fixture = materialize_hero_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    missing_key = await service.plan_and_create_copy(
        _origin_request(
            source=fixture.sofia_root,
            output=output,
            key="mcp-live-without-key-0001",
            mode="live",
        )
    )
    assert missing_key.status == "blocked"
    assert missing_key.blocker_code == "live_credential_missing"
    assert not (runtime / ".name-atlas" / "jobs").exists()
    assert budget.read_bytes() == b"budget-sentinel\n"


@pytest.mark.anyio
async def test_recorded_origin_is_restart_safe_and_exactly_idempotent(
    tmp_path: Path,
) -> None:
    fixture = materialize_hero_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    key = "mcp-recorded-origin-0001"
    request = _origin_request(
        source=fixture.sofia_root,
        output=output,
        key=key,
    )
    source_before = scan_folder(fixture.sofia_root).inventory

    first_service = NameAtlasMcpService(project_root=runtime)
    started = await first_service.plan_and_create_copy(request)
    assert started.job_handle is not None
    assert started.job_id == started.job_handle
    assert started.execution_origin == "gpt_planned"
    assert started.provider_kind == "recorded_replay"

    restarted_service = NameAtlasMcpService(project_root=runtime)
    terminal = await _wait_for_lifecycle(
        restarted_service,
        started.job_handle,
        "verified",
        "blocked",
    )
    assert terminal.lifecycle == "verified"
    change_file = await restarted_service.get_change_file(
        JobHandleRequest(job_handle=started.job_handle)
    )
    assert change_file.status == "verified"
    assert change_file.change_file_path is not None

    moved_source = tmp_path / "source-moved-after-verification"
    shutil.move(fixture.sofia_root, moved_source)
    retry = await restarted_service.plan_and_create_copy(request)
    assert retry.job_handle == started.job_handle
    assert retry.lifecycle == "verified"
    assert retry.result_root == terminal.result_root
    assert scan_folder(moved_source).inventory == source_before
    assert len(tuple((runtime / ".name-atlas" / "jobs").glob("*.json"))) == 1

    mode_conflict = await restarted_service.plan_and_create_copy(
        request.model_copy(update={"mode": "live"})
    )
    assert mode_conflict.status == "blocked"
    assert mode_conflict.blocker_code == "idempotency_key_conflict"


@pytest.mark.anyio
async def test_startup_recovery_resumes_an_abandoned_durable_origin(
    tmp_path: Path,
) -> None:
    fixture = materialize_hero_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    planner = ConnectedOriginPlanningService()
    abandoned = await asyncio.to_thread(
        planner.create,
        source_root=fixture.sofia_root,
        output_parent=output,
        job_path=default_connected_change_job_path(project_root=runtime),
        request=HERO_REQUEST,
        idempotency_key="mcp-abandoned-origin-0001",
        provider_kind="recorded_replay",
    )
    persisted_before = abandoned.job_path.read_bytes()

    restarted = NameAtlasMcpService(project_root=runtime)
    idle = await restarted.job_status(JobHandleRequest(job_handle=abandoned.job_id))
    assert idle.lifecycle == "planning"
    assert idle.active_operation is False
    assert abandoned.job_path.read_bytes() == persisted_before

    assert await restarted.recover_nonterminal_jobs() == 1
    terminal = await _wait_for_lifecycle(
        restarted,
        abandoned.job_id,
        "verified",
        "blocked",
    )
    assert terminal.lifecycle == "verified"
    await restarted.wait_for_operations()
    assert await restarted.recover_nonterminal_jobs() == 0


@pytest.mark.anyio
async def test_live_startup_without_credential_reports_truthful_paused_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = materialize_hero_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    planner = ConnectedOriginPlanningService()
    abandoned = await asyncio.to_thread(
        planner.create,
        source_root=fixture.sofia_root,
        output_parent=output,
        job_path=default_connected_change_job_path(project_root=runtime),
        request=HERO_REQUEST,
        idempotency_key="mcp-abandoned-live-origin-01",
        provider_kind="live",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    restarted = NameAtlasMcpService(project_root=runtime)
    assert await restarted.recover_nonterminal_jobs() == 0
    status = await restarted.job_status(JobHandleRequest(job_handle=abandoned.job_id))

    assert status.status == "accepted"
    assert status.lifecycle == "planning"
    assert status.active_operation is False
    assert "paused" in status.message
    assert "repeat the exact plan_and_create_copy request" in status.message


@pytest.mark.anyio
async def test_startup_recovery_waits_for_an_overlapping_durable_writer(
    tmp_path: Path,
) -> None:
    fixture = materialize_hero_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    planner = ConnectedOriginPlanningService()
    abandoned = await asyncio.to_thread(
        planner.create,
        source_root=fixture.sofia_root,
        output_parent=output,
        job_path=default_connected_change_job_path(project_root=runtime),
        request=HERO_REQUEST,
        idempotency_key="mcp-overlapping-recovery-001",
        provider_kind="recorded_replay",
    )
    store = FolderRefactorJobV2Store(abandoned.job_path)
    restarted = NameAtlasMcpService(project_root=runtime)

    with store.writer():
        assert await restarted.recover_nonterminal_jobs() == 1
        await asyncio.sleep(0.1)

    terminal = await _wait_for_lifecycle(
        restarted,
        abandoned.job_id,
        "verified",
        "blocked",
    )
    assert terminal.lifecycle == "verified"


@pytest.mark.anyio
async def test_startup_recovery_resumes_an_abandoned_receiver_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_connected_change_fixture(tmp_path / "projects")
    origin_output = tmp_path / "origin-output"
    receiver_output = tmp_path / "receiver-output"
    origin_output.mkdir()
    receiver_output.mkdir()
    origin = create_connected_change_origin(
        source_root=fixture.sofia_root,
        output_parent=origin_output,
        request=fixture.request,
        result_folder_name=fixture.result_name,
        target_by_original_path=fixture.target_paths,
    )
    runtime = tmp_path / "runtime"
    budget = runtime / ".name-atlas" / "api_budget.json"
    budget.parent.mkdir(parents=True)
    budget.write_bytes(b"receiver-budget-sentinel\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    jobs = ConnectedChangeJobService()
    abandoned = await asyncio.to_thread(
        jobs.create_application_job,
        change_file_path=origin.change_file_path,
        source_root=fixture.martin_root,
        output_parent=receiver_output,
        job_path=default_connected_change_job_path(project_root=runtime),
        idempotency_key="mcp-abandoned-receiver-001",
    )

    restarted = NameAtlasMcpService(project_root=runtime, job_service=jobs)
    assert await restarted.recover_nonterminal_jobs() == 1
    terminal = await _wait_for_lifecycle(
        restarted,
        abandoned.job_id,
        "verified",
        "blocked",
    )

    assert terminal.lifecycle == "verified"
    assert terminal.execution_origin == "capsule_applied"
    assert terminal.provider_kind is None
    assert budget.read_bytes() == b"receiver-budget-sentinel\n"


@pytest.mark.anyio
async def test_background_replay_binding_failure_is_durable_blocked(
    tmp_path: Path,
) -> None:
    fixture = materialize_hero_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    service = NameAtlasMcpService(project_root=runtime)
    started = await service.plan_and_create_copy(
        _origin_request(
            source=fixture.sofia_root,
            output=output,
            key="mcp-unmatched-replay-0001",
            request=f"{HERO_REQUEST} This request is intentionally unmatched.",
        )
    )
    assert started.job_handle is not None
    terminal = await _wait_for_lifecycle(
        service,
        started.job_handle,
        "blocked",
    )
    assert terminal.status == "blocked"
    assert terminal.blocker_code == "planner_background_failed"
    assert terminal.active_operation is False
    assert terminal.result_root is None

    restarted = NameAtlasMcpService(project_root=runtime)
    repeated = await restarted.job_status(
        JobHandleRequest(job_handle=started.job_handle)
    )
    assert repeated.lifecycle == "blocked"
    assert repeated.revision == terminal.revision
    assert repeated.blocker_code == "planner_background_failed"


@pytest.mark.anyio
async def test_clarification_binds_key_revision_question_and_answer(
    tmp_path: Path,
) -> None:
    fixture = materialize_ambiguity_fixture(tmp_path / "fixture")
    output = tmp_path / "output"
    output.mkdir()
    runtime = tmp_path / "runtime"
    start_key = "mcp-clarification-start-001"
    answer_key = "mcp-clarification-answer-01"
    service = NameAtlasMcpService(project_root=runtime)
    started = await service.plan_and_create_copy(
        _origin_request(
            source=fixture.source_root,
            output=output,
            key=start_key,
            request=AMBIGUITY_REQUEST,
        )
    )
    assert started.job_handle is not None
    waiting = await _wait_for_lifecycle(
        service,
        started.job_handle,
        "awaiting_clarification",
        "blocked",
    )
    assert waiting.lifecycle == "awaiting_clarification"
    assert waiting.revision is not None
    assert waiting.clarification_question_fingerprint is not None

    wrong_question = await service.answer_clarification(
        AnswerClarificationRequest(
            job_handle=started.job_handle,
            expected_revision=waiting.revision,
            question_fingerprint="0" * 64,
            answer=AMBIGUITY_ANSWER,
            idempotency_key=answer_key,
        )
    )
    assert wrong_question.blocker_code == "clarification_question_mismatch"

    wrong_revision = await service.answer_clarification(
        AnswerClarificationRequest(
            job_handle=started.job_handle,
            expected_revision=waiting.revision + 1,
            question_fingerprint=waiting.clarification_question_fingerprint,
            answer=AMBIGUITY_ANSWER,
            idempotency_key=answer_key,
        )
    )
    assert wrong_revision.blocker_code == "clarification_revision_mismatch"

    exact_request = AnswerClarificationRequest(
        job_handle=started.job_handle,
        expected_revision=waiting.revision,
        question_fingerprint=waiting.clarification_question_fingerprint,
        answer=AMBIGUITY_ANSWER,
        idempotency_key=answer_key,
    )
    restarted = NameAtlasMcpService(project_root=runtime)
    accepted = await restarted.answer_clarification(exact_request)
    assert accepted.lifecycle in {"planning", "executing", "verified"}
    verified = await _wait_for_lifecycle(
        restarted,
        started.job_handle,
        "verified",
        "blocked",
    )
    assert verified.lifecycle == "verified"

    repeated = await restarted.answer_clarification(exact_request)
    assert repeated.lifecycle == "verified"
    assert repeated.revision == verified.revision
    changed_answer = await restarted.answer_clarification(
        exact_request.model_copy(update={"answer": "Candidate B is approved."})
    )
    assert changed_answer.blocker_code == "idempotency_key_conflict"
    changed_key = await restarted.answer_clarification(
        exact_request.model_copy(
            update={"idempotency_key": "mcp-clarification-other-001"}
        )
    )
    assert changed_key.blocker_code == "idempotency_key_conflict"

    job_path = next((runtime / ".name-atlas" / "jobs").glob("*.json"))
    job = FolderRefactorJobV2Store(job_path).inspect()
    assert isinstance(job, FolderRefactorJobV2)
    assert isinstance(job.authority, GptPlannedJobAuthorityV2)
    assert job.authority.planner_checkpoint.clarification_answer == AMBIGUITY_ANSWER
    assert tuple(item.operation for item in job.operation_idempotency) == (
        "recreate_original",
        "answer_clarification",
    )


@pytest.mark.anyio
async def test_receiver_apply_verify_and_reconstruct_are_keyless_and_retry_safe(
    tmp_path: Path,
) -> None:
    fixture = materialize_hero_fixture(tmp_path / "fixture")
    origin_output = tmp_path / "origin-output"
    receiver_output = tmp_path / "receiver-output"
    origin_output.mkdir()
    receiver_output.mkdir()
    runtime = tmp_path / "runtime"
    service = NameAtlasMcpService(project_root=runtime)

    origin_key = "mcp-receiver-origin-key-01"
    origin = await service.plan_and_create_copy(
        _origin_request(
            source=fixture.sofia_root,
            output=origin_output,
            key=origin_key,
        )
    )
    assert origin.job_handle is not None
    origin_terminal = await _wait_for_lifecycle(
        service,
        origin.job_handle,
        "verified",
        "blocked",
    )
    assert origin_terminal.lifecycle == "verified"
    change = await service.get_change_file(
        JobHandleRequest(job_handle=origin.job_handle)
    )
    assert change.change_file_path is not None
    standalone_change = tmp_path / "northstar.nameatlas-change.json"
    shutil.copyfile(change.change_file_path, standalone_change)

    martin_before = scan_folder(fixture.martin_root).inventory
    receiver_key = "mcp-receiver-apply-key-001"
    apply_request = ApplyChangeFileRequest(
        change_file_path=str(standalone_change),
        source_root=str(fixture.martin_root),
        output_parent=str(receiver_output),
        idempotency_key=receiver_key,
    )
    receiver = await service.apply_change_file(apply_request)
    assert receiver.job_handle is not None
    assert receiver.execution_origin == "capsule_applied"
    assert receiver.provider_kind is None
    assert "GPT" not in receiver.message

    restarted = NameAtlasMcpService(project_root=runtime)
    same_start = await restarted.apply_change_file(apply_request)
    assert same_start.job_handle == receiver.job_handle
    receiver_terminal = await _wait_for_lifecycle(
        restarted,
        receiver.job_handle,
        "verified",
        "blocked",
    )
    assert receiver_terminal.lifecycle == "verified"
    assert receiver_terminal.result_root is not None

    unrelated = tmp_path / "unrelated" / "received-result"
    unrelated.parent.mkdir()
    shutil.copytree(receiver_terminal.result_root, unrelated)
    independent = NameAtlasMcpService(project_root=tmp_path / "empty-runtime")
    verification = await independent.verify_result(
        VerifyResultRequest(result_root=str(unrelated))
    )
    assert verification.status == "verified"
    assert verification.receipt_fingerprint == receiver_terminal.receipt_fingerprint
    assert (
        verification.organized_tree_commitment
        == receiver_terminal.organized_tree_commitment
    )

    moved_source = tmp_path / "martin-source-moved"
    shutil.move(fixture.martin_root, moved_source)
    standalone_change.unlink()
    terminal_retry = await restarted.apply_change_file(apply_request)
    assert terminal_retry.job_handle == receiver.job_handle
    assert terminal_retry.lifecycle == "verified"

    reconstruction = await restarted.recreate_original(
        RecreateOriginalRequest(
            job_handle=receiver.job_handle,
            idempotency_key=receiver_key,
        )
    )
    assert reconstruction.status == "verified"
    assert scan_folder(Path(reconstruction.destination)).inventory == martin_before
    repeated_reconstruction = await restarted.recreate_original(
        RecreateOriginalRequest(
            job_handle=receiver.job_handle,
            idempotency_key=receiver_key,
        )
    )
    assert repeated_reconstruction.status == "verified"
    assert repeated_reconstruction.destination == reconstruction.destination
    assert scan_folder(moved_source).inventory == martin_before
