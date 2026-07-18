"""Exact, path-neutral source-staleness contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from name_atlas.cases import (
    CaseLifecycle,
    CaseSourceSnapshot,
    MigrationCase,
    SourceDifference,
    SourceDifferenceKind,
    SourceScanBlocker,
    compare_source_snapshots,
    format_source_differences,
    new_migration_case,
)
from name_atlas.domain import ContentRole, MemberKind
from name_atlas.package_import import import_package
from name_atlas.proposals import build_proposals
from name_atlas.source import SourceMember, SourceSnapshot

HERO_ROOT = Path(__file__).parents[1] / "sample_data" / "hero"


def _member(relative_path: str, payload: bytes) -> SourceMember:
    return SourceMember(
        relative_path=relative_path,
        role=ContentRole.ORIGINAL,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        kind=MemberKind.CONTENT_OBJECT,
    )


def _commitment(members: tuple[SourceMember, ...]) -> str:
    payload = json.dumps(
        [member.model_dump(mode="json") for member in members],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _snapshots(
    previous: tuple[SourceMember, ...],
    current: tuple[SourceMember, ...],
) -> tuple[CaseSourceSnapshot, SourceSnapshot]:
    previous = tuple(sorted(previous, key=lambda member: member.relative_path))
    current = tuple(sorted(current, key=lambda member: member.relative_path))
    return (
        CaseSourceSnapshot(
            members=previous,
            commitment=_commitment(previous),
        ),
        SourceSnapshot(
            source_root=Path("/current-source"),
            members=current,
            commitment=_commitment(current),
        ),
    )


def _validated_case(case: MigrationCase, **updates: object) -> MigrationCase:
    values = case.model_dump(mode="python")
    values.update(updates)
    return MigrationCase.model_validate(values, strict=True)


def test_unchanged_snapshot_has_no_differences() -> None:
    member = _member("objects/item.txt", b"unchanged")
    previous, current = _snapshots((member,), (member,))

    assert compare_source_snapshots(previous, current) == ()
    assert format_source_differences(()) == ()


def test_added_and_removed_members_retain_exact_records() -> None:
    retained = _member("objects/retained.txt", b"retained")
    removed = _member("objects/removed.txt", b"removed")
    added = _member("objects/added.txt", b"added")
    previous, current = _snapshots((retained, removed), (added, retained))

    differences = compare_source_snapshots(previous, current)

    assert differences == (
        SourceDifference(kind=SourceDifferenceKind.ADDED, after=added),
        SourceDifference(kind=SourceDifferenceKind.REMOVED, before=removed),
    )
    assert format_source_differences(differences) == (
        "added: objects/added.txt",
        "removed: objects/removed.txt",
    )


def test_unique_payload_and_role_match_is_reported_as_rename() -> None:
    before = _member("objects/before.txt", b"same payload")
    after = before.model_copy(update={"relative_path": "objects/after.txt"})
    previous, current = _snapshots((before,), (after,))

    differences = compare_source_snapshots(previous, current)

    assert differences == (
        SourceDifference(
            kind=SourceDifferenceKind.RENAMED,
            before=before,
            after=after,
        ),
    )
    assert format_source_differences(differences) == (
        "renamed: objects/before.txt -> objects/after.txt",
    )


def test_ambiguous_identical_payload_moves_remain_added_and_removed() -> None:
    first = _member("objects/a.txt", b"duplicate")
    second = first.model_copy(update={"relative_path": "objects/b.txt"})
    third = first.model_copy(update={"relative_path": "objects/c.txt"})
    fourth = first.model_copy(update={"relative_path": "objects/d.txt"})
    previous, current = _snapshots((first, second), (third, fourth))

    differences = compare_source_snapshots(previous, current)

    assert tuple(difference.kind for difference in differences) == (
        SourceDifferenceKind.REMOVED,
        SourceDifferenceKind.REMOVED,
        SourceDifferenceKind.ADDED,
        SourceDifferenceKind.ADDED,
    )
    assert all(
        difference.kind is not SourceDifferenceKind.RENAMED
        for difference in differences
    )


def test_resized_and_same_size_content_changes_are_distinct() -> None:
    resized_before = _member("objects/resized.txt", b"short")
    resized_after = _member("objects/resized.txt", b"a longer payload")
    changed_before = _member("objects/changed.txt", b"alpha")
    changed_after = _member("objects/changed.txt", b"omega")
    previous, current = _snapshots(
        (changed_before, resized_before),
        (changed_after, resized_after),
    )

    differences = compare_source_snapshots(previous, current)

    assert tuple(difference.kind for difference in differences) == (
        SourceDifferenceKind.CONTENT_CHANGED,
        SourceDifferenceKind.RESIZED,
    )
    assert differences[0].before == changed_before
    assert differences[0].after == changed_after
    assert differences[1].before == resized_before
    assert differences[1].after == resized_after
    assert format_source_differences(differences) == (
        "content_changed: objects/changed.txt",
        "resized: objects/resized.txt (5 -> 16 bytes)",
    )


def test_difference_contract_rejects_invented_rename() -> None:
    before = _member("objects/before.txt", b"before")
    after = _member("objects/after.txt", b"after")

    with pytest.raises(ValidationError, match="exact payload identity"):
        SourceDifference(
            kind=SourceDifferenceKind.RENAMED,
            before=before,
            after=after,
        )


def test_difference_contract_rejects_resized_member_with_same_digest() -> None:
    before = _member("objects/resized.txt", b"short")
    fabricated_after = before.model_copy(update={"size": before.size + 1})

    with pytest.raises(ValidationError, match="different digest"):
        SourceDifference(
            kind=SourceDifferenceKind.RESIZED,
            before=before,
            after=fabricated_after,
        )


def test_case_lifecycle_requires_exact_stale_differences(tmp_path: Path) -> None:
    package = import_package(HERO_ROOT)
    case = new_migration_case(
        package,
        build_proposals(package.families),
        case_path=tmp_path / "case.json",
        output_root=tmp_path / "output",
        case_name="Hero migration",
    )
    added = _member("objects/new.txt", b"new")
    difference = SourceDifference(kind=SourceDifferenceKind.ADDED, after=added)

    stale = _validated_case(
        case,
        lifecycle=CaseLifecycle.STALE,
        stale_differences=(difference,),
    )
    assert stale.stale_differences == (difference,)

    with pytest.raises(ValidationError, match="differences or a scan blocker"):
        _validated_case(case, lifecycle=CaseLifecycle.STALE)
    with pytest.raises(ValidationError, match="Only a stale case"):
        _validated_case(case, stale_differences=(difference,))

    scan_blocked = _validated_case(
        case,
        lifecycle=CaseLifecycle.STALE,
        source_scan_blocker=SourceScanBlocker(
            detail="Source root cannot be inspected."
        ),
    )
    assert scan_blocked.source_scan_blocker is not None
    with pytest.raises(ValidationError, match="cannot mix"):
        _validated_case(
            case,
            lifecycle=CaseLifecycle.STALE,
            stale_differences=(difference,),
            source_scan_blocker=scan_blocked.source_scan_blocker,
        )


def test_existing_case_bytes_load_with_empty_default_stale_differences(
    tmp_path: Path,
) -> None:
    package = import_package(HERO_ROOT)
    case = new_migration_case(
        package,
        build_proposals(package.families),
        case_path=tmp_path / "case.json",
        output_root=tmp_path / "output",
        case_name="Hero migration",
    )
    previous_contract = case.model_dump(mode="python")
    del previous_contract["stale_differences"]
    del previous_contract["source_scan_blocker"]

    loaded = MigrationCase.model_validate(previous_contract, strict=True)

    assert loaded.lifecycle is CaseLifecycle.REVIEW
    assert loaded.stale_differences == ()
