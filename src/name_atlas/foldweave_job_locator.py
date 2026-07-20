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
    FolderRefactorJobV3Store,
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


class FoldweaveJobLocator:
    """Resolve jobs without mirroring, migrating, or mutating their authority."""

    def __init__(self, jobs_root: Path) -> None:
        candidate = jobs_root.expanduser()
        if not candidate.is_absolute():
            raise ValueError("Foldweave jobs root must be absolute.")
        self.jobs_root = Path(os.path.abspath(candidate))

    def discover(self) -> tuple[LocatedFoldweaveJob, ...]:
        """Strictly load every immediate JSON authority under the jobs root."""

        if not os.path.lexists(self.jobs_root):
            return ()
        metadata = self.jobs_root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise FoldweaveJobLocatorError(
                "jobs_root_invalid",
                "Foldweave jobs root must be a real directory.",
            )

        resolved_root = self.jobs_root.resolve(strict=True)
        by_id: dict[str, LocatedFoldweaveJob] = {}
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
                job = FolderRefactorJobV3Store(resolved).inspect()
            except FolderJobV3LoadError as exc:
                raise FoldweaveJobLocatorError(
                    "job_authority_invalid",
                    "Foldweave job candidate is not a strict v3 record: "
                    f"{candidate.name}",
                ) from exc
            previous = by_id.get(job.job_id)
            if previous is not None:
                raise FoldweaveJobLocatorError(
                    "duplicate_job_id",
                    "More than one durable authority contains the requested job ID.",
                )
            by_id[job.job_id] = LocatedFoldweaveJob(path=resolved, job=job)
        return tuple(by_id[job_id] for job_id in sorted(by_id))

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
        matches = tuple(item for item in self.discover() if item.job.job_id == job_id)
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
