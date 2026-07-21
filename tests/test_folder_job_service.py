"""Integrated durable browser-service tests for the A3 folder workflow."""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path
from typing import Literal

import httpx
import pytest

import name_atlas.folder_job_service as folder_job_service_module
import name_atlas.folder_refactor.planner_evidence as planner_evidence_module
import name_atlas.folder_refactor.transaction as folder_transaction_module
from name_atlas.folder_app import (
    FolderClarificationRequest,
    FolderRunPresentation,
    FolderWorkPhase,
    create_folder_app,
)
from name_atlas.folder_job_service import (
    FolderJobServiceError,
    JobBackedFolderRunService,
)
from name_atlas.folder_refactor.inventory import FolderScan
from name_atlas.folder_refactor.job import (
    FolderJobLifecycle,
    FolderRefactorJobStore,
    FolderRefactorJobWriter,
    expected_final_result_path,
    expected_pending_result_path,
)
from name_atlas.folder_refactor.markdown_contracts import FolderReferenceGraph
from name_atlas.folder_refactor.planner_contracts import (
    FolderPlannerTurnInput,
    ProviderToolResponse,
    RequestClarificationCall,
)
from name_atlas.folder_refactor.planner_provider import (
    DETERMINISTIC_DEVELOPMENT_REQUEST,
    DeterministicDevelopmentPlannerProvider,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderReceiptVerificationStatus,
)
from name_atlas.folder_refactor.transaction import FolderTransactionError

REQUEST = DETERMINISTIC_DEVELOPMENT_REQUEST


class _SimulatedProcessTermination(BaseException):
    pass


async def _wait_for_lifecycle(
    client: httpx.AsyncClient,
    expected: str,
) -> httpx.Response:
    for _ in range(100):
        response = await client.get("/status")
        if response.json()["lifecycle"] == expected:
            return response
        await asyncio.sleep(0.01)
    raise AssertionError(f"Lifecycle did not reach {expected}.")


def _source(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    output = tmp_path / "results"
    source.mkdir()
    output.mkdir()
    (source / "note.md").write_text("[report](report.txt)\n", encoding="utf-8")
    (source / "report.txt").write_text("approved\n", encoding="utf-8")
    (source / ".env.local").write_text("DEMO=true\n", encoding="utf-8")
    return source, output


class _ClarificationProvider:
    def __init__(self, calls: list[FolderPlannerTurnInput]) -> None:
        self._calls = calls

    @property
    def provider_kind(self) -> Literal["deterministic"]:
        return "deterministic"

    async def exchange(self, turn_input: FolderPlannerTurnInput, /):
        self._calls.append(turn_input)
        if turn_input.clarification_answer is None:
            return ProviderToolResponse(
                provider_kind="deterministic",
                tool_calls=(
                    RequestClarificationCall(
                        call_id="one-question",
                        question="Which presentation is approved for delivery?",
                        missing_facts=("approved_presentation",),
                        evidence_ids=("initial_inventory",),
                    ),
                ),
            )
        return await DeterministicDevelopmentPlannerProvider(
            result_folder_name="clarified-result",
            allowed_request=turn_input.request,
        ).exchange(turn_input)


@pytest.mark.anyio
async def test_zero_question_job_creates_one_verified_process_result(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "zero-question.json"
    service = JobBackedFolderRunService(
        job_path=job_path,
        result_folder_name="organized-result",
    )
    phases: list[FolderWorkPhase] = []
    service.set_progress_callback(phases.append)

    result = await service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )

    assert isinstance(result, FolderRunPresentation)
    assert result.supported_link_count == 1
    assert result.source_unchanged is True
    assert (result.data_root / "organized" / "note.md").is_file()
    assert (result.data_root / ".env.local").is_file()
    assert result.independent_verification_passed is True
    assert result.reconstruction_available is True
    job = FolderRefactorJobStore(job_path).load()
    assert job.lifecycle is FolderJobLifecycle.VERIFIED
    assert job.accepted_plan is not None
    assert job.change_ledger is not None
    assert job.receipt_fingerprint is not None
    checkpoint = service.web_checkpoint()
    assert checkpoint is not None
    assert checkpoint.result == result
    assert phases == [
        FolderWorkPhase.READING,
        FolderWorkPhase.PLANNING,
        FolderWorkPhase.CHECKING,
        FolderWorkPhase.CREATING,
        FolderWorkPhase.UPDATING_LINKS,
        FolderWorkPhase.VERIFYING,
    ]


@pytest.mark.anyio
async def test_unsupported_request_blocks_before_provider_or_result(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "blocked.json"
    provider = DeterministicDevelopmentPlannerProvider()
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    with pytest.raises(FolderJobServiceError, match="file_deletion_unsupported"):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request="Eliminate outdated files and organize the rest.",
        )

    assert provider.invocation_count == 0
    assert not any(output.iterdir())
    assert FolderRefactorJobStore(job_path).load().lifecycle is (
        FolderJobLifecycle.BLOCKED
    )


@pytest.mark.anyio
async def test_unrecorded_request_cannot_reach_done_or_create_result(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "unrecorded-request.json"
    provider = DeterministicDevelopmentPlannerProvider()
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    with pytest.raises(
        FolderJobServiceError,
        match="deterministic_request_not_recorded",
    ):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request="Toss obsolete drafts and organize the rest.",
        )

    blocked = FolderRefactorJobStore(job_path).load()
    assert blocked.lifecycle is FolderJobLifecycle.BLOCKED
    assert blocked.blocker_code == "deterministic_request_not_recorded"
    assert provider.invocation_count == 1
    assert not any(output.iterdir())


@pytest.mark.anyio
async def test_oversized_initial_evidence_is_durably_blocked_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "evidence-limit.json"
    provider = DeterministicDevelopmentPlannerProvider()
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    monkeypatch.setattr(
        planner_evidence_module,
        "MAX_TOTAL_OUTBOUND_EVIDENCE_BYTES",
        64,
    )

    with pytest.raises(FolderJobServiceError, match="initial_evidence_limit_exceeded"):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )

    blocked = FolderRefactorJobStore(job_path).load()
    assert blocked.lifecycle is FolderJobLifecycle.BLOCKED
    assert blocked.blocker_code == "initial_evidence_limit_exceeded"
    assert provider.invocation_count == 0
    assert not any(output.iterdir())


@pytest.mark.anyio
async def test_real_long_path_inventory_limit_rehydrates_as_terminal_blocker(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "results"
    source.mkdir()
    output.mkdir()
    deepest = source
    for character in ("a", "b", "c", "d"):
        deepest /= character * 200
        deepest.mkdir()
    for index in range(500):
        filename = f"{index:03d}-{'x' * 72}.txt"
        (deepest / filename).write_text("x", encoding="utf-8")

    job_path = tmp_path / "jobs" / "real-evidence-limit.json"
    provider = DeterministicDevelopmentPlannerProvider()
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    with pytest.raises(FolderJobServiceError, match="initial_evidence_limit_exceeded"):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )

    blocked = FolderRefactorJobStore(job_path).load()
    assert blocked.lifecycle is FolderJobLifecycle.BLOCKED
    assert blocked.blocker_code == "initial_evidence_limit_exceeded"
    assert provider.invocation_count == 0
    assert not any(output.iterdir())

    restarted = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    checkpoint = restarted.web_checkpoint()
    assert checkpoint is not None
    assert checkpoint.lifecycle.value == "blocked"
    assert checkpoint.blocker is not None
    assert checkpoint.blocker.startswith("initial_evidence_limit_exceeded:")
    assert provider.invocation_count == 0


@pytest.mark.anyio
async def test_protected_markdown_link_context_blocks_before_provider(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "results"
    source.mkdir()
    output.mkdir()
    (source / ".notes.md").write_text("[report](report.txt)\n", encoding="utf-8")
    (source / "report.txt").write_text("report\n", encoding="utf-8")
    provider = DeterministicDevelopmentPlannerProvider()
    job_path = tmp_path / "jobs" / "protected-link.json"
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    with pytest.raises(
        FolderTransactionError,
        match="protected_markdown_link_context_unsupported",
    ):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )

    assert provider.invocation_count == 0
    assert not job_path.exists()
    assert not any(output.iterdir())


@pytest.mark.anyio
async def test_one_question_rehydrates_without_duplicate_provider_call(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "clarification.json"
    calls: list[FolderPlannerTurnInput] = []

    def provider_factory(_job: object) -> _ClarificationProvider:
        return _ClarificationProvider(calls)

    first_service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=provider_factory,
    )

    first = await first_service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request="Put the approved presentation in final deliverables.",
    )
    assert isinstance(first, FolderClarificationRequest)
    assert len(calls) == 1

    resumed_service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=provider_factory,
    )
    checkpoint = resumed_service.web_checkpoint()
    assert checkpoint is not None
    assert checkpoint.clarification == first
    assert len(calls) == 1

    result = await resumed_service.continue_after_clarification(
        continuation_token=first.continuation_token,
        answer="Use the Northstar final presentation.",
    )

    assert result.result_root.name == "clarified-result"
    assert len(calls) == 2
    loaded = FolderRefactorJobStore(job_path).load()
    assert loaded.planner_progress is not None
    assert loaded.planner_progress.clarification_answer == (
        "Use the Northstar final presentation."
    )


@pytest.mark.anyio
async def test_executing_job_resumes_without_another_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "resume-execution.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="resumed-result"
    )
    first_service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    def crash_before_execution(*_args, **_kwargs):
        raise RuntimeError("simulated process stop before execution")

    monkeypatch.setattr(first_service, "_execute", crash_before_execution)
    with pytest.raises(RuntimeError, match="simulated process stop"):
        await first_service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    assert provider.invocation_count == 1
    assert FolderRefactorJobStore(job_path).load().lifecycle is (
        FolderJobLifecycle.EXECUTING
    )

    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    result = await resumed.resume_existing_job()

    assert isinstance(result, FolderRunPresentation)
    assert result.result_root.name == "resumed-result"
    assert provider.invocation_count == 1


@pytest.mark.anyio
async def test_incomplete_owned_pending_restarts_without_another_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "resume-incomplete-pending.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="restarted-incomplete-result"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    original_copy = folder_transaction_module._copy_verified_file

    def terminate_after_pending_creation(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedProcessTermination

    monkeypatch.setattr(
        folder_transaction_module,
        "_copy_verified_file",
        terminate_after_pending_creation,
    )
    with pytest.raises(_SimulatedProcessTermination):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    interrupted = FolderRefactorJobStore(job_path).load()
    assert interrupted.lifecycle is FolderJobLifecycle.EXECUTING
    assert interrupted.pending_result_path is not None
    assert interrupted.pending_result_path.is_dir()
    assert interrupted.final_result_path is not None
    assert not interrupted.final_result_path.exists()
    assert provider.invocation_count == 1

    monkeypatch.setattr(
        folder_transaction_module,
        "_copy_verified_file",
        original_copy,
    )
    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    result = await resumed.resume_existing_job()

    assert isinstance(result, FolderRunPresentation)
    assert result.result_root.name == "restarted-incomplete-result"
    assert provider.invocation_count == 1
    completed = FolderRefactorJobStore(job_path).load()
    assert completed.lifecycle is FolderJobLifecycle.VERIFIED
    assert completed.pending_result_path is None


@pytest.mark.anyio
async def test_promoted_result_rehydrates_after_process_restart_without_new_work(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "completed-restart.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="restart-safe-result"
    )
    first = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    original = await first.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )
    assert isinstance(original, FolderRunPresentation)
    assert provider.invocation_count == 1

    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    recovered = await resumed.resume_existing_job()

    assert recovered == original
    assert provider.invocation_count == 1
    assert FolderRefactorJobStore(job_path).load().lifecycle is (
        FolderJobLifecycle.VERIFIED
    )

    restarted = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    checkpoint = restarted.web_checkpoint()
    assert checkpoint is not None
    assert checkpoint.lifecycle.value == "verified"
    assert checkpoint.result == original
    assert provider.invocation_count == 1


@pytest.mark.anyio
async def test_promoted_receipt_finalizes_after_crash_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "crash-after-promotion.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="promoted-before-job-save"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    original_finalize = FolderRefactorJobWriter.finalize_verified

    def terminate_before_job_save(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedProcessTermination

    monkeypatch.setattr(
        FolderRefactorJobWriter,
        "finalize_verified",
        terminate_before_job_save,
    )
    with pytest.raises(_SimulatedProcessTermination):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    interrupted = FolderRefactorJobStore(job_path).load()
    assert interrupted.lifecycle is FolderJobLifecycle.EXECUTING
    assert interrupted.pending_result_path is not None
    assert interrupted.final_result_path is not None
    assert not interrupted.pending_result_path.exists()
    assert interrupted.final_result_path.is_dir()
    assert provider.invocation_count == 1

    monkeypatch.setattr(
        FolderRefactorJobWriter,
        "finalize_verified",
        original_finalize,
    )
    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    recovered = await resumed.resume_existing_job()

    assert isinstance(recovered, FolderRunPresentation)
    assert recovered.independent_verification_passed is True
    assert provider.invocation_count == 1
    assert FolderRefactorJobStore(job_path).load().lifecycle is (
        FolderJobLifecycle.VERIFIED
    )


@pytest.mark.anyio
async def test_promoted_receipt_becomes_stale_if_source_changes_before_job_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "changed-after-promotion.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="changed-after-promotion"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    original_finalize = FolderRefactorJobWriter.finalize_verified

    def terminate_before_job_save(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedProcessTermination

    monkeypatch.setattr(
        FolderRefactorJobWriter,
        "finalize_verified",
        terminate_before_job_save,
    )
    with pytest.raises(_SimulatedProcessTermination):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    interrupted = FolderRefactorJobStore(job_path).load()
    assert interrupted.final_result_path is not None
    assert interrupted.final_result_path.is_dir()
    source.rename(tmp_path / "source-moved-after-promotion")

    monkeypatch.setattr(
        FolderRefactorJobWriter,
        "finalize_verified",
        original_finalize,
    )
    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    with pytest.raises(FolderJobServiceError, match="source_changed"):
        await resumed.resume_existing_job()

    stale = FolderRefactorJobStore(job_path).load()
    assert stale.lifecycle is FolderJobLifecycle.STALE
    assert stale.source_scan_blocker is not None
    assert stale.pending_result_path == interrupted.pending_result_path
    assert stale.final_result_path == interrupted.final_result_path
    assert stale.final_result_path.is_dir()
    assert provider.invocation_count == 1


@pytest.mark.anyio
async def test_receipt_finalized_pending_promotes_on_restart_without_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "crash-before-promotion.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="pending-before-promotion"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    original_promote = folder_transaction_module.promote_directory_no_replace

    def terminate_before_promotion(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedProcessTermination

    monkeypatch.setattr(
        folder_transaction_module,
        "promote_directory_no_replace",
        terminate_before_promotion,
    )
    with pytest.raises(_SimulatedProcessTermination):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    interrupted = FolderRefactorJobStore(job_path).load()
    assert interrupted.lifecycle is FolderJobLifecycle.EXECUTING
    assert interrupted.pending_result_path is not None
    assert interrupted.final_result_path is not None
    assert interrupted.pending_result_path.is_dir()
    assert not interrupted.final_result_path.exists()
    assert provider.invocation_count == 1

    monkeypatch.setattr(
        folder_transaction_module,
        "promote_directory_no_replace",
        original_promote,
    )
    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    recovered = await resumed.resume_existing_job()

    assert isinstance(recovered, FolderRunPresentation)
    assert recovered.independent_verification_passed is True
    assert provider.invocation_count == 1
    completed = FolderRefactorJobStore(job_path).load()
    assert completed.lifecycle is FolderJobLifecycle.VERIFIED
    assert completed.final_result_path is not None
    assert completed.final_result_path.is_dir()


@pytest.mark.anyio
async def test_receipt_finalized_pending_stays_owned_when_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "pending-source-changed.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="pending-source-changed"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    original_promote = folder_transaction_module.promote_directory_no_replace

    def terminate_before_promotion(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedProcessTermination

    monkeypatch.setattr(
        folder_transaction_module,
        "promote_directory_no_replace",
        terminate_before_promotion,
    )
    with pytest.raises(_SimulatedProcessTermination):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    interrupted = FolderRefactorJobStore(job_path).load()
    assert interrupted.pending_result_path is not None
    assert interrupted.pending_result_path.is_dir()
    assert interrupted.final_result_path is not None
    (source / "report.txt").write_text("changed after receipt\n", encoding="utf-8")

    monkeypatch.setattr(
        folder_transaction_module,
        "promote_directory_no_replace",
        original_promote,
    )
    resumed = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    with pytest.raises(FolderJobServiceError, match="source_changed"):
        await resumed.resume_existing_job()

    stale = FolderRefactorJobStore(job_path).load()
    assert stale.lifecycle is FolderJobLifecycle.STALE
    assert tuple(item.kind.value for item in stale.stale_differences) == ("resized",)
    assert stale.pending_result_path == interrupted.pending_result_path
    assert stale.final_result_path == interrupted.final_result_path
    assert stale.pending_result_path.is_dir()
    assert not stale.final_result_path.exists()
    assert provider.invocation_count == 1


@pytest.mark.parametrize("candidate_state", ("pending", "final"))
@pytest.mark.anyio
async def test_restart_rejects_valid_receipt_owned_by_another_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidate_state: str,
) -> None:
    source, first_output = _source(tmp_path)
    first_provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="shared-result"
    )
    first = JobBackedFolderRunService(
        job_path=tmp_path / "jobs" / "first.json",
        provider_factory=lambda _job: first_provider,
    )
    first_result = await first.plan_and_create_copy(
        source_root=source,
        output_parent=first_output,
        request=REQUEST,
    )
    assert isinstance(first_result, FolderRunPresentation)

    second_output = tmp_path / "second-results"
    second_output.mkdir()
    second_job_path = tmp_path / "jobs" / "second.json"
    second_provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="shared-result"
    )
    second = JobBackedFolderRunService(
        job_path=second_job_path,
        provider_factory=lambda _job: second_provider,
    )

    def stop_before_execution(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedProcessTermination

    monkeypatch.setattr(second, "_execute", stop_before_execution)
    with pytest.raises(_SimulatedProcessTermination):
        await second.plan_and_create_copy(
            source_root=source,
            output_parent=second_output,
            request=REQUEST,
        )
    store = FolderRefactorJobStore(second_job_path)
    executing = store.load()
    with store.writer() as writer:
        executing = writer.begin_execution(
            executing,
            pending_result_path=expected_pending_result_path(executing),
            final_result_path=expected_final_result_path(executing),
            expected_revision=executing.revision,
        )
    assert executing.pending_result_path is not None
    assert executing.final_result_path is not None
    candidate_path = (
        executing.pending_result_path
        if candidate_state == "pending"
        else executing.final_result_path
    )
    shutil.copytree(first_result.result_root, candidate_path)

    resumed = JobBackedFolderRunService(
        job_path=second_job_path,
        provider_factory=lambda _job: second_provider,
    )
    expected_error = (
        "pending_result_job_id_mismatch"
        if candidate_state == "pending"
        else "final_result_job_id_mismatch"
    )
    with pytest.raises(FolderJobServiceError, match=expected_error):
        await resumed.resume_existing_job()

    assert FolderRefactorJobStore(second_job_path).load().lifecycle is (
        FolderJobLifecycle.EXECUTING
    )
    assert first_provider.invocation_count == 1
    assert second_provider.invocation_count == 1


@pytest.mark.anyio
async def test_source_change_during_transaction_persists_exact_stale_state(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "source-changed-during-copy.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="source-changed-result"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    changed = False

    def mutate_during_proof(phase: FolderWorkPhase) -> None:
        nonlocal changed
        if phase is FolderWorkPhase.VERIFYING and not changed:
            changed = True
            (source / "report.txt").write_text(
                "changed during proof\n",
                encoding="utf-8",
            )

    service.set_progress_callback(mutate_during_proof)

    with pytest.raises(FolderJobServiceError, match="source_changed"):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )

    stale = FolderRefactorJobStore(job_path).load()
    assert stale.lifecycle is FolderJobLifecycle.STALE
    assert tuple(item.kind.value for item in stale.stale_differences) == ("resized",)
    assert stale.stale_differences[0].before is not None
    assert stale.stale_differences[0].before.relative_path == "report.txt"
    assert stale.stale_differences[0].after is not None
    assert stale.stale_differences[0].after.relative_path == "report.txt"
    assert not (output / "source-changed-result").exists()
    assert provider.invocation_count == 1


@pytest.mark.anyio
async def test_verify_and_recreate_actions_are_keyless_and_job_immutable(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "result-actions.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="action-result"
    )
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    result = await service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )
    assert isinstance(result, FolderRunPresentation)
    job_bytes = job_path.read_bytes()

    verification = service.verify_again()
    assert result.result_root is not None
    restored = service.recreate_original(
        result.result_root.parent / "recreated-original"
    )

    assert verification.status is FolderReceiptVerificationStatus.VERIFIED
    assert (
        restored.destination
        == (result.result_root.parent / "recreated-original").resolve()
    )
    assert restored.restored_file_count == 3
    assert (restored.destination / "note.md").read_bytes() == (
        source / "note.md"
    ).read_bytes()
    assert (restored.destination / "report.txt").read_bytes() == (
        source / "report.txt"
    ).read_bytes()
    assert (restored.destination / ".env.local").read_bytes() == (
        source / ".env.local"
    ).read_bytes()
    assert job_path.read_bytes() == job_bytes
    assert provider.invocation_count == 1


@pytest.mark.anyio
async def test_recreate_original_rejects_destination_inside_source(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "source-overlap.json"
    service = JobBackedFolderRunService(
        job_path=job_path,
        result_folder_name="source-overlap-result",
    )
    result = await service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )
    assert isinstance(result, FolderRunPresentation)
    source_before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    overlapping_destination = source / "recreated-original"

    with pytest.raises(
        FolderJobServiceError,
        match="reconstruction_destination_overlaps_source",
    ):
        service.recreate_original(overlapping_destination)

    assert not overlapping_destination.exists()
    assert {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    } == source_before


@pytest.mark.anyio
async def test_corrupt_existing_result_is_durably_blocked_on_restart(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "corrupt-restart.json"
    provider = DeterministicDevelopmentPlannerProvider(
        result_folder_name="corrupt-result"
    )
    first = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    original = await first.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )
    assert isinstance(original, FolderRunPresentation)
    (original.data_root / "organized" / "report.txt").write_text(
        "tampered\n",
        encoding="utf-8",
    )

    checkpoint = first.web_checkpoint()

    restarted = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    restarted_checkpoint = restarted.web_checkpoint()

    assert checkpoint is not None
    assert checkpoint.lifecycle.value == "blocked"
    assert checkpoint.blocker is not None
    assert checkpoint.blocker.startswith("verified_result_no_longer_valid:")
    assert restarted_checkpoint is not None
    assert restarted_checkpoint.lifecycle.value == "blocked"
    assert FolderRefactorJobStore(job_path).load().lifecycle is (
        FolderJobLifecycle.VERIFIED
    )
    assert provider.invocation_count == 1


@pytest.mark.anyio
async def test_source_change_rehydrates_as_stale_without_provider_call(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "stale.json"
    calls: list[FolderPlannerTurnInput] = []
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: _ClarificationProvider(calls),
    )

    outcome = await service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request="Put the approved presentation in final deliverables.",
    )
    assert isinstance(outcome, FolderClarificationRequest)
    (source / "report.txt").write_text("changed\n", encoding="utf-8")

    restarted = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: _ClarificationProvider(calls),
    )
    checkpoint = restarted.web_checkpoint()

    assert checkpoint is not None
    assert checkpoint.blocker is not None
    assert checkpoint.blocker.startswith("source_changed:")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_real_browser_start_to_done_uses_durable_job_service(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "browser-zero-question.json"
    service = JobBackedFolderRunService(
        job_path=job_path,
        result_folder_name="browser-result",
    )
    app = create_folder_app(service)
    transport = httpx.ASGITransport(app=app)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client,
    ):
        csrf_token = app.state.folder_web_state.csrf_token
        started = await client.post(
            "/start",
            data={
                "source_root": str(source),
                "user_request": REQUEST,
                "output_parent": str(output),
                "csrf_token": csrf_token,
            },
        )
        completed = await _wait_for_lifecycle(client, "verified")
        done = await client.get("/done")

    assert started.status_code == 303
    assert completed.json()["done_url"] == "/done"
    assert done.status_code == 200
    assert "Files</dt><dd>3, exactly once" in done.text
    assert "Links updated</dt><dd>0 of 1" in done.text
    assert job_path.is_file()
    assert (output / "browser-result" / "data" / "organized" / "note.md").is_file()


@pytest.mark.anyio
async def test_real_browser_remains_responsive_during_slow_mechanical_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "responsive-browser.json"
    service = JobBackedFolderRunService(
        job_path=job_path,
        result_folder_name="responsive-result",
    )
    original_scan = folder_job_service_module.scan_folder_with_references
    scan_started = threading.Event()
    release_scan = threading.Event()

    def delayed_scan(source_root: Path) -> tuple[FolderScan, FolderReferenceGraph]:
        scan_started.set()
        if not release_scan.wait(timeout=2):
            raise RuntimeError("test scan release timed out")
        return original_scan(source_root)

    monkeypatch.setattr(
        folder_job_service_module,
        "scan_folder_with_references",
        delayed_scan,
    )
    app = create_folder_app(service)
    fallback_release = threading.Timer(1.0, release_scan.set)
    fallback_release.start()
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            started_at = time.monotonic()
            started = await client.post(
                "/start",
                data={
                    "source_root": str(source),
                    "user_request": REQUEST,
                    "output_parent": str(output),
                    "csrf_token": app.state.folder_web_state.csrf_token,
                },
            )
            response_seconds = time.monotonic() - started_at
            assert await asyncio.to_thread(scan_started.wait, 0.5)
            status = await client.get("/status")
            working = await client.get("/working")

            assert started.status_code == 303
            assert response_seconds < 0.5
            assert status.json()["lifecycle"] == "planning"
            assert status.json()["current_stage"] == 0
            assert working.status_code == 200
            assert app.state.folder_web_state.worker is not None
            assert app.state.folder_web_state.worker.done() is False
            assert not (output / "responsive-result").exists()

            app.state.folder_web_state.worker.cancel()
            await asyncio.sleep(0)
            assert app.state.folder_web_state.worker.done() is False
            release_scan.set()
            completed = await _wait_for_lifecycle(client, "verified")

        assert completed.json()["done_url"] == "/done"
        assert (output / "responsive-result").is_dir()
        completed_job = FolderRefactorJobStore(job_path).load()
        assert completed_job.accepted_plan is not None
        assert completed_job.lifecycle is FolderJobLifecycle.VERIFIED
    finally:
        release_scan.set()
        fallback_release.cancel()
        fallback_release.join(timeout=1)


@pytest.mark.anyio
async def test_browser_restart_rehydrates_question_without_duplicate_turn(
    tmp_path: Path,
) -> None:
    source, output = _source(tmp_path)
    job_path = tmp_path / "jobs" / "browser-question.json"
    calls: list[FolderPlannerTurnInput] = []

    def provider_factory(_job: object) -> _ClarificationProvider:
        return _ClarificationProvider(calls)

    first_service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=provider_factory,
    )
    first_app = create_folder_app(first_service)

    async with (
        first_app.router.lifespan_context(first_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first_app),
            base_url="http://testserver",
        ) as client,
    ):
        csrf_token = first_app.state.folder_web_state.csrf_token
        await client.post(
            "/start",
            data={
                "source_root": str(source),
                "user_request": (
                    "Put the approved presentation in final deliverables."
                ),
                "output_parent": str(output),
                "csrf_token": csrf_token,
            },
        )
        await _wait_for_lifecycle(client, "awaiting_clarification")
    assert len(calls) == 1

    resumed_service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=provider_factory,
    )
    resumed_app = create_folder_app(resumed_service)
    async with (
        resumed_app.router.lifespan_context(resumed_app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=resumed_app),
            base_url="http://testserver",
        ) as client,
    ):
        root = await client.get("/", follow_redirects=False)
        question = await client.get("/working")
        assert len(calls) == 1
        answered = await client.post(
            "/clarify",
            data={
                "answer": "Use the Northstar final presentation.",
                "csrf_token": resumed_app.state.folder_web_state.csrf_token,
            },
        )
        await _wait_for_lifecycle(client, "verified")
        done = await client.get("/done")

    assert root.status_code == 303
    assert root.headers["location"] == "/working"
    assert "Which presentation is approved for delivery?" in question.text
    assert answered.status_code == 303
    assert len(calls) == 2
    assert "Your new folder is ready" in done.text
