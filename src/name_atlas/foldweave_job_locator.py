"""Read-only discovery of durable Foldweave v3 jobs by embedded identity."""

from __future__ import annotations

import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderJobV3LoadError,
    FolderRefactorJobV3,
    UnsupportedPreFinalFolderJobV3,
    load_folder_job_routing_record_v3,
)

_JOB_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


class FoldweaveJobLocatorError(RuntimeError):
    """Stable failure raised by strict read-only job discovery."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class LocatedFoldweaveJob:
    """One strict v3 job and its canonical authority path."""

    path: Path
    job: FolderRefactorJobV3


@dataclass(frozen=True, slots=True)
class UnsupportedLocatedFoldweaveJob:
    """One preserved pre-final authority usable only for safe routing."""

    path: Path
    record: UnsupportedPreFinalFolderJobV3


@dataclass(frozen=True, slots=True)
class FoldweaveJobRegistrySnapshot:
    """Strict current jobs plus isolated, non-resumable pre-final headers."""

    current: tuple[LocatedFoldweaveJob, ...]
    unsupported: tuple[UnsupportedLocatedFoldweaveJob, ...]


class FoldweaveJobLocator:
    """Resolve jobs without mirroring, migrating, or mutating their authority."""

    def __init__(self, jobs_root: Path) -> None:
        candidate = jobs_root.expanduser()
        if not candidate.is_absolute():
            raise ValueError("Foldweave jobs root must be absolute.")
        self.jobs_root = Path(os.path.abspath(candidate))

    def discover(self) -> tuple[LocatedFoldweaveJob, ...]:
        """Return current jobs while preserving classified pre-final records."""

        return self.inspect_registry().current

    def inspect_registry(self) -> FoldweaveJobRegistrySnapshot:
        """Classify every authority without treating pre-final state as current."""

        if not os.path.lexists(self.jobs_root):
            return FoldweaveJobRegistrySnapshot(current=(), unsupported=())
        metadata = self.jobs_root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise FoldweaveJobLocatorError(
                "jobs_root_invalid",
                "Foldweave jobs root must be a real directory.",
            )

        resolved_root = self.jobs_root.resolve(strict=True)
        by_id: dict[str, LocatedFoldweaveJob] = {}
        unsupported_by_id: dict[str, UnsupportedLocatedFoldweaveJob] = {}
        for candidate in sorted(self.jobs_root.iterdir(), key=lambda path: path.name):
            if candidate.suffix.casefold() != ".json":
                continue
            candidate_metadata = candidate.lstat()
            if stat.S_ISLNK(candidate_metadata.st_mode) or not stat.S_ISREG(
                candidate_metadata.st_mode
            ):
                raise FoldweaveJobLocatorError(
                    "job_authority_invalid",
                    f"Foldweave job candidate is not a regular file: {candidate.name}",
                )
            resolved = candidate.resolve(strict=True)
            if resolved.parent != resolved_root:
                raise FoldweaveJobLocatorError(
                    "job_authority_outside_root",
                    "Foldweave job candidate resolves outside the jobs root.",
                )
            try:
                record = load_folder_job_routing_record_v3(resolved)
            except FolderJobV3LoadError as exc:
                raise FoldweaveJobLocatorError(
                    "job_authority_invalid",
                    "Foldweave job candidate is not a strict v3 record: "
                    f"{candidate.name}",
                ) from exc
            job_id = record.job_id
            if job_id in by_id or job_id in unsupported_by_id:
                raise FoldweaveJobLocatorError(
                    "duplicate_job_id",
                    "More than one durable authority contains the requested job ID.",
                )
            if isinstance(record, FolderRefactorJobV3):
                by_id[job_id] = LocatedFoldweaveJob(path=resolved, job=record)
            else:
                unsupported_by_id[job_id] = UnsupportedLocatedFoldweaveJob(
                    path=resolved,
                    record=record,
                )
        return FoldweaveJobRegistrySnapshot(
            current=tuple(by_id[item] for item in sorted(by_id)),
            unsupported=tuple(
                unsupported_by_id[item] for item in sorted(unsupported_by_id)
            ),
        )

    def resolve(self, job_id: str) -> LocatedFoldweaveJob:
        """Resolve exactly one strict v3 authority by its embedded UUID4 hex ID."""

        if _JOB_ID_PATTERN.fullmatch(job_id) is None:
            raise FoldweaveJobLocatorError(
                "job_id_invalid",
                "Foldweave job ID must be lowercase UUID4 hex.",
            )
        try:
            parsed = uuid.UUID(hex=job_id)
        except ValueError as exc:
            raise FoldweaveJobLocatorError(
                "job_id_invalid",
                "Foldweave job ID must be lowercase UUID4 hex.",
            ) from exc
        if parsed.version != 4 or parsed.hex != job_id:
            raise FoldweaveJobLocatorError(
                "job_id_invalid",
                "Foldweave job ID must be lowercase UUID4 hex.",
            )
        registry = self.inspect_registry()
        matches = tuple(item for item in registry.current if item.job.job_id == job_id)
        unsupported_matches = tuple(
            item for item in registry.unsupported if item.record.job_id == job_id
        )
        if unsupported_matches:
            raise FoldweaveJobLocatorError(
                "job_requires_fresh_start",
                "This preserved pre-final Foldweave job cannot be resumed; "
                "create a fresh job.",
            )
        if not matches:
            raise FoldweaveJobLocatorError(
                "job_not_found",
                "No durable Foldweave v3 job has the requested ID.",
            )
        if len(matches) != 1:
            raise FoldweaveJobLocatorError(
                "duplicate_job_id",
                "More than one durable authority contains the requested job ID.",
            )
        return matches[0]
