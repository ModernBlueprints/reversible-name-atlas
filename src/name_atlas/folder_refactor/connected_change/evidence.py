"""Truthful portable planner evidence for Connected Change origin results."""

from __future__ import annotations

from name_atlas.folder_refactor.connected_change.accepted_plan import (
    FolderAcceptedPlanV2,
)
from name_atlas.folder_refactor.connected_change.contracts import (
    GptPlannedExecutionOrigin,
)
from name_atlas.folder_refactor.contracts import (
    FolderInventory,
    FolderPlan,
    FolderPlanEntry,
)
from name_atlas.folder_refactor.planner_contracts import (
    FolderPlannerProgress,
    FolderPlannerTurnInput,
    PlannerObservableTurn,
    SubmitPlanCall,
    observable_turn_payload,
)
from name_atlas.folder_refactor.planner_evidence import (
    create_initial_evidence_ledger,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderEvidenceLedger,
    FolderPlannerUsage,
)
from name_atlas.folder_refactor.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)


def build_deterministic_origin_evidence(
    *,
    job_id: str,
    inventory: FolderInventory,
    request: str,
    accepted_plan: FolderAcceptedPlanV2,
) -> tuple[GptPlannedExecutionOrigin, FolderEvidenceLedger]:
    """Build one exact local-development plan transcript and public ledger."""

    evidence_state = create_initial_evidence_ledger(inventory, request)
    if evidence_state.evidence_fingerprint != accepted_plan.evidence_fingerprint:
        raise ValueError(
            "Accepted plan evidence fingerprint differs from initial evidence."
        )
    submitted_plan = FolderPlan(
        source_commitment=inventory.source_commitment,
        request_fingerprint=accepted_plan.request_fingerprint,
        request_scope=accepted_plan.request_scope,
        evidence_fingerprint=accepted_plan.evidence_fingerprint,
        result_folder_name=accepted_plan.result_folder_name,
        entries=tuple(
            FolderPlanEntry(
                file_id=mapping.file_id,
                original_path=mapping.original_path,
                proposed_target=mapping.target_path,
                rationale="Deterministic development plan for the accepted change.",
                evidence_ids=("initial_inventory",),
            )
            for mapping in accepted_plan.file_mappings
            if not mapping.protected
        ),
        exclusions=(),
    )
    submit_call = SubmitPlanCall(
        call_id="deterministic-development-submit",
        plan=submitted_plan,
    )
    turn_input = FolderPlannerTurnInput(
        job_id=job_id,
        response_turn=1,
        provider_kind="deterministic",
        request=request,
        request_fingerprint=accepted_plan.request_fingerprint,
        source_commitment=inventory.source_commitment,
        evidence_ledger=evidence_state,
        prior_turns=(),
        compiler_failures=(),
    )
    input_payload = turn_input.model_dump(mode="json")
    turn_values = {
        "response_turn": 1,
        "provider_kind": "deterministic",
        "returned_model": None,
        "observable_output_items": (),
        "tool_calls": (submit_call,),
        "blocker_code": None,
        "input_bytes": len(canonical_json_bytes(input_payload)),
        "input_fingerprint": canonical_sha256(input_payload),
        "input_payload": input_payload,
    }
    draft_turn = PlannerObservableTurn.model_construct(
        **turn_values,
        response_fingerprint="0" * 64,
    )
    turn = PlannerObservableTurn(
        **turn_values,
        response_fingerprint=canonical_sha256(observable_turn_payload(draft_turn)),
    )
    ledger_values = {
        "job_id": job_id,
        "source_commitment": inventory.source_commitment,
        "request_fingerprint": accepted_plan.request_fingerprint,
        "request_scope": accepted_plan.request_scope,
        "provider_kind": "deterministic",
        "returned_model_ids": (),
        "store_false": None,
        "initial_evidence": evidence_state.initial_evidence,
        "initial_evidence_bytes": evidence_state.initial_evidence_bytes,
        "evidence_records": (),
        "aggregate_result_bytes": 0,
        "total_outbound_evidence_bytes": evidence_state.initial_evidence_bytes,
        "evidence_fingerprint": evidence_state.evidence_fingerprint,
        "observable_turns": (turn,),
        "compiler_failures": (),
        "response_turn_count": 1,
        "evidence_call_count": 0,
        "plan_submission_count": 1,
        "clarification_question": None,
        "clarification_answer": None,
        "accepted_plan_fingerprint": canonical_sha256(accepted_plan),
        "usage": (),
    }
    draft_ledger = FolderEvidenceLedger.model_construct(
        **ledger_values,
        transcript_fingerprint="0" * 64,
    )
    ledger = FolderEvidenceLedger(
        **ledger_values,
        transcript_fingerprint=canonical_sha256(
            draft_ledger.model_dump(
                mode="json",
                exclude={"transcript_fingerprint"},
            )
        ),
    )
    origin = GptPlannedExecutionOrigin(
        planner_kind="deterministic_development",
        observable_transcript=tuple(
            item.model_dump(mode="json") for item in ledger.observable_turns
        ),
        evidence_fingerprint=ledger.evidence_fingerprint,
        accepted_plan_fingerprint=ledger.accepted_plan_fingerprint,
        provider_call_count=0,
        api_used=False,
        external_network_used=False,
    )
    return origin, ledger


def build_planner_origin_evidence(
    *,
    progress: FolderPlannerProgress,
    accepted_plan: FolderAcceptedPlanV2,
    usage: tuple[FolderPlannerUsage, ...] = (),
) -> tuple[GptPlannedExecutionOrigin, FolderEvidenceLedger]:
    """Project accepted restart state into v2-bound portable origin evidence."""

    if progress.status != "accepted" or progress.accepted_plan is None:
        raise ValueError(
            "Connected origin evidence requires accepted planner progress."
        )
    if (
        progress.accepted_plan.source_commitment != accepted_plan.source_commitment
        or progress.accepted_plan.request_fingerprint
        != accepted_plan.request_fingerprint
        or progress.accepted_plan.evidence_fingerprint
        != accepted_plan.evidence_fingerprint
    ):
        raise ValueError("Planner progress and connected accepted plan do not agree.")

    base = FolderEvidenceLedger.from_progress(
        job_id=progress.job_id,
        progress=progress,
        usage=usage,
        store_false=True if progress.provider_kind == "live" else None,
    )
    accepted_plan_fingerprint = canonical_sha256(accepted_plan)
    values = base.model_dump(mode="python", exclude={"transcript_fingerprint"})
    values["accepted_plan_fingerprint"] = accepted_plan_fingerprint
    fingerprint_values = base.model_dump(
        mode="json",
        exclude={"transcript_fingerprint"},
    )
    fingerprint_values["accepted_plan_fingerprint"] = accepted_plan_fingerprint
    ledger = FolderEvidenceLedger(
        **values,
        transcript_fingerprint=canonical_sha256(fingerprint_values),
    )
    planner_kind = {
        "deterministic": "deterministic_development",
        "live": "live",
        "recorded_replay": "recorded_replay",
    }[progress.provider_kind]
    live = progress.provider_kind == "live"
    returned_model_id = (
        ledger.returned_model_ids[-1] if ledger.returned_model_ids else None
    )
    origin = GptPlannedExecutionOrigin(
        planner_kind=planner_kind,
        returned_model_id=returned_model_id,
        observable_transcript=tuple(
            turn.model_dump(mode="json") for turn in ledger.observable_turns
        ),
        clarification_question=ledger.clarification_question,
        clarification_answer=ledger.clarification_answer,
        evidence_fingerprint=ledger.evidence_fingerprint,
        accepted_plan_fingerprint=accepted_plan_fingerprint,
        provider_call_count=ledger.response_turn_count if live else 0,
        api_used=live,
        store_false=True if live else None,
        external_network_used=live,
    )
    return origin, ledger
