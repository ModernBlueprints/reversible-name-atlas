"""A3 terminal execution and restart-recovery tests for the job authority."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from name_atlas.folder_job_service import JobBackedFolderRunService
from name_atlas.folder_refactor.job import (
    FolderJobFinalization,
    FolderJobFinalizedError,
    FolderJobLifecycle,
    FolderJobRecoveryError,
    FolderJobRecoveryState,
    FolderJobRevisionError,
    FolderJobWriteError,
    FolderRefactorJob,
    FolderRefactorJobStore,
    canonical_job_bytes,
    classify_job_recovery_state,
    expected_final_result_path,
    expected_pending_result_path,
)
from name_atlas.folder_refactor.planner_provider import (
    DETERMINISTIC_DEVELOPMENT_REQUEST,
)
from name_atlas.folder_refactor.receipt_contracts import (
    RECEIPT_JSON_PATH,
    FolderChangeEntry,
    FolderChangeLedger,
    FolderReceiptVerification,
    FolderReceiptVerificationCheck,
    FolderReceiptVerificationStatus,
)
from name_atlas.folder_refactor.serialization import (
    canonical_sha256,
    request_fingerprint,
)


class _StopBeforeA2Transaction(RuntimeError):
    pass


async def _accepted_executing_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FolderRefactorJob, FolderRefactorJobStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source"
    output = tmp_path / "output"
    job_path = tmp_path / "state" / "job.json"
    source.mkdir()
    output.mkdir()
    (source / "note.md").write_text("[report](report.txt)\n", encoding="utf-8")
    (source / "report.txt").write_text("approved\n", encoding="utf-8")
    service = JobBackedFolderRunService(
        job_path=job_path,
        result_folder_name="organized-result",
    )

    def stop_before_transaction(*_args: object) -> None:
        raise _StopBeforeA2Transaction

    monkeypatch.setattr(service, "_execute", stop_before_transaction)
    with pytest.raises(_StopBeforeA2Transaction):
        await service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=DETERMINISTIC_DEVELOPMENT_REQUEST,
        )
    store = FolderRefactorJobStore(job_path)
    job = store.load()
    assert job.lifecycle is FolderJobLifecycle.EXECUTING
    assert job.accepted_plan is not None
    assert job.pending_result_path is None
    assert job.final_result_path is None
    return job, store


def _verified_receipt(
    job_id: str,
    fingerprint: str = "f" * 64,
) -> FolderReceiptVerification:
    return FolderReceiptVerification(
        status=FolderReceiptVerificationStatus.VERIFIED,
        job_id=job_id,
        receipt_fingerprint=fingerprint,
        checks=(
            FolderReceiptVerificationCheck(
                check_id="receipt_consistency",
                passed=True,
                detail="Independent receiver verification passed.",
            ),
        ),
    )


def _change_ledger(job: FolderRefactorJob) -> FolderChangeLedger:
    plan = job.accepted_plan
    assert plan is not None
    source_by_path = {
        source_file.relative_path: source_file
        for source_file in job.source_inventory.files
    }
    entries = tuple(
        FolderChangeEntry(
            file_id=mapping.file_id,
            original_path=mapping.original_path,
            result_path=mapping.target_path,
            original_size=source_by_path[mapping.original_path].size,
            original_sha256=source_by_path[mapping.original_path].sha256,
            result_size=source_by_path[mapping.original_path].size,
            result_sha256=source_by_path[mapping.original_path].sha256,
            protected=mapping.protected,
            path_changed=mapping.original_path != mapping.target_path,
            markdown_rewritten=False,
        )
        for mapping in plan.file_mappings
    )
    return FolderChangeLedger(
        source_commitment=job.source_inventory.source_commitment,
        request_fingerprint=request_fingerprint(job.user_request),
        evidence_fingerprint=plan.evidence_fingerprint,
        accepted_plan_fingerprint=canonical_sha256(plan),
        reference_graph_fingerprint="a" * 64,
        entries=entries,
        file_count=len(entries),
        source_bytes=sum(entry.original_size for entry in entries),
        result_bytes=sum(entry.result_size for entry in entries),
        path_change_count=sum(entry.path_changed for entry in entries),
        protected_file_count=sum(entry.protected for entry in entries),
        supported_link_count=0,
        rewritten_link_count=0,
        rewritten_markdown_file_count=0,
    )


def _finalization(
    job: FolderRefactorJob,
    *,
    fingerprint: str = "f" * 64,
) -> FolderJobFinalization:
    plan = job.accepted_plan
    assert plan is not None
    assert job.pending_result_path is not None
    assert job.final_result_path is not None
    return FolderJobFinalization(
        job_id=job.job_id,
        source_commitment=job.source_inventory.source_commitment,
        request_fingerprint=request_fingerprint(job.user_request),
        evidence_fingerprint=plan.evidence_fingerprint,
        accepted_plan_fingerprint=canonical_sha256(plan),
        pending_result_path=job.pending_result_path,
        final_result_path=job.final_result_path,
        change_ledger=_change_ledger(job),
        receipt_fingerprint=fingerprint,
        receipt_verification=_verified_receipt(job.job_id, fingerprint),
    )


def _begin(
    job: FolderRefactorJob,
    store: FolderRefactorJobStore,
) -> FolderRefactorJob:
    with store.writer() as writer:
        return writer.begin_execution(
            job,
            pending_result_path=expected_pending_result_path(job),
            final_result_path=expected_final_result_path(job),
            expected_revision=job.revision,
        )


@pytest.mark.anyio
async def test_begin_execution_persists_exact_paths_before_result_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)

    begun = _begin(job, store)

    assert begun.revision == job.revision + 1
    assert begun.pending_result_path == expected_pending_result_path(job)
    assert begun.final_result_path == expected_final_result_path(job)
    assert begun.pending_result_path.parent == job.output_parent
    assert begun.final_result_path.parent == job.output_parent
    assert classify_job_recovery_state(begun).state is (
        FolderJobRecoveryState.READY_TO_EXECUTE
    )
    assert FolderRefactorJobStore(store.path).load() == begun


@pytest.mark.anyio
async def test_begin_execution_rejects_wrong_path_existing_member_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    with (
        store.writer() as writer,
        pytest.raises(FolderJobWriteError, match="exact job-owned"),
    ):
        writer.begin_execution(
            job,
            pending_result_path=job.output_parent / "unowned.pending",
            final_result_path=expected_final_result_path(job),
            expected_revision=job.revision,
        )

    target = tmp_path / "unrelated"
    target.mkdir()
    expected_pending_result_path(job).symlink_to(target, target_is_directory=True)
    with (
        store.writer() as writer,
        pytest.raises(FolderJobWriteError, match="must be absent"),
    ):
        writer.begin_execution(
            job,
            pending_result_path=expected_pending_result_path(job),
            final_result_path=expected_final_result_path(job),
            expected_revision=job.revision,
        )
    assert store.load() == job


@pytest.mark.anyio
async def test_job_model_rejects_forged_execution_path_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)
    payload = begun.model_dump(mode="python")
    payload["pending_result_path"] = job.output_parent / "other.pending"

    with pytest.raises(ValidationError, match="not exactly owned"):
        FolderRefactorJob.model_validate(payload, strict=True)


@pytest.mark.anyio
async def test_generic_save_cannot_bypass_execution_path_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    candidate = job.model_copy(
        update={
            "pending_result_path": expected_pending_result_path(job),
            "final_result_path": expected_final_result_path(job),
        }
    )

    with (
        store.writer() as writer,
        pytest.raises(FolderJobRevisionError, match="dedicated"),
    ):
        writer.save(candidate, expected_revision=job.revision)


@pytest.mark.anyio
async def test_same_revision_out_of_band_replacement_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)
    forged = begun.model_copy(update={"display_name": "Out-of-band replacement"})
    store.path.write_bytes(canonical_job_bytes(forged))

    with (
        store.writer() as writer,
        pytest.raises(FolderJobRevisionError, match="differs"),
    ):
        writer.mark_execution_blocked(
            begun,
            code="execution_failed",
            message="The transaction failed.",
            expected_revision=begun.revision,
        )


@pytest.mark.anyio
async def test_finalize_verified_binds_proof_and_remains_restart_immutable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)
    assert begun.final_result_path is not None
    begun.final_result_path.mkdir()
    result_member = begun.final_result_path / "payload.txt"
    result_member.write_text("verified\n", encoding="utf-8")
    finalization = _finalization(begun)

    with store.writer() as writer:
        verified = writer.finalize_verified(
            begun,
            finalization,
            expected_revision=begun.revision,
        )

    assert verified.lifecycle is FolderJobLifecycle.VERIFIED
    assert verified.pending_result_path is None
    assert verified.final_result_path == begun.final_result_path
    assert verified.change_ledger == finalization.change_ledger
    assert verified.receipt_fingerprint == finalization.receipt_fingerprint
    durable_bytes = store.path.read_bytes()
    (job.source_root / "report.txt").write_text("changed\n", encoding="utf-8")
    result_member.write_text("corrupt after finalization\n", encoding="utf-8")
    assert store.load() == verified
    assert store.path.read_bytes() == durable_bytes
    with (
        store.writer() as writer,
        pytest.raises(FolderJobFinalizedError, match="immutable"),
    ):
        writer.finalize_verified(
            verified,
            finalization,
            expected_revision=verified.revision,
        )


@pytest.mark.anyio
async def test_finalize_verified_rejects_mismatched_binding_and_pending_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)
    assert begun.pending_result_path is not None
    assert begun.final_result_path is not None
    begun.pending_result_path.mkdir()
    begun.final_result_path.mkdir()

    with (
        store.writer() as writer,
        pytest.raises(FolderJobWriteError, match="another job"),
    ):
        writer.finalize_verified(
            begun,
            _finalization(begun).model_copy(update={"source_commitment": "b" * 64}),
            expected_revision=begun.revision,
        )
    other_job_id = "123e4567e89b42d3a456426614174000"
    assert other_job_id != begun.job_id
    wrong_receiver = _verified_receipt(other_job_id)
    with (
        store.writer() as writer,
        pytest.raises(FolderJobWriteError, match="Receiver verification"),
    ):
        writer.finalize_verified(
            begun,
            _finalization(begun).model_copy(
                update={"receipt_verification": wrong_receiver}
            ),
            expected_revision=begun.revision,
        )
    with (
        store.writer() as writer,
        pytest.raises(FolderJobWriteError, match="Pending result still exists"),
    ):
        writer.finalize_verified(
            begun,
            _finalization(begun),
            expected_revision=begun.revision,
        )
    assert store.load() == begun


@pytest.mark.anyio
async def test_execution_blocker_preserves_exact_paths_and_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)

    with store.writer() as writer:
        blocked = writer.mark_execution_blocked(
            begun,
            code="receipt_verification_failed",
            message="Independent verification blocked promotion.",
            expected_revision=begun.revision,
        )

    assert blocked.lifecycle is FolderJobLifecycle.BLOCKED
    assert blocked.pending_result_path == begun.pending_result_path
    assert blocked.final_result_path == begun.final_result_path
    with (
        store.writer() as writer,
        pytest.raises(FolderJobFinalizedError, match="immutable"),
    ):
        writer.mark_execution_blocked(
            blocked,
            code="again",
            message="No terminal mutation.",
            expected_revision=blocked.revision,
        )


@pytest.mark.anyio
async def test_recovery_classifier_distinguishes_owned_pending_states_and_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)
    assert begun.pending_result_path is not None
    assert begun.final_result_path is not None
    begun.pending_result_path.mkdir()

    assert classify_job_recovery_state(begun).state is (
        FolderJobRecoveryState.INCOMPLETE_OWNED_PENDING
    )
    receipt_path = begun.pending_result_path / RECEIPT_JSON_PATH
    receipt_path.parent.mkdir()
    receipt_path.write_text("{}\n", encoding="utf-8")
    assert classify_job_recovery_state(begun).state is (
        FolderJobRecoveryState.RECEIPT_FINALIZED_PENDING
    )
    begun.final_result_path.mkdir()
    assert classify_job_recovery_state(begun).state is (
        FolderJobRecoveryState.AMBIGUOUS
    )


@pytest.mark.anyio
async def test_recovery_classifier_requires_verification_for_final_and_rejects_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, store = await _accepted_executing_job(tmp_path, monkeypatch)
    begun = _begin(job, store)
    assert begun.final_result_path is not None
    begun.final_result_path.mkdir()

    assert classify_job_recovery_state(begun).state is (
        FolderJobRecoveryState.AMBIGUOUS
    )
    assert (
        classify_job_recovery_state(
            begun,
            final_verification=_verified_receipt(begun.job_id),
        ).state
        is FolderJobRecoveryState.VERIFIED_FINAL
    )

    other = tmp_path / "other-result"
    other.mkdir()
    linked_job, linked_store = await _accepted_executing_job(
        tmp_path / "linked",
        monkeypatch,
    )
    linked_begun = _begin(linked_job, linked_store)
    assert linked_begun.pending_result_path is not None
    linked_begun.pending_result_path.symlink_to(other, target_is_directory=True)
    with pytest.raises(FolderJobRecoveryError, match="real directory"):
        classify_job_recovery_state(linked_begun)


@pytest.mark.anyio
async def test_unreserved_preexisting_result_is_ambiguous_even_if_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job, _store = await _accepted_executing_job(tmp_path, monkeypatch)
    expected_final_result_path(job).mkdir()

    classified = classify_job_recovery_state(
        job,
        final_verification=_verified_receipt(job.job_id),
    )

    assert classified.state is FolderJobRecoveryState.AMBIGUOUS
    assert "ownership was not persisted" in classified.detail
