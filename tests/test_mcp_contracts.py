"""Strict public data-contract checks for the shared MCP surface."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from name_atlas.mcp_contracts import (
    McpChangeFileResult,
    McpJobStatus,
    McpReconstructionResult,
    McpVerificationResult,
    PlanAndCreateCopyRequest,
)

SHA = "a" * 64
HANDLE = "b" * 32


def test_request_contract_is_strict_and_consent_is_literal_boolean() -> None:
    payload = {
        "source_root": "/tmp/source",
        "output_parent": "/tmp/output",
        "user_request": "Organize this project.",
        "mode": "replay",
        "idempotency_key": "mcp-contract-key-0001",
        "evidence_disclosure_acknowledged": False,
    }
    request = PlanAndCreateCopyRequest.model_validate(payload, strict=True)
    assert request.evidence_disclosure_acknowledged is False

    with pytest.raises(ValidationError):
        PlanAndCreateCopyRequest.model_validate(
            {**payload, "unknown": "has-no-authority"},
            strict=True,
        )
    with pytest.raises(ValidationError):
        PlanAndCreateCopyRequest.model_validate(
            {**payload, "evidence_disclosure_acknowledged": "true"},
            strict=True,
        )
    with pytest.raises(ValidationError):
        PlanAndCreateCopyRequest.model_validate(
            {**payload, "user_request": "   \n"},
            strict=True,
        )
    with pytest.raises(ValidationError):
        PlanAndCreateCopyRequest.model_validate(
            {**payload, "idempotency_key": "short"},
            strict=True,
        )


def test_result_contracts_enforce_mutually_exclusive_shapes() -> None:
    verified_job = McpJobStatus(
        status="accepted",
        message="Verified.",
        job_handle=HANDLE,
        job_id=HANDLE,
        revision=4,
        lifecycle="verified",
        execution_origin="capsule_applied",
        result_root="/tmp/result",
        receipt_fingerprint=SHA,
        organized_tree_commitment=SHA,
    )
    assert verified_job.provider_kind is None

    with pytest.raises(ValidationError):
        McpJobStatus(
            status="accepted",
            message="Invalid capsule provider claim.",
            job_handle=HANDLE,
            job_id=HANDLE,
            revision=4,
            lifecycle="verified",
            execution_origin="capsule_applied",
            provider_kind="live",
        )
    with pytest.raises(ValidationError):
        McpJobStatus(
            status="accepted",
            message="Question in the wrong lifecycle.",
            job_handle=HANDLE,
            job_id=HANDLE,
            revision=1,
            lifecycle="planning",
            execution_origin="gpt_planned",
            provider_kind="recorded_replay",
            clarification_question="Which file?",
            clarification_question_fingerprint=SHA,
        )
    with pytest.raises(ValidationError):
        McpChangeFileResult(
            status="verified",
            message="Missing receipt identity.",
            job_handle=HANDLE,
            change_file_path="/tmp/change.nameatlas-change.json",
            change_file_fingerprint=SHA,
        )
    with pytest.raises(ValidationError):
        McpVerificationResult(
            status="blocked",
            message="Blocked without a failed check.",
            result_root="/tmp/result",
        )
    with pytest.raises(ValidationError):
        McpReconstructionResult(
            status="blocked",
            message="Blocked while retaining success proof.",
            result_root="/tmp/result",
            destination="/tmp/restored",
            receipt_fingerprint=SHA,
            blocker_code="reconstruction_blocked",
        )
