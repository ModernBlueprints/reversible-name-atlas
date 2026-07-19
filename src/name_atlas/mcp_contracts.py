"""Strict public contracts for the shared Name Atlas MCP surface."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from name_atlas.folder_refactor.contracts import SHA256_PATTERN, StrictFrozenModel

MCP_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$"
JOB_HANDLE_PATTERN = r"^[a-f0-9]{32}$"


class PlanAndCreateCopyRequest(StrictFrozenModel):
    """Bounded origin-planning request accepted by MCP."""

    source_root: str = Field(min_length=1, max_length=4_096)
    output_parent: str | None = Field(default=None, min_length=1, max_length=4_096)
    user_request: str = Field(min_length=1, max_length=20_000)
    mode: Literal["live", "replay"]
    idempotency_key: str = Field(pattern=MCP_IDEMPOTENCY_KEY_PATTERN)
    evidence_disclosure_acknowledged: bool = False

    @field_validator("user_request")
    @classmethod
    def require_meaningful_request(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("User request must be nonblank, NUL-free text.")
        return value


class JobHandleRequest(StrictFrozenModel):
    """Read-only request for one opaque durable job handle."""

    job_handle: str = Field(pattern=JOB_HANDLE_PATTERN)


class AnswerClarificationRequest(JobHandleRequest):
    """Exactly bound answer to the sole persisted clarification."""

    expected_revision: int = Field(ge=0)
    question_fingerprint: str = Field(pattern=SHA256_PATTERN)
    answer: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(pattern=MCP_IDEMPOTENCY_KEY_PATTERN)

    @field_validator("answer")
    @classmethod
    def require_meaningful_answer(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Clarification answer must be nonblank, NUL-free text.")
        return value


class ApplyChangeFileRequest(StrictFrozenModel):
    """Provider-free receiver application request."""

    change_file_path: str = Field(min_length=1, max_length=4_096)
    source_root: str = Field(min_length=1, max_length=4_096)
    output_parent: str | None = Field(default=None, min_length=1, max_length=4_096)
    idempotency_key: str = Field(pattern=MCP_IDEMPOTENCY_KEY_PATTERN)


class VerifyResultRequest(StrictFrozenModel):
    """Source-free verification request for one candidate result."""

    result_root: str = Field(min_length=1, max_length=4_096)


class RecreateOriginalRequest(JobHandleRequest):
    """Job-bound no-replace reconstruction request."""

    idempotency_key: str = Field(pattern=MCP_IDEMPOTENCY_KEY_PATTERN)


class McpJobStatus(StrictFrozenModel):
    """One concise projection of the durable FolderRefactorJob authority."""

    schema_version: Literal["name-atlas-mcp-job-status.v1"] = (
        "name-atlas-mcp-job-status.v1"
    )
    status: Literal["accepted", "consent_required", "blocked"]
    message: str = Field(min_length=1, max_length=2_000)
    job_handle: str | None = Field(default=None, pattern=JOB_HANDLE_PATTERN)
    job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    revision: int | None = Field(default=None, ge=0)
    lifecycle: (
        Literal[
            "planning",
            "awaiting_clarification",
            "executing",
            "verified",
            "stale",
            "blocked",
        ]
        | None
    ) = None
    execution_origin: Literal["gpt_planned", "capsule_applied"] | None = None
    provider_kind: Literal["deterministic", "live", "recorded_replay"] | None = None
    active_operation: bool = False
    clarification_question: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )
    clarification_question_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    result_root: str | None = Field(default=None, max_length=4_096)
    receipt_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    organized_tree_commitment: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    blocker_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )

    @model_validator(mode="after")
    def require_status_shape(self) -> McpJobStatus:
        if self.status == "consent_required":
            if (
                any(
                    value is not None
                    for value in (
                        self.job_handle,
                        self.job_id,
                        self.revision,
                        self.lifecycle,
                        self.execution_origin,
                        self.provider_kind,
                        self.result_root,
                        self.receipt_fingerprint,
                        self.organized_tree_commitment,
                        self.blocker_code,
                    )
                )
                or self.active_operation
            ):
                raise ValueError(
                    "Consent refusal cannot expose or imply a job mutation."
                )
            return self
        if (
            self.job_handle is None
            or self.job_id is None
            or self.revision is None
            or self.lifecycle is None
            or self.execution_origin is None
        ) and (self.status != "blocked" or self.blocker_code is None):
            raise ValueError("A job status requires complete durable identity.")
        if self.lifecycle == "awaiting_clarification":
            if (
                self.clarification_question is None
                or self.clarification_question_fingerprint is None
            ):
                raise ValueError("Clarification state requires its exact question.")
        elif (
            self.clarification_question is not None
            or self.clarification_question_fingerprint is not None
        ):
            raise ValueError("Only clarification state can expose a question.")
        if self.status == "blocked" and self.blocker_code is None:
            raise ValueError("A blocked MCP result requires a stable blocker code.")
        if self.execution_origin == "gpt_planned" and self.provider_kind is None:
            raise ValueError("GPT-planned status requires its truthful provider kind.")
        if (
            self.execution_origin == "capsule_applied"
            and self.provider_kind is not None
        ):
            raise ValueError("Capsule-applied status cannot claim a provider kind.")
        return self


class McpChangeFileResult(StrictFrozenModel):
    """Verified transferable Change File identity returned from one job."""

    schema_version: Literal["name-atlas-mcp-change-file-result.v1"] = (
        "name-atlas-mcp-change-file-result.v1"
    )
    status: Literal["verified", "blocked"]
    message: str = Field(min_length=1, max_length=2_000)
    job_handle: str = Field(pattern=JOB_HANDLE_PATTERN)
    change_file_path: str | None = Field(default=None, max_length=4_096)
    change_file_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    originating_receipt_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    blocker_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )

    @model_validator(mode="after")
    def require_status_shape(self) -> McpChangeFileResult:
        identities = (
            self.change_file_path,
            self.change_file_fingerprint,
            self.originating_receipt_fingerprint,
        )
        if self.status == "verified":
            if (
                any(value is None for value in identities)
                or self.blocker_code is not None
            ):
                raise ValueError("Verified Change File output requires all identities.")
        elif self.blocker_code is None or any(
            value is not None for value in identities
        ):
            raise ValueError("Blocked Change File output requires only a blocker.")
        return self


class McpVerificationResult(StrictFrozenModel):
    """Source-free receiver-verification projection."""

    schema_version: Literal["name-atlas-mcp-verification-result.v1"] = (
        "name-atlas-mcp-verification-result.v1"
    )
    status: Literal["verified", "blocked"]
    message: str = Field(min_length=1, max_length=2_000)
    result_root: str = Field(min_length=1, max_length=4_096)
    job_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    receipt_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    organized_tree_commitment: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    failed_check_ids: tuple[
        str,
        ...,
    ] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def require_status_shape(self) -> McpVerificationResult:
        proof = (
            self.job_id,
            self.receipt_fingerprint,
            self.organized_tree_commitment,
        )
        if len(set(self.failed_check_ids)) != len(self.failed_check_ids):
            raise ValueError("Failed verification check IDs must be unique.")
        if any(
            not item
            or len(item) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in self.failed_check_ids
        ):
            raise ValueError("Failed verification check IDs must be bounded text.")
        if self.status == "verified":
            if any(value is None for value in proof) or self.failed_check_ids:
                raise ValueError("Verified output requires proof and no failures.")
        elif any(value is not None for value in proof) or not self.failed_check_ids:
            raise ValueError("Blocked output requires failures and no success proof.")
        return self


class McpReconstructionResult(StrictFrozenModel):
    """Exact non-destructive original-layout reconstruction result."""

    schema_version: Literal["name-atlas-mcp-reconstruction-result.v1"] = (
        "name-atlas-mcp-reconstruction-result.v1"
    )
    status: Literal["verified", "blocked"]
    message: str = Field(min_length=1, max_length=2_000)
    result_root: str = Field(max_length=4_096)
    destination: str = Field(max_length=4_096)
    receipt_fingerprint: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    source_commitment: str | None = Field(default=None, pattern=SHA256_PATTERN)
    restored_file_count: int | None = Field(default=None, ge=1, le=500)
    restored_bytes: int | None = Field(default=None, ge=0)
    restored_empty_directory_count: int | None = Field(
        default=None,
        ge=0,
        le=1_000,
    )
    blocker_code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_:-]{1,128}$",
    )

    @model_validator(mode="after")
    def require_status_shape(self) -> McpReconstructionResult:
        proof = (
            self.receipt_fingerprint,
            self.source_commitment,
            self.restored_file_count,
            self.restored_bytes,
            self.restored_empty_directory_count,
        )
        if self.status == "verified":
            if any(value is None for value in proof) or self.blocker_code is not None:
                raise ValueError("Verified reconstruction requires complete proof.")
        elif self.blocker_code is None or any(value is not None for value in proof):
            raise ValueError("Blocked reconstruction requires only a blocker.")
        return self
