"""Fail-closed source-package snapshots and safe relative-path primitives."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from enum import StrEnum
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from name_atlas.domain import ContentRole, MemberKind

HASH_CHUNK_SIZE = 1024 * 1024


class SourceError(ValueError):
    """The source tree is outside the supported local package contract."""


class ControlRole(StrEnum):
    """Roles of the two declared control files."""

    METADATA = "metadata"
    NORMALIZATION = "normalization"


class SourceMember(BaseModel):
    """One immutable member in the initial source snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    relative_path: str = Field(min_length=1, max_length=4_096)
    role: ContentRole | ControlRole
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: MemberKind


class SourceSnapshot(BaseModel):
    """Complete deterministic inventory of every source-package member."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_root: Path
    members: tuple[SourceMember, ...] = Field(min_length=1)
    commitment: str = Field(pattern=r"^[a-f0-9]{64}$")


def validate_relative_path(value: str) -> str:
    """Validate raw POSIX syntax before PurePosixPath can normalize it away."""

    if not isinstance(value, str) or not value:
        raise SourceError("Relative path must be a non-empty string.")
    if "\x00" in value:
        raise SourceError(f"Relative path contains NUL: {value!r}")
    if "\\" in value:
        raise SourceError(f"Relative path contains a backslash: {value!r}")
    if value.startswith("/"):
        raise SourceError(f"Absolute path is unsupported: {value!r}")
    raw_segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in raw_segments):
        raise SourceError(f"Relative path contains an empty/dot segment: {value!r}")
    normalized = PurePosixPath(value)
    if normalized.is_absolute() or normalized.as_posix() != value:
        raise SourceError(f"Relative path is not in normalized POSIX form: {value!r}")
    return value


def snapshot_tree(root: Path) -> SourceSnapshot:
    """Snapshot every supported regular file with streamed SHA-256."""

    resolved_root = _require_root(root)
    members: list[SourceMember] = []
    for directory, directory_names, file_names in os.walk(
        resolved_root,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in sorted(directory_names):
            child = directory_path / name
            _require_directory(child, resolved_root)
        for name in sorted(file_names):
            path = directory_path / name
            relative_path = path.relative_to(resolved_root).as_posix()
            validate_relative_path(relative_path)
            role, kind = _classify_path(relative_path)
            try:
                file_mode = path.lstat().st_mode
            except OSError as exc:
                raise SourceError(
                    f"Source member cannot be inspected: {relative_path}"
                ) from exc
            if stat.S_ISLNK(file_mode) or not stat.S_ISREG(file_mode):
                raise SourceError(
                    f"Unsupported symlink or special-file member: {relative_path}"
                )
            size, digest = _hash_regular_file(path)
            members.append(
                SourceMember(
                    relative_path=relative_path,
                    role=role,
                    size=size,
                    sha256=digest,
                    kind=kind,
                )
            )
    members.sort(key=lambda member: member.relative_path)
    if not members:
        raise SourceError("Selected source package contains no supported members.")
    commitment_value = json.dumps(
        [member.model_dump(mode="json") for member in members],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return SourceSnapshot(
        source_root=resolved_root,
        members=tuple(members),
        commitment=hashlib.sha256(commitment_value).hexdigest(),
    )


def read_member_bytes(root: Path, member: SourceMember) -> bytes:
    """Read one snapshotted control file and prove it still matches the snapshot."""

    path = root / member.relative_path
    size, digest, data = _read_regular_file(path, retain=True)
    if size != member.size or digest != member.sha256:
        raise SourceError(
            f"Source member changed during import: {member.relative_path}"
        )
    return data


def _require_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise SourceError("Source root must be a pathlib.Path.")
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise SourceError(f"Source root cannot be inspected: {root}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise SourceError(f"Source root must be a non-symlink directory: {root}")
    return root.resolve(strict=True)


def _require_directory(path: Path, root: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SourceError(f"Directory cannot be inspected: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        relative = path.relative_to(root).as_posix()
        raise SourceError(f"Unsupported non-directory or symlink member: {relative}")
    relative = path.relative_to(root).as_posix()
    if not _directory_is_supported(relative):
        raise SourceError(f"Unexpected directory in source package: {relative}")


def _directory_is_supported(relative_path: str) -> bool:
    allowed_exact = {
        "objects",
        "manualNormalization",
        "manualNormalization/access",
        "manualNormalization/preservation",
        "metadata",
    }
    allowed_prefixes = (
        "objects/",
        "manualNormalization/access/",
        "manualNormalization/preservation/",
    )
    return relative_path in allowed_exact or relative_path.startswith(allowed_prefixes)


def _classify_path(
    relative_path: str,
) -> tuple[ContentRole | ControlRole, MemberKind]:
    if relative_path == "metadata/metadata.csv":
        return ControlRole.METADATA, MemberKind.DECLARED_CONTROL_FILE
    if relative_path == "normalization.csv":
        return ControlRole.NORMALIZATION, MemberKind.DECLARED_CONTROL_FILE
    if relative_path.startswith("objects/"):
        return ContentRole.ORIGINAL, MemberKind.CONTENT_OBJECT
    if relative_path.startswith("manualNormalization/access/"):
        return ContentRole.ACCESS, MemberKind.CONTENT_OBJECT
    if relative_path.startswith("manualNormalization/preservation/"):
        return ContentRole.PRESERVATION, MemberKind.CONTENT_OBJECT
    raise SourceError(f"Unexpected regular file in source package: {relative_path}")


def _hash_regular_file(path: Path) -> tuple[int, str]:
    size, digest, _ = _read_regular_file(path, retain=False)
    return size, digest


def _read_regular_file(path: Path, *, retain: bool) -> tuple[int, str, bytes]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceError(f"Source member cannot be opened safely: {path}") from exc

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SourceError(f"Source member is not a regular file: {path}")
        size = 0
        while chunk := os.read(descriptor, HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            if retain:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or size != after.st_size:
            raise SourceError(f"Source member changed while being read: {path}")
    finally:
        os.close(descriptor)
    return size, digest.hexdigest(), b"".join(chunks)
