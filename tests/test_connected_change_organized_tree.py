"""Path-sensitive organized-tree commitment tests."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from name_atlas.folder_refactor.connected_change.organized_tree import (
    OrganizedTreeCommitmentMismatch,
    OrganizedTreeError,
    OrganizedTreeMember,
    OrganizedTreeSnapshot,
    compute_organized_tree_commitment,
    require_organized_tree_commitment,
    scan_organized_tree,
)
from name_atlas.folder_refactor.serialization import canonical_sha256


def _write(root: Path, relative_path: str, payload: bytes) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_snapshot_is_complete_sorted_path_sensitive_and_deterministic(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first" / "data"
    first_root.mkdir(parents=True)
    _write(first_root, "zeta.bin", b"zeta")
    _write(first_root, "alpha/note.md", b"[asset](../zeta.bin)\n")
    (first_root / "empty" / "nested").mkdir(parents=True)

    first = scan_organized_tree(first_root)
    repeated = scan_organized_tree(first_root)

    assert first == repeated
    assert first.schema_version == "organized-tree.v1"
    assert [(member.relative_path, member.member_kind) for member in first.members] == [
        ("alpha/note.md", "regular_file"),
        ("empty/nested", "empty_directory"),
        ("zeta.bin", "regular_file"),
    ]
    assert first.file_count == 2
    assert first.empty_directory_count == 1
    assert first.total_bytes == len(b"zeta") + len(b"[asset](../zeta.bin)\n")
    assert (
        first.members[0].sha256 == hashlib.sha256(b"[asset](../zeta.bin)\n").hexdigest()
    )
    assert first.commitment == canonical_sha256(
        [member.model_dump(mode="json") for member in first.members]
    )
    assert str(first_root.resolve()) not in repr(first.model_dump(mode="json"))

    second_root = tmp_path / "second" / "data"
    second_root.mkdir(parents=True)
    _write(second_root, "zeta.bin", b"zeta")
    _write(second_root, "renamed/note.md", b"[asset](../zeta.bin)\n")
    (second_root / "empty" / "nested").mkdir(parents=True)

    second = scan_organized_tree(second_root)

    assert second.total_bytes == first.total_bytes
    assert second.commitment != first.commitment


def test_creation_order_and_absolute_location_do_not_affect_commitment(
    tmp_path: Path,
) -> None:
    first = tmp_path / "one" / "data"
    second = tmp_path / "two" / "data"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    _write(first, "b/item.bin", b"b")
    _write(first, "a/item.bin", b"a")
    (first / "empty").mkdir()
    (second / "empty").mkdir()
    _write(second, "a/item.bin", b"a")
    _write(second, "b/item.bin", b"b")

    assert (
        scan_organized_tree(first).commitment == scan_organized_tree(second).commitment
    )


def test_models_are_strict_frozen_and_commitment_bound() -> None:
    member = OrganizedTreeMember(
        member_kind="regular_file",
        relative_path="folder/file.txt",
        size=3,
        sha256=hashlib.sha256(b"abc").hexdigest(),
    )
    commitment = compute_organized_tree_commitment((member,))
    snapshot = OrganizedTreeSnapshot(members=(member,), commitment=commitment)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OrganizedTreeMember.model_validate(
            {**member.model_dump(mode="python"), "unexpected": True},
            strict=True,
        )
    with pytest.raises(ValidationError, match="Instance is frozen"):
        snapshot.commitment = "f" * 64
    with pytest.raises(ValidationError, match="does not match"):
        OrganizedTreeSnapshot(members=(member,), commitment="f" * 64)
    with pytest.raises(ValidationError, match="require size and SHA-256"):
        OrganizedTreeMember(
            member_kind="regular_file",
            relative_path="missing.bin",
        )
    with pytest.raises(ValidationError, match="cannot carry payload metadata"):
        OrganizedTreeMember(
            member_kind="empty_directory",
            relative_path="empty",
            size=0,
            sha256=hashlib.sha256(b"").hexdigest(),
        )


def test_snapshot_rejects_unordered_and_duplicate_paths() -> None:
    alpha = OrganizedTreeMember(
        member_kind="empty_directory",
        relative_path="alpha",
    )
    zeta = OrganizedTreeMember(
        member_kind="empty_directory",
        relative_path="zeta",
    )

    assert compute_organized_tree_commitment(
        (zeta, alpha)
    ) == compute_organized_tree_commitment((alpha, zeta))
    with pytest.raises(ValidationError, match="canonically sorted"):
        OrganizedTreeSnapshot(
            members=(zeta, alpha),
            commitment=compute_organized_tree_commitment((zeta, alpha)),
        )
    with pytest.raises(ValidationError, match="paths must be unique"):
        OrganizedTreeSnapshot(
            members=(alpha, alpha),
            commitment=compute_organized_tree_commitment((alpha, alpha)),
        )


def test_exact_commitment_comparison_returns_snapshot_or_stable_blocker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    _write(root, "file.bin", b"payload")
    snapshot = scan_organized_tree(root)

    assert require_organized_tree_commitment(snapshot, snapshot.commitment) is snapshot
    with pytest.raises(
        OrganizedTreeCommitmentMismatch,
        match=r"^organized_tree_commitment_mismatch:",
    ) as raised:
        require_organized_tree_commitment(snapshot, "f" * 64)
    assert raised.value.blocker_id == "organized_tree_commitment_mismatch"
    with pytest.raises(OrganizedTreeError, match="lowercase SHA-256"):
        require_organized_tree_commitment(snapshot, "not-a-digest")


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_symlink_members_are_rejected(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "data"
    root.mkdir()
    if kind == "file":
        target = _write(tmp_path, "target.bin", b"target")
    else:
        target = tmp_path / "target-directory"
        target.mkdir()
    (root / "linked").symlink_to(target, target_is_directory=kind == "directory")

    with pytest.raises(OrganizedTreeError, match="Symlink"):
        scan_organized_tree(root)


def test_hard_link_members_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    original = _write(root, "original.bin", b"same inode")
    os.link(original, root / "duplicate.bin")

    with pytest.raises(OrganizedTreeError, match="Hard-linked"):
        scan_organized_tree(root)


def test_special_members_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    os.mkfifo(root / "pipe")

    with pytest.raises(OrganizedTreeError, match="Special filesystem member"):
        scan_organized_tree(root)


def test_root_must_be_path_to_existing_non_symlink_directory(tmp_path: Path) -> None:
    regular_file = _write(tmp_path, "file.bin", b"payload")
    linked_root = tmp_path / "linked-root"
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    linked_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(OrganizedTreeError, match="pathlib.Path"):
        scan_organized_tree(str(real_root))  # type: ignore[arg-type]
    with pytest.raises(OrganizedTreeError, match="non-symlink directory"):
        scan_organized_tree(regular_file)
    with pytest.raises(OrganizedTreeError, match="non-symlink directory"):
        scan_organized_tree(linked_root)
    with pytest.raises(OrganizedTreeError, match="cannot be inspected"):
        scan_organized_tree(tmp_path / "missing")
