"""Strict acyclic v2 receipts for Connected Change origin and receiver results."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from name_atlas.domain import PackageValidationResult
from name_atlas.folder_refactor.connected_change.accepted_plan import (
    FolderAcceptedPlanV2,
)
from name_atlas.folder_refactor.connected_change.contracts import (
    CapsuleAppliedExecutionOrigin,
    FolderExecutionOrigin,
    GptPlannedExecutionOrigin,
)
from name_atlas.folder_refactor.connected_change.organized_tree import (
    OrganizedTreeSnapshot,
)
from name_atlas.folder_refactor.connected_change.receipt_contracts import (
    FolderReceiptCoreV2,
    FolderReceiptEnvelopeV2,
)
from name_atlas.folder_refactor.contracts import (
    FolderInventory,
    FolderVerificationReport,
)
from name_atlas.folder_refactor.markdown_contracts import FolderReferenceGraph
from name_atlas.folder_refactor.portable_artifacts import regular_file_measurement
from name_atlas.folder_refactor.receipt_contracts import (
    FolderArtifactCommitment,
    FolderChangeLedger,
    FolderPathMapRow,
    FolderStagedDataMember,
    FolderUserRequestArtifact,
)
from name_atlas.folder_refactor.serialization import canonical_sha256

EXECUTION_ORIGIN_PATH = "name-atlas/execution_origin.json"
CONNECTED_CHANGE_PATH = "name-atlas/connected_change_capsule.json"
CONNECTED_CHANGE_MATCH_REPORT_PATH = "name-atlas/connected_change_match_report.json"


def build_connected_artifact_commitments(
    pending_root: Path,
    *,
    original_content_file_ids: tuple[str, ...],
    include_match_report: bool,
) -> tuple[FolderArtifactCommitment, ...]:
    """Measure the exact pre-receipt portable authority set."""

    if not isinstance(pending_root, Path):
        raise ValueError("Pending result root must be a pathlib.Path.")
    paths = {
        "bag-info.txt",
        "bagit.txt",
        "manifest-sha256.txt",
        "name-atlas/accepted_plan.json",
        "name-atlas/change_ledger.json",
        EXECUTION_ORIGIN_PATH,
        "name-atlas/forward_path_map.csv",
        "name-atlas/reference_graph.json",
        "name-atlas/reverse_path_map.csv",
        "name-atlas/source_snapshot.json",
        "name-atlas/user_request.json",
        "name-atlas/verification_report.json",
    }
    if include_match_report:
        paths.add(CONNECTED_CHANGE_MATCH_REPORT_PATH)
    paths.update(
        f"name-atlas/original-content/{file_id}.bin"
        for file_id in original_content_file_ids
    )
    commitments = []
    for relative_path in sorted(paths):
        size, digest = regular_file_measurement(pending_root, relative_path)
        commitments.append(
            FolderArtifactCommitment(
                path=relative_path,
                size=size,
                sha256=digest,
            )
        )
    return tuple(commitments)


def build_connected_receipt(
    *,
    execution_role: Literal["origin", "receiver"],
    job_id: str,
    inventory: FolderInventory,
    user_request: FolderUserRequestArtifact,
    accepted_plan: FolderAcceptedPlanV2,
    reference_graph: FolderReferenceGraph,
    path_rows: tuple[FolderPathMapRow, ...],
    change_ledger: FolderChangeLedger,
    report: FolderVerificationReport,
    execution_origin: FolderExecutionOrigin,
    artifact_commitments: tuple[FolderArtifactCommitment, ...],
    staged_members: tuple[FolderStagedDataMember, ...],
    staged_data_commitment: str,
    organized_tree: OrganizedTreeSnapshot,
    producer_bagit_validation: PackageValidationResult,
    connected_change_core_fingerprint: str,
    imported_change_file_fingerprint: str | None = None,
    imported_change_file_sha256: str | None = None,
    originating_receipt_fingerprint: str | None = None,
    match_report_fingerprint: str | None = None,
    match_report_sha256: str | None = None,
) -> FolderReceiptEnvelopeV2:
    """Build a strict role-aware receipt after independently binding authorities."""

    plan_fingerprint = canonical_sha256(accepted_plan)
    graph_fingerprint = canonical_sha256(reference_graph)
    if not producer_bagit_validation.valid:
        raise ValueError("Producer BagIt validation must pass before receipt creation.")
    if not (
        inventory.source_commitment
        == accepted_plan.source_commitment
        == reference_graph.source_commitment
        == report.source_commitment
        == change_ledger.source_commitment
    ):
        raise ValueError("Receipt authorities target different source commitments.")
    if not (
        user_request.request_fingerprint
        == accepted_plan.request_fingerprint
        == report.request_fingerprint
        == change_ledger.request_fingerprint
    ):
        raise ValueError("Receipt authorities target different requests.")
    if change_ledger.accepted_plan_fingerprint != plan_fingerprint:
        raise ValueError("Change ledger does not bind the v2 accepted plan.")
    if change_ledger.reference_graph_fingerprint != graph_fingerprint:
        raise ValueError("Change ledger does not bind the derived reference graph.")
    if report.accepted_plan_fingerprint != plan_fingerprint:
        raise ValueError("Verification report does not bind the v2 accepted plan.")
    if change_ledger.evidence_fingerprint != accepted_plan.evidence_fingerprint:
        raise ValueError("Change ledger does not bind the origin evidence identity.")
    if execution_role == "origin":
        if not isinstance(execution_origin, GptPlannedExecutionOrigin):
            raise ValueError("An origin receipt requires gpt_planned authority.")
    elif not isinstance(execution_origin, CapsuleAppliedExecutionOrigin):
        raise ValueError("A receiver receipt requires capsule_applied authority.")
    execution_plan_fingerprint = (
        execution_origin.accepted_plan_fingerprint
        if isinstance(execution_origin, GptPlannedExecutionOrigin)
        else execution_origin.receiver_accepted_plan_fingerprint
    )
    if execution_plan_fingerprint != plan_fingerprint:
        raise ValueError("Execution origin does not bind the accepted plan.")
    if staged_data_commitment != report.staged_data_commitment:
        raise ValueError("Staged commitment differs from the verification report.")
    core = FolderReceiptCoreV2(
        execution_role=execution_role,
        job_id=job_id,
        source_commitment=inventory.source_commitment,
        source_file_count=len(inventory.files),
        source_directory_count=inventory.directory_count,
        source_bytes=inventory.total_bytes,
        request_fingerprint=user_request.request_fingerprint,
        evidence_fingerprint=accepted_plan.evidence_fingerprint,
        accepted_plan_fingerprint=plan_fingerprint,
        reference_graph_fingerprint=graph_fingerprint,
        execution_origin_fingerprint=canonical_sha256(execution_origin),
        change_ledger_fingerprint=canonical_sha256(change_ledger),
        verification_report_fingerprint=canonical_sha256(report),
        connected_change_core_fingerprint=connected_change_core_fingerprint,
        imported_change_file_fingerprint=imported_change_file_fingerprint,
        imported_change_file_sha256=imported_change_file_sha256,
        originating_receipt_fingerprint=originating_receipt_fingerprint,
        match_report_fingerprint=match_report_fingerprint,
        match_report_sha256=match_report_sha256,
        artifact_commitments=artifact_commitments,
        staged_data_members=staged_members,
        staged_data_commitment=staged_data_commitment,
        organized_tree=organized_tree,
        map_row_count=len(path_rows),
        path_change_count=change_ledger.path_change_count,
        supported_link_count=change_ledger.supported_link_count,
        rewritten_link_count=change_ledger.rewritten_link_count,
        producer_bagit_messages=producer_bagit_validation.messages,
    )
    return FolderReceiptEnvelopeV2(
        receipt=core,
        receipt_fingerprint=canonical_sha256(core),
    )
