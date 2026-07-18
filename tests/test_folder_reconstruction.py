"""Exact copy-only reconstruction tests for generic-folder receipts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from test_folder_receipt_verifier import (
    _tree_snapshot,
    create_verified_folder_fixture,
)

import name_atlas.folder_refactor.reconstruction as reconstruction_module
from name_atlas.folder_refactor.inventory import scan_folder
from name_atlas.folder_refactor.portable_artifacts import (
    ACCEPTED_PLAN_PATH,
    FORWARD_PATH_MAP_PATH,
    REVERSE_PATH_MAP_PATH,
    canonical_portable_json_bytes,
    parse_folder_path_map,
    render_folder_path_map,
    strict_json_object,
)
from name_atlas.folder_refactor.receipt_contracts import FolderReceiptVerificationStatus
from name_atlas.folder_refactor.receipt_verifier import verify_folder_receipt
from name_atlas.folder_refactor.reconstruction import (
    FolderReconstructionError,
    restore_folder_receipt,
)
from name_atlas.verification.bag_writer import BagItWriter


def test_reconstructs_original_paths_bytes_and_empty_directories(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    destination = fixture.result_root.parent / "recreated-original"
    result_before = _tree_snapshot(fixture.result_root)
    original_inventory = scan_folder(fixture.source_root).inventory
    staged_markdown = (
        fixture.result_root / "data" / "handoff" / "notes.md"
    ).read_bytes()

    report = restore_folder_receipt(fixture.result_root, destination)

    assert destination.is_dir()
    assert scan_folder(destination).inventory == original_inventory
    assert (destination / "empty" / "keep").is_dir()
    assert (destination / "notes.md").read_bytes() == (
        fixture.source_root / "notes.md"
    ).read_bytes()
    assert (destination / "notes.md").read_bytes() != staged_markdown
    assert report.destination == destination
    assert report.restored_file_count == len(original_inventory.files)
    assert report.restored_bytes == original_inventory.total_bytes
    assert report.restored_empty_directory_count == len(
        original_inventory.empty_directories
    )
    assert _tree_snapshot(fixture.result_root) == result_before


def test_existing_destination_is_refused_without_pending_output(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    destination = tmp_path / "existing"
    destination.mkdir()
    marker = destination / "user-data.txt"
    marker.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(FolderReconstructionError) as caught:
        restore_folder_receipt(fixture.result_root, destination)

    assert caught.value.code == "destination_exists"
    assert marker.read_text(encoding="utf-8") == "preserve\n"
    assert tuple(destination.parent.glob(f".{destination.name}.pending-*")) == ()


def test_relative_and_overlapping_destinations_are_refused(tmp_path: Path) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")

    with pytest.raises(FolderReconstructionError) as relative:
        restore_folder_receipt(fixture.result_root, Path("relative-destination"))
    with pytest.raises(FolderReconstructionError) as overlap:
        restore_folder_receipt(
            fixture.result_root,
            fixture.result_root / "nested-reconstruction",
        )

    assert relative.value.code == "destination_must_be_absolute"
    assert overlap.value.code == "destination_overlaps_result"


def test_invalid_destination_component_is_refused(tmp_path: Path) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")

    with pytest.raises(FolderReconstructionError) as caught:
        restore_folder_receipt(
            fixture.result_root,
            Path(f"{tmp_path}/invalid\x00destination"),
        )

    assert caught.value.code == "destination_parent_invalid"


def test_tampered_result_blocks_before_destination_creation(tmp_path: Path) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    altered = tmp_path / "altered-result"
    shutil.copytree(fixture.result_root, altered)
    accepted_path = altered / ACCEPTED_PLAN_PATH
    payload = strict_json_object(accepted_path.read_bytes())
    mappings = payload["file_mappings"]
    assert isinstance(mappings, list)
    mappings[0]["target_path"] = "syntactically-valid/changed-target.bin"
    accepted_path.write_bytes(canonical_portable_json_bytes(payload))
    BagItWriter().finalize_tagmanifest(altered)
    destination = tmp_path / "must-not-exist"

    verification = verify_folder_receipt(altered)
    with pytest.raises(FolderReconstructionError) as caught:
        restore_folder_receipt(altered, destination)

    assert verification.status is FolderReceiptVerificationStatus.BLOCKED
    assert verification.failed_check_ids == ("artifact_digest_mismatch:accepted_plan",)
    assert caught.value.code == "receipt_verification_blocked"
    assert caught.value.failed_check_ids == verification.failed_check_ids
    assert not destination.exists()
    assert tuple(tmp_path.glob(f".{destination.name}.pending-*")) == ()


def test_reconstruction_preserves_received_result_across_repeated_runs(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    result_before = _tree_snapshot(fixture.result_root)

    first = restore_folder_receipt(
        fixture.result_root,
        fixture.result_root.parent / "first",
    )
    second = restore_folder_receipt(
        fixture.result_root,
        fixture.result_root.parent / "second",
    )

    assert first.source_commitment == second.source_commitment
    assert first.receipt_fingerprint == second.receipt_fingerprint
    assert _tree_snapshot(fixture.result_root) == result_before


def test_authority_change_after_verification_fails_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    destination = fixture.result_root.parent / "must-not-exist"
    original_verifier = verify_folder_receipt

    def verify_then_change(result_root: Path):
        verification = original_verifier(result_root)
        forward_path = result_root / FORWARD_PATH_MAP_PATH
        reverse_path = result_root / REVERSE_PATH_MAP_PATH
        rows = parse_folder_path_map(forward_path.read_bytes(), reverse=False)
        changed_first = rows[0].model_copy(
            update={"result_path": "time-of-check/changed-target"}
        )
        changed_rows = (changed_first, *rows[1:])
        forward_path.write_bytes(render_folder_path_map(changed_rows, reverse=False))
        reverse_path.write_bytes(render_folder_path_map(changed_rows, reverse=True))
        return verification

    monkeypatch.setattr(
        reconstruction_module,
        "verify_folder_receipt",
        verify_then_change,
    )

    with pytest.raises(FolderReconstructionError) as caught:
        restore_folder_receipt(fixture.result_root, destination)

    assert caught.value.code == "receipt_reparse_failed"
    assert not destination.exists()
    assert tuple(destination.parent.glob(f".{destination.name}.pending-*")) == ()


def test_destination_inside_original_source_is_refused_without_mutation(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    source_before = _tree_snapshot(fixture.source_root)
    destination = fixture.source_root / "nested-reconstruction"

    with pytest.raises(FolderReconstructionError) as caught:
        restore_folder_receipt(fixture.result_root, destination)

    assert caught.value.code == "destination_must_share_result_parent"
    assert not destination.exists()
    assert _tree_snapshot(fixture.source_root) == source_before
