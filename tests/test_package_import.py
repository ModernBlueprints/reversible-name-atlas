"""Strict package import, proposals, and human-decision tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from name_atlas.decisions import (
    DecisionError,
    HumanAction,
    approve_family,
    edit_family,
    proposals_after_decision,
)
from name_atlas.domain import ContentRole
from name_atlas.package_import import PackageImportError, import_package
from name_atlas.proposals import ProposalSource, RiskCategory, build_proposals
from name_atlas.source import snapshot_tree, validate_relative_path

HERO_ROOT = Path(__file__).parents[1] / "sample_data" / "hero"


def _copy_hero(tmp_path: Path) -> Path:
    destination = tmp_path / "hero"
    shutil.copytree(HERO_ROOT, destination)
    return destination


def test_hero_import_is_complete_and_stable() -> None:
    package = import_package(HERO_ROOT)
    repeated = import_package(HERO_ROOT)

    assert len(package.snapshot.members) == 30
    assert len(package.content_members) == 28
    assert len(package.families) == 12
    family = next(
        item for item in package.families if item.canonical_identifier == "NA-0001"
    )
    assert family.canonical_identifier == "NA-0001"
    assert {member.role for member in family.members} == {
        ContentRole.ORIGINAL,
        ContentRole.ACCESS,
        ContentRole.PRESERVATION,
    }
    assert package.snapshot.commitment == repeated.snapshot.commitment
    repeated_family = next(
        item for item in repeated.families if item.canonical_identifier == "NA-0001"
    )
    assert family.family_id == repeated_family.family_id


def test_campana_projection_is_visible_and_requires_a_card() -> None:
    package = import_package(HERO_ROOT)
    family = next(
        item for item in package.families if item.canonical_identifier == "NA-0001"
    )
    proposals = build_proposals(package.families)
    selected = tuple(item for item in proposals if item.family_id == family.family_id)

    assert len(proposals) == 28
    assert len(selected) == 3
    assert all("__campana-poster__" in item.proposed_relative_path for item in selected)
    assert all(
        any(risk.category is RiskCategory.MEANING for risk in item.risk_signals)
        for item in selected
    )
    with pytest.raises(DecisionError, match="validated decision card"):
        approve_family(family, proposals, semantic_card_available=False)

    decision = approve_family(family, proposals, semantic_card_available=True)
    assert decision.action is HumanAction.APPROVED
    assert set(decision.resolved_targets) == {
        ContentRole.ORIGINAL,
        ContentRole.ACCESS,
        ContentRole.PRESERVATION,
    }


def test_one_human_edit_propagates_to_every_family_role() -> None:
    package = import_package(HERO_ROOT)
    family = next(
        item for item in package.families if item.canonical_identifier == "NA-0001"
    )
    proposals = build_proposals(package.families)

    decision = edit_family(
        family,
        proposals,
        descriptor="campana-es",
        semantic_card_available=True,
    )
    updated = proposals_after_decision(proposals, decision)

    assert all(
        "__campana-es__" in target for target in decision.resolved_targets.values()
    )
    selected = tuple(item for item in updated if item.family_id == family.family_id)
    unselected = tuple(item for item in updated if item.family_id != family.family_id)
    assert all(item.proposal_source is ProposalSource.HUMAN_EDIT for item in selected)
    assert all(
        item.proposal_source is ProposalSource.REPOSITORY_READY_PROFILE
        for item in unselected
    )


def test_casefold_collision_can_be_resolved_by_one_human_edit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "collision"
    (root / "objects").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "objects" / "Archive map.svg").write_text("north", encoding="utf-8")
    (root / "objects" / "archive-map.svg").write_text("south", encoding="utf-8")
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\n"
        "objects/Archive map.svg,CASE-001,North map\n"
        "objects/archive-map.svg,case-001,South map\n",
        encoding="utf-8",
    )
    package = import_package(root)
    proposals = build_proposals(package.families)

    assert all(
        any(
            risk.code == "target_collision_nfc_casefold"
            for risk in proposal.risk_signals
        )
        for proposal in proposals
    )
    with pytest.raises(DecisionError, match="Mechanical blockers"):
        approve_family(
            package.families[0],
            proposals,
            semantic_card_available=False,
        )

    first_family = package.families[0]
    other_targets = tuple(
        proposal.proposed_relative_path
        for proposal in proposals
        if proposal.family_id != first_family.family_id
    )
    decision = edit_family(
        first_family,
        proposals,
        descriptor="archive-map-north",
        semantic_card_available=False,
        other_resolved_targets=other_targets,
    )
    updated = proposals_after_decision(proposals, decision)

    assert not any(
        risk.category is RiskCategory.COLLISION
        for proposal in updated
        for risk in proposal.risk_signals
    )
    approve_family(
        package.families[1],
        updated,
        semantic_card_available=False,
    )
    with pytest.raises(TypeError):
        decision.resolved_targets[ContentRole.ORIGINAL] = "objects/mutated.svg"  # type: ignore[index]


@pytest.mark.parametrize("value", ["/absolute", "a/../b", "a/./b", "a//b", "a\\b"])
def test_raw_relative_path_validation_rejects_normalization_tricks(value: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_path(value)


def test_metadata_row_with_missing_trailing_field_fails_closed(tmp_path: Path) -> None:
    root = _copy_hero(tmp_path)
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\nobjects/campaña-poster.svg,NA-0001\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageImportError, match="fields; expected"):
        import_package(root)


def test_orphaned_derivative_reference_fails_closed(tmp_path: Path) -> None:
    root = _copy_hero(tmp_path)
    (root / "normalization.csv").write_text(
        "objects/campaña-poster.svg,manualNormalization/access/missing.svg,\n",
        encoding="utf-8",
    )

    with pytest.raises(PackageImportError, match="references missing access"):
        import_package(root)


def test_symlink_member_fails_closed(tmp_path: Path) -> None:
    root = _copy_hero(tmp_path)
    (root / "objects" / "linked.svg").symlink_to(
        root / "objects" / "campaña-poster.svg"
    )

    with pytest.raises(
        PackageImportError, match="Unsupported symlink or special-file member"
    ):
        import_package(root)


def test_source_snapshot_changes_when_payload_changes(tmp_path: Path) -> None:
    root = _copy_hero(tmp_path)
    before = snapshot_tree(root)
    payload = root / "objects" / "campaña-poster.svg"
    payload.write_text(payload.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    after = snapshot_tree(root)

    assert before.commitment != after.commitment


def test_special_file_fails_without_blocking_on_open(tmp_path: Path) -> None:
    root = _copy_hero(tmp_path)
    fifo = root / "objects" / "unsupported.fifo"
    os.mkfifo(fifo)

    with pytest.raises(PackageImportError, match="special-file"):
        import_package(root)
