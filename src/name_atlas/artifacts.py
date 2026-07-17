"""Typed proof artifacts for one copy-only staging transaction."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from name_atlas.decisions import HumanDecision
from name_atlas.domain import ContentRole, PackageValidationResult
from name_atlas.source import SourceSnapshot

oslo_tz = ZoneInfo("Europe/Oslo")


class ProofStatus(StrEnum):
    """Serialized stage proof state."""

    VERIFIED = "verified"
    BLOCKED = "blocked"


class PathMapRow(BaseModel):
    """One complete content-object identity mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    family_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_identifier: str = Field(min_length=1, max_length=64)
    role: ContentRole
    source_path: str
    target_path: str
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ControlFileProof(BaseModel):
    """Proof that only declared path-reference fields changed."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    logical_path: str
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    staged_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rewritten_fields: tuple[str, ...] = Field(min_length=1)
    non_path_fields_unchanged: bool


class VerificationCheck(BaseModel):
    """One deterministic proof check displayed by the Proof UI."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    check_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    passed: bool
    detail: str = Field(min_length=1, max_length=1_000)


class VerificationReport(BaseModel):
    """Complete serialized authority for the staged Proof view."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: str = "verification-report.v1"
    status: ProofStatus
    claim: str | None
    generated_at: datetime
    staged_location: str
    source_snapshot_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    prestaging_snapshot_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    postcopy_snapshot_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_unchanged: bool
    content_object_count: int = Field(ge=0)
    content_bytes: int = Field(ge=0)
    control_files: tuple[ControlFileProof, ...]
    map_row_count: int = Field(ge=0)
    checks: tuple[VerificationCheck, ...] = Field(min_length=1)
    bagit_validation: PackageValidationResult
    artifact_paths: tuple[str, ...] = Field(min_length=1)
    blockers: tuple[str, ...]


class StageArtifacts(BaseModel):
    """In-memory artifact set returned after a staging transaction."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    forward_map: tuple[PathMapRow, ...]
    reverse_map: tuple[PathMapRow, ...]
    report: VerificationReport


def canonical_json_bytes(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    """Serialize one artifact deterministically as UTF-8 with a final newline."""

    if isinstance(value, BaseModel):
        serializable = value.model_dump(mode="json")
    else:
        serializable = value
    rendered = json.dumps(
        serializable,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


def write_source_snapshot(path: Path, snapshot: SourceSnapshot) -> None:
    """Write the complete initial source ledger without payload bytes."""

    _write_new(path, canonical_json_bytes(snapshot))


def write_decision_ledger(path: Path, decisions: tuple[HumanDecision, ...]) -> None:
    """Write every family-level human decision."""

    _write_new(
        path,
        canonical_json_bytes(
            {
                "schema_version": "decision-ledger.v1",
                "decisions": [
                    decision.model_dump(mode="json") for decision in decisions
                ],
            }
        ),
    )


def write_path_map(
    path: Path,
    rows: tuple[PathMapRow, ...],
    *,
    reverse: bool,
) -> None:
    """Write one deterministic logical forward or reverse CSV map."""

    import io

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    if reverse:
        writer.writerow(
            (
                "family_id",
                "canonical_identifier",
                "role",
                "target_path",
                "source_path",
                "size",
                "sha256",
            )
        )
    else:
        writer.writerow(
            (
                "family_id",
                "canonical_identifier",
                "role",
                "source_path",
                "target_path",
                "size",
                "sha256",
            )
        )
    for row in rows:
        common = (row.family_id, row.canonical_identifier, row.role.value)
        paths = (
            (row.target_path, row.source_path)
            if reverse
            else (row.source_path, row.target_path)
        )
        writer.writerow((*common, *paths, row.size, row.sha256))
    _write_new(path, stream.getvalue().encode())


def write_verification_report(path: Path, report: VerificationReport) -> None:
    """Write a new verification report."""

    _write_new(path, canonical_json_bytes(report))


def replace_verification_report(path: Path, report: VerificationReport) -> None:
    """Atomically replace only the product-owned pending verification report."""

    temporary = path.with_name(f".{path.name}.tmp")
    try:
        _write_new(temporary, canonical_json_bytes(report))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_summary(path: Path, *, content_objects: int, content_bytes: int) -> None:
    """Write a stable human-readable pointer to the serialized final report."""

    text = (
        "# Reversible Name Atlas verification summary\n\n"
        f"- Content objects staged copy-only: {content_objects}\n"
        f"- Content bytes staged: {content_bytes}\n"
        "- Complete deterministic and BagIt results: "
        "`verification_report.json`\n"
        "- Forward and reverse logical maps: exact content-object inverses\n"
        "- Source payload bytes are not stored in proof artifacts\n"
    )
    _write_new(path, text.encode())


def _write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
