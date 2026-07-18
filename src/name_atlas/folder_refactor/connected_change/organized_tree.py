"""Path-sensitive commitments for completed organized data trees."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from name_atlas.folder_refactor.serialization import canonical_sha256

HASH_CHUNK_SIZE = 1024 * 1024
SHA256_PATTERN = r"^[a-f0-9]{64}$"


class OrganizedTreeError(ValueError):
    """The supplied data tree cannot produce a supported snapshot."""


class OrganizedTreeCommitmentMismatch(OrganizedTreeError):
    """An organized data tree does not have the required commitment."""

    blocker_id = "organized_tree_commitment_mismatch"


class _StrictFrozenOrganizedTreeModel(BaseModel):
    """Immutable, fail-closed base for organized-tree records."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OrganizedTreeMember(_StrictFrozenOrganizedTreeModel):
    """One path-sensitive regular file or explicit empty directory."""

    member_kind: Literal["regular_file", "empty_directory"]
    relative_path: str = Field(min_length=1, max_length=4_096)
    size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_kind_specific_fields(self) -> Self:
        """Bind payload metadata to files and forbid it on directories."""

        _require_relative_posix_path(self.relative_path)
        if self.member_kind == "regular_file":
            if self.size is None or self.sha256 is None:
                raise ValueError(
                    "Regular organized-tree members require size and SHA-256."
                )
        elif self.size is not None or self.sha256 is not None:
            raise ValueError(
                "Empty organized-tree directories cannot carry payload metadata."
            )
        return self


class OrganizedTreeSnapshot(_StrictFrozenOrganizedTreeModel):
    """Canonical complete description of every member below one data root."""

    schema_version: Literal["organized-tree.v1"] = "organized-tree.v1"
    members: tuple[OrganizedTreeMember, ...] = ()
    commitment: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def require_canonical_complete_list(self) -> Self:
        """Reject unordered, duplicate, or incorrectly committed snapshots."""

        sort_keys = [
            (member.relative_path, member.member_kind) for member in self.members
        ]
        if sort_keys != sorted(sort_keys):
            raise ValueError("Organized-tree members must be canonically sorted.")
        paths = [member.relative_path for member in self.members]
        if len(paths) != len(set(paths)):
            raise ValueError("Organized-tree member paths must be unique.")
        expected = compute_organized_tree_commitment(self.members)
        if self.commitment != expected:
            raise ValueError(
                "Organized-tree commitment does not match its complete member list."
            )
        return self

    @property
    def file_count(self) -> int:
        """Return the number of regular files in the snapshot."""

        return sum(member.member_kind == "regular_file" for member in self.members)

    @property
    def empty_directory_count(self) -> int:
        """Return the number of explicit empty directories in the snapshot."""

        return sum(member.member_kind == "empty_directory" for member in self.members)

    @property
    def total_bytes(self) -> int:
        """Return the total byte size of all regular members."""

        return sum(member.size or 0 for member in self.members)


def compute_organized_tree_commitment(
    members: tuple[OrganizedTreeMember, ...],
) -> str:
    """Hash the canonical JSON list of path-sensitive organized members."""

    canonical_members = sorted(
        members,
        key=lambda member: (member.relative_path, member.member_kind),
    )
    return canonical_sha256(
        [member.model_dump(mode="json") for member in canonical_members]
    )


def scan_organized_tree(data_root: Path) -> OrganizedTreeSnapshot:
    """Enumerate and commit every supported member below ``data_root``."""

    root = _require_data_root(data_root)
    members: list[OrganizedTreeMember] = []

    def fail_walk(error: OSError) -> None:
        raise OrganizedTreeError(
            f"Organized data directory cannot be read: {error.filename}"
        )

    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=fail_walk,
    ):
        directory_path = Path(directory)
        directory_names.sort()
        file_names.sort()

        for name in directory_names:
            child = directory_path / name
            relative_path = child.relative_to(root).as_posix()
            _require_relative_posix_path(relative_path)
            _require_directory(child, relative_path)

        if directory_path != root and not directory_names and not file_names:
            relative_path = directory_path.relative_to(root).as_posix()
            _require_relative_posix_path(relative_path)
            members.append(
                OrganizedTreeMember(
                    member_kind="empty_directory",
                    relative_path=relative_path,
                )
            )

        for name in file_names:
            path = directory_path / name
            relative_path = path.relative_to(root).as_posix()
            _require_relative_posix_path(relative_path)
            size, digest = _hash_regular_file(path, relative_path)
            members.append(
                OrganizedTreeMember(
                    member_kind="regular_file",
                    relative_path=relative_path,
                    size=size,
                    sha256=digest,
                )
            )

    members.sort(key=lambda member: (member.relative_path, member.member_kind))
    member_tuple = tuple(members)
    return OrganizedTreeSnapshot(
        members=member_tuple,
        commitment=compute_organized_tree_commitment(member_tuple),
    )


def require_organized_tree_commitment(
    snapshot: OrganizedTreeSnapshot,
    expected_commitment: str,
) -> OrganizedTreeSnapshot:
    """Return ``snapshot`` only when its commitment is exactly expected."""

    if (
        not isinstance(expected_commitment, str)
        or re.fullmatch(SHA256_PATTERN, expected_commitment) is None
    ):
        raise OrganizedTreeError(
            "Expected organized-tree commitment must be lowercase SHA-256 text."
        )
    if snapshot.commitment != expected_commitment:
        raise OrganizedTreeCommitmentMismatch(
            f"{OrganizedTreeCommitmentMismatch.blocker_id}: "
            f"expected {expected_commitment}, observed {snapshot.commitment}"
        )
    return snapshot


def _require_data_root(data_root: Path) -> Path:
    if not isinstance(data_root, Path):
        raise OrganizedTreeError("Organized data root must be a pathlib.Path.")
    try:
        metadata = data_root.lstat()
    except OSError as exc:
        raise OrganizedTreeError(
            f"Organized data root cannot be inspected: {data_root}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OrganizedTreeError(
            "Organized data root must be an existing non-symlink directory: "
            f"{data_root}"
        )
    try:
        return data_root.resolve(strict=True)
    except OSError as exc:
        raise OrganizedTreeError(
            f"Organized data root cannot be resolved: {data_root}"
        ) from exc


def _require_directory(path: Path, relative_path: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OrganizedTreeError(
            f"Organized directory cannot be inspected: {relative_path}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise OrganizedTreeError(
            f"Symlink directories are unsupported: {relative_path}"
        )
    if not stat.S_ISDIR(metadata.st_mode):
        raise OrganizedTreeError(
            f"Special filesystem member is unsupported: {relative_path}"
        )


def _hash_regular_file(path: Path, relative_path: str) -> tuple[int, str]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OrganizedTreeError(
            f"Organized member cannot be inspected: {relative_path}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise OrganizedTreeError(f"Symlink files are unsupported: {relative_path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise OrganizedTreeError(
            f"Special filesystem member is unsupported: {relative_path}"
        )
    if metadata.st_nlink > 1:
        raise OrganizedTreeError(f"Hard-linked files are unsupported: {relative_path}")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OrganizedTreeError(
            f"Organized file cannot be opened safely: {relative_path}"
        ) from exc

    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OrganizedTreeError(
                f"Organized member is not regular: {relative_path}"
            )
        if before.st_nlink > 1:
            raise OrganizedTreeError(
                f"Hard-linked files are unsupported: {relative_path}"
            )
        size = 0
        while chunk := os.read(descriptor, HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_nlink,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_nlink,
        )
        if identity_before != identity_after or size != after.st_size:
            raise OrganizedTreeError(
                f"Organized file changed while being read: {relative_path}"
            )
    finally:
        os.close(descriptor)
    return size, digest.hexdigest()


def _require_relative_posix_path(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OrganizedTreeError(
            f"Organized member path is not valid UTF-8: {value!r}"
        ) from exc
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise OrganizedTreeError(
            f"Organized member path must be relative POSIX syntax: {value!r}"
        )
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise OrganizedTreeError(
            f"Organized member path contains a control character: {value!r}"
        )
    if any(segment in {"", ".", ".."} for segment in value.split("/")):
        raise OrganizedTreeError(
            f"Organized member path contains an empty or dot segment: {value!r}"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise OrganizedTreeError(
            f"Organized member path is not normalized POSIX syntax: {value!r}"
        )
