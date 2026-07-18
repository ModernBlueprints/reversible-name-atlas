"""Durable browser/CLI adapter for the AI-first folder workflow."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from name_atlas.folder_app import (
    FolderClarificationRequest,
    FolderProgressCallback,
    FolderRunOutcome,
    FolderRunPresentation,
    FolderWebCheckpoint,
    FolderWebLifecycle,
    FolderWorkPhase,
)
from name_atlas.folder_refactor.inventory import FolderScan
from name_atlas.folder_refactor.job import (
    FolderJobFinalization,
    FolderJobLifecycle,
    FolderJobRecoveryState,
    FolderRefactorJob,
    FolderRefactorJobStore,
    FolderRefactorJobWriter,
    build_new_job,
    classify_job_recovery_state,
    expected_final_result_path,
    expected_pending_result_path,
)
from name_atlas.folder_refactor.job_planning import JobPlannerCheckpoint
from name_atlas.folder_refactor.markdown_contracts import FolderReferenceGraph
from name_atlas.folder_refactor.planner_evidence import (
    LocalFolderEvidenceService,
    PlannerEvidenceError,
)
from name_atlas.folder_refactor.planner_orchestrator import PlannerOrchestrator
from name_atlas.folder_refactor.planner_provider import (
    DeterministicDevelopmentPlannerProvider,
    PlannerProvider,
)
from name_atlas.folder_refactor.portable_artifacts import (
    CHANGE_LEDGER_PATH,
    FolderPortableArtifactError,
    read_regular_bytes,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderChangeLedger,
    FolderEvidenceLedger,
    FolderReceiptVerification,
    FolderReceiptVerificationCheck,
    FolderReceiptVerificationStatus,
    FolderRestoreReport,
)
from name_atlas.folder_refactor.receipt_verifier import (
    FolderReceiptCandidateError,
    verify_folder_receipt,
)
from name_atlas.folder_refactor.transaction import (
    FolderReceiptContext,
    FolderRunResult,
    FolderTransactionError,
    FolderTransactionPhase,
    execute_accepted_folder_plan,
    maximum_rewritten_markdown_bytes,
    preflight_output_parent,
    scan_folder_with_references,
)
from name_atlas.verification.bag_writer import BagItWriter
from name_atlas.verification.bagit_validator import BagItPackageValidator
from name_atlas.verification.promotion import promote_directory_no_replace

PlannerProviderFactory = Callable[[FolderRefactorJob], PlannerProvider]
logger = logging.getLogger(__name__)


class FolderJobServiceError(RuntimeError):
    """The persisted folder workflow cannot continue or produce a result."""


class JobBackedFolderRunService:
    """Join durable planner state to one copy-only browser transaction."""

    def __init__(
        self,
        *,
        job_path: Path,
        provider_factory: PlannerProviderFactory | None = None,
        result_folder_name: str = "name-atlas-organized-copy",
        target_prefix: str = "organized",
    ) -> None:
        self._job_path = job_path.expanduser().resolve(strict=False)
        self._provider_factory = provider_factory or (
            lambda _job: DeterministicDevelopmentPlannerProvider(
                result_folder_name=result_folder_name,
                target_prefix=target_prefix,
            )
        )
        self._completed_presentation: FolderRunPresentation | None = None
        self._completed_request: str | None = None
        self._progress_callback: FolderProgressCallback | None = None

    @property
    def run_in_worker_thread(self) -> bool:
        """Keep scanning, planning, copying, and proof off the web loop."""

        return True

    def set_progress_callback(
        self,
        callback: FolderProgressCallback | None,
        /,
    ) -> None:
        """Install one presentation-only callback for the current browser process."""

        self._progress_callback = callback

    @property
    def job_path(self) -> Path:
        """Return the exact absolute durable job path."""

        return self._job_path

    def web_checkpoint(self) -> FolderWebCheckpoint | None:
        """Project current durable state without invoking a provider."""

        if not os.path.lexists(self._job_path):
            return None
        store = FolderRefactorJobStore(self._job_path)
        with store.writer() as writer:
            job = writer.load()
            if job.lifecycle is FolderJobLifecycle.EXECUTING:
                try:
                    recovered = self._recover_terminal_checkpoint(writer, job)
                except FolderJobServiceError as exc:
                    current = writer.load()
                    if not current.lifecycle.terminal:
                        self._mark_blocked(
                            writer,
                            "existing_result_recovery_failed",
                            str(exc),
                        )
                    job = writer.load()
                else:
                    if recovered is not None:
                        return self._verified_checkpoint(recovered)
                    job = writer.load()
                    if job.lifecycle is FolderJobLifecycle.EXECUTING:
                        job = writer.rehydrate()
            if job.lifecycle is FolderJobLifecycle.VERIFIED:
                try:
                    presentation = self._presentation_from_verified_job(job)
                except FolderJobServiceError as exc:
                    return FolderWebCheckpoint(
                        lifecycle=FolderWebLifecycle.BLOCKED,
                        source_root=job.source_root,
                        output_parent=job.output_parent,
                        request=job.user_request,
                        blocker=str(exc),
                    )
                return self._verified_checkpoint(presentation)
            if job.lifecycle in {
                FolderJobLifecycle.PLANNING,
                FolderJobLifecycle.AWAITING_CLARIFICATION,
            }:
                job = writer.rehydrate()
        if job.lifecycle is FolderJobLifecycle.AWAITING_CLARIFICATION:
            progress = job.planner_progress
            if progress is None or progress.clarification_question is None:
                raise FolderJobServiceError("awaiting_clarification_without_question")
            return FolderWebCheckpoint(
                lifecycle=FolderWebLifecycle.AWAITING_CLARIFICATION,
                source_root=job.source_root,
                output_parent=job.output_parent,
                request=job.user_request,
                clarification=FolderClarificationRequest(
                    question=progress.clarification_question,
                    continuation_token=job.job_id,
                ),
            )
        if job.lifecycle in {
            FolderJobLifecycle.PLANNING,
            FolderJobLifecycle.EXECUTING,
        }:
            return FolderWebCheckpoint(
                lifecycle=FolderWebLifecycle.PLANNING,
                source_root=job.source_root,
                output_parent=job.output_parent,
                request=job.user_request,
                resume_required=True,
            )
        if job.lifecycle in {FolderJobLifecycle.STALE, FolderJobLifecycle.BLOCKED}:
            return FolderWebCheckpoint(
                lifecycle=FolderWebLifecycle.BLOCKED,
                source_root=job.source_root,
                output_parent=job.output_parent,
                request=job.user_request,
                blocker=_job_blocker(job),
            )
        raise FolderJobServiceError(f"unsupported_job_lifecycle:{job.lifecycle.value}")

    async def plan_and_create_copy(
        self,
        *,
        source_root: Path,
        output_parent: Path,
        request: str,
    ) -> FolderRunOutcome:
        """Create one absent durable job, plan it, and execute if accepted."""

        if os.path.lexists(self._job_path):
            raise FolderJobServiceError(
                "job_already_exists: resume the exact job or choose an absent path"
            )
        self._report_progress(FolderWorkPhase.READING)
        scan, reference_graph = scan_folder_with_references(source_root)
        resolved_output_parent = preflight_output_parent(
            source_root=scan.source_root,
            output_parent=output_parent,
            source_bytes=scan.inventory.total_bytes,
            rewritten_markdown_original_bytes=maximum_rewritten_markdown_bytes(scan),
        )
        job = build_new_job(
            source_root=scan.source_root,
            output_parent=resolved_output_parent,
            job_path=self._job_path,
            user_request=request,
            scan=scan,
        )
        store = FolderRefactorJobStore(self._job_path)
        with store.writer() as writer:
            saved = writer.save(job, expected_revision=None)
            return await self._continue_job(
                writer=writer,
                job=saved,
                scan=scan,
                reference_graph=reference_graph,
                answer=None,
            )

    async def continue_after_clarification(
        self,
        *,
        continuation_token: str,
        answer: str,
    ) -> FolderRunPresentation:
        """Persist the one answer and continue the exact existing job."""

        store = FolderRefactorJobStore(self._job_path)
        with store.writer() as writer:
            job = writer.rehydrate()
            if continuation_token != job.job_id:
                raise FolderJobServiceError("clarification_job_token_mismatch")
            if job.lifecycle is not FolderJobLifecycle.AWAITING_CLARIFICATION:
                raise FolderJobServiceError("clarification_not_active")
            job, scan, reference_graph = self._scan_job(writer, job)
            outcome = await self._continue_job(
                writer=writer,
                job=job,
                scan=scan,
                reference_graph=reference_graph,
                answer=answer,
            )
        if not isinstance(outcome, FolderRunPresentation):
            raise FolderJobServiceError("second_clarification_not_allowed")
        return outcome

    async def resume_existing_job(self) -> FolderRunOutcome:
        """Continue a persisted planning/execution state exactly once."""

        store = FolderRefactorJobStore(self._job_path)
        with store.writer() as writer:
            job = writer.load()
            if job.lifecycle is FolderJobLifecycle.VERIFIED:
                return self._presentation_from_verified_job(job)
            if job.lifecycle is FolderJobLifecycle.EXECUTING:
                recovered = self._recover_terminal_checkpoint(writer, job)
                if recovered is not None:
                    return recovered
                job = writer.load()
                if job.lifecycle is FolderJobLifecycle.EXECUTING:
                    job = writer.rehydrate()
            if job.lifecycle is FolderJobLifecycle.AWAITING_CLARIFICATION:
                progress = job.planner_progress
                if progress is None or progress.clarification_question is None:
                    raise FolderJobServiceError(
                        "awaiting_clarification_without_question"
                    )
                return FolderClarificationRequest(
                    question=progress.clarification_question,
                    continuation_token=job.job_id,
                )
            if job.lifecycle not in {
                FolderJobLifecycle.PLANNING,
                FolderJobLifecycle.EXECUTING,
            }:
                raise FolderJobServiceError(_job_blocker(job))
            self._report_progress(FolderWorkPhase.READING)
            job, scan, reference_graph = self._scan_job(writer, job)
            return await self._continue_job(
                writer=writer,
                job=job,
                scan=scan,
                reference_graph=reference_graph,
                answer=None,
            )

    async def _continue_job(
        self,
        *,
        writer: FolderRefactorJobWriter,
        job: FolderRefactorJob,
        scan: FolderScan,
        reference_graph: FolderReferenceGraph,
        answer: str | None,
    ) -> FolderRunOutcome:
        preflight_output_parent(
            source_root=scan.source_root,
            output_parent=job.output_parent,
            source_bytes=scan.inventory.total_bytes,
            rewritten_markdown_original_bytes=maximum_rewritten_markdown_bytes(scan),
        )
        if job.lifecycle is FolderJobLifecycle.EXECUTING:
            self._report_progress(FolderWorkPhase.CHECKING)
            return self._execute(writer, job, scan, reference_graph)
        self._report_progress(FolderWorkPhase.PLANNING)
        provider = self._provider_factory(job)
        orchestrator = PlannerOrchestrator(
            job_id=job.job_id,
            scan=scan,
            request=job.user_request,
            provider=provider,
            evidence_service=LocalFolderEvidenceService(
                scan,
                reference_graph=reference_graph,
            ),
            reference_graph=reference_graph,
            checkpoint=JobPlannerCheckpoint(writer),
        )
        try:
            progress = job.planner_progress or orchestrator.initial_progress()
        except PlannerEvidenceError as exc:
            self._mark_blocked(writer, exc.code, exc.message)
            raise FolderJobServiceError(f"{exc.code}: {exc.message}") from exc
        if answer is None:
            progress = await orchestrator.run(progress)
        else:
            progress = await orchestrator.answer_clarification(progress, answer)
        latest = writer.load()
        if progress.status == "awaiting_clarification":
            if progress.clarification_question is None:
                raise FolderJobServiceError("awaiting_clarification_without_question")
            return FolderClarificationRequest(
                question=progress.clarification_question,
                continuation_token=latest.job_id,
            )
        if progress.status == "blocked":
            raise FolderJobServiceError(
                f"{progress.blocker_code}: planner could not produce an accepted plan"
            )
        if progress.status != "accepted":
            raise FolderJobServiceError("planner_stopped_without_terminal_outcome")
        self._report_progress(FolderWorkPhase.CHECKING)
        return self._execute(writer, latest, scan, reference_graph)

    def _execute(
        self,
        writer: FolderRefactorJobWriter,
        job: FolderRefactorJob,
        scan: FolderScan,
        reference_graph: FolderReferenceGraph,
    ) -> FolderRunPresentation:
        if job.accepted_plan is None:
            raise FolderJobServiceError("execution_without_accepted_plan")
        recovered = self._recover_terminal_checkpoint(writer, job)
        if recovered is not None:
            return recovered
        job = writer.load()
        recovery = classify_job_recovery_state(job)
        if recovery.state is FolderJobRecoveryState.INCOMPLETE_OWNED_PENDING:
            self._remove_incomplete_pending(job)
            recovery = classify_job_recovery_state(job)
        if recovery.state is not FolderJobRecoveryState.READY_TO_EXECUTE:
            message = f"execution_recovery_ambiguous: {recovery.detail}"
            self._mark_blocked(writer, "execution_recovery_ambiguous", message)
            raise FolderJobServiceError(message)
        if job.pending_result_path is None or job.final_result_path is None:
            job = writer.begin_execution(
                job,
                pending_result_path=expected_pending_result_path(job),
                final_result_path=expected_final_result_path(job),
                expected_revision=job.revision,
            )
        evidence_ledger = _public_evidence_ledger(job)
        assert job.pending_result_path is not None
        assert job.final_result_path is not None
        try:
            result = execute_accepted_folder_plan(
                initial_scan=scan,
                output_parent=job.output_parent,
                request=job.user_request,
                accepted_plan=job.accepted_plan,
                reference_graph=reference_graph,
                bag_writer=BagItWriter(),
                package_validator=BagItPackageValidator(),
                progress_callback=self._report_transaction_phase,
                receipt_context=FolderReceiptContext(
                    job_id=job.job_id,
                    evidence_ledger=evidence_ledger,
                    pending_root=job.pending_result_path,
                    final_root=job.final_result_path,
                ),
            )
        except FolderTransactionError as exc:
            stale = writer.rehydrate()
            if stale.lifecycle is FolderJobLifecycle.STALE:
                raise FolderJobServiceError(_job_blocker(stale)) from exc
            self._mark_blocked(writer, "folder_transaction_blocked", str(exc))
            raise FolderJobServiceError(str(exc)) from exc
        if (
            result.change_ledger is None
            or result.receipt_fingerprint is None
            or result.receiver_verification is None
        ):
            self._mark_blocked(
                writer,
                "receipt_transaction_incomplete",
                "A3 transaction returned without complete receipt proof.",
            )
            raise FolderJobServiceError("receipt_transaction_incomplete")
        finalization = FolderJobFinalization(
            job_id=job.job_id,
            source_commitment=job.source_inventory.source_commitment,
            request_fingerprint=job.accepted_plan.request_fingerprint,
            evidence_fingerprint=job.accepted_plan.evidence_fingerprint,
            accepted_plan_fingerprint=evidence_ledger.accepted_plan_fingerprint,
            pending_result_path=job.pending_result_path,
            final_result_path=job.final_result_path,
            change_ledger=result.change_ledger,
            receipt_fingerprint=result.receipt_fingerprint,
            receipt_verification=result.receiver_verification,
        )
        verified_job = writer.finalize_verified(
            job,
            finalization,
            expected_revision=job.revision,
        )
        presentation = _presentation(verified_job, result)
        self._completed_presentation = presentation
        self._completed_request = verified_job.user_request
        return presentation

    def verify_again(self) -> FolderReceiptVerification:
        """Rerun the source-free verifier without mutating durable job state."""

        job = self._load_verified_job()
        return _verify_job_receipt(job)

    def recreate_original(self, destination: Path) -> FolderRestoreReport:
        """Recreate original paths and bytes without mutating source or result."""

        job = self._load_verified_job()
        resolved_destination = destination.expanduser().resolve(strict=False)
        if _paths_overlap(resolved_destination, job.source_root):
            raise FolderJobServiceError("reconstruction_destination_overlaps_source")
        verification = _verify_job_receipt(job)
        if verification.status is not FolderReceiptVerificationStatus.VERIFIED:
            failures = ",".join(verification.failed_check_ids)
            raise FolderJobServiceError(
                f"receipt_verification_blocked:{failures or 'unknown'}"
            )
        from name_atlas.folder_refactor.reconstruction import (
            restore_folder_receipt,
        )

        assert job.final_result_path is not None
        return restore_folder_receipt(job.final_result_path, resolved_destination)

    def _recover_terminal_checkpoint(
        self,
        writer: FolderRefactorJobWriter,
        job: FolderRefactorJob,
    ) -> FolderRunPresentation | None:
        """Recover only exact persisted execution roots without provider activity."""

        if job.lifecycle is not FolderJobLifecycle.EXECUTING:
            return None
        recovery = classify_job_recovery_state(job)
        final_root = job.final_result_path
        pending_root = job.pending_result_path
        if (
            final_root is not None
            and os.path.lexists(final_root)
            and (pending_root is None or not os.path.lexists(pending_root))
        ):
            verification = _verify_candidate(final_root)
            if (
                verification.status is FolderReceiptVerificationStatus.VERIFIED
                and verification.job_id != job.job_id
            ):
                raise FolderJobServiceError("final_result_job_id_mismatch")
            recovery = classify_job_recovery_state(
                job,
                final_verification=verification,
            )
            if recovery.state is not FolderJobRecoveryState.VERIFIED_FINAL:
                failures = ",".join(verification.failed_check_ids)
                raise FolderJobServiceError(
                    f"final_result_verification_blocked:{failures or recovery.detail}"
                )
            job, _scan, _graph = self._scan_job(writer, job)
            verified = self._finalize_recovered_result(
                writer,
                job,
                final_root,
                verification,
            )
            return self._presentation_from_verified_job(
                verified,
                verification=verification,
            )
        if recovery.state is FolderJobRecoveryState.RECEIPT_FINALIZED_PENDING:
            assert pending_root is not None
            verification = _verify_candidate(pending_root)
            if verification.status is not FolderReceiptVerificationStatus.VERIFIED:
                failures = ",".join(verification.failed_check_ids)
                raise FolderJobServiceError(
                    f"pending_result_verification_blocked:{failures or 'unknown'}"
                )
            if verification.job_id != job.job_id:
                raise FolderJobServiceError("pending_result_job_id_mismatch")
            job, _scan, _graph = self._scan_job(writer, job)
            assert job.final_result_path is not None
            promote_directory_no_replace(pending_root, job.final_result_path)
            verified = self._finalize_recovered_result(
                writer,
                job,
                job.final_result_path,
                verification,
            )
            return self._presentation_from_verified_job(
                verified,
                verification=verification,
            )
        if recovery.state is FolderJobRecoveryState.AMBIGUOUS:
            raise FolderJobServiceError(
                f"execution_recovery_ambiguous:{recovery.detail}"
            )
        return None

    def _finalize_recovered_result(
        self,
        writer: FolderRefactorJobWriter,
        job: FolderRefactorJob,
        result_root: Path,
        verification: FolderReceiptVerification,
    ) -> FolderRefactorJob:
        plan = job.accepted_plan
        if (
            plan is None
            or job.pending_result_path is None
            or job.final_result_path is None
            or verification.receipt_fingerprint is None
        ):
            raise FolderJobServiceError("recovered_result_missing_job_authority")
        try:
            change_ledger = FolderChangeLedger.model_validate_json(
                read_regular_bytes(result_root, CHANGE_LEDGER_PATH),
                strict=True,
            )
        except (FolderPortableArtifactError, ValueError) as exc:
            raise FolderJobServiceError("recovered_change_ledger_invalid") from exc
        evidence_ledger = _public_evidence_ledger(job)
        finalization = FolderJobFinalization(
            job_id=job.job_id,
            source_commitment=job.source_inventory.source_commitment,
            request_fingerprint=plan.request_fingerprint,
            evidence_fingerprint=plan.evidence_fingerprint,
            accepted_plan_fingerprint=evidence_ledger.accepted_plan_fingerprint,
            pending_result_path=job.pending_result_path,
            final_result_path=job.final_result_path,
            change_ledger=change_ledger,
            receipt_fingerprint=verification.receipt_fingerprint,
            receipt_verification=verification,
        )
        return writer.finalize_verified(
            job,
            finalization,
            expected_revision=job.revision,
        )

    def _presentation_from_verified_job(
        self,
        job: FolderRefactorJob,
        *,
        verification: FolderReceiptVerification | None = None,
    ) -> FolderRunPresentation:
        actual_verification = verification or _verify_job_receipt(job)
        if actual_verification.status is not FolderReceiptVerificationStatus.VERIFIED:
            failures = ",".join(actual_verification.failed_check_ids)
            raise FolderJobServiceError(
                f"verified_result_no_longer_valid:{failures or 'unknown'}"
            )
        ledger = job.change_ledger
        if (
            ledger is None
            or job.final_result_path is None
            or job.receipt_fingerprint is None
        ):
            raise FolderJobServiceError("verified_job_missing_receipt_authority")
        presentation = FolderRunPresentation(
            source_root=job.source_root,
            output_parent=job.output_parent,
            result_root=job.final_result_path,
            data_root=job.final_result_path / "data",
            source_file_count=ledger.file_count,
            path_change_count=ledger.path_change_count,
            supported_link_count=ledger.supported_link_count,
            supported_link_update_count=ledger.rewritten_link_count,
            source_unchanged=True,
            all_files_present_once=True,
            deterministic_proof_passed=True,
            independent_verification_passed=True,
            reconstruction_available=True,
            technical_facts=(
                ("Job ID", job.job_id),
                ("Source commitment", job.source_inventory.source_commitment),
                ("Receipt fingerprint", job.receipt_fingerprint),
                ("Independent verification", "Passed without GPT or source"),
            ),
        )
        self._completed_presentation = presentation
        self._completed_request = job.user_request
        return presentation

    def _verified_checkpoint(
        self,
        presentation: FolderRunPresentation,
    ) -> FolderWebCheckpoint:
        request = self._completed_request
        if request is None:
            raise FolderJobServiceError("verified_presentation_without_request")
        return FolderWebCheckpoint(
            lifecycle=FolderWebLifecycle.VERIFIED,
            source_root=presentation.source_root,
            output_parent=presentation.output_parent,
            request=request,
            result=presentation,
        )

    def _load_verified_job(self) -> FolderRefactorJob:
        store = FolderRefactorJobStore(self._job_path)
        with store.writer() as writer:
            job = writer.load()
        if job.lifecycle is not FolderJobLifecycle.VERIFIED:
            raise FolderJobServiceError(
                f"verified_result_required:{job.lifecycle.value}"
            )
        return job

    @staticmethod
    def _remove_incomplete_pending(job: FolderRefactorJob) -> None:
        pending = job.pending_result_path
        if pending is None:
            raise FolderJobServiceError("incomplete_pending_without_owned_path")
        size_bytes = _directory_entry_bytes(pending)
        try:
            shutil.rmtree(pending)
        except OSError as exc:
            raise FolderJobServiceError("incomplete_pending_cleanup_failed") from exc
        logger.warning(
            "Removed regenerable incomplete pending result before exact restart: "
            "path=%s size_bytes=%d reason=incomplete_owned_pending "
            "regeneration=resume_same_job",
            pending,
            size_bytes,
        )

    def _report_transaction_phase(self, phase: FolderTransactionPhase) -> None:
        mapped = {
            FolderTransactionPhase.CREATING_RESULT: FolderWorkPhase.CREATING,
            FolderTransactionPhase.UPDATING_SUPPORTED_LINKS: (
                FolderWorkPhase.UPDATING_LINKS
            ),
            FolderTransactionPhase.VERIFYING_RESULT: FolderWorkPhase.VERIFYING,
        }[phase]
        self._report_progress(mapped)

    def _report_progress(self, phase: FolderWorkPhase) -> None:
        callback = self._progress_callback
        if callback is not None:
            callback(phase)

    def _scan_job(
        self,
        writer: FolderRefactorJobWriter,
        job: FolderRefactorJob,
    ) -> tuple[FolderRefactorJob, FolderScan, FolderReferenceGraph]:
        rehydrated = writer.rehydrate()
        if rehydrated.lifecycle is FolderJobLifecycle.STALE:
            raise FolderJobServiceError("source_changed: durable inventory mismatch")
        scan, graph = scan_folder_with_references(job.source_root)
        rehydrated = writer.rehydrate_against(scan)
        if rehydrated.lifecycle is FolderJobLifecycle.STALE:
            raise FolderJobServiceError("source_changed: durable inventory mismatch")
        return rehydrated, scan, graph

    def _mark_blocked(
        self,
        writer: FolderRefactorJobWriter,
        code: str,
        message: str,
    ) -> None:
        current = writer.load()
        if current.lifecycle.terminal:
            return
        if current.lifecycle is FolderJobLifecycle.EXECUTING:
            writer.mark_execution_blocked(
                current,
                code=code,
                message=message[:2_000],
                expected_revision=current.revision,
            )
            return
        payload = current.model_dump(mode="python")
        payload.update(
            {
                "lifecycle": FolderJobLifecycle.BLOCKED,
                "blocker_code": code,
                "blocker_message": message[:2_000],
            }
        )
        candidate = FolderRefactorJob.model_validate(payload, strict=True)
        writer.save(candidate, expected_revision=current.revision)


def _public_evidence_ledger(job: FolderRefactorJob) -> FolderEvidenceLedger:
    progress = job.planner_progress
    if (
        progress is None
        or progress.status != "accepted"
        or progress.accepted_plan is None
    ):
        raise FolderJobServiceError("accepted_planner_evidence_required")
    return FolderEvidenceLedger.from_progress(
        job_id=job.job_id,
        progress=progress,
        store_false=True if progress.provider_kind == "live" else None,
    )


def _directory_entry_bytes(root: Path) -> int:
    """Measure one exact job-owned pending tree before recorded cleanup."""

    total = root.lstat().st_size

    def raise_walk_error(error: OSError) -> None:
        raise error

    for current_root, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=raise_walk_error,
    ):
        current = Path(current_root)
        for name in (*directory_names, *file_names):
            total += (current / name).lstat().st_size
    return total


def _verify_candidate(result_root: Path) -> FolderReceiptVerification:
    try:
        return verify_folder_receipt(result_root)
    except FolderReceiptCandidateError as exc:
        raise FolderJobServiceError("result_candidate_cannot_be_opened") from exc


def _verify_job_receipt(job: FolderRefactorJob) -> FolderReceiptVerification:
    if job.lifecycle is not FolderJobLifecycle.VERIFIED:
        raise FolderJobServiceError("verified_job_required")
    if job.final_result_path is None or job.receipt_fingerprint is None:
        raise FolderJobServiceError("verified_job_missing_receipt_authority")
    result = _verify_candidate(job.final_result_path)
    if result.status is FolderReceiptVerificationStatus.VERIFIED and (
        result.job_id != job.job_id
        or result.receipt_fingerprint != job.receipt_fingerprint
    ):
        mismatch = FolderReceiptVerificationCheck(
            check_id="job_receipt_authority_mismatch",
            passed=False,
            detail="Verified receipt identity differs from the immutable job.",
        )
        return FolderReceiptVerification(
            status=FolderReceiptVerificationStatus.BLOCKED,
            job_id=result.job_id,
            receipt_fingerprint=result.receipt_fingerprint,
            checks=(*result.checks, mismatch),
            failed_check_ids=(mismatch.check_id,),
        )
    return result


def _presentation(
    job: FolderRefactorJob,
    result: FolderRunResult,
) -> FolderRunPresentation:
    checks = {check.check_id: check.passed for check in result.report.checks}
    independently_verified = (
        result.receiver_verification is not None
        and result.receiver_verification.status
        is FolderReceiptVerificationStatus.VERIFIED
        and result.receipt_fingerprint is not None
    )
    if independently_verified:
        technical_facts = (
            ("Job ID", job.job_id),
            ("Source commitment", result.report.source_commitment),
            ("Receipt fingerprint", result.receipt_fingerprint or ""),
            ("Independent verification", "Passed without GPT or source"),
        )
    else:
        technical_facts = (
            ("Job ID", job.job_id),
            ("Source commitment", result.report.source_commitment),
            ("Staged data commitment", result.report.staged_data_commitment),
            ("Portable package", "BagIt validation passed"),
        )
    return FolderRunPresentation(
        source_root=job.source_root,
        output_parent=job.output_parent,
        result_root=result.result_root,
        data_root=result.data_root,
        source_file_count=result.report.file_count,
        path_change_count=result.report.path_change_count,
        supported_link_count=result.report.supported_link_count,
        supported_link_update_count=result.report.rewritten_link_count,
        source_unchanged=checks.get("source_unchanged") is True,
        all_files_present_once=(
            checks.get("complete_file_bijection") is True
            and checks.get("payload_hashes_preserved") is True
        ),
        deterministic_proof_passed=bool(checks) and all(checks.values()),
        independent_verification_passed=independently_verified,
        reconstruction_available=independently_verified,
        technical_facts=technical_facts,
    )


def _job_blocker(job: FolderRefactorJob) -> str:
    if job.lifecycle is FolderJobLifecycle.STALE:
        if job.stale_differences:
            changed = ", ".join(
                difference.kind.value for difference in job.stale_differences[:5]
            )
            return f"source_changed: {changed}"
        if job.source_scan_blocker is not None:
            return f"source_scan_failed: {job.source_scan_blocker.detail}"
        return "source_changed"
    if job.blocker_code is not None:
        return f"{job.blocker_code}: {job.blocker_message}"
    return f"job_not_resumable:{job.lifecycle.value}"


def _paths_overlap(left: Path, right: Path) -> bool:
    """Return whether two resolved local paths contain one another."""

    return left == right or left in right.parents or right in left.parents
