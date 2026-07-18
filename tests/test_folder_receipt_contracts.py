"""Strict A3 contracts for portable evidence, receipts, and proof results."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from name_atlas.domain import PackageValidationResult
from name_atlas.folder_refactor.receipt_contracts import (
    RECEIPT_CLAIM_BOUNDARIES,
    FolderArtifactCommitment,
    FolderReceiptCore,
    FolderReceiptEnvelope,
    FolderReceiptVerification,
    FolderReceiptVerificationCheck,
    FolderReceiptVerificationStatus,
    FolderUserRequestArtifact,
    build_folder_receipt_envelope,
)
from name_atlas.folder_refactor.serialization import request_fingerprint

JOB_ID = uuid.UUID("123e4567-e89b-42d3-a456-426614174000").hex
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
REQUIRED_ARTIFACT_PATHS = (
    "bag-info.txt",
    "bagit.txt",
    "manifest-sha256.txt",
    "name-atlas/accepted_plan.json",
    "name-atlas/change_ledger.json",
    "name-atlas/evidence_ledger.json",
    "name-atlas/forward_path_map.csv",
    "name-atlas/reference_graph.json",
    "name-atlas/reverse_path_map.csv",
    "name-atlas/source_snapshot.json",
    "name-atlas/user_request.json",
    "name-atlas/verification_report.json",
)


def _receipt_core(**overrides: object) -> FolderReceiptCore:
    values: dict[str, object] = {
        "job_id": JOB_ID,
        "source_commitment": SHA_A,
        "source_file_count": 1,
        "source_directory_count": 0,
        "source_bytes": 5,
        "request_fingerprint": SHA_B,
        "evidence_fingerprint": SHA_C,
        "accepted_plan_fingerprint": SHA_A,
        "reference_graph_fingerprint": SHA_B,
        "provider_kind": "deterministic",
        "staged_data_commitment": SHA_C,
        "staged_data_file_count": 1,
        "staged_data_bytes": 5,
        "artifact_commitments": tuple(
            FolderArtifactCommitment(path=path, size=index, sha256=SHA_A)
            for index, path in enumerate(REQUIRED_ARTIFACT_PATHS, start=1)
        ),
        "map_row_count": 1,
        "path_change_count": 1,
        "supported_link_count": 1,
        "rewritten_link_count": 1,
        "rewritten_markdown_file_count": 1,
        "producer_bagit_validation": PackageValidationResult(
            validator="bagit",
            valid=True,
            messages=("BagIt validation passed.",),
        ),
        "claim_boundaries": RECEIPT_CLAIM_BOUNDARIES,
    }
    values.update(overrides)
    return FolderReceiptCore.model_validate(values, strict=True)


def test_user_request_artifact_requires_exact_fingerprint() -> None:
    request = "Prepare this folder for handoff."
    artifact = FolderUserRequestArtifact(
        request=request,
        request_fingerprint=request_fingerprint(request),
    )

    assert artifact.request == request
    with pytest.raises(ValidationError, match="fingerprint"):
        FolderUserRequestArtifact(
            request=request,
            request_fingerprint=SHA_A,
        )


def test_receipt_envelope_fingerprint_is_outside_its_hash_domain() -> None:
    core = _receipt_core()
    envelope = build_folder_receipt_envelope(core)

    assert (
        FolderReceiptEnvelope.model_validate_json(
            envelope.model_dump_json(),
            strict=True,
        )
        == envelope
    )
    with pytest.raises(ValidationError, match="fingerprint"):
        FolderReceiptEnvelope(
            receipt=core,
            receipt_fingerprint=SHA_A,
        )


@pytest.mark.parametrize(
    "path",
    [
        "name-atlas/change_receipt.json",
        "name-atlas/proof_and_restore.html",
        "tagmanifest-sha256.txt",
        "name-atlas/original-content/not-hex.bin",
    ],
)
def test_receipt_rejects_circular_or_unsupported_commitments(path: str) -> None:
    commitments = list(_receipt_core().artifact_commitments)
    commitments.append(FolderArtifactCommitment(path=path, size=1, sha256=SHA_A))
    commitments.sort(key=lambda item: item.path)

    with pytest.raises(ValidationError, match="circular|unsupported"):
        _receipt_core(artifact_commitments=tuple(commitments))


def test_live_receipt_requires_store_false_and_returned_model() -> None:
    with pytest.raises(ValidationError, match="store=false"):
        _receipt_core(provider_kind="live")

    core = _receipt_core(
        provider_kind="live",
        returned_model_ids=("gpt-5.6-2026-07-01",),
        store_false=True,
    )
    assert core.store_false is True


def test_receiver_status_cannot_disagree_with_checks() -> None:
    result = FolderReceiptVerification(
        status=FolderReceiptVerificationStatus.VERIFIED,
        job_id=JOB_ID,
        receipt_fingerprint=SHA_A,
        checks=(
            FolderReceiptVerificationCheck(
                check_id="receipt_fingerprint",
                passed=True,
                detail="The receipt core fingerprint matches.",
            ),
        ),
        failed_check_ids=(),
    )
    assert result.status is FolderReceiptVerificationStatus.VERIFIED

    with pytest.raises(ValidationError, match="Failed-check IDs"):
        FolderReceiptVerification(
            status=FolderReceiptVerificationStatus.BLOCKED,
            checks=(
                FolderReceiptVerificationCheck(
                    check_id="artifact_digest_mismatch:accepted_plan",
                    passed=False,
                    detail="The accepted plan bytes differ from the receipt.",
                ),
            ),
            failed_check_ids=(),
        )
