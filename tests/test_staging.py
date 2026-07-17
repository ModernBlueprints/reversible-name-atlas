"""Focused copy-only stage and proof transaction tests."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from name_atlas.decisions import HumanDecision, approve_family, unresolved_family
from name_atlas.domain import ContentRole
from name_atlas.package_import import import_package
from name_atlas.proposals import build_proposals
from name_atlas.staging import VERIFIED_CLAIM, StagingError, stage_package
from name_atlas.verification import BagItPackageValidator


def _copy_hero(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "objects").mkdir(parents=True)
    (source / "manualNormalization" / "access").mkdir(parents=True)
    (source / "manualNormalization" / "preservation").mkdir(parents=True)
    (source / "metadata").mkdir()
    (source / "objects" / "campaña-poster.svg").write_text("original", encoding="utf-8")
    (source / "manualNormalization" / "access" / "campaña-access.svg").write_text(
        "access", encoding="utf-8"
    )
    (
        source / "manualNormalization" / "preservation" / "campaña-preservation.svg"
    ).write_text("preservation", encoding="utf-8")
    (source / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\n"
        "objects/campaña-poster.svg,NA-0001,Campaña poster\n",
        encoding="utf-8",
    )
    (source / "normalization.csv").write_text(
        "objects/campaña-poster.svg,"
        "manualNormalization/access/campaña-access.svg,"
        "manualNormalization/preservation/campaña-preservation.svg\n",
        encoding="utf-8",
    )
    return source


def _read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_resolved_hero_stages_copy_only_with_complete_proof(tmp_path: Path) -> None:
    source = _copy_hero(tmp_path)
    before = _read_tree(source)
    package = import_package(source)
    proposals = build_proposals(package.families)
    decision = approve_family(
        package.families[0],
        proposals,
        semantic_card_available=True,
    )

    result = stage_package(
        package,
        (decision,),
        output_root=tmp_path / "output",
        package_validator=BagItPackageValidator(),
    )

    assert _read_tree(source) == before
    assert result.stage_root.is_dir()
    assert BagItPackageValidator().validate(result.stage_root).valid is True
    assert result.artifacts.report.claim == VERIFIED_CLAIM
    assert result.artifacts.report.source_unchanged is True
    assert result.artifacts.report.map_row_count == 3
    assert all(check.passed for check in result.artifacts.report.checks)
    assert result.artifacts.report.bagit_validation.valid is True

    forward = result.artifacts.forward_map
    assert {row.role for row in forward} == {
        ContentRole.ORIGINAL,
        ContentRole.ACCESS,
        ContentRole.PRESERVATION,
    }
    for row in forward:
        assert (result.stage_root / "data" / row.target_path).read_bytes() == (
            source / row.source_path
        ).read_bytes()

    metadata_path = result.stage_root / "data" / "metadata" / "metadata.csv"
    with metadata_path.open(newline="", encoding="utf-8") as stream:
        metadata_rows = list(csv.DictReader(stream))
    assert (
        metadata_rows[0]["filename"] == decision.resolved_targets[ContentRole.ORIGINAL]
    )
    assert metadata_rows[0]["dc.title"] == "Campaña poster"

    report_path = result.stage_root / "name-atlas" / "verification_report.json"
    assert json.loads(report_path.read_text(encoding="utf-8")) == (
        result.artifacts.report.model_dump(mode="json")
    )


def test_unresolved_family_blocks_before_output_creation(tmp_path: Path) -> None:
    source = _copy_hero(tmp_path)
    package = import_package(source)
    output = tmp_path / "output"

    with pytest.raises(StagingError, match="no complete resolved target"):
        stage_package(
            package,
            (unresolved_family(package.families[0].family_id),),
            output_root=output,
            package_validator=BagItPackageValidator(),
        )

    assert not output.exists()


def test_changed_source_blocks_before_copy(tmp_path: Path) -> None:
    source = _copy_hero(tmp_path)
    package = import_package(source)
    proposals = build_proposals(package.families)
    decision = approve_family(
        package.families[0],
        proposals,
        semantic_card_available=True,
    )
    payload = source / "objects" / "campaña-poster.svg"
    payload.write_text(payload.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(StagingError, match="changed after the initial snapshot"):
        stage_package(
            package,
            (decision,),
            output_root=tmp_path / "output",
            package_validator=BagItPackageValidator(),
        )


def test_output_inside_source_is_rejected_without_mutation(tmp_path: Path) -> None:
    source = _copy_hero(tmp_path)
    package = import_package(source)
    proposals = build_proposals(package.families)
    decision = approve_family(
        package.families[0],
        proposals,
        semantic_card_available=True,
    )
    before = _read_tree(source)

    with pytest.raises(StagingError, match="outside the immutable source"):
        stage_package(
            package,
            (decision,),
            output_root=source / "staging",
            package_validator=BagItPackageValidator(),
        )

    assert _read_tree(source) == before
    assert not (source / "staging").exists()


def test_crafted_escaping_resolved_target_blocks_before_output(tmp_path: Path) -> None:
    source = _copy_hero(tmp_path)
    package = import_package(source)
    proposals = build_proposals(package.families)
    decision = approve_family(
        package.families[0],
        proposals,
        semantic_card_available=True,
    )
    targets = dict(decision.resolved_targets)
    targets[ContentRole.ORIGINAL] = "../../outside.svg"
    crafted = HumanDecision(
        family_id=decision.family_id,
        action=decision.action,
        human_input=None,
        resolved_targets=targets,
    )
    output = tmp_path / "output"

    with pytest.raises(StagingError, match="safe relative path"):
        stage_package(
            package,
            (crafted,),
            output_root=output,
            package_validator=BagItPackageValidator(),
        )

    assert not output.exists()
    assert not (tmp_path / "outside.svg").exists()
