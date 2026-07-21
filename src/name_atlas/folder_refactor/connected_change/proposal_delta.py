"""Read-only projection of the latest accepted Foldweave proposal revision."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from name_atlas.folder_refactor.compiler import compile_plan
from name_atlas.folder_refactor.connected_change.accepted_plan import (
    FolderAcceptedPlanV2,
    convert_planner_accepted_plan,
)
from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderRefactorJobV3,
    GptDerivativeJobAuthorityV3,
    GptHostedJobAuthorityV3,
    GptPlannedJobAuthorityV3,
)
from name_atlas.folder_refactor.connected_change.preview import (
    FolderPlanRevisionDeltaEntryV1,
    FolderPlanRevisionDeltaV1,
)
from name_atlas.folder_refactor.connected_change.sparse_revision import (
    compile_sparse_revision_from_base,
)
from name_atlas.folder_refactor.foldweave_host_contracts import (
    FolderHostDerivativeRevisionTurnRecordV1,
    FolderHostPlanRevisionV1,
    FolderHostPlanSubmissionV1,
    FolderHostRevisionTurnRecordV1,
)
from name_atlas.folder_refactor.foldweave_planning_contracts import (
    FolderDerivativeRevisionTurnRecordV1,
    FolderEvidenceLedgerV2,
    FolderPlanningSegmentV1,
    FolderPlanRevisionEntryV1,
    FolderPlanRevisionV1,
    FolderRevisionTurnRecordV1,
)
from name_atlas.folder_refactor.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)


class FolderProposalDeltaProjectionError(ValueError):
    """Persisted revision evidence cannot reproduce the visible candidate."""


@dataclass(frozen=True, slots=True)
class _RevisionRecord:
    revision: FolderPlanRevisionV1
    outcome: str | None
    embedded_base_candidate: FolderAcceptedPlanV2 | None


def project_latest_accepted_proposal_delta(
    job: FolderRefactorJobV3,
) -> FolderPlanRevisionDeltaV1 | None:
    """Reproduce and compare the latest accepted revision from durable evidence."""

    if job.proposal_revision == 0:
        return None
    if job.candidate_plan is None or job.preview is None or job.reference_graph is None:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_current_review_missing: revised job lacks its candidate, "
            "preview, or reference graph"
        )
    ledger = _require_evidence_ledger(job)
    if not (
        ledger.selected_proposal_revision == job.proposal_revision
        and ledger.accepted_plan_fingerprint == canonical_sha256(job.candidate_plan)
        and job.preview.compiled_candidate_fingerprint
        == canonical_sha256(job.candidate_plan)
    ):
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_current_binding_mismatch: job, ledger, candidate, and "
            "preview do not identify one selected proposal"
        )

    current = _initial_candidate(job, ledger)
    current_fingerprint = canonical_sha256(current)
    latest_pair: (
        tuple[
            FolderAcceptedPlanV2,
            FolderAcceptedPlanV2,
            FolderPlanningSegmentV1,
        ]
        | None
    ) = None
    selected_count = 0

    for segment in ledger.segments:
        if segment.segment_kind == "initial_plan":
            if not (
                selected_count == 0
                and segment.final_candidate_fingerprint == current_fingerprint
            ):
                raise FolderProposalDeltaProjectionError(
                    "proposal_delta_initial_binding_mismatch: initial segment differs "
                    "from the reconstructed root candidate"
                )
            continue
        if segment.base_candidate_fingerprint != current_fingerprint:
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_base_candidate_mismatch: revision segment targets "
                "another selected candidate"
            )
        record = _parse_revision_record(segment)
        if record.embedded_base_candidate is not None and (
            record.embedded_base_candidate != current
        ):
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_embedded_base_mismatch: observable turn contains "
                "another base candidate"
            )
        if record.outcome is not None and record.outcome != segment.outcome:
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_outcome_mismatch: segment and observable turn disagree"
            )
        if not segment.selected:
            if segment.final_candidate_fingerprint != current_fingerprint:
                raise FolderProposalDeltaProjectionError(
                    "proposal_delta_rejected_candidate_changed: rejected revision "
                    "changed the selected candidate"
                )
            continue

        previous = current
        current = compile_sparse_revision_from_base(
            inventory=job.source_inventory,
            request=job.user_request,
            reference_graph=job.reference_graph,
            base_candidate=previous,
            revision=record.revision,
            evidence_fingerprint=ledger.evidence_fingerprint,
            known_evidence_ids=_known_evidence_ids(ledger),
        )
        current_fingerprint = canonical_sha256(current)
        if current_fingerprint != segment.final_candidate_fingerprint:
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_final_candidate_mismatch: deterministic replay "
                "differs from the persisted segment"
            )
        selected_count += 1
        if not (
            segment.proposal_revision_before == selected_count - 1
            and segment.proposal_revision_after == selected_count
        ):
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_revision_counter_mismatch: selected segment has an "
                "invalid proposal revision"
            )
        latest_pair = (previous, current, segment)

    if not (
        selected_count == job.proposal_revision
        and current == job.candidate_plan
        and current_fingerprint == ledger.accepted_plan_fingerprint
        and latest_pair is not None
    ):
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_final_job_mismatch: reconstructed evidence does not "
            "produce the current job candidate"
        )
    previous, current, segment = latest_pair
    return _build_delta(job=job, previous=previous, current=current, segment=segment)


def _require_evidence_ledger(job: FolderRefactorJobV3) -> FolderEvidenceLedgerV2:
    authority = job.authority
    if isinstance(
        authority,
        (
            GptPlannedJobAuthorityV3,
            GptHostedJobAuthorityV3,
            GptDerivativeJobAuthorityV3,
        ),
    ):
        ledger = authority.evidence_ledger
    else:
        ledger = None
    if ledger is None:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_evidence_missing: revised job lacks composite evidence"
        )
    return ledger


def _initial_candidate(
    job: FolderRefactorJobV3,
    ledger: FolderEvidenceLedgerV2,
) -> FolderAcceptedPlanV2:
    authority = job.authority
    if isinstance(authority, GptDerivativeJobAuthorityV3):
        candidate = authority.parent_binding.parent_candidate
    elif isinstance(authority, GptPlannedJobAuthorityV3):
        progress = authority.planner_checkpoint.progress
        if (
            progress is None
            or progress.status != "accepted"
            or progress.accepted_plan is None
        ):
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_initial_plan_missing: direct root authority lacks "
                "its accepted planner plan"
            )
        candidate = convert_planner_accepted_plan(
            inventory=job.source_inventory,
            request=job.user_request,
            plan=progress.accepted_plan,
            evidence_schema_version="folder-evidence-ledger.v2",
        )
    elif isinstance(authority, GptHostedJobAuthorityV3):
        accepted_submissions = tuple(
            event
            for event in authority.planning_state.events
            if isinstance(event, FolderHostPlanSubmissionV1)
            and event.outcome == "accepted"
        )
        if len(accepted_submissions) != 1:
            raise FolderProposalDeltaProjectionError(
                "proposal_delta_initial_plan_missing: hosted root authority must "
                "retain exactly one accepted full plan"
            )
        compiled = compile_plan(
            job.source_inventory,
            job.user_request,
            accepted_submissions[0].plan,
            known_evidence_ids=_known_evidence_ids(ledger),
            evidence_fingerprint=ledger.evidence_fingerprint,
            reference_graph=job.reference_graph,
        )
        candidate = convert_planner_accepted_plan(
            inventory=job.source_inventory,
            request=job.user_request,
            plan=compiled,
            evidence_schema_version="folder-evidence-ledger.v2",
        )
    else:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_authority_unsupported: revised job has no reproducible "
            "planning authority"
        )
    return candidate


def _parse_revision_record(segment: FolderPlanningSegmentV1) -> _RevisionRecord:
    if len(segment.observable_records) != 1:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_record_count_invalid: revision segment must contain one "
            "observable turn"
        )
    raw = segment.observable_records[0]
    if not isinstance(raw, dict):
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_record_invalid: revision record must be an object"
        )
    schema_version = raw.get("schema_version")
    try:
        if schema_version == "folder-revision-turn-record.v1":
            turn = FolderRevisionTurnRecordV1.model_validate_json(
                canonical_json_bytes(raw),
                strict=True,
            )
            return _RevisionRecord(
                revision=turn.response.revision,
                outcome=None,
                embedded_base_candidate=turn.input.base_candidate,
            )
        if schema_version == "folder-derivative-revision-turn-record.v1":
            derivative_turn = FolderDerivativeRevisionTurnRecordV1.model_validate_json(
                canonical_json_bytes(raw),
                strict=True,
            )
            return _RevisionRecord(
                revision=derivative_turn.response.revision,
                outcome=None,
                embedded_base_candidate=derivative_turn.input.base_candidate,
            )
        if schema_version == "folder-host-revision-turn-record.v1":
            host_turn = FolderHostRevisionTurnRecordV1.model_validate_json(
                canonical_json_bytes(raw),
                strict=True,
            )
            return _RevisionRecord(
                revision=_shared_revision(host_turn.revision),
                outcome=host_turn.outcome,
                embedded_base_candidate=None,
            )
        if schema_version == "folder-host-derivative-revision-turn-record.v1":
            host_derivative_turn = (
                FolderHostDerivativeRevisionTurnRecordV1.model_validate_json(
                    canonical_json_bytes(raw),
                    strict=True,
                )
            )
            return _RevisionRecord(
                revision=_shared_revision(host_derivative_turn.revision),
                outcome=host_derivative_turn.outcome,
                embedded_base_candidate=None,
            )
    except (TypeError, ValueError) as exc:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_record_invalid: revision record failed strict validation"
        ) from exc
    raise FolderProposalDeltaProjectionError(
        "proposal_delta_record_unsupported: revision record schema is unsupported"
    )


def _shared_revision(revision: FolderHostPlanRevisionV1) -> FolderPlanRevisionV1:
    return FolderPlanRevisionV1(
        base_candidate_fingerprint=revision.base_candidate_fingerprint,
        replacement_result_folder_name=revision.replacement_result_folder_name,
        entries=tuple(
            FolderPlanRevisionEntryV1(
                file_id=entry.file_id,
                replacement_target_path=entry.replacement_target_path,
                rationale=entry.rationale,
                evidence_ids=entry.evidence_ids,
            )
            for entry in revision.entries
        ),
    )


def _known_evidence_ids(ledger: FolderEvidenceLedgerV2) -> set[str]:
    evidence_records = getattr(ledger.initial_ledger, "evidence_records", ())
    return {
        "initial_inventory",
        *(record.fingerprint for record in evidence_records),
    }


def _build_delta(
    *,
    job: FolderRefactorJobV3,
    previous: FolderAcceptedPlanV2,
    current: FolderAcceptedPlanV2,
    segment: FolderPlanningSegmentV1,
) -> FolderPlanRevisionDeltaV1:
    previous_by_id = {mapping.file_id: mapping for mapping in previous.file_mappings}
    current_by_id = {mapping.file_id: mapping for mapping in current.file_mappings}
    if set(previous_by_id) != set(current_by_id):
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_member_bijection_mismatch: revision changed complete "
            "file accounting"
        )
    entries = tuple(
        FolderPlanRevisionDeltaEntryV1(
            member_id=member_id,
            previous_path=previous_by_id[member_id].target_path,
            current_path=current_by_id[member_id].target_path,
        )
        for member_id in sorted(previous_by_id)
        if previous_by_id[member_id].target_path != current_by_id[member_id].target_path
    )
    if job.preview is None:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_current_preview_missing: job has no current preview"
        )
    values: dict[str, Any] = {
        "job_id": job.job_id,
        "proposal_revision_before": segment.proposal_revision_before,
        "proposal_revision_after": segment.proposal_revision_after,
        "base_candidate_fingerprint": canonical_sha256(previous),
        "base_preview_fingerprint": segment.base_preview_fingerprint,
        "current_candidate_fingerprint": canonical_sha256(current),
        "current_preview_fingerprint": job.preview.preview_fingerprint,
        "previous_result_folder_name": previous.result_folder_name,
        "current_result_folder_name": current.result_folder_name,
        "entries": entries,
    }
    if values["base_candidate_fingerprint"] != segment.base_candidate_fingerprint:
        raise FolderProposalDeltaProjectionError(
            "proposal_delta_base_candidate_mismatch: latest segment and reproduced "
            "candidate disagree"
        )
    draft = FolderPlanRevisionDeltaV1.model_construct(
        **values,
        delta_fingerprint="0" * 64,
    )
    return FolderPlanRevisionDeltaV1(
        **values,
        delta_fingerprint=canonical_sha256(
            draft.model_dump(mode="json", exclude={"delta_fingerprint"})
        ),
    )
