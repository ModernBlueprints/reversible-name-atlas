"""Focused tests for generic-folder portable artifact I/O."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from name_atlas.folder_refactor.portable_artifacts import (
    SOURCE_SNAPSHOT_PATH,
    FolderPortableArtifactError,
    artifact_commitments,
    canonical_portable_json_bytes,
    contains_exact_local_path,
    parse_folder_path_map,
    parse_portable_model,
    read_regular_bytes,
    render_folder_path_map,
    staged_data_commitment,
    staged_data_members,
    strict_json_object,
    write_new_portable_json,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderPathMapRow,
    FolderReceiptVerification,
    FolderReceiptVerificationCheck,
    FolderReceiptVerificationStatus,
    FolderUserRequestArtifact,
)
from name_atlas.folder_refactor.serialization import request_fingerprint


def _map_rows() -> tuple[FolderPathMapRow, ...]:
    return (
        FolderPathMapRow(
            file_id="a" * 64,
            original_path="notes/alpha.md",
            result_path="handoff/alpha.md",
            original_size=10,
            original_sha256="b" * 64,
            result_size=12,
            result_sha256="c" * 64,
            protected=False,
            markdown_rewritten=True,
        ),
        FolderPathMapRow(
            file_id="d" * 64,
            original_path="photo, one.jpg",
            result_path="handoff/photo, one.jpg",
            original_size=20,
            original_sha256="e" * 64,
            result_size=20,
            result_sha256="e" * 64,
            protected=False,
            markdown_rewritten=False,
        ),
    )


def test_canonical_json_and_strict_model_parsing_are_exact() -> None:
    artifact = FolderUserRequestArtifact(
        request="Organize this folder.",
        request_fingerprint=request_fingerprint("Organize this folder."),
    )

    payload = canonical_portable_json_bytes(artifact)

    assert not payload.endswith(b"\n")
    assert payload == canonical_portable_json_bytes(artifact)
    assert parse_portable_model(payload, FolderUserRequestArtifact) == artifact
    assert strict_json_object(payload)["schema_version"] == "folder-user-request.v1"


def test_strict_model_parsing_uses_json_semantics_for_enums_and_tuples() -> None:
    artifact = FolderReceiptVerification(
        status=FolderReceiptVerificationStatus.BLOCKED,
        checks=(
            FolderReceiptVerificationCheck(
                check_id="controlled_failure",
                passed=False,
                detail="The controlled failure remained blocked.",
            ),
        ),
        failed_check_ids=("controlled_failure",),
    )

    payload = canonical_portable_json_bytes(artifact)

    assert parse_portable_model(payload, FolderReceiptVerification) == artifact


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":"x","schema_version":"y"}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b"[]",
    ),
)
def test_strict_json_rejects_duplicate_nonfinite_and_nonobject_values(
    payload: bytes,
) -> None:
    with pytest.raises(FolderPortableArtifactError):
        strict_json_object(payload)


def test_exclusive_json_write_and_safe_exact_read(tmp_path: Path) -> None:
    (tmp_path / "name-atlas").mkdir()
    value = {"schema_version": "example.v1", "value": "café"}

    written = write_new_portable_json(tmp_path, SOURCE_SNAPSHOT_PATH, value)

    assert written == canonical_portable_json_bytes(value)
    assert read_regular_bytes(tmp_path, SOURCE_SNAPSHOT_PATH) == written
    with pytest.raises(FolderPortableArtifactError, match="created exclusively"):
        write_new_portable_json(tmp_path, SOURCE_SNAPSHOT_PATH, value)


def test_safe_read_rejects_a_symlink_when_supported(tmp_path: Path) -> None:
    (tmp_path / "name-atlas").mkdir()
    target = tmp_path / "target.json"
    target.write_bytes(b"{}")
    link = tmp_path / SOURCE_SNAPSHOT_PATH
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are unavailable on this platform.")

    with pytest.raises(FolderPortableArtifactError, match="regular file"):
        read_regular_bytes(tmp_path, SOURCE_SNAPSHOT_PATH)


@pytest.mark.parametrize("reverse", (False, True))
def test_path_map_round_trip_is_canonical(reverse: bool) -> None:
    rows = _map_rows()

    payload = render_folder_path_map(rows, reverse=reverse)

    assert b"\r" not in payload
    assert payload.endswith(b"\n")
    assert parse_folder_path_map(payload, reverse=reverse) == rows


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload.replace(b"\n", b"\r\n"),
        lambda payload: payload.replace(b",false,true\n", b",no,true\n", 1),
        lambda payload: payload.replace(b"original_path", b"source_path", 1),
        lambda payload: payload.rstrip(b"\n"),
    ),
)
def test_path_map_rejects_noncanonical_serializations(
    mutation: Callable[[bytes], bytes],
) -> None:
    payload = render_folder_path_map(_map_rows(), reverse=False)

    with pytest.raises(FolderPortableArtifactError):
        parse_folder_path_map(mutation(payload), reverse=False)


def test_staged_data_members_and_commitment_are_deterministic(tmp_path: Path) -> None:
    (tmp_path / "data" / "nested").mkdir(parents=True)
    (tmp_path / "data" / "z.bin").write_bytes(b"z")
    (tmp_path / "data" / "nested" / "a.txt").write_bytes(b"alpha")

    members = staged_data_members(tmp_path)
    commitment = staged_data_commitment(members)

    assert tuple(member.path for member in members) == ("nested/a.txt", "z.bin")
    expected_payload = canonical_portable_json_bytes(
        [member.model_dump(mode="json") for member in members]
    )
    assert commitment == hashlib.sha256(expected_payload).hexdigest()
    assert staged_data_members(tmp_path) == members


def test_staged_data_rejects_a_symlink_when_supported(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    target = tmp_path / "outside.bin"
    target.write_bytes(b"outside")
    try:
        (tmp_path / "data" / "linked.bin").symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are unavailable on this platform.")

    with pytest.raises(FolderPortableArtifactError, match="symlink"):
        staged_data_members(tmp_path)


def test_artifact_commitments_enforce_dynamic_original_content_allowlist(
    tmp_path: Path,
) -> None:
    (tmp_path / "name-atlas" / "original-content").mkdir(parents=True)
    source = tmp_path / SOURCE_SNAPSHOT_PATH
    source.write_bytes(b"snapshot")
    file_id = "f" * 64
    original = tmp_path / "name-atlas" / "original-content" / f"{file_id}.bin"
    original.write_bytes(b"original markdown")

    commitments = artifact_commitments(
        tmp_path,
        static_paths=(SOURCE_SNAPSHOT_PATH,),
        original_content_file_ids=(file_id,),
    )

    assert tuple(item.path for item in commitments) == (
        f"name-atlas/original-content/{file_id}.bin",
        SOURCE_SNAPSHOT_PATH,
    )
    assert commitments[0].sha256 == hashlib.sha256(b"original markdown").hexdigest()
    assert commitments[1].size == len(b"snapshot")

    (tmp_path / "name-atlas" / "original-content" / "unexpected.bin").write_bytes(
        b"unexpected"
    )
    with pytest.raises(FolderPortableArtifactError, match="not an allowed file ID"):
        artifact_commitments(
            tmp_path,
            static_paths=(SOURCE_SNAPSHOT_PATH,),
            original_content_file_ids=(file_id,),
        )


def test_artifact_commitments_reject_unknown_static_path(tmp_path: Path) -> None:
    with pytest.raises(FolderPortableArtifactError, match="not allowed"):
        artifact_commitments(tmp_path, static_paths=("name-atlas/unknown.json",))


def test_exact_local_path_detection_has_no_invented_pattern_matching() -> None:
    local = "/Users/nikolai/private/source"
    value = {
        "detail": f"The source was {local} before planning.",
        "portable": "folders/source",
    }

    assert contains_exact_local_path(value, sender_local_paths={local}) is True
    assert (
        contains_exact_local_path(
            {"detail": "/Users/another/source"},
            sender_local_paths={local},
        )
        is False
    )
    assert contains_exact_local_path(value, sender_local_paths=set()) is False


def test_path_map_rejects_unsorted_or_duplicate_rows() -> None:
    rows = _map_rows()

    with pytest.raises(FolderPortableArtifactError, match="source-path sorted"):
        render_folder_path_map(tuple(reversed(rows)), reverse=False)
    with pytest.raises(FolderPortableArtifactError, match="must be unique"):
        render_folder_path_map((rows[0], rows[0]), reverse=False)
