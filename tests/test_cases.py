"""Persistent Migration Case contract and crash-safe store tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from name_atlas import cases as case_module
from name_atlas.cases import (
    CardDisplayOrigin,
    CaseDecisionBinding,
    CaseDecisionCardRecord,
    CaseDecisionMethod,
    CaseEvidenceRecord,
    CaseFinalizedError,
    CaseLifecycle,
    CaseLoadError,
    CaseLockError,
    CaseRevisionError,
    CaseWriteError,
    LocalCasePointers,
    MigrationCase,
    MigrationCaseStore,
    SourceDifference,
    SourceDifferenceKind,
    SourceScanBlocker,
    canonical_case_bytes,
    card_fingerprint,
    default_case_path,
    load_case,
    new_migration_case,
)
from name_atlas.decision_cards import (
    build_evidence_packet,
    evidence_fingerprint,
    load_recorded_decision_card,
)
from name_atlas.decisions import approve_family, pending_family
from name_atlas.package_import import SourcePackage, import_package
from name_atlas.proposals import PathProposal, build_proposals

HERO_ROOT = Path(__file__).parents[1] / "sample_data" / "hero"
REPLAY_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "name_atlas"
    / "recordings"
    / "hero_decision_card.json"
)
oslo_tz = ZoneInfo("Europe/Oslo")


@pytest.fixture(scope="module")
def hero_contract() -> tuple[SourcePackage, tuple[PathProposal, ...]]:
    package = import_package(HERO_ROOT)
    return package, build_proposals(package.families)


def _new_case(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
    *,
    now: datetime | None = None,
) -> MigrationCase:
    package, proposals = hero_contract
    return new_migration_case(
        package,
        proposals,
        case_path=tmp_path / "case.json",
        output_root=tmp_path / "output",
        case_name="Hero migration",
        now=now or datetime(2026, 7, 18, 1, 0, tzinfo=oslo_tz),
    )


def _replace_case(case: MigrationCase, **updates: object) -> MigrationCase:
    values = case.model_dump(mode="python")
    values.update(updates)
    return MigrationCase.model_validate(values, strict=True)


def _change_snapshot_commitment(raw: bytes) -> bytes:
    value = json.loads(raw)
    value["source_snapshot"]["commitment"] = "f" * 64
    return json.dumps(value).encode()


def _meaning_review_records(
    package: SourcePackage,
    proposals: tuple[PathProposal, ...],
) -> tuple[CaseEvidenceRecord, CaseDecisionCardRecord, CaseDecisionBinding]:
    family = next(
        family
        for family in package.families
        if family.canonical_identifier == "NA-0001"
    )
    packet = build_evidence_packet(package, family, proposals)
    fingerprint = evidence_fingerprint(packet)
    replay = load_recorded_decision_card(REPLAY_PATH.read_bytes())
    assert replay.evidence_fingerprint == fingerprint
    decision = approve_family(
        family,
        proposals,
        semantic_card_available=True,
    )
    evidence = CaseEvidenceRecord(
        family_id=family.family_id,
        packet=packet,
        evidence_fingerprint=fingerprint,
    )
    card_record = CaseDecisionCardRecord(
        family_id=family.family_id,
        evidence_fingerprint=fingerprint,
        card=replay.decision_card,
        card_fingerprint=card_fingerprint(replay.decision_card),
        display_origin=CardDisplayOrigin.RECORDED_REPLAY,
        generated_at=replay.generated_at,
        usage=replay.usage,
    )
    binding = CaseDecisionBinding(
        family_id=family.family_id,
        decision=decision,
        decision_method=CaseDecisionMethod.INDIVIDUAL_APPROVAL,
        decision_timestamp=datetime(2026, 7, 18, 1, 5, tzinfo=oslo_tz),
        evidence_fingerprint=fingerprint,
        card_fingerprint=card_record.card_fingerprint,
    )
    return evidence, card_record, binding


def test_default_case_path_uses_exact_resolved_root_hash(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    case_directory = tmp_path / "workspace" / ".name-atlas" / "cases"

    selected = default_case_path(source, case_directory=case_directory)

    expected_digest = hashlib.sha256(
        f"case-root\0{source.resolve().as_posix()}".encode()
    ).hexdigest()[:16]
    assert selected == case_directory.resolve() / f"{expected_digest}.json"
    assert selected.is_absolute()


def test_default_case_path_remains_derivable_after_source_disappears(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    case_directory = tmp_path / ".name-atlas" / "cases"
    before = default_case_path(source, case_directory=case_directory)
    source.rmdir()

    assert default_case_path(source, case_directory=case_directory) == before


def test_new_case_is_strict_path_neutral_and_deterministic(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    case = _new_case(tmp_path, hero_contract)

    assert case.schema_version == "migration-case.v1"
    assert case.revision == 0
    assert case.lifecycle is CaseLifecycle.REVIEW
    assert case.source_root == HERO_ROOT.resolve()
    assert "source_root" not in case.source_snapshot.model_dump(mode="json")
    assert canonical_case_bytes(case) == canonical_case_bytes(case)
    assert canonical_case_bytes(case).endswith(b"\n")
    assert b'"schema_version":"migration-case.v1"' in canonical_case_bytes(case)
    with pytest.raises(ValidationError):
        MigrationCase.model_validate(
            {**case.model_dump(mode="python"), "unexpected": True},
            strict=True,
        )


def test_nested_evidence_card_and_human_binding_survive_restart(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    package, proposals = hero_contract
    case = _new_case(tmp_path, hero_contract)
    evidence, card, binding = _meaning_review_records(package, proposals)
    bound_case = _replace_case(
        case,
        evidence_records=(evidence,),
        card_records=(card,),
        decisions=(binding,),
    )
    store = MigrationCaseStore(bound_case.local_paths.case_path)

    with store.writer() as writer:
        saved = writer.save(bound_case, expected_revision=None)
    loaded = store.load()

    assert loaded == saved
    assert loaded.evidence_records == (evidence,)
    assert loaded.card_records == (card,)
    assert loaded.decisions == (binding,)
    assert loaded.decisions[0].decision.resolved_targets == (
        binding.decision.resolved_targets
    )
    assert bound_case.source_root.as_posix().encode() in canonical_case_bytes(
        bound_case
    )


def test_meaning_decision_cannot_claim_batch_approval(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    package, proposals = hero_contract
    case = _new_case(tmp_path, hero_contract)
    evidence, card, binding = _meaning_review_records(package, proposals)
    forged_binding = CaseDecisionBinding.model_validate(
        {
            **binding.model_dump(mode="python"),
            "decision_method": CaseDecisionMethod.BATCH_APPROVAL,
        },
        strict=True,
    )

    with pytest.raises(
        ValidationError,
        match="Batch approval is permitted only for mechanically low-risk families",
    ):
        _replace_case(
            case,
            evidence_records=(evidence,),
            card_records=(card,),
            decisions=(forged_binding,),
        )


def test_pending_decision_cannot_claim_human_timestamp_or_card_provenance(
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    package, proposals = hero_contract
    evidence, card, binding = _meaning_review_records(package, proposals)
    family_id = binding.family_id

    with pytest.raises(ValidationError, match="human-action timestamp"):
        CaseDecisionBinding(
            family_id=family_id,
            decision=pending_family(family_id),
            decision_method=None,
            decision_timestamp=binding.decision_timestamp,
        )

    with pytest.raises(ValidationError, match="cannot claim evidence/card"):
        CaseDecisionBinding(
            family_id=family_id,
            decision=pending_family(family_id),
            decision_method=None,
            decision_timestamp=None,
            evidence_fingerprint=evidence.evidence_fingerprint,
            card_fingerprint=card.card_fingerprint,
        )


def test_partial_edited_target_map_is_a_clean_case_load_blocker(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    package, proposals = hero_contract
    case = _new_case(tmp_path, hero_contract)
    evidence, card, binding = _meaning_review_records(package, proposals)
    payload = case.model_dump(mode="json")
    payload["evidence_records"] = [evidence.model_dump(mode="json")]
    payload["card_records"] = [card.model_dump(mode="json")]
    binding_payload = binding.model_dump(mode="json")
    binding_payload["decision"]["action"] = "edited"
    binding_payload["decision"]["human_input"] = "human-choice"
    binding_payload["decision_method"] = "human_edit"
    del binding_payload["decision"]["resolved_targets"]["original"]
    payload["decisions"] = [binding_payload]
    case_path = case.local_paths.case_path
    case_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CaseLoadError,
        match="missing, unreadable, corrupt, or unsupported",
    ):
        load_case(case_path)


def test_case_authority_rejects_orphan_and_recomputed_evidence(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    package, proposals = hero_contract
    case = _new_case(tmp_path, hero_contract)
    evidence, card, binding = _meaning_review_records(package, proposals)

    with pytest.raises(ValidationError, match="exact family pairs"):
        _replace_case(case, evidence_records=(evidence,))

    altered_packet = evidence.packet.model_copy(
        update={
            "neighboring_paths": (
                *evidence.packet.neighboring_paths,
                "objects/forged-neighbor.txt",
            )
        }
    )
    altered_fingerprint = evidence_fingerprint(altered_packet)
    altered_evidence = evidence.model_copy(
        update={
            "packet": altered_packet,
            "evidence_fingerprint": altered_fingerprint,
        }
    )
    altered_card = card.model_copy(update={"evidence_fingerprint": altered_fingerprint})
    altered_binding = binding.model_copy(
        update={"evidence_fingerprint": altered_fingerprint}
    )
    with pytest.raises(ValidationError, match="deterministic family evidence"):
        _replace_case(
            case,
            evidence_records=(altered_evidence,),
            card_records=(altered_card,),
            decisions=(altered_binding,),
        )


def test_stale_differences_must_be_exact_transitions_from_case_snapshot(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    case = _new_case(tmp_path, hero_contract)
    snapshot_member = case.source_snapshot.members[0]
    unknown_member = snapshot_member.model_copy(
        update={"relative_path": "objects/not-in-snapshot.txt"}
    )

    with pytest.raises(ValidationError, match="exactly match the case source snapshot"):
        _replace_case(
            case,
            lifecycle=CaseLifecycle.STALE,
            stale_differences=(
                SourceDifference(
                    kind=SourceDifferenceKind.REMOVED,
                    before=unknown_member,
                ),
            ),
        )

    with pytest.raises(ValidationError, match="must be absent"):
        _replace_case(
            case,
            lifecycle=CaseLifecycle.STALE,
            stale_differences=(
                SourceDifference(
                    kind=SourceDifferenceKind.ADDED,
                    after=snapshot_member,
                ),
            ),
        )


def test_case_lifecycle_must_match_durable_decisions(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    case = _new_case(tmp_path, hero_contract)

    with pytest.raises(ValidationError, match="lifecycle does not match"):
        _replace_case(case, lifecycle=CaseLifecycle.BLOCKED)


def test_store_owns_monotonic_revision_and_rejects_stale_writer(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    initial_time = datetime(2026, 7, 18, 1, 0, tzinfo=oslo_tz)
    update_time = initial_time + timedelta(minutes=3)
    case = _new_case(tmp_path, hero_contract, now=initial_time)
    store = MigrationCaseStore(
        case.local_paths.case_path,
        clock=lambda: update_time,
    )
    with store.writer() as writer:
        created = writer.save(case, expected_revision=None)

    changed = _replace_case(created, case_name="Reviewed hero migration")
    with store.writer() as writer:
        revision_one = writer.save(changed, expected_revision=0)

    assert revision_one.revision == 1
    assert revision_one.updated_at == update_time
    with (
        store.writer() as writer,
        pytest.raises(CaseRevisionError, match="revision changed"),
    ):
        writer.save(changed, expected_revision=0)
    assert store.load() == revision_one


def test_writer_lock_is_nonblocking_and_held_for_complete_context(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    case = _new_case(tmp_path, hero_contract)
    first = MigrationCaseStore(case.local_paths.case_path)
    second = MigrationCaseStore(case.local_paths.case_path)

    with first.writer() as first_writer:
        first_writer.save(case, expected_revision=None)
        with (
            pytest.raises(CaseLockError, match="already open"),
            second.writer(),
        ):
            pytest.fail("A second writer unexpectedly acquired the case lock.")

    with second.writer() as second_writer:
        assert second_writer.load() == case


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda _: b"{not-json", "corrupt, or unsupported"),
        (
            lambda raw: json.dumps(
                {
                    **json.loads(raw),
                    "schema_version": "migration-case.v999",
                }
            ).encode(),
            "corrupt, or unsupported",
        ),
        (
            lambda raw: json.dumps({**json.loads(raw), "unexpected": True}).encode(),
            "corrupt, or unsupported",
        ),
        (_change_snapshot_commitment, "corrupt, or unsupported"),
    ],
)
def test_strict_load_blocks_corruption_unknown_schema_and_extra_fields(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
    mutation: Callable[[bytes], bytes],
    message: str,
) -> None:
    case = _new_case(tmp_path, hero_contract)
    path = case.local_paths.case_path
    path.write_bytes(mutation(canonical_case_bytes(case)))

    with pytest.raises(CaseLoadError, match=message):
        MigrationCaseStore(path).load()


def test_atomic_replace_failure_preserves_prior_revision(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _new_case(tmp_path, hero_contract)
    store = MigrationCaseStore(case.local_paths.case_path)
    with store.writer() as writer:
        writer.save(case, expected_revision=None)
    original_bytes = case.local_paths.case_path.read_bytes()
    changed = _replace_case(case, case_name="Must not become durable")

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(case_module.os, "replace", fail_replace)
    with (
        store.writer() as writer,
        pytest.raises(CaseWriteError, match="atomically"),
    ):
        writer.save(changed, expected_revision=0)

    assert case.local_paths.case_path.read_bytes() == original_bytes
    assert not tuple(case.local_paths.case_path.parent.glob(".case.json.*.tmp"))


def test_handoff_ready_case_is_read_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "objects").mkdir(parents=True)
    (source / "metadata").mkdir()
    (source / "objects" / "poster.svg").write_text("poster", encoding="utf-8")
    (source / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\n"
        "objects/poster.svg,LOW-0001,Ordinary poster\n",
        encoding="utf-8",
    )
    package = import_package(source)
    proposals = build_proposals(package.families)
    base = new_migration_case(
        package,
        proposals,
        case_path=tmp_path / "case.json",
        output_root=tmp_path / "output",
        case_name="Finalized low-risk case",
        now=datetime(2026, 7, 18, 1, 0, tzinfo=oslo_tz),
    )
    family = package.families[0]
    decision = CaseDecisionBinding(
        family_id=family.family_id,
        decision=approve_family(
            family,
            proposals,
            semantic_card_available=False,
        ),
        decision_method=CaseDecisionMethod.BATCH_APPROVAL,
        decision_timestamp=datetime(
            2026,
            7,
            18,
            1,
            10,
            tzinfo=oslo_tz,
        ),
        evidence_fingerprint=None,
        card_fingerprint=None,
    )
    finalized = _replace_case(
        base,
        decisions=(decision,),
        local_paths=LocalCasePointers(
            output_root=base.local_paths.output_root,
            case_path=base.local_paths.case_path,
            stage_path=(tmp_path / "output" / "stage").resolve(),
            handoff_path=(tmp_path / "handoff" / "bag").resolve(),
        ),
        receipt_fingerprint="a" * 64,
        lifecycle=CaseLifecycle.HANDOFF_READY,
    )
    store = MigrationCaseStore(finalized.local_paths.case_path)
    with store.writer() as writer:
        writer.save(finalized, expected_revision=None)

    with (
        store.writer() as writer,
        pytest.raises(CaseFinalizedError, match="read-only"),
    ):
        writer.save(finalized, expected_revision=0)


def test_stale_case_is_terminal_at_store_boundary(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    base = _new_case(tmp_path, hero_contract)
    store = MigrationCaseStore(base.local_paths.case_path)
    with store.writer() as writer:
        writer.save(base, expected_revision=None)
    stale = _replace_case(
        base,
        lifecycle=CaseLifecycle.STALE,
        source_scan_blocker=SourceScanBlocker(
            detail="Source root cannot be inspected."
        ),
    )
    with store.writer() as writer:
        durable_stale = writer.save(stale, expected_revision=0)
    stale_bytes = base.local_paths.case_path.read_bytes()

    with (
        store.writer() as writer,
        pytest.raises(CaseFinalizedError, match="stale Migration Case is terminal"),
    ):
        writer.save(durable_stale, expected_revision=1)

    assert base.local_paths.case_path.read_bytes() == stale_bytes


def test_writer_refuses_calls_outside_process_lock(
    tmp_path: Path,
    hero_contract: tuple[SourcePackage, tuple[PathProposal, ...]],
) -> None:
    case = _new_case(tmp_path, hero_contract)
    writer = MigrationCaseStore(case.local_paths.case_path).writer()

    with pytest.raises(CaseLockError, match="active process-held"):
        writer.save(case, expected_revision=None)
