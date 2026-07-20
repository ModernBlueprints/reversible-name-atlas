"""Hosted Foldweave planning over the single durable v3 review authority."""

from __future__ import annotations

import fcntl
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from name_atlas.folder_refactor.compiler import PlanCompilationError, compile_plan
from name_atlas.folder_refactor.connected_change.accepted_plan import (
    FolderAcceptedPlanV2,
    convert_planner_accepted_plan,
)
from name_atlas.folder_refactor.connected_change.job_v2 import (
    FolderIdempotencyBindingV2,
    FolderMutationRequestV2,
    JobLocalDirectoryIdentityV2,
    JobLocalFileIdentityV2,
    build_idempotency_binding,
    build_new_gpt_job_v2,
)
from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderHostMutationBindingV1,
    FolderJobLifecycleV3,
    FolderJobV3IdempotencyConflict,
    FolderJobV3RevisionError,
    FolderRefactorJobV3,
    FolderRefactorJobV3Store,
    FolderRevisionFailureV1,
    GptHostedJobAuthorityV3,
    build_host_mutation_binding,
    build_revision_instruction,
    evolve_job_v3,
    host_clarification_question_fingerprint,
)
from name_atlas.folder_refactor.connected_change.preview import (
    FolderPlanPreviewV1,
    build_folder_plan_preview,
)
from name_atlas.folder_refactor.connected_change.review_service import (
    FoldweaveReviewService,
)
from name_atlas.folder_refactor.connected_change.verification import (
    ConnectedReceiptVerification,
)
from name_atlas.folder_refactor.contracts import FolderPlan, FolderPlanEntry
from name_atlas.folder_refactor.foldweave_host_contracts import (
    FolderHostClarificationEventV1,
    FolderHostEvidenceObservationV1,
    FolderHostPlanningStateV1,
    FolderHostPlanRevisionV1,
    FolderHostPlanSubmissionV1,
    HostModelTransport,
    build_host_compiler_failure,
    build_host_event,
    build_host_evidence_ledger,
    build_host_pending_revision,
    build_host_planning_state,
    build_host_revision_turn,
    host_contract_freeze_fingerprint,
)
from name_atlas.folder_refactor.foldweave_planning_contracts import (
    FolderEvidenceLedgerV2,
    append_failed_host_revision_evidence,
    append_successful_host_revision_evidence,
    build_execution_origin_v2,
    build_initial_composite_evidence,
)
from name_atlas.folder_refactor.planner_contracts import (
    InspectMarkdownLinksCall,
    ListInventoryPageCall,
    ReadTextExcerptCall,
)
from name_atlas.folder_refactor.planner_evidence import (
    LocalFolderEvidenceService,
    append_evidence_execution,
    create_initial_evidence_ledger,
)
from name_atlas.folder_refactor.serialization import (
    canonical_sha256,
    request_fingerprint,
)
from name_atlas.folder_refactor.transaction import scan_folder_with_references
from name_atlas.foldweave_job_locator import (
    FoldweaveJobLocator,
    FoldweaveJobLocatorError,
)
from name_atlas.foldweave_local_handles import (
    FoldweaveLocalHandleStore,
    LocalHandleChannel,
    OpaqueLocalItemHandle,
)
from name_atlas.foldweave_paths import FoldweavePaths, foldweave_paths
from name_atlas.native_bridge import (
    MacOSNativePathBridge,
    NativePathBridge,
    NativePathRole,
    NativeSelectionStatus,
)

oslo_tz = ZoneInfo("Europe/Oslo")
HOST_CONTRACT_FREEZE_FINGERPRINT = host_contract_freeze_fingerprint()


class FoldweaveHostServiceError(RuntimeError):
    """One stable host-tool failure with no local-path disclosure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class FoldweaveHostPlanningService:
    """Expose bounded host-model planning without any direct-provider dependency."""

    def __init__(
        self,
        *,
        paths: FoldweavePaths | None = None,
        handle_store: FoldweaveLocalHandleStore | None = None,
        native_bridge: NativePathBridge | None = None,
        review_service: FoldweaveReviewService | None = None,
        clock=None,
    ) -> None:
        self._paths = paths or foldweave_paths()
        self._handles = handle_store or FoldweaveLocalHandleStore()
        self._native_bridge = native_bridge or MacOSNativePathBridge()
        self._review = review_service or FoldweaveReviewService()
        self._clock = clock or (lambda: datetime.now(tz=oslo_tz))

    async def choose_local_item(
        self,
        *,
        role: NativePathRole,
        channel: LocalHandleChannel,
    ) -> tuple[NativeSelectionStatus, OpaqueLocalItemHandle | None, str | None]:
        """Select one fixed-role native item and return no local path."""

        selection = await self._native_bridge.choose_path(role)
        if selection.status is not NativeSelectionStatus.SELECTED:
            return selection.status, None, selection.reason_code
        assert selection.path is not None
        public = self._handles.register(
            role=role,
            path=selection.path,
            channel=channel,
        )
        return selection.status, public, None

    def create_or_resume_planning_job(
        self,
        *,
        source_handle: str,
        output_handle: str,
        request: str,
        disclosure_acknowledged: bool,
        idempotency_key: str,
        model_transport: HostModelTransport,
    ) -> FolderRefactorJobV3:
        """Create one durable hosted v3 job without invoking a provider."""

        if disclosure_acknowledged is not True:
            raise FoldweaveHostServiceError(
                "evidence_disclosure_required",
                "Planning requires literal acceptance of the bounded evidence "
                "disclosure.",
            )
        source_root = self._handles.resolve(
            source_handle,
            role=NativePathRole.SOURCE_FOLDER,
            channel=model_transport,
        )
        output_parent = self._handles.resolve(
            output_handle,
            role=NativePathRole.OUTPUT_PARENT,
            channel=model_transport,
        )
        normalized_request = request.strip()
        if not normalized_request or normalized_request != request:
            raise FoldweaveHostServiceError(
                "request_invalid",
                "Planning request must be nonblank, trimmed UTF-8 text.",
            )
        mutation = FolderMutationRequestV2(
            operation="gpt_planned",
            source_root=source_root,
            output_parent=output_parent,
            user_request=request,
        )
        binding = build_idempotency_binding(idempotency_key, mutation)
        existing = self._find_idempotent_job(binding.key_sha256)
        if existing is not None:
            return _require_matching_host_job(
                existing,
                binding=binding,
                model_transport=model_transport,
            )

        job_id = uuid.uuid4().hex
        job_path = self._job_path(job_id)
        seed = build_new_gpt_job_v2(
            source_root=source_root,
            output_parent=output_parent,
            job_path=job_path,
            user_request=request,
            idempotency_key=idempotency_key,
            job_id=job_id,
            clock=self._clock,
        )
        evidence_state = create_initial_evidence_ledger(
            seed.source_inventory,
            request,
        )
        planning_state = build_host_planning_state(
            job_id=job_id,
            model_transport=model_transport,
            source_commitment=seed.source_inventory.source_commitment,
            request_fingerprint=request_fingerprint(request),
            evidence_state=evidence_state,
            events=(),
            compiler_failures=(),
            response_turn_count=0,
            plan_submission_count=0,
            clarification_question=None,
            clarification_answer=None,
            accepted_plan_fingerprint=None,
            status="planning",
        )
        authority = GptHostedJobAuthorityV3(
            model_transport=model_transport,
            planning_state=planning_state,
        )
        initial = FolderRefactorJobV3(
            revision=seed.revision,
            job_id=seed.job_id,
            display_name=seed.display_name,
            created_at=seed.created_at,
            updated_at=seed.updated_at,
            source_root=seed.source_root,
            output_parent=seed.output_parent,
            job_path=seed.job_path,
            source_inventory=seed.source_inventory,
            local_file_identities=seed.local_file_identities,
            local_directory_identities=seed.local_directory_identities,
            user_request=seed.user_request,
            idempotency=seed.idempotency,
            authority=authority,
            lifecycle=FolderJobLifecycleV3.PLANNING,
        )
        self._paths.jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _host_job_creation_lock(self._paths.jobs):
            existing = self._find_idempotent_job(binding.key_sha256)
            if existing is not None:
                return _require_matching_host_job(
                    existing,
                    binding=binding,
                    model_transport=model_transport,
                )
            with FolderRefactorJobV3Store(job_path).writer() as writer:
                return writer.save_new(initial)

    def list_inventory_page(
        self,
        *,
        job_id: str,
        call_id: str,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> tuple[FolderRefactorJobV3, JsonValue | None, str | None]:
        return self._execute_evidence(
            job_id,
            ListInventoryPageCall(
                call_id=call_id,
                cursor=cursor,
                page_size=page_size,
            ),
        )

    def read_text_excerpt(
        self,
        *,
        job_id: str,
        call_id: str,
        file_id: str,
        start_byte: int,
        max_bytes: int,
    ) -> tuple[FolderRefactorJobV3, JsonValue | None, str | None]:
        return self._execute_evidence(
            job_id,
            ReadTextExcerptCall(
                call_id=call_id,
                file_id=file_id,
                start_byte=start_byte,
                max_bytes=max_bytes,
            ),
        )

    def inspect_markdown_links(
        self,
        *,
        job_id: str,
        call_id: str,
        file_id: str,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> tuple[FolderRefactorJobV3, JsonValue | None, str | None]:
        return self._execute_evidence(
            job_id,
            InspectMarkdownLinksCall(
                call_id=call_id,
                file_id=file_id,
                cursor=cursor,
                page_size=page_size,
            ),
        )

    def request_clarification(
        self,
        *,
        job_id: str,
        expected_revision: int,
        question: str,
        idempotency_key: str,
    ) -> FolderRefactorJobV3:
        """Persist the sole host-originated clarification question."""

        with FolderRefactorJobV3Store(self._job_path(job_id)).writer() as writer:
            current = writer.rehydrate()
            authority = _require_host_authority(current)
            state = authority.planning_state
            normalized = question.strip()
            if not normalized or normalized != question:
                raise FoldweaveHostServiceError(
                    "clarification_invalid",
                    "Clarification must be nonblank, trimmed UTF-8 text.",
                )
            question_fingerprint = host_clarification_question_fingerprint(question)
            binding = build_host_mutation_binding(
                operation="request_clarification",
                job_id=job_id,
                expected_job_revision=expected_revision,
                question_fingerprint=question_fingerprint,
                answer=None,
                idempotency_key=idempotency_key,
            )
            repeated = _require_host_mutation_retry_or_none(current, binding)
            if repeated is not None:
                return repeated
            if current.revision != expected_revision:
                raise FoldweaveHostServiceError(
                    "clarification_binding_mismatch",
                    "Clarification request targets another durable job revision.",
                )
            prior_questions = tuple(
                event
                for event in state.events
                if isinstance(event, FolderHostClarificationEventV1)
                and event.phase == "question"
            )
            if prior_questions:
                if all(event.text == question for event in prior_questions):
                    return current
                raise FoldweaveHostServiceError(
                    "clarification_limit_exceeded",
                    "This planning job has already used its sole clarification.",
                )
            if current.lifecycle is not FolderJobLifecycleV3.PLANNING:
                raise FoldweaveHostServiceError(
                    "clarification_unavailable",
                    "Clarification is available only while planning.",
                )
            response_turn = _next_host_response_turn(state)
            event = build_host_event(
                FolderHostClarificationEventV1,
                event_index=len(state.events) + 1,
                response_turn=response_turn,
                phase="question",
                text=question,
                question_fingerprint=question_fingerprint,
            )
            updated_state = _rebuild_state(
                state,
                events=(*state.events, event),
                response_turn_count=response_turn,
                clarification_question=question,
                status="awaiting_clarification",
            )
            successor = evolve_job_v3(
                current,
                revision=current.revision + 1,
                updated_at=self._now(),
                authority=_replace_host_authority(
                    authority,
                    planning_state=updated_state,
                ),
                lifecycle=FolderJobLifecycleV3.AWAITING_CLARIFICATION,
                clarification_count=1,
                host_mutation_bindings=(*current.host_mutation_bindings, binding),
            )
            return writer.save(successor, expected_current=current)

    def answer_clarification(
        self,
        *,
        job_id: str,
        expected_revision: int,
        question_fingerprint: str,
        answer: str,
        idempotency_key: str,
    ) -> FolderRefactorJobV3:
        """Bind the user's exact answer before hosted planning continues."""

        with FolderRefactorJobV3Store(self._job_path(job_id)).writer() as writer:
            current = writer.rehydrate()
            authority = _require_host_authority(current)
            state = authority.planning_state
            normalized = answer.strip()
            if not normalized or normalized != answer:
                raise FoldweaveHostServiceError(
                    "clarification_answer_invalid",
                    "Clarification answer must be nonblank and trimmed.",
                )
            binding = build_host_mutation_binding(
                operation="answer_clarification",
                job_id=job_id,
                expected_job_revision=expected_revision,
                question_fingerprint=question_fingerprint,
                answer=answer,
                idempotency_key=idempotency_key,
            )
            repeated = _require_host_mutation_retry_or_none(current, binding)
            if repeated is not None:
                return repeated
            if current.revision != expected_revision:
                raise FoldweaveHostServiceError(
                    "clarification_binding_mismatch",
                    "Clarification answer targets another durable job revision.",
                )
            prior_answers = tuple(
                event
                for event in state.events
                if isinstance(event, FolderHostClarificationEventV1)
                and event.phase == "answer"
            )
            if prior_answers:
                if all(event.text == answer for event in prior_answers):
                    return current
                raise FolderJobV3IdempotencyConflict(
                    "Hosted clarification answer is bound to another exact value."
                )
            if (
                current.lifecycle is not FolderJobLifecycleV3.AWAITING_CLARIFICATION
                or state.clarification_question is None
                or state.clarification_answer is not None
            ):
                raise FoldweaveHostServiceError(
                    "clarification_answer_unavailable",
                    "The job has no unanswered hosted clarification.",
                )
            question_event = next(
                event
                for event in state.events
                if isinstance(event, FolderHostClarificationEventV1)
                and event.phase == "question"
            )
            if question_event.question_fingerprint != question_fingerprint:
                raise FoldweaveHostServiceError(
                    "clarification_binding_mismatch",
                    "Clarification answer targets another exact question.",
                )
            response_turn = state.response_turn_count + 1
            event = build_host_event(
                FolderHostClarificationEventV1,
                event_index=len(state.events) + 1,
                response_turn=response_turn,
                phase="answer",
                text=answer,
                question_fingerprint=question_event.question_fingerprint,
            )
            updated_state = _rebuild_state(
                state,
                events=(*state.events, event),
                response_turn_count=response_turn,
                clarification_answer=answer,
                status="planning",
            )
            successor = evolve_job_v3(
                current,
                revision=current.revision + 1,
                updated_at=self._now(),
                authority=_replace_host_authority(
                    authority,
                    planning_state=updated_state,
                ),
                lifecycle=FolderJobLifecycleV3.PLANNING,
                host_mutation_bindings=(*current.host_mutation_bindings, binding),
            )
            return writer.save(successor, expected_current=current)

    def submit_plan(
        self,
        *,
        job_id: str,
        call_id: str,
        plan: FolderPlan,
    ) -> FolderRefactorJobV3:
        """Compile one complete host plan and stop at immutable review."""

        with FolderRefactorJobV3Store(self._job_path(job_id)).writer() as writer:
            current = writer.rehydrate()
            authority = _require_host_authority(current)
            state = authority.planning_state
            prior_submissions = tuple(
                event
                for event in state.events
                if isinstance(event, FolderHostPlanSubmissionV1)
                and event.call_id == call_id
            )
            if prior_submissions:
                if any(event.plan != plan for event in prior_submissions):
                    raise FolderJobV3IdempotencyConflict(
                        "Hosted plan call ID is bound to another complete plan."
                    )
                return current
            if current.lifecycle is not FolderJobLifecycleV3.PLANNING:
                raise FoldweaveHostServiceError(
                    "plan_submission_unavailable",
                    f"Plan cannot be submitted from {current.lifecycle.value}.",
                )
            if state.plan_submission_count >= 3:
                raise FoldweaveHostServiceError(
                    "plan_submission_limit_exceeded",
                    "Hosted planning has exhausted its three complete submissions.",
                )
            scan, reference_graph = scan_folder_with_references(current.source_root)
            _require_exact_scan(current, scan)
            submission_index = state.plan_submission_count + 1
            response_turn = _next_host_response_turn(state)
            try:
                compiled = compile_plan(
                    current.source_inventory,
                    current.user_request,
                    plan,
                    known_evidence_ids={
                        "initial_inventory",
                        *(
                            record.fingerprint
                            for record in state.evidence_state.records
                        ),
                    },
                    evidence_fingerprint=state.evidence_state.evidence_fingerprint,
                    reference_graph=reference_graph,
                )
                accepted = convert_planner_accepted_plan(
                    inventory=current.source_inventory,
                    request=current.user_request,
                    plan=compiled,
                    evidence_schema_version="folder-evidence-ledger.v2",
                )
            except (PlanCompilationError, ValueError) as exc:
                code = getattr(exc, "code", "host_plan_invalid")
                detail = getattr(exc, "message", str(exc))
                failure = build_host_compiler_failure(
                    submission_index=submission_index,
                    call_id=call_id,
                    plan_fingerprint=canonical_sha256(plan),
                    code=code,
                    detail=detail,
                )
                event = build_host_event(
                    FolderHostPlanSubmissionV1,
                    event_index=len(state.events) + 1,
                    response_turn=response_turn,
                    submission_index=submission_index,
                    call_id=call_id,
                    plan=plan,
                    outcome="rejected",
                    accepted_plan_fingerprint=None,
                    compiler_failure_fingerprint=failure.failure_fingerprint,
                )
                updated_state = _rebuild_state(
                    state,
                    events=(*state.events, event),
                    compiler_failures=(*state.compiler_failures, failure),
                    response_turn_count=response_turn,
                    plan_submission_count=submission_index,
                    status=("blocked" if submission_index == 3 else state.status),
                )
                successor = evolve_job_v3(
                    current,
                    revision=current.revision + 1,
                    updated_at=self._now(),
                    authority=_replace_host_authority(
                        authority,
                        planning_state=updated_state,
                    ),
                    lifecycle=(
                        FolderJobLifecycleV3.BLOCKED
                        if submission_index == 3
                        else current.lifecycle
                    ),
                    blocker_code=(
                        "host_plan_submission_exhausted"
                        if submission_index == 3
                        else current.blocker_code
                    ),
                    blocker_message=(
                        "Hosted planning exhausted its three complete plan "
                        "submissions; start a fresh job."
                        if submission_index == 3
                        else current.blocker_message
                    ),
                )
                return writer.save(successor, expected_current=current)

            accepted_fingerprint = canonical_sha256(accepted)
            event = build_host_event(
                FolderHostPlanSubmissionV1,
                event_index=len(state.events) + 1,
                response_turn=response_turn,
                submission_index=submission_index,
                call_id=call_id,
                plan=plan,
                outcome="accepted",
                accepted_plan_fingerprint=accepted_fingerprint,
                compiler_failure_fingerprint=None,
            )
            accepted_state = _rebuild_state(
                state,
                events=(*state.events, event),
                response_turn_count=response_turn,
                plan_submission_count=submission_index,
                accepted_plan_fingerprint=accepted_fingerprint,
                status="accepted",
            )
            initial_ledger = build_host_evidence_ledger(
                job_id=current.job_id,
                provider_kind=authority.model_transport,
                source_commitment=current.source_inventory.source_commitment,
                request_fingerprint=request_fingerprint(current.user_request),
                request_scope="rename_and_move_every_file",
                evidence_state=accepted_state.evidence_state,
                observable_records=tuple(
                    item.model_dump(mode="json") for item in accepted_state.events
                ),
                response_turn_count=accepted_state.response_turn_count,
                evidence_call_count=len(accepted_state.evidence_state.records),
                plan_submission_count=accepted_state.plan_submission_count,
                clarification_question=accepted_state.clarification_question,
                clarification_answer=accepted_state.clarification_answer,
                returned_model_ids=(),
                usage=(),
                store_false=None,
                evidence_fingerprint=accepted_state.evidence_state.evidence_fingerprint,
                accepted_plan_fingerprint=accepted_fingerprint,
            )
            ledger = build_initial_composite_evidence(
                initial_ledger=initial_ledger,
                accepted_plan=accepted,
                contract_freeze_fingerprint=HOST_CONTRACT_FREEZE_FINGERPRINT,
                model_transport=authority.model_transport,
            )
            origin = build_execution_origin_v2(ledger)
            preview = build_folder_plan_preview(
                job_id=current.job_id,
                expected_job_revision=current.revision + 1,
                proposal_revision=0,
                proposal_basis="fresh_gpt_plan",
                inventory=current.source_inventory,
                reference_graph=reference_graph,
                accepted_plan=accepted,
            )
            successor = evolve_job_v3(
                current,
                revision=current.revision + 1,
                updated_at=self._now(),
                lifecycle=FolderJobLifecycleV3.REVIEWING,
                authority=GptHostedJobAuthorityV3(
                    model_transport=authority.model_transport,
                    planning_state=accepted_state,
                    evidence_ledger=ledger,
                    execution_origin=origin,
                ),
                candidate_plan=accepted,
                reference_graph=reference_graph,
                preview=preview,
            )
            return writer.save(successor, expected_current=current)

    def begin_revision(
        self,
        *,
        job_id: str,
        expected_revision: int,
        candidate_fingerprint: str,
        preview_fingerprint: str,
        instruction: str,
        idempotency_key: str,
    ) -> FolderRefactorJobV3:
        """Durably reserve a hosted revision before the widget messages the model."""

        with FolderRefactorJobV3Store(self._job_path(job_id)).writer() as writer:
            current = writer.rehydrate()
            authority = _require_host_authority(current)
            revision_instruction = build_revision_instruction(
                base_candidate_fingerprint=candidate_fingerprint,
                base_preview_fingerprint=preview_fingerprint,
                instruction=instruction,
                idempotency_key=idempotency_key,
            )
            if current.lifecycle is FolderJobLifecycleV3.REVISING:
                pending = authority.pending_revision
                if pending is not None and (
                    pending.expected_job_revision == expected_revision
                    and pending.base_candidate_fingerprint == candidate_fingerprint
                    and pending.base_preview_fingerprint == preview_fingerprint
                    and pending.revision_instruction_fingerprint
                    == revision_instruction.instruction_fingerprint
                    and pending.idempotency_key_sha256
                    == revision_instruction.idempotency_key_sha256
                ):
                    return current
                raise FolderJobV3RevisionError(
                    "Revision idempotency key conflicts with the pending request."
                )
            if current.lifecycle not in {
                FolderJobLifecycleV3.REVIEWING,
                FolderJobLifecycleV3.REVISION_FAILED,
            }:
                raise FoldweaveHostServiceError(
                    "revision_unavailable",
                    f"Revision cannot begin from {current.lifecycle.value}.",
                )
            if current.revision_attempt_count >= 2:
                raise FoldweaveHostServiceError(
                    "revision_limit_exceeded",
                    "This planning job has used both revision attempts.",
                )
            if current.preview is None or current.candidate_plan is None:
                raise FoldweaveHostServiceError(
                    "preview_unavailable",
                    "Hosted revision requires a complete current preview.",
                )
            if not (
                expected_revision == current.revision
                and preview_fingerprint == current.preview.preview_fingerprint
                and candidate_fingerprint == canonical_sha256(current.candidate_plan)
            ):
                raise FolderJobV3RevisionError(
                    "Hosted revision targets a stale or unseen preview."
                )
            ledger = _require_host_ledger(authority)
            pending = build_host_pending_revision(
                job_id=current.job_id,
                model_transport=authority.model_transport,
                expected_job_revision=expected_revision,
                proposal_revision=current.proposal_revision,
                response_turn=ledger.response_turn_count + 1,
                base_candidate_fingerprint=candidate_fingerprint,
                base_preview_fingerprint=preview_fingerprint,
                revision_instruction_fingerprint=(
                    revision_instruction.instruction_fingerprint
                ),
                evidence_fingerprint=ledger.evidence_fingerprint,
                prior_transcript_fingerprint=ledger.transcript_fingerprint,
                idempotency_key_sha256=revision_instruction.idempotency_key_sha256,
            )
            successor = evolve_job_v3(
                current,
                revision=current.revision + 1,
                revision_attempt_count=current.revision_attempt_count + 1,
                updated_at=self._now(),
                lifecycle=FolderJobLifecycleV3.REVISING,
                authority=_replace_host_authority(
                    authority,
                    pending_revision=pending,
                ),
                revision_instruction=revision_instruction,
                revision_failure=None,
            )
            return writer.save(successor, expected_current=current)

    def submit_plan_revision(
        self,
        *,
        job_id: str,
        call_id: str,
        revision: FolderHostPlanRevisionV1,
    ) -> FolderRefactorJobV3:
        """Compile a host revision while preserving the prior preview on failure."""

        with FolderRefactorJobV3Store(self._job_path(job_id)).writer() as writer:
            current = writer.rehydrate()
            authority = _require_host_authority(current)
            if authority.evidence_ledger is not None:
                prior_revision_records = tuple(
                    record
                    for segment in authority.evidence_ledger.segments[1:]
                    for record in segment.observable_records
                    if isinstance(record, dict)
                    and record.get("schema_version")
                    == "folder-host-revision-turn-record.v1"
                    and record.get("call_id") == call_id
                )
                if prior_revision_records:
                    expected = revision.model_dump(mode="json")
                    if any(
                        record.get("revision") != expected
                        for record in prior_revision_records
                    ):
                        raise FolderJobV3IdempotencyConflict(
                            "Hosted revision call ID is bound to another sparse "
                            "revision."
                        )
                    return current
            pending = authority.pending_revision
            instruction = current.revision_instruction
            ledger = _require_host_ledger(authority)
            if (
                current.lifecycle is not FolderJobLifecycleV3.REVISING
                or pending is None
                or instruction is None
                or current.preview is None
                or current.candidate_plan is None
            ):
                raise FoldweaveHostServiceError(
                    "host_revision_not_reserved",
                    "Hosted revision must be durably reserved before submission.",
                )
            try:
                accepted = _compile_host_sparse_revision(
                    current,
                    ledger=ledger,
                    revision=revision,
                )
            except (PlanCompilationError, ValueError) as exc:
                code = getattr(exc, "code", "host_revision_invalid")
                detail = getattr(exc, "message", str(exc))
                turn = build_host_revision_turn(
                    model_transport=authority.model_transport,
                    response_turn=pending.response_turn,
                    call_id=call_id,
                    pending_revision_fingerprint=pending.pending_fingerprint,
                    base_candidate_fingerprint=pending.base_candidate_fingerprint,
                    base_preview_fingerprint=pending.base_preview_fingerprint,
                    revision_instruction_fingerprint=(
                        pending.revision_instruction_fingerprint
                    ),
                    evidence_fingerprint=pending.evidence_fingerprint,
                    prior_transcript_fingerprint=(pending.prior_transcript_fingerprint),
                    revision=revision,
                    outcome="rejected",
                    accepted_plan_fingerprint=None,
                    failure_code=code,
                    failure_detail=detail,
                )
                failed_ledger = append_failed_host_revision_evidence(
                    ledger=ledger,
                    turn=turn,
                    base_preview_fingerprint=current.preview.preview_fingerprint,
                    revision_instruction_fingerprint=(
                        instruction.instruction_fingerprint
                    ),
                )
                failure = FolderRevisionFailureV1(
                    code=code,
                    detail=detail,
                    attempted_instruction_fingerprint=(
                        instruction.instruction_fingerprint
                    ),
                )
                retained_preview = build_folder_plan_preview(
                    job_id=current.job_id,
                    expected_job_revision=current.revision + 1,
                    proposal_revision=current.proposal_revision,
                    proposal_basis=current.preview.proposal_basis,
                    inventory=current.source_inventory,
                    reference_graph=_require_reference_graph(current),
                    accepted_plan=current.candidate_plan,
                    imported_change_file_fingerprint=(
                        current.preview.imported_change_file_fingerprint
                    ),
                    match_report_fingerprint=(current.preview.match_report_fingerprint),
                    immediate_parent_candidate_fingerprint=(
                        current.immediate_parent_candidate_fingerprint
                    ),
                )
                successor = evolve_job_v3(
                    current,
                    revision=current.revision + 1,
                    updated_at=self._now(),
                    lifecycle=FolderJobLifecycleV3.REVISION_FAILED,
                    authority=_replace_host_authority(
                        authority,
                        evidence_ledger=failed_ledger,
                        execution_origin=build_execution_origin_v2(failed_ledger),
                        pending_revision=None,
                    ),
                    preview=retained_preview,
                    revision_failure=failure,
                )
                return writer.save(successor, expected_current=current)

            accepted_fingerprint = canonical_sha256(accepted)
            turn = build_host_revision_turn(
                model_transport=authority.model_transport,
                response_turn=pending.response_turn,
                call_id=call_id,
                pending_revision_fingerprint=pending.pending_fingerprint,
                base_candidate_fingerprint=pending.base_candidate_fingerprint,
                base_preview_fingerprint=pending.base_preview_fingerprint,
                revision_instruction_fingerprint=pending.revision_instruction_fingerprint,
                evidence_fingerprint=pending.evidence_fingerprint,
                prior_transcript_fingerprint=pending.prior_transcript_fingerprint,
                revision=revision,
                outcome="accepted",
                accepted_plan_fingerprint=accepted_fingerprint,
                failure_code=None,
                failure_detail=None,
            )
            revised_ledger = append_successful_host_revision_evidence(
                ledger=ledger,
                turn=turn,
                accepted_plan=accepted,
                base_preview_fingerprint=current.preview.preview_fingerprint,
                revision_instruction_fingerprint=instruction.instruction_fingerprint,
            )
            next_proposal_revision = current.proposal_revision + 1
            preview = build_folder_plan_preview(
                job_id=current.job_id,
                expected_job_revision=current.revision + 1,
                proposal_revision=next_proposal_revision,
                proposal_basis=(
                    "gpt_derivative"
                    if current.immediate_parent_candidate_fingerprint is not None
                    else "fresh_gpt_plan"
                ),
                inventory=current.source_inventory,
                reference_graph=_require_reference_graph(current),
                accepted_plan=accepted,
                imported_change_file_fingerprint=(
                    current.preview.imported_change_file_fingerprint
                ),
                match_report_fingerprint=current.preview.match_report_fingerprint,
                immediate_parent_candidate_fingerprint=(
                    current.immediate_parent_candidate_fingerprint
                ),
            )
            successor = evolve_job_v3(
                current,
                revision=current.revision + 1,
                proposal_revision=next_proposal_revision,
                updated_at=self._now(),
                lifecycle=FolderJobLifecycleV3.REVIEWING,
                authority=_replace_host_authority(
                    authority,
                    evidence_ledger=revised_ledger,
                    execution_origin=build_execution_origin_v2(revised_ledger),
                    pending_revision=None,
                ),
                candidate_plan=accepted,
                preview=preview,
                revision_failure=None,
            )
            return writer.save(successor, expected_current=current)

    def get_plan_preview(self, job_id: str) -> FolderPlanPreviewV1:
        job = self.status(job_id)
        if (
            job.lifecycle
            not in {
                FolderJobLifecycleV3.REVIEWING,
                FolderJobLifecycleV3.REVISION_FAILED,
                FolderJobLifecycleV3.EXECUTING,
                FolderJobLifecycleV3.VERIFIED,
            }
            or job.preview is None
        ):
            raise FoldweaveHostServiceError(
                "preview_unavailable",
                f"Job is not reviewable: {job.lifecycle.value}.",
            )
        return job.preview

    def get_compiler_failures(self, job_id: str):
        authority = _require_host_authority(self.status(job_id))
        return authority.planning_state.compiler_failures

    def status(self, job_id: str) -> FolderRefactorJobV3:
        return FolderRefactorJobV3Store(self._job_path(job_id)).inspect()

    def keep_previous_proposal(
        self,
        *,
        job_id: str,
        expected_revision: int,
        preview_fingerprint: str,
        candidate_fingerprint: str,
        idempotency_key: str,
    ) -> FolderRefactorJobV3:
        return self._review.keep_previous_proposal(
            self._job_path(job_id),
            expected_revision=expected_revision,
            preview_fingerprint=preview_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            idempotency_key=idempotency_key,
        )

    def accept_plan_and_create_copy(
        self,
        *,
        job_id: str,
        expected_revision: int,
        preview_fingerprint: str,
        candidate_fingerprint: str,
        result_folder_name: str,
        idempotency_key: str,
        channel: Literal["chatgpt_hosted", "codex_mcp", "local_mcp"],
    ) -> FolderRefactorJobV3:
        job = self.status(job_id)
        return self._review.accept(
            job.job_path,
            expected_revision=expected_revision,
            preview_fingerprint=preview_fingerprint,
            candidate_fingerprint=candidate_fingerprint,
            output_parent=job.output_parent,
            result_folder_name=result_folder_name,
            idempotency_key=idempotency_key,
            channel=channel,
        )

    def verify_result(self, job_id: str) -> ConnectedReceiptVerification:
        return self._review.verify_result(self._job_path(job_id))

    def _execute_evidence(
        self,
        job_id: str,
        call: ListInventoryPageCall | ReadTextExcerptCall | InspectMarkdownLinksCall,
    ) -> tuple[FolderRefactorJobV3, JsonValue | None, str | None]:
        with FolderRefactorJobV3Store(self._job_path(job_id)).writer() as writer:
            current = writer.rehydrate()
            authority = _require_host_authority(current)
            state = authority.planning_state
            call_payload = call.model_dump(mode="json")
            call_payload.pop("call_id")
            call_payload.pop("tool_name")
            prior_records = tuple(
                record
                for record in state.evidence_state.records
                if record.call_id == call.call_id
            )
            if prior_records:
                if any(
                    record.tool_name != call.tool_name
                    or record.arguments != call_payload
                    for record in prior_records
                ):
                    raise FolderJobV3IdempotencyConflict(
                        "Hosted evidence call ID is bound to another exact request."
                    )
                record = prior_records[-1]
                return current, record.result, record.error_code
            if current.lifecycle is not FolderJobLifecycleV3.PLANNING:
                raise FoldweaveHostServiceError(
                    "evidence_unavailable",
                    "Evidence tools are available only while hosted planning is "
                    "active.",
                )
            scan, reference_graph = scan_folder_with_references(current.source_root)
            _require_exact_scan(current, scan)
            response_turn = _current_evidence_turn(state)
            execution = LocalFolderEvidenceService(
                scan,
                reference_graph=reference_graph,
            ).execute(call)
            evidence_state = append_evidence_execution(
                state.evidence_state,
                response_turn=response_turn,
                call=call,
                execution=execution,
            )
            record = evidence_state.records[-1]
            event = build_host_event(
                FolderHostEvidenceObservationV1,
                event_index=len(state.events) + 1,
                response_turn=response_turn,
                evidence_call_number=record.evidence_call_number,
                evidence_record_fingerprint=record.fingerprint,
            )
            updated_state = _rebuild_state(
                state,
                evidence_state=evidence_state,
                events=(*state.events, event),
                response_turn_count=max(state.response_turn_count, response_turn),
            )
            successor = evolve_job_v3(
                current,
                revision=current.revision + 1,
                updated_at=self._now(),
                authority=_replace_host_authority(
                    authority,
                    planning_state=updated_state,
                ),
            )
            saved = writer.save(successor, expected_current=current)
            return saved, record.result, record.error_code

    def _find_idempotent_job(self, key_sha256: str) -> FolderRefactorJobV3 | None:
        try:
            discovered = FoldweaveJobLocator(self._paths.jobs).discover()
        except FoldweaveJobLocatorError as exc:
            raise FoldweaveHostServiceError(
                "host_job_registry_invalid",
                "A durable Foldweave job cannot be inspected safely.",
            ) from exc
        matches: list[FolderRefactorJobV3] = []
        for located in discovered:
            job = located.job
            if job.idempotency.key_sha256 == key_sha256:
                matches.append(job)
        if len(matches) > 1:
            raise FolderJobV3IdempotencyConflict(
                "Hosted idempotency key appears in multiple durable jobs."
            )
        return matches[0] if matches else None

    def _job_path(self, job_id: str) -> Path:
        try:
            parsed = uuid.UUID(hex=job_id)
        except ValueError as exc:
            raise FoldweaveHostServiceError(
                "job_handle_invalid",
                "Foldweave job handle is invalid.",
            ) from exc
        if parsed.version != 4 or parsed.hex != job_id:
            raise FoldweaveHostServiceError(
                "job_handle_invalid",
                "Foldweave job handle is invalid.",
            )
        expected = self._paths.jobs / f"{job_id}.json"
        if os.path.lexists(expected):
            try:
                located = FoldweaveJobLocator(self._paths.jobs).resolve(job_id)
            except FoldweaveJobLocatorError as exc:
                raise FoldweaveHostServiceError(
                    "host_job_registry_invalid",
                    "The durable Foldweave job registry is invalid.",
                ) from exc
            return located.path
        try:
            return FoldweaveJobLocator(self._paths.jobs).resolve(job_id).path
        except FoldweaveJobLocatorError as exc:
            if exc.code == "job_not_found":
                return expected
            raise FoldweaveHostServiceError(
                "host_job_registry_invalid",
                "The durable Foldweave job registry is invalid.",
            ) from exc

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Hosted planning clock must be timezone aware.")
        return value.astimezone(oslo_tz)


def _require_host_authority(job: FolderRefactorJobV3) -> GptHostedJobAuthorityV3:
    if not isinstance(job.authority, GptHostedJobAuthorityV3):
        raise FoldweaveHostServiceError(
            "host_authority_mismatch",
            "Job does not use hosted planning authority.",
        )
    return job.authority


def _require_matching_host_job(
    job: FolderRefactorJobV3,
    *,
    binding: FolderIdempotencyBindingV2,
    model_transport: HostModelTransport,
) -> FolderRefactorJobV3:
    if job.idempotency != binding:
        raise FolderJobV3IdempotencyConflict(
            "Hosted idempotency key is bound to another exact request."
        )
    if not isinstance(job.authority, GptHostedJobAuthorityV3) or (
        job.authority.model_transport != model_transport
    ):
        raise FolderJobV3IdempotencyConflict(
            "Hosted idempotency key is bound to another planning transport."
        )
    return job


@contextmanager
def _host_job_creation_lock(jobs_directory: Path) -> Iterator[None]:
    """Serialize the one create-if-absent decision across local processes."""

    lock_path = jobs_directory / ".foldweave-host-create.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _require_host_ledger(
    authority: GptHostedJobAuthorityV3,
) -> FolderEvidenceLedgerV2:
    if authority.evidence_ledger is None:
        raise FoldweaveHostServiceError(
            "host_evidence_unavailable",
            "Hosted planning has not produced an accepted evidence ledger.",
        )
    return authority.evidence_ledger


def _replace_host_authority(
    authority: GptHostedJobAuthorityV3,
    **updates,
) -> GptHostedJobAuthorityV3:
    return GptHostedJobAuthorityV3.model_validate(
        {**authority.model_dump(mode="python"), **updates},
        strict=True,
    )


def _require_host_mutation_retry_or_none(
    job: FolderRefactorJobV3,
    binding: FolderHostMutationBindingV1,
) -> FolderRefactorJobV3 | None:
    """Return an exact persisted retry or reject conflicting key reuse."""

    matching_key = tuple(
        existing
        for existing in job.host_mutation_bindings
        if existing.idempotency_key_sha256 == binding.idempotency_key_sha256
    )
    if not matching_key:
        return None
    if matching_key != (binding,):
        raise FoldweaveHostServiceError(
            "clarification_idempotency_conflict",
            "Hosted mutation idempotency key is bound to another exact request.",
        )
    return job


def _rebuild_state(
    state: FolderHostPlanningStateV1,
    **updates,
) -> FolderHostPlanningStateV1:
    values = {
        **{
            field_name: getattr(state, field_name)
            for field_name in FolderHostPlanningStateV1.model_fields
            if field_name != "state_fingerprint"
        },
        **updates,
    }
    return build_host_planning_state(**values)


def _current_evidence_turn(state: FolderHostPlanningStateV1) -> int:
    if not state.events:
        return 1
    last = state.events[-1]
    if isinstance(last, FolderHostPlanSubmissionV1):
        return state.response_turn_count + 1
    return max(1, state.response_turn_count)


def _next_host_response_turn(state: FolderHostPlanningStateV1) -> int:
    if not state.events:
        return 1
    if isinstance(state.events[-1], FolderHostPlanSubmissionV1):
        return state.response_turn_count + 1
    return max(1, state.response_turn_count)


def _require_exact_scan(job: FolderRefactorJobV3, scan) -> None:
    if not (
        scan.inventory == job.source_inventory
        and tuple(
            JobLocalFileIdentityV2.from_scan(item)
            for item in scan.local_file_identities
        )
        == tuple(job.local_file_identities)
        and tuple(
            JobLocalDirectoryIdentityV2.from_scan(item)
            for item in scan.local_directory_identities
        )
        == tuple(job.local_directory_identities)
    ):
        raise FoldweaveHostServiceError(
            "source_changed",
            "Selected source changed while hosted planning was active.",
        )


def _require_reference_graph(job: FolderRefactorJobV3):
    if job.reference_graph is None:
        raise FoldweaveHostServiceError(
            "reference_graph_missing",
            "Hosted revision lacks the immutable source reference graph.",
        )
    return job.reference_graph


def _compile_host_sparse_revision(
    job: FolderRefactorJobV3,
    *,
    ledger: FolderEvidenceLedgerV2,
    revision: FolderHostPlanRevisionV1,
) -> FolderAcceptedPlanV2:
    candidate = job.candidate_plan
    if candidate is None:
        raise FoldweaveHostServiceError(
            "candidate_missing",
            "Hosted revision lacks the complete base candidate.",
        )
    if revision.base_candidate_fingerprint != canonical_sha256(candidate):
        raise PlanCompilationError(
            "revision_base_mismatch",
            "Sparse hosted revision targets another base candidate.",
        )
    by_file_id = {item.file_id: item for item in revision.entries}
    mappings = {item.file_id: item for item in candidate.file_mappings}
    unknown = set(by_file_id) - set(mappings)
    protected = {
        file_id for file_id, mapping in mappings.items() if mapping.protected
    } & set(by_file_id)
    if unknown:
        raise PlanCompilationError(
            "revision_unknown_file_id",
            f"Sparse hosted revision names unknown file IDs: {sorted(unknown)!r}.",
        )
    if protected:
        raise PlanCompilationError(
            "revision_protected_file",
            f"Sparse hosted revision names protected file IDs: {sorted(protected)!r}.",
        )
    result_folder_name = (
        revision.replacement_result_folder_name or candidate.result_folder_name
    )
    changed_target = any(
        mappings[file_id].target_path != entry.replacement_target_path
        for file_id, entry in by_file_id.items()
    )
    if not changed_target and result_folder_name == candidate.result_folder_name:
        raise PlanCompilationError(
            "revision_no_change",
            "Sparse hosted revision does not change the reviewed structure.",
        )
    entries = []
    for mapping in candidate.file_mappings:
        if mapping.protected:
            continue
        replacement = by_file_id.get(mapping.file_id)
        entries.append(
            FolderPlanEntry(
                file_id=mapping.file_id,
                original_path=mapping.original_path,
                proposed_target=(
                    replacement.replacement_target_path
                    if replacement is not None
                    else mapping.target_path
                ),
                rationale=(
                    replacement.rationale
                    if replacement is not None
                    else "Retained from the accepted base proposal."
                ),
                evidence_ids=(
                    replacement.evidence_ids
                    if replacement is not None
                    else ("initial_inventory",)
                ),
            )
        )
    complete = FolderPlan(
        source_commitment=candidate.source_commitment,
        request_fingerprint=candidate.request_fingerprint,
        request_scope=candidate.request_scope,
        evidence_fingerprint=ledger.evidence_fingerprint,
        result_folder_name=result_folder_name,
        entries=tuple(entries),
        exclusions=(),
    )
    initial = ledger.initial_ledger
    known_evidence = {
        "initial_inventory",
        *(record.fingerprint for record in initial.evidence_records),
    }
    compiled = compile_plan(
        job.source_inventory,
        job.user_request,
        complete,
        known_evidence_ids=known_evidence,
        evidence_fingerprint=ledger.evidence_fingerprint,
        reference_graph=_require_reference_graph(job),
    )
    return convert_planner_accepted_plan(
        inventory=job.source_inventory,
        request=job.user_request,
        plan=compiled,
        evidence_schema_version="folder-evidence-ledger.v2",
    )
