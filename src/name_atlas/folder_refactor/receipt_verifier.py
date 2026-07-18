"""Independent read-only verification of generic-folder Name Atlas receipts."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from name_atlas.domain import PackageValidationResult
from name_atlas.folder_refactor.compiler import (
    PlanCompilationError,
    validate_accepted_plan,
)
from name_atlas.folder_refactor.contracts import (
    FolderAcceptedPlan,
    FolderFile,
    FolderInventory,
    FolderVerificationReport,
)
from name_atlas.folder_refactor.inventory import FolderScanError, scan_folder
from name_atlas.folder_refactor.markdown_contracts import FolderReferenceGraph
from name_atlas.folder_refactor.markdown_links import (
    MARKDOWN_SUFFIXES,
    MarkdownLinkError,
    build_reference_graph_from_reader,
    derive_reference_rewrites,
    verify_reference_rewrites,
)
from name_atlas.folder_refactor.portable_artifacts import (
    ACCEPTED_PLAN_PATH,
    CHANGE_LEDGER_PATH,
    CHANGE_RECEIPT_PATH,
    EVIDENCE_LEDGER_PATH,
    FORWARD_PATH_MAP_PATH,
    ORIGINAL_CONTENT_ROOT,
    PROOF_AND_RESTORE_HTML_PATH,
    RECEIPT_COMMITTED_STATIC_PATHS,
    REFERENCE_GRAPH_PATH,
    REVERSE_PATH_MAP_PATH,
    SOURCE_SNAPSHOT_PATH,
    TAG_MANIFEST_PATH,
    USER_REQUEST_PATH,
    VERIFICATION_REPORT_PATH,
    FolderPortableArtifactError,
    artifact_commitments,
    canonical_portable_json_bytes,
    contains_exact_local_path,
    parse_folder_path_map,
    parse_portable_model,
    read_regular_bytes,
    regular_file_measurement,
    staged_data_commitment,
    staged_data_members,
    strict_json_object,
)
from name_atlas.folder_refactor.receipt_builder import (
    FolderReceiptBuilderError,
    ObservedResultFile,
    build_folder_path_rows_and_change_ledger,
    build_folder_receipt,
    contains_sender_local_path,
    render_folder_proof_html,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderArtifactCommitment,
    FolderChangeLedger,
    FolderEvidenceLedger,
    FolderPathMapRow,
    FolderReceiptCore,
    FolderReceiptEnvelope,
    FolderReceiptVerification,
    FolderReceiptVerificationCheck,
    FolderReceiptVerificationStatus,
    FolderStagedDataMember,
    FolderUserRequestArtifact,
    folder_receipt_fingerprint,
)
from name_atlas.folder_refactor.serialization import canonical_sha256
from name_atlas.verification.bagit_validator import (
    BagItAdapterError,
    BagItPackageValidator,
)

_Model = TypeVar("_Model", bound=BaseModel)
_SHA256_TEXT = re.compile(r"[a-f0-9]{64}\Z")
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_REQUIRED_REPORT_CHECK_IDS = frozenset(
    {
        "bagit_validation",
        "complete_file_bijection",
        "empty_directories_preserved",
        "payload_hashes_preserved",
        "protected_paths_preserved",
        "result_is_separate",
        "source_unchanged",
        "supported_markdown_links_resolve",
    }
)
_ROOT_FILES = frozenset(
    {
        "bag-info.txt",
        "bagit.txt",
        "manifest-sha256.txt",
        TAG_MANIFEST_PATH,
    }
)
_ARTIFACT_SLUGS = {
    ACCEPTED_PLAN_PATH: "accepted_plan",
    CHANGE_LEDGER_PATH: "change_ledger",
    EVIDENCE_LEDGER_PATH: "evidence_ledger",
    FORWARD_PATH_MAP_PATH: "forward_path_map",
    REFERENCE_GRAPH_PATH: "reference_graph",
    REVERSE_PATH_MAP_PATH: "reverse_path_map",
    SOURCE_SNAPSHOT_PATH: "source_snapshot",
    USER_REQUEST_PATH: "user_request",
    VERIFICATION_REPORT_PATH: "verification_report",
    "bag-info.txt": "bag_info",
    "bagit.txt": "bagit",
    "manifest-sha256.txt": "payload_manifest",
}


class FolderReceiptCandidateError(ValueError):
    """The supplied path cannot be opened as a candidate folder receipt."""


@dataclass(frozen=True, slots=True)
class _PortableArtifacts:
    envelope: FolderReceiptEnvelope
    inventory: FolderInventory
    user_request: FolderUserRequestArtifact
    evidence_ledger: FolderEvidenceLedger
    accepted_plan: FolderAcceptedPlan
    reference_graph: FolderReferenceGraph
    forward_rows: tuple[FolderPathMapRow, ...]
    reverse_rows: tuple[FolderPathMapRow, ...]
    change_ledger: FolderChangeLedger
    report: FolderVerificationReport


@dataclass(frozen=True, slots=True)
class _CandidateTree:
    files: frozenset[str]
    directories: frozenset[str]


def verify_folder_receipt(
    result_root: Path,
    *,
    source_root: Path | None = None,
) -> FolderReceiptVerification:
    """Verify a result without a job, GPT, API key, network, or writes."""

    root = _require_candidate_directory(result_root)
    checks: list[FolderReceiptVerificationCheck] = []
    try:
        candidate_tree = _scan_candidate_tree(root)
    except FolderPortableArtifactError:
        _record_failure(
            checks,
            "candidate_tree_unsupported",
            "The candidate contains an unreadable, linked, hard-linked, "
            "or special member.",
        )
        return _result(checks)

    try:
        bagit_result = BagItPackageValidator().validate(root)
    except BagItAdapterError:
        _record_failure(
            checks,
            "bagit_validation_error",
            "BagIt validation could not complete through the read-only adapter.",
        )
        return _result(checks)
    if not bagit_result.valid:
        _record_failure(
            checks,
            "bagit_validation_failed",
            "BagIt fixity or completeness validation failed.",
        )
        return _result(checks)
    _record_success(checks, "bagit_valid", "BagIt validation passed.")

    try:
        receipt_bytes = read_regular_bytes(root, CHANGE_RECEIPT_PATH)
        core, fingerprint = _parse_receipt_core(receipt_bytes)
    except (FolderPortableArtifactError, ValidationError, ValueError):
        _record_failure(
            checks,
            "receipt_schema_invalid",
            "The machine receipt does not satisfy folder-change-receipt.v1.",
        )
        return _result(checks)

    expected_fingerprint = folder_receipt_fingerprint(core)
    if fingerprint != expected_fingerprint:
        _record_failure(
            checks,
            "receipt_fingerprint_mismatch",
            "Canonical ReceiptCore bytes do not match the envelope fingerprint.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    envelope = FolderReceiptEnvelope(
        receipt=core,
        receipt_fingerprint=fingerprint,
    )
    if canonical_portable_json_bytes(envelope) != receipt_bytes:
        _record_failure(
            checks,
            "receipt_serialization_invalid",
            "The receipt envelope is not in canonical serialized form.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "receipt_fingerprint_valid",
        "The non-self-referential receipt fingerprint matches.",
    )

    artifact_failures, actual_commitments = _verify_raw_commitments(root, core)
    if artifact_failures:
        checks.extend(artifact_failures)
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    if actual_commitments is None:
        raise AssertionError("Successful raw verification must return commitments.")
    _record_success(
        checks,
        "artifact_commitments_valid",
        f"All {len(actual_commitments)} receipt-bound artifact digests match.",
    )

    try:
        artifacts = _parse_portable_artifacts(root, envelope)
    except (FolderPortableArtifactError, ValidationError, ValueError):
        _record_failure(
            checks,
            "portable_artifact_schema_invalid",
            "A receipt-bound artifact is noncanonical or violates its exact schema.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "portable_artifact_schemas_valid",
        "Every generic-folder machine artifact parsed canonically and strictly.",
    )

    portable_values = (
        artifacts.envelope,
        artifacts.inventory,
        artifacts.user_request,
        artifacts.evidence_ledger,
        artifacts.accepted_plan,
        artifacts.reference_graph,
        artifacts.forward_rows,
        artifacts.change_ledger,
        artifacts.report,
    )
    caller_paths = {str(root), root.as_posix()}
    if source_root is not None:
        caller_paths.update({str(source_root), source_root.as_posix()})
    if contains_sender_local_path(portable_values) or contains_exact_local_path(
        portable_values,
        sender_local_paths=caller_paths,
    ):
        _record_failure(
            checks,
            "portable_artifact_contains_local_path",
            "A portable machine artifact contains an absolute local path or file URI.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "portable_artifacts_path_neutral",
        "Portable machine artifacts contain only path-neutral authority.",
    )

    try:
        data_members = staged_data_members(root)
        actual_data_commitment = staged_data_commitment(data_members)
    except FolderPortableArtifactError:
        _record_failure(
            checks,
            "staged_data_unreadable",
            "The result data tree is not a complete ordinary-file tree.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    if not _staged_summary_matches(core, data_members, actual_data_commitment):
        _record_failure(
            checks,
            "staged_data_commitment_mismatch",
            "The complete data member commitment or count differs from the receipt.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "staged_data_commitment_valid",
        f"All {len(data_members)} staged data members match the receipt.",
    )

    try:
        _require_authority_bindings(artifacts)
        validate_accepted_plan(
            artifacts.inventory,
            artifacts.user_request.request,
            artifacts.accepted_plan,
        )
    except (PlanCompilationError, ValueError):
        _record_failure(
            checks,
            "accepted_plan_binding_mismatch",
            "The request, evidence, accepted plan, and source inventory disagree.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "accepted_plan_bindings_valid",
        "The complete accepted plan is bound to the request, evidence, and source.",
    )

    try:
        _require_map_bijection(artifacts, data_members)
    except ValueError:
        _record_failure(
            checks,
            "path_maps_inconsistent",
            "Forward and reverse maps do not exactly bind source files "
            "to result files.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "path_maps_valid",
        "Forward and reverse maps are complete exact inverses.",
    )

    try:
        _require_result_tree_shape(
            candidate_tree,
            artifacts,
            data_members,
        )
    except ValueError:
        _record_failure(
            checks,
            "result_tree_shape_mismatch",
            "The result contains missing, unexpected, linked, or misplaced members.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "result_tree_shape_valid",
        "The complete result file and directory tree matches the accepted transaction.",
    )

    try:
        _require_markdown_authority(root, artifacts)
    except (FolderPortableArtifactError, MarkdownLinkError, ValueError):
        _record_failure(
            checks,
            "markdown_reference_mismatch",
            "Supported Markdown spans do not deterministically preserve "
            "target identity.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "markdown_references_valid",
        "Supported Markdown rewrites reapply exactly and retain stable "
        "target identities.",
    )

    try:
        observed = _observed_result_files(artifacts, data_members)
        expected_rows, expected_ledger = build_folder_path_rows_and_change_ledger(
            inventory=artifacts.inventory,
            accepted_plan=artifacts.accepted_plan,
            reference_graph=artifacts.reference_graph,
            observed_result_files=observed,
        )
        if (
            expected_rows != artifacts.forward_rows
            or expected_ledger != artifacts.change_ledger
        ):
            raise ValueError("Change authority differs from deterministic facts.")
    except (FolderReceiptBuilderError, ValueError):
        _record_failure(
            checks,
            "change_ledger_inconsistent",
            "The change ledger does not equal the deterministic file and link changes.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "change_ledger_valid",
        "The change ledger exactly matches every file and supported-link change.",
    )

    try:
        _require_report_agreement(artifacts, bagit_result)
        rebuilt = build_folder_receipt(
            job_id=core.job_id,
            inventory=artifacts.inventory,
            user_request=artifacts.user_request,
            evidence_ledger=artifacts.evidence_ledger,
            accepted_plan=artifacts.accepted_plan,
            reference_graph=artifacts.reference_graph,
            path_rows=artifacts.forward_rows,
            change_ledger=artifacts.change_ledger,
            verification_report=artifacts.report,
            artifact_commitments=actual_commitments,
            staged_data_members=data_members,
            staged_data_commitment=actual_data_commitment,
            producer_bagit_validation=bagit_result,
        )
        if rebuilt != artifacts.envelope:
            raise ValueError("Receipt differs from recomputed transaction facts.")
    except (FolderReceiptBuilderError, ValueError):
        _record_failure(
            checks,
            "receipt_transaction_inconsistent",
            "The receipt or producer report disagrees with recomputed "
            "transaction facts.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "receipt_transaction_valid",
        "Receipt, report, request, evidence, plan, maps, changes, and payloads agree.",
    )

    try:
        actual_html = read_regular_bytes(root, PROOF_AND_RESTORE_HTML_PATH)
        expected_html = render_folder_proof_html(
            artifacts.envelope,
            artifacts.change_ledger,
            artifacts.report,
        )
    except (FolderPortableArtifactError, FolderReceiptBuilderError, ValueError):
        _record_failure(
            checks,
            "offline_proof_invalid",
            "The human-readable proof cannot be reconstructed from machine facts.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    if actual_html != expected_html:
        _record_failure(
            checks,
            "offline_proof_mismatch",
            "The human-readable proof differs from the committed machine facts.",
        )
        return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
    _record_success(
        checks,
        "offline_proof_valid",
        "The offline proof is exactly derived from the verified machine facts.",
    )

    if source_root is not None:
        try:
            supplied = scan_folder(source_root).inventory
        except (FolderScanError, OSError, ValueError):
            _record_failure(
                checks,
                "supplied_source_unreadable",
                "The optional source cannot be read under the supported "
                "folder contract.",
            )
            return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
        if supplied != artifacts.inventory:
            _record_failure(
                checks,
                "supplied_source_mismatch",
                "The optional source differs from the committed source description.",
            )
            return _result(checks, job_id=core.job_id, fingerprint=fingerprint)
        _record_success(
            checks,
            "supplied_source_matches",
            "The optional source exactly matches every committed path and byte.",
        )

    return _result(checks, job_id=core.job_id, fingerprint=fingerprint)


def _parse_receipt_core(data: bytes) -> tuple[FolderReceiptCore, str]:
    value = strict_json_object(data)
    if set(value) != {"receipt", "receipt_fingerprint"}:
        raise ValueError("Receipt envelope fields are not exact.")
    core_value = value["receipt"]
    fingerprint = value["receipt_fingerprint"]
    if not isinstance(core_value, dict) or not isinstance(fingerprint, str):
        raise ValueError("Receipt envelope value types are invalid.")
    if _SHA256_TEXT.fullmatch(fingerprint) is None:
        raise ValueError("Receipt fingerprint syntax is invalid.")
    core_bytes = canonical_portable_json_bytes(core_value)
    return FolderReceiptCore.model_validate_json(core_bytes, strict=True), fingerprint


def _verify_raw_commitments(
    root: Path,
    core: FolderReceiptCore,
) -> tuple[
    tuple[FolderReceiptVerificationCheck, ...],
    tuple[FolderArtifactCommitment, ...] | None,
]:
    failures: list[FolderReceiptVerificationCheck] = []
    for expected in core.artifact_commitments:
        slug = _artifact_slug(expected.path)
        try:
            size, digest = regular_file_measurement(root, expected.path)
        except FolderPortableArtifactError:
            failures.append(
                _failed(
                    f"artifact_missing_or_unreadable:{slug}",
                    f"Receipt-bound artifact is unavailable: {expected.path}.",
                )
            )
            continue
        if size != expected.size or digest != expected.sha256:
            failures.append(
                _failed(
                    f"artifact_digest_mismatch:{slug}",
                    "Raw artifact bytes no longer equal the receipt commitment: "
                    f"{expected.path}.",
                )
            )
    if failures:
        return tuple(failures), None

    original_ids = tuple(
        PurePosixPath(item.path).stem
        for item in core.artifact_commitments
        if item.path.startswith(f"{ORIGINAL_CONTENT_ROOT}/")
    )
    try:
        actual = artifact_commitments(
            root,
            static_paths=RECEIPT_COMMITTED_STATIC_PATHS,
            original_content_file_ids=original_ids,
        )
    except FolderPortableArtifactError:
        return (
            (
                _failed(
                    "artifact_set_mismatch",
                    "The authoritative artifact set differs from the "
                    "receipt allowlist.",
                ),
            ),
            None,
        )
    if actual != core.artifact_commitments:
        return (
            (
                _failed(
                    "artifact_commitment_mismatch",
                    "The authoritative artifact commitment set differs "
                    "from the receipt.",
                ),
            ),
            None,
        )
    return (), actual


def _parse_portable_artifacts(
    root: Path,
    envelope: FolderReceiptEnvelope,
) -> _PortableArtifacts:
    inventory = _read_canonical_model(root, SOURCE_SNAPSHOT_PATH, FolderInventory)
    user_request = _read_canonical_model(
        root,
        USER_REQUEST_PATH,
        FolderUserRequestArtifact,
    )
    evidence_ledger = _read_canonical_model(
        root,
        EVIDENCE_LEDGER_PATH,
        FolderEvidenceLedger,
    )
    accepted_plan = _read_canonical_model(
        root,
        ACCEPTED_PLAN_PATH,
        FolderAcceptedPlan,
    )
    reference_graph = _read_canonical_model(
        root,
        REFERENCE_GRAPH_PATH,
        FolderReferenceGraph,
    )
    change_ledger = _read_canonical_model(
        root,
        CHANGE_LEDGER_PATH,
        FolderChangeLedger,
    )
    report = _read_canonical_model(
        root,
        VERIFICATION_REPORT_PATH,
        FolderVerificationReport,
    )
    forward_rows = parse_folder_path_map(
        read_regular_bytes(root, FORWARD_PATH_MAP_PATH),
        reverse=False,
    )
    reverse_rows = parse_folder_path_map(
        read_regular_bytes(root, REVERSE_PATH_MAP_PATH),
        reverse=True,
    )
    return _PortableArtifacts(
        envelope=envelope,
        inventory=inventory,
        user_request=user_request,
        evidence_ledger=evidence_ledger,
        accepted_plan=accepted_plan,
        reference_graph=reference_graph,
        forward_rows=forward_rows,
        reverse_rows=reverse_rows,
        change_ledger=change_ledger,
        report=report,
    )


def _read_canonical_model(
    root: Path,
    relative_path: str,
    model_type: type[_Model],
) -> _Model:
    payload = read_regular_bytes(root, relative_path)
    model = parse_portable_model(payload, model_type)
    if canonical_portable_json_bytes(model) != payload:
        raise FolderPortableArtifactError(
            f"Portable JSON is noncanonical: {relative_path}."
        )
    return model


def _require_authority_bindings(artifacts: _PortableArtifacts) -> None:
    core = artifacts.envelope.receipt
    inventory = artifacts.inventory
    request = artifacts.user_request
    evidence = artifacts.evidence_ledger
    accepted = artifacts.accepted_plan
    graph = artifacts.reference_graph
    ledger = artifacts.change_ledger
    report = artifacts.report
    accepted_fingerprint = canonical_sha256(accepted)
    graph_fingerprint = canonical_sha256(graph)
    equalities = (
        core.source_commitment == inventory.source_commitment,
        core.source_file_count == len(inventory.files),
        core.source_directory_count == inventory.directory_count,
        core.source_bytes == inventory.total_bytes,
        core.request_fingerprint == request.request_fingerprint,
        core.request_fingerprint == evidence.request_fingerprint,
        core.request_fingerprint == accepted.request_fingerprint,
        core.evidence_fingerprint == evidence.evidence_fingerprint,
        core.evidence_fingerprint == accepted.evidence_fingerprint,
        core.accepted_plan_fingerprint == accepted_fingerprint,
        core.reference_graph_fingerprint == graph_fingerprint,
        evidence.job_id == core.job_id,
        evidence.source_commitment == inventory.source_commitment,
        evidence.accepted_plan_fingerprint == accepted_fingerprint,
        evidence.request_scope == accepted.request_scope,
        graph.source_commitment == inventory.source_commitment,
        ledger.source_commitment == inventory.source_commitment,
        ledger.request_fingerprint == request.request_fingerprint,
        ledger.evidence_fingerprint == evidence.evidence_fingerprint,
        ledger.accepted_plan_fingerprint == accepted_fingerprint,
        ledger.reference_graph_fingerprint == graph_fingerprint,
        report.source_commitment == inventory.source_commitment,
        report.request_fingerprint == request.request_fingerprint,
        report.accepted_plan_fingerprint == accepted_fingerprint,
        core.model_alias == evidence.model_alias,
        core.provider_kind == evidence.provider_kind,
        core.returned_model_ids == evidence.returned_model_ids,
        core.store_false == evidence.store_false,
        core.clarification_question == evidence.clarification_question,
        core.clarification_answer == evidence.clarification_answer,
    )
    if not all(equalities):
        raise ValueError("Portable authorities do not bind one transaction.")


def _require_map_bijection(
    artifacts: _PortableArtifacts,
    data_members: tuple[FolderStagedDataMember, ...],
) -> None:
    if artifacts.forward_rows != artifacts.reverse_rows:
        raise ValueError("Forward and reverse map rows differ.")
    rows = artifacts.forward_rows
    if len(rows) != len(artifacts.inventory.files):
        raise ValueError("Map row count differs from source inventory.")
    inventory_by_id = {item.file_id: item for item in artifacts.inventory.files}
    mapping_by_id = {
        item.file_id: item for item in artifacts.accepted_plan.file_mappings
    }
    data_by_path = {item.path: item for item in data_members}
    if set(inventory_by_id) != set(mapping_by_id) or set(data_by_path) != {
        row.result_path for row in rows
    }:
        raise ValueError("Maps do not account for source and result exactly.")
    for row in rows:
        source = inventory_by_id.get(row.file_id)
        mapping = mapping_by_id.get(row.file_id)
        result = data_by_path.get(row.result_path)
        if source is None or mapping is None or result is None:
            raise ValueError("Map row refers to an unknown member.")
        if (
            row.original_path != source.relative_path
            or row.original_size != source.size
            or row.original_sha256 != source.sha256
            or row.protected != source.protected
            or row.original_path != mapping.original_path
            or row.result_path != mapping.target_path
            or row.result_size != result.size
            or row.result_sha256 != result.sha256
        ):
            raise ValueError("Map row facts differ from source, plan, or result.")


def _require_result_tree_shape(
    candidate: _CandidateTree,
    artifacts: _PortableArtifacts,
    data_members: tuple[FolderStagedDataMember, ...],
) -> None:
    committed = {item.path for item in artifacts.envelope.receipt.artifact_commitments}
    expected_files = {
        *_ROOT_FILES,
        CHANGE_RECEIPT_PATH,
        PROOF_AND_RESTORE_HTML_PATH,
        *committed,
        *(f"data/{member.path}" for member in data_members),
    }
    expected_directories = {"data", "name-atlas"}
    for file_path in expected_files:
        parts = PurePosixPath(file_path).parts
        expected_directories.update(
            PurePosixPath(*parts[:index]).as_posix() for index in range(1, len(parts))
        )
    for empty_path in artifacts.accepted_plan.empty_directories:
        full = PurePosixPath("data", empty_path)
        expected_directories.update(
            PurePosixPath(*full.parts[:index]).as_posix()
            for index in range(1, len(full.parts) + 1)
        )
    if candidate.files != frozenset(expected_files) or candidate.directories != (
        frozenset(expected_directories)
    ):
        raise ValueError("Result tree differs from its exact portable authorities.")


def _require_markdown_authority(
    root: Path,
    artifacts: _PortableArtifacts,
) -> None:
    rows_by_id = {row.file_id: row for row in artifacts.forward_rows}
    rewritten_ids = {
        reference.source_file_id
        for reference in artifacts.reference_graph.references
        if reference.verification_status == "rewritten"
    }
    expected_original_paths = {
        f"{ORIGINAL_CONTENT_ROOT}/{file_id}.bin" for file_id in rewritten_ids
    }
    committed_original_paths = {
        item.path
        for item in artifacts.envelope.receipt.artifact_commitments
        if item.path.startswith(f"{ORIGINAL_CONTENT_ROOT}/")
    }
    if expected_original_paths != committed_original_paths:
        raise ValueError("Original-content artifacts differ from rewritten sources.")

    markdown_files = tuple(
        source
        for source in artifacts.inventory.files
        if PurePosixPath(source.relative_path).suffix.casefold() in MARKDOWN_SUFFIXES
    )

    def read_original_markdown(source: FolderFile) -> bytes:
        row = rows_by_id[source.file_id]
        relative = (
            f"{ORIGINAL_CONTENT_ROOT}/{source.file_id}.bin"
            if source.file_id in rewritten_ids
            else f"data/{row.result_path}"
        )
        original = read_regular_bytes(root, relative)
        if len(original) != source.size or hashlib.sha256(original).hexdigest() != (
            source.sha256
        ):
            raise ValueError("Original Markdown bytes differ from the inventory.")
        return original

    expected_source_graph = build_reference_graph_from_reader(
        artifacts.inventory,
        read_original_markdown,
    )
    expected_derived_graph = derive_reference_rewrites(
        expected_source_graph,
        artifacts.accepted_plan,
    )
    if expected_derived_graph != artifacts.reference_graph:
        raise ValueError("Reference graph differs from deterministic reconstruction.")

    rewritten_from_rows = {
        row.file_id for row in artifacts.forward_rows if row.markdown_rewritten
    }
    if rewritten_from_rows != rewritten_ids:
        raise ValueError("Path-map rewrite flags differ from reference authority.")
    for source in markdown_files:
        original = read_original_markdown(source)
        row = rows_by_id[source.file_id]
        staged = read_regular_bytes(root, f"data/{row.result_path}")
        verify_reference_rewrites(
            original,
            staged,
            source_file_id=source.file_id,
            graph=artifacts.reference_graph,
        )


def _observed_result_files(
    artifacts: _PortableArtifacts,
    data_members: tuple[FolderStagedDataMember, ...],
) -> dict[str, ObservedResultFile]:
    data_by_path = {item.path: item for item in data_members}
    return {
        mapping.file_id: ObservedResultFile(
            relative_path=mapping.target_path,
            size=data_by_path[mapping.target_path].size,
            sha256=data_by_path[mapping.target_path].sha256,
        )
        for mapping in artifacts.accepted_plan.file_mappings
    }


def _require_report_agreement(
    artifacts: _PortableArtifacts,
    bagit_result: PackageValidationResult,
) -> None:
    core = artifacts.envelope.receipt
    report = artifacts.report
    check_ids = tuple(check.check_id for check in report.checks)
    if len(check_ids) != len(set(check_ids)) or set(check_ids) != (
        _REQUIRED_REPORT_CHECK_IDS
    ):
        raise ValueError("Producer report check IDs differ from the exact contract.")
    expected = {
        "source_commitment": core.source_commitment,
        "request_fingerprint": core.request_fingerprint,
        "accepted_plan_fingerprint": core.accepted_plan_fingerprint,
        "staged_data_commitment": core.staged_data_commitment,
        "file_count": core.map_row_count,
        "path_change_count": core.path_change_count,
        "protected_file_count": artifacts.change_ledger.protected_file_count,
        "empty_directory_count": len(artifacts.inventory.empty_directories),
        "supported_link_count": core.supported_link_count,
        "rewritten_link_count": core.rewritten_link_count,
        "rewritten_markdown_file_count": core.rewritten_markdown_file_count,
    }
    if any(getattr(report, field) != value for field, value in expected.items()):
        raise ValueError("Producer report summary differs from receipt facts.")
    if core.producer_bagit_validation != bagit_result:
        raise ValueError("Producer and receiver BagIt facts differ.")


def _staged_summary_matches(
    core: FolderReceiptCore,
    members: tuple[FolderStagedDataMember, ...],
    commitment: str,
) -> bool:
    return (
        commitment == core.staged_data_commitment
        and len(members) == core.staged_data_file_count
        and sum(member.size for member in members) == core.staged_data_bytes
    )


def _require_candidate_directory(value: Path) -> Path:
    if not isinstance(value, Path):
        raise FolderReceiptCandidateError("Result bag must be a pathlib.Path.")
    try:
        metadata = value.lstat()
    except OSError as exc:
        raise FolderReceiptCandidateError("Result bag cannot be inspected.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FolderReceiptCandidateError("Result bag must be a real directory.")
    if metadata.st_mode & 0o444 == 0 or metadata.st_mode & 0o111 == 0:
        raise FolderReceiptCandidateError("Result bag directory is not readable.")
    descriptor: int | None = None
    try:
        descriptor = os.open(value, _DIRECTORY_OPEN_FLAGS)
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (metadata.st_dev, metadata.st_ino):
            raise FolderReceiptCandidateError("Result bag identity changed.")
        with os.scandir(descriptor) as entries:
            tuple(entries)
        return value.resolve(strict=True)
    except FolderReceiptCandidateError:
        raise
    except OSError as exc:
        raise FolderReceiptCandidateError("Result bag cannot be read safely.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _scan_candidate_tree(root: Path) -> _CandidateTree:
    files: set[str] = set()
    directories: set[str] = set()
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FolderPortableArtifactError(
                "Candidate directory cannot be enumerated."
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FolderPortableArtifactError(
                    "Candidate member cannot be inspected."
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise FolderPortableArtifactError("Candidate contains a symlink.")
            if stat.S_ISDIR(metadata.st_mode):
                if metadata.st_mode & 0o444 == 0 or metadata.st_mode & 0o111 == 0:
                    raise FolderPortableArtifactError(
                        "Candidate contains an unreadable directory."
                    )
                directories.add(relative)
                pending.append(path)
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink > 1 or metadata.st_mode & 0o444 == 0:
                    raise FolderPortableArtifactError(
                        "Candidate contains an unreadable or hard-linked file."
                    )
                files.add(relative)
            else:
                raise FolderPortableArtifactError(
                    "Candidate contains a special filesystem member."
                )
    return _CandidateTree(
        files=frozenset(files),
        directories=frozenset(directories),
    )


def _artifact_slug(relative_path: str) -> str:
    known = _ARTIFACT_SLUGS.get(relative_path)
    if known is not None:
        return known
    prefix = f"{ORIGINAL_CONTENT_ROOT}/"
    if relative_path.startswith(prefix) and relative_path.endswith(".bin"):
        file_id = PurePosixPath(relative_path).stem
        if _SHA256_TEXT.fullmatch(file_id) is not None:
            return f"original_content:{file_id}"
    raise ValueError(f"Unsupported receipt commitment path: {relative_path}")


def _record_success(
    checks: list[FolderReceiptVerificationCheck],
    check_id: str,
    detail: str,
) -> None:
    checks.append(
        FolderReceiptVerificationCheck(
            check_id=check_id,
            passed=True,
            detail=detail,
        )
    )


def _record_failure(
    checks: list[FolderReceiptVerificationCheck],
    check_id: str,
    detail: str,
) -> None:
    checks.append(_failed(check_id, detail))


def _failed(check_id: str, detail: str) -> FolderReceiptVerificationCheck:
    return FolderReceiptVerificationCheck(
        check_id=check_id,
        passed=False,
        detail=detail,
    )


def _result(
    checks: list[FolderReceiptVerificationCheck],
    *,
    job_id: str | None = None,
    fingerprint: str | None = None,
) -> FolderReceiptVerification:
    failed = tuple(check.check_id for check in checks if not check.passed)
    return FolderReceiptVerification(
        status=(
            FolderReceiptVerificationStatus.BLOCKED
            if failed
            else FolderReceiptVerificationStatus.VERIFIED
        ),
        job_id=job_id,
        receipt_fingerprint=fingerprint,
        checks=tuple(checks),
        failed_check_ids=failed,
    )
