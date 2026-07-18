"""Safe deterministic I/O for generic-folder portable proof artifacts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
from collections.abc import Collection, Iterable
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from name_atlas.folder_refactor.inventory import HASH_CHUNK_SIZE
from name_atlas.folder_refactor.receipt_contracts import (
    FolderArtifactCommitment,
    FolderPathMapRow,
    FolderStagedDataMember,
)
from name_atlas.folder_refactor.serialization import canonical_json_bytes

SOURCE_SNAPSHOT_PATH = "name-atlas/source_snapshot.json"
USER_REQUEST_PATH = "name-atlas/user_request.json"
EVIDENCE_LEDGER_PATH = "name-atlas/evidence_ledger.json"
ACCEPTED_PLAN_PATH = "name-atlas/accepted_plan.json"
REFERENCE_GRAPH_PATH = "name-atlas/reference_graph.json"
FORWARD_PATH_MAP_PATH = "name-atlas/forward_path_map.csv"
REVERSE_PATH_MAP_PATH = "name-atlas/reverse_path_map.csv"
CHANGE_LEDGER_PATH = "name-atlas/change_ledger.json"
VERIFICATION_REPORT_PATH = "name-atlas/verification_report.json"
CHANGE_RECEIPT_PATH = "name-atlas/change_receipt.json"
PROOF_AND_RESTORE_HTML_PATH = "name-atlas/proof_and_restore.html"
ORIGINAL_CONTENT_ROOT = "name-atlas/original-content"
BAGIT_PATH = "bagit.txt"
BAG_INFO_PATH = "bag-info.txt"
PAYLOAD_MANIFEST_PATH = "manifest-sha256.txt"
TAG_MANIFEST_PATH = "tagmanifest-sha256.txt"

RECEIPT_COMMITTED_STATIC_PATHS = tuple(
    sorted(
        {
            ACCEPTED_PLAN_PATH,
            BAG_INFO_PATH,
            BAGIT_PATH,
            CHANGE_LEDGER_PATH,
            EVIDENCE_LEDGER_PATH,
            FORWARD_PATH_MAP_PATH,
            PAYLOAD_MANIFEST_PATH,
            REFERENCE_GRAPH_PATH,
            REVERSE_PATH_MAP_PATH,
            SOURCE_SNAPSHOT_PATH,
            USER_REQUEST_PATH,
            VERIFICATION_REPORT_PATH,
        }
    )
)

FORWARD_PATH_MAP_HEADER = (
    "file_id",
    "original_path",
    "result_path",
    "original_size",
    "original_sha256",
    "result_size",
    "result_sha256",
    "protected",
    "markdown_rewritten",
)
REVERSE_PATH_MAP_HEADER = (
    "file_id",
    "result_path",
    "original_path",
    "original_size",
    "original_sha256",
    "result_size",
    "result_sha256",
    "protected",
    "markdown_rewritten",
)

_SHA256_TEXT = re.compile(r"[a-f0-9]{64}\Z")
_NONNEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_Model = TypeVar("_Model", bound=BaseModel)


class FolderPortableArtifactError(ValueError):
    """Portable proof bytes or filesystem state violate the folder contract."""


def canonical_portable_json_bytes(value: BaseModel | Any) -> bytes:
    """Render exact compact UTF-8 JSON with no trailing newline."""

    return canonical_json_bytes(value)


def strict_json_object(data: bytes) -> dict[str, Any]:
    """Parse one strict JSON object, rejecting duplicate keys and non-finite values."""

    if not isinstance(data, bytes):
        raise FolderPortableArtifactError("Portable JSON must be bytes.")
    try:
        value = json.loads(
            data.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FolderPortableArtifactError(
            "Portable artifact is not strict JSON."
        ) from exc
    if not isinstance(value, dict):
        raise FolderPortableArtifactError("Portable JSON root must be an object.")
    return value


def parse_portable_model(data: bytes, model_type: type[_Model]) -> _Model:
    """Strictly parse one portable JSON object through a Pydantic contract."""

    strict_json_object(data)
    try:
        return model_type.model_validate_json(data, strict=True)
    except ValidationError as exc:
        raise FolderPortableArtifactError(
            "Portable JSON does not satisfy its declared schema."
        ) from exc


def write_new_portable_json(
    root: Path,
    relative_path: str,
    value: BaseModel | Any,
) -> bytes:
    """Exclusively write one canonical JSON artifact below a real root."""

    payload = canonical_portable_json_bytes(value)
    _write_new_regular_bytes(root, relative_path, payload)
    return payload


def read_regular_bytes(root: Path, relative_path: str) -> bytes:
    """Read stable exact bytes from one regular file without following symlinks."""

    path = _require_regular_relative_file(root, relative_path)
    payload, _size, _digest = _read_regular_file(path, relative_path, retain=True)
    if payload is None:
        raise AssertionError("Retained artifact bytes were unexpectedly absent.")
    return payload


def regular_file_measurement(root: Path, relative_path: str) -> tuple[int, str]:
    """Return stable byte size and lowercase SHA-256 for one regular file."""

    path = _require_regular_relative_file(root, relative_path)
    _payload, size, digest = _read_regular_file(path, relative_path, retain=False)
    return size, digest


def render_folder_path_map(
    rows: Iterable[FolderPathMapRow],
    *,
    reverse: bool,
) -> bytes:
    """Render one canonical forward or reverse generic-folder path map."""

    canonical_rows = tuple(rows)
    _validate_map_rows(canonical_rows)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(REVERSE_PATH_MAP_HEADER if reverse else FORWARD_PATH_MAP_HEADER)
    for row in canonical_rows:
        paths = (
            (row.result_path, row.original_path)
            if reverse
            else (row.original_path, row.result_path)
        )
        writer.writerow(
            (
                row.file_id,
                *paths,
                row.original_size,
                row.original_sha256,
                row.result_size,
                row.result_sha256,
                _render_boolean(row.protected),
                _render_boolean(row.markdown_rewritten),
            )
        )
    return stream.getvalue().encode("utf-8")


def parse_folder_path_map(
    data: bytes,
    *,
    reverse: bool,
) -> tuple[FolderPathMapRow, ...]:
    """Strictly parse and canonicalize one generic-folder path map."""

    label = "reverse" if reverse else "forward"
    if not isinstance(data, bytes):
        raise FolderPortableArtifactError(f"{label} path map must be bytes.")
    if not data.endswith(b"\n") or b"\r" in data:
        raise FolderPortableArtifactError(
            f"{label} path map must use canonical LF line endings."
        )
    try:
        text = data.decode("utf-8", errors="strict")
        raw_rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise FolderPortableArtifactError(
            f"{label} path map is not strict UTF-8 CSV."
        ) from exc
    expected_header = REVERSE_PATH_MAP_HEADER if reverse else FORWARD_PATH_MAP_HEADER
    if not raw_rows or tuple(raw_rows[0]) != expected_header:
        raise FolderPortableArtifactError(f"{label} path map header is invalid.")
    if len(raw_rows) == 1:
        raise FolderPortableArtifactError(f"{label} path map contains no rows.")

    parsed: list[FolderPathMapRow] = []
    for values in raw_rows[1:]:
        if len(values) != len(expected_header):
            raise FolderPortableArtifactError(
                f"{label} path map row has an invalid field count."
            )
        (
            file_id,
            first_path,
            second_path,
            original_size,
            original_sha256,
            result_size,
            result_sha256,
            protected,
            markdown_rewritten,
        ) = values
        try:
            row = FolderPathMapRow(
                file_id=file_id,
                original_path=second_path if reverse else first_path,
                result_path=first_path if reverse else second_path,
                original_size=_parse_nonnegative_integer(original_size),
                original_sha256=original_sha256,
                result_size=_parse_nonnegative_integer(result_size),
                result_sha256=result_sha256,
                protected=_parse_boolean(protected),
                markdown_rewritten=_parse_boolean(markdown_rewritten),
            )
        except (ValidationError, ValueError) as exc:
            raise FolderPortableArtifactError(
                f"{label} path map row is invalid."
            ) from exc
        parsed.append(row)
    result = tuple(parsed)
    _validate_map_rows(result)
    if render_folder_path_map(result, reverse=reverse) != data:
        raise FolderPortableArtifactError(
            f"{label} path map is not in canonical serialized form."
        )
    return result


def staged_data_members(root: Path) -> tuple[FolderStagedDataMember, ...]:
    """Enumerate and hash every regular member below ``data/`` safely."""

    bag_root = _require_real_directory(root, label="Portable result root")
    data_root = _require_real_directory(bag_root / "data", label="data directory")
    members: list[FolderStagedDataMember] = []
    pending = [data_root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FolderPortableArtifactError(
                "Staged data directory cannot be enumerated."
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative_path = path.relative_to(data_root).as_posix()
            _require_relative_posix(relative_path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FolderPortableArtifactError(
                    "Staged data member cannot be inspected."
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise FolderPortableArtifactError("Staged data contains a symlink.")
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise FolderPortableArtifactError(
                    "Staged data contains a special file."
                )
            _payload, size, digest = _read_regular_file(
                path,
                relative_path,
                retain=False,
                expected_metadata=metadata,
            )
            members.append(
                FolderStagedDataMember(
                    path=relative_path,
                    size=size,
                    sha256=digest,
                )
            )
    if not members:
        raise FolderPortableArtifactError("Staged data contains no regular files.")
    result = tuple(sorted(members, key=lambda item: item.path))
    paths = tuple(item.path for item in result)
    if len(paths) != len(set(paths)):
        raise FolderPortableArtifactError("Staged data paths are not unique.")
    return result


def staged_data_commitment(members: Collection[FolderStagedDataMember]) -> str:
    """Hash the canonical sorted complete staged-data member list."""

    ordered = tuple(members)
    paths = tuple(item.path for item in ordered)
    if not ordered or paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise FolderPortableArtifactError(
            "Staged data members must be nonempty, path-sorted, and unique."
        )
    payload = [item.model_dump(mode="json") for item in ordered]
    return hashlib.sha256(canonical_portable_json_bytes(payload)).hexdigest()


def artifact_commitments(
    root: Path,
    *,
    static_paths: Collection[str] = RECEIPT_COMMITTED_STATIC_PATHS,
    original_content_file_ids: Collection[str] = (),
) -> tuple[FolderArtifactCommitment, ...]:
    """Commit an explicit static allowlist plus exact original-content members."""

    requested_static = tuple(static_paths)
    if len(requested_static) != len(set(requested_static)):
        raise FolderPortableArtifactError("Static commitment paths are duplicated.")
    unknown_static = set(requested_static) - set(RECEIPT_COMMITTED_STATIC_PATHS)
    if unknown_static:
        raise FolderPortableArtifactError("Static commitment path is not allowed.")
    for relative_path in requested_static:
        _require_relative_posix(relative_path)

    expected_ids = tuple(original_content_file_ids)
    if len(expected_ids) != len(set(expected_ids)) or any(
        _SHA256_TEXT.fullmatch(file_id) is None for file_id in expected_ids
    ):
        raise FolderPortableArtifactError(
            "Original-content file IDs must be unique lowercase SHA-256 text."
        )
    actual_ids = _original_content_file_ids(root)
    if set(actual_ids) != set(expected_ids):
        raise FolderPortableArtifactError(
            "Original-content files do not match the explicit allowlist."
        )
    dynamic_paths = tuple(
        f"{ORIGINAL_CONTENT_ROOT}/{file_id}.bin" for file_id in expected_ids
    )
    committed_paths = tuple(sorted((*requested_static, *dynamic_paths)))
    commitments: list[FolderArtifactCommitment] = []
    for relative_path in committed_paths:
        size, digest = regular_file_measurement(root, relative_path)
        commitments.append(
            FolderArtifactCommitment(
                path=relative_path,
                size=size,
                sha256=digest,
            )
        )
    return tuple(commitments)


def contains_exact_local_path(
    value: object,
    *,
    sender_local_paths: Collection[str],
) -> bool:
    """Detect only caller-supplied exact local-path strings in portable values."""

    paths = tuple(path for path in sender_local_paths if path)
    if isinstance(value, BaseModel):
        return contains_exact_local_path(
            value.model_dump(mode="json"),
            sender_local_paths=paths,
        )
    if isinstance(value, dict):
        return any(
            contains_exact_local_path(item, sender_local_paths=paths)
            for item in value.values()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(
            contains_exact_local_path(item, sender_local_paths=paths) for item in value
        )
    if not isinstance(value, str):
        return False
    return any(path in value for path in paths)


def _validate_map_rows(rows: tuple[FolderPathMapRow, ...]) -> None:
    if not rows:
        raise FolderPortableArtifactError("Path map contains no rows.")
    original_paths = tuple(row.original_path for row in rows)
    file_ids = tuple(row.file_id for row in rows)
    result_paths = tuple(row.result_path for row in rows)
    if original_paths != tuple(sorted(original_paths)):
        raise FolderPortableArtifactError("Path-map rows must be source-path sorted.")
    if (
        len(original_paths) != len(set(original_paths))
        or len(file_ids) != len(set(file_ids))
        or len(result_paths) != len(set(result_paths))
    ):
        raise FolderPortableArtifactError(
            "Path-map source paths, file IDs, and result paths must be unique."
        )
    for row in rows:
        _require_relative_posix(row.original_path)
        _require_relative_posix(row.result_path)


def _render_boolean(value: bool) -> str:
    return "true" if value else "false"


def _parse_boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("Boolean field is not canonical.")


def _parse_nonnegative_integer(value: str) -> int:
    if _NONNEGATIVE_INTEGER.fullmatch(value) is None:
        raise ValueError("Integer field is not canonical.")
    return int(value)


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise FolderPortableArtifactError(
                "Portable JSON contains a duplicate object key."
            )
        value[key] = item
    return value


def _reject_json_constant(constant: str) -> None:
    raise FolderPortableArtifactError(
        f"Portable JSON contains unsupported constant: {constant}."
    )


def _require_relative_posix(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise FolderPortableArtifactError("Portable path must be nonempty text.")
    if value.startswith("/") or "\\" in value or "\x00" in value:
        raise FolderPortableArtifactError(
            "Portable path must be relative POSIX syntax."
        )
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise FolderPortableArtifactError("Portable path contains a control character.")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise FolderPortableArtifactError(
            "Portable path is not normalized POSIX syntax."
        )
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise FolderPortableArtifactError("Portable path contains a dot segment.")
    return value


def _require_real_directory(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path):
        raise FolderPortableArtifactError(f"{label} must be a pathlib.Path.")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FolderPortableArtifactError(f"{label} cannot be inspected.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FolderPortableArtifactError(f"{label} must be a real directory.")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise FolderPortableArtifactError(f"{label} cannot be resolved.") from exc


def _require_regular_relative_file(root: Path, relative_path: str) -> Path:
    current = _require_real_directory(root, label="Portable result root")
    relative = _require_relative_posix(relative_path)
    parts = PurePosixPath(relative).parts
    for part in parts[:-1]:
        current = _require_real_directory(current / part, label="Artifact parent")
    path = current / parts[-1]
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FolderPortableArtifactError(
            "Portable artifact cannot be inspected."
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FolderPortableArtifactError("Portable artifact must be a regular file.")
    return path


def _read_regular_file(
    path: Path,
    relative_path: str,
    *,
    retain: bool,
    expected_metadata: os.stat_result | None = None,
) -> tuple[bytes | None, int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    chunks: list[bytes] | None = [] if retain else None
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FolderPortableArtifactError(
                f"Portable member is not regular: {relative_path}."
            )
        if expected_metadata is not None and _file_identity(before) != _file_identity(
            expected_metadata
        ):
            raise FolderPortableArtifactError(
                f"Portable member changed before reading: {relative_path}."
            )
        size = 0
        while chunk := os.read(descriptor, HASH_CHUNK_SIZE):
            size += len(chunk)
            digest.update(chunk)
            if chunks is not None:
                chunks.append(chunk)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after) or size != after.st_size:
            raise FolderPortableArtifactError(
                f"Portable member changed while reading: {relative_path}."
            )
    except FolderPortableArtifactError:
        raise
    except OSError as exc:
        raise FolderPortableArtifactError(
            f"Portable member cannot be read safely: {relative_path}."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    payload = None if chunks is None else b"".join(chunks)
    return payload, size, digest.hexdigest()


def _write_new_regular_bytes(root: Path, relative_path: str, payload: bytes) -> None:
    current = _require_real_directory(root, label="Portable result root")
    relative = _require_relative_posix(relative_path)
    parts = PurePosixPath(relative).parts
    for part in parts[:-1]:
        current = _require_real_directory(current / part, label="Artifact parent")
    path = current / parts[-1]
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("Portable artifact write made no progress.")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise FolderPortableArtifactError(
            "Portable artifact cannot be created exclusively."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_nlink,
    )


def _original_content_file_ids(root: Path) -> tuple[str, ...]:
    bag_root = _require_real_directory(root, label="Portable result root")
    directory = bag_root / ORIGINAL_CONTENT_ROOT
    if not os.path.lexists(directory):
        return ()
    original_root = _require_real_directory(directory, label="Original-content root")
    try:
        entries = sorted(os.scandir(original_root), key=lambda entry: entry.name)
    except OSError as exc:
        raise FolderPortableArtifactError(
            "Original-content root cannot be enumerated."
        ) from exc
    file_ids: list[str] = []
    for entry in entries:
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise FolderPortableArtifactError(
                "Original-content member cannot be inspected."
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise FolderPortableArtifactError(
                "Original-content root contains a non-regular member."
            )
        name = entry.name
        if not name.endswith(".bin") or _SHA256_TEXT.fullmatch(name[:-4]) is None:
            raise FolderPortableArtifactError(
                "Original-content member name is not an allowed file ID."
            )
        file_ids.append(name[:-4])
    return tuple(file_ids)
