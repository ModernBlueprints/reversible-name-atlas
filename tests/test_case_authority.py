"""Integrated fail-closed tests for the Migration Case as sole authority."""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from name_atlas import workflow as workflow_module
from name_atlas.cases import (
    CaseFinalizedError,
    CaseLifecycle,
    CaseRevisionError,
    MigrationCase,
    MigrationCaseError,
    SourceDifferenceKind,
    canonical_case_bytes,
    load_case,
)
from name_atlas.decision_cards import (
    RecordedDecisionCard,
    RecordedReplayDecisionCardProvider,
    ReplayUsage,
    build_evidence_packet,
    evidence_fingerprint,
)
from name_atlas.domain import (
    CandidateExplanation,
    DecisionCard,
    EvidencePacket,
    LinkedObservation,
)
from name_atlas.package_import import import_package
from name_atlas.proposals import build_proposals
from name_atlas.verification import BagItPackageValidator
from name_atlas.workflow import WorkflowSession

PROJECT_ROOT = Path(__file__).parents[1]
HERO_ROOT = PROJECT_ROOT / "sample_data" / "hero"
REPLAY_RECORD = (
    PROJECT_ROOT / "src" / "name_atlas" / "recordings" / "hero_decision_card.json"
)


class NeverCalledProvider:
    """Provider double that makes an authority-ordering failure unmistakable."""

    provider_kind = "test"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        del packet
        self.calls += 1
        raise AssertionError("Provider was called before durable case validation.")


class CountingReplayProvider:
    """Count calls while preserving the exact recorded-provider boundary."""

    provider_kind = "replay"

    def __init__(self, record: bytes | RecordedDecisionCard | None = None) -> None:
        self._provider = RecordedReplayDecisionCardProvider(
            record if record is not None else REPLAY_RECORD.read_bytes()
        )
        self.record = self._provider.record
        self.calls = 0

    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        self.calls += 1
        return await self._provider.generate(packet)


class SourceMutatingReplayProvider(CountingReplayProvider):
    """Change one source member while the provider request is in flight."""

    def __init__(self, source_member: Path, record: RecordedDecisionCard) -> None:
        super().__init__(record)
        self._source_member = source_member

    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        card = await super().generate(packet)
        _rewrite_same_size(self._source_member)
        return card


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _write_low_risk_package(root: Path) -> None:
    (root / "objects").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "objects" / "plain-note.txt").write_bytes(b"plain")
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\nobjects/plain-note.txt,LOW-0001,Plain note\n",
        encoding="utf-8",
    )


def _write_meaning_risk_package(root: Path) -> None:
    (root / "objects").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "objects" / "campaña.svg").write_bytes(b"campaign")
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\nobjects/campaña.svg,MEAN-0001,Campaña\n",
        encoding="utf-8",
    )


def _record_for_single_meaning_family(source: Path) -> RecordedDecisionCard:
    package = import_package(source)
    proposals = build_proposals(package.families)
    family = package.families[0]
    packet = build_evidence_packet(package, family, proposals)
    evidence_id = packet.metadata_evidence[0].evidence_id
    observation = LinkedObservation(
        text="The normalized descriptor may lose a meaningful written distinction.",
        evidence_ids=(evidence_id,),
    )
    card = DecisionCard(
        possible_interpretations=(observation,),
        possible_meaning_loss=(observation,),
        uncertainty="The bounded evidence does not establish human intent.",
        why_the_distinction_matters="The descriptor remains visible after ingest.",
        discriminating_question="Which supplied path preserves the intended name?",
        candidate_explanations=(
            CandidateExplanation(
                candidate_path=packet.candidate_paths[0],
                explanation="This candidate follows the deterministic profile.",
                evidence_ids=(evidence_id,),
            ),
        ),
    )
    return RecordedDecisionCard(
        model="gpt-5.6",
        schema_version="decision-card.v1",
        evidence_fingerprint=evidence_fingerprint(packet),
        generated_at=datetime(
            2026,
            7,
            18,
            2,
            0,
            tzinfo=ZoneInfo("Europe/Oslo"),
        ),
        decision_card=card,
        usage=ReplayUsage(
            input_tokens=0,
            cached_input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
            estimated_cost_usd=0.0,
        ),
    )


def _rewrite_same_size(path: Path) -> None:
    original = path.read_bytes()
    assert original
    path.write_bytes(bytes((original[0] ^ 1,)) + original[1:])
    assert path.stat().st_size == len(original)


def _add_source_member(source: Path) -> None:
    (source / "objects" / "added.txt").write_bytes(b"added")


def _remove_source_member(source: Path) -> None:
    (source / "objects" / "plain-note.txt").unlink()


def _rename_source_member(source: Path) -> None:
    (source / "objects" / "plain-note.txt").rename(
        source / "objects" / "renamed-note.txt"
    )


def _resize_source_member(source: Path) -> None:
    (source / "objects" / "plain-note.txt").write_bytes(b"plain but longer")


def _meaning_family_id(workflow: WorkflowSession) -> str:
    return next(
        family.family_id
        for family in workflow.package.families
        if family.canonical_identifier == "NA-0001"
    )


def _make_workflow(
    *,
    source: Path,
    output: Path,
    case_path: Path,
    provider: NeverCalledProvider | CountingReplayProvider,
) -> WorkflowSession:
    return WorkflowSession(
        source_root=source,
        output_root=output,
        decision_card_provider=provider,
        package_validator=BagItPackageValidator(),
        case_path=case_path,
        case_name="Authority test case",
    )


def test_same_size_content_change_before_batch_approval_persists_exact_stale_case(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    provider = NeverCalledProvider()
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=provider,
    )
    try:
        _rewrite_same_size(source / "objects" / "plain-note.txt")

        with pytest.raises(
            MigrationCaseError,
            match=r"content_changed: objects/plain-note\.txt",
        ):
            workflow.approve_low_risk()

        assert workflow.decisions == {}
        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.stale_differences[0].kind is (
            SourceDifferenceKind.CONTENT_CHANGED
        )
        assert workflow.case.stale_differences[0].before is not None
        assert (
            workflow.case.stale_differences[0].before.relative_path
            == "objects/plain-note.txt"
        )
        assert load_case(case_path) == workflow.case
        assert provider.calls == 0
    finally:
        workflow.close()


@pytest.mark.parametrize(
    ("mutator", "expected_kind", "expected_summary"),
    (
        (
            _add_source_member,
            SourceDifferenceKind.ADDED,
            "added: objects/added.txt",
        ),
        (
            _remove_source_member,
            SourceDifferenceKind.REMOVED,
            "removed: objects/plain-note.txt",
        ),
        (
            _rename_source_member,
            SourceDifferenceKind.RENAMED,
            "renamed: objects/plain-note.txt -> objects/renamed-note.txt",
        ),
        (
            _resize_source_member,
            SourceDifferenceKind.RESIZED,
            "resized: objects/plain-note.txt (5 -> 16 bytes)",
        ),
    ),
    ids=("added", "removed", "renamed", "resized"),
)
def test_each_structural_source_change_persists_exact_stale_authority(
    tmp_path: Path,
    mutator: Callable[[Path], None],
    expected_kind: SourceDifferenceKind,
    expected_summary: str,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    assert workflow.case is not None
    original_snapshot = workflow.case.source_snapshot
    mutator(source)
    try:
        with pytest.raises(MigrationCaseError, match=re.escape(expected_summary)):
            workflow.approve_low_risk()

        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.source_snapshot == original_snapshot
        assert tuple(
            difference.kind for difference in workflow.case.stale_differences
        ) == (expected_kind,)
        assert workflow.case.source_scan_blocker is None
        assert workflow.case.revision == 1
        assert workflow.decisions == {}
        assert load_case(case_path) == workflow.case
    finally:
        workflow.close()


def test_source_scan_failure_persists_blocker_without_invented_member_diff(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    moved_source = tmp_path / "source-moved"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    source.rename(moved_source)
    try:
        with pytest.raises(MigrationCaseError, match="source_scan_failed"):
            workflow.approve_low_risk()

        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.stale_differences == ()
        assert workflow.case.source_scan_blocker is not None
        assert workflow.case.source_scan_blocker.code.value == "source_scan_failed"
        assert load_case(case_path) == workflow.case
    finally:
        workflow.close()


def test_stale_case_is_terminal_and_idempotent_after_source_is_restored(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    original = (source / "objects" / "plain-note.txt").read_bytes()
    _rewrite_same_size(source / "objects" / "plain-note.txt")
    with pytest.raises(MigrationCaseError, match="content_changed"):
        workflow.approve_low_risk()
    workflow.close()
    stale_bytes = case_path.read_bytes()
    (source / "objects" / "plain-note.txt").write_bytes(original)

    restarted = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    try:
        assert restarted.case is not None
        assert restarted.case.lifecycle is CaseLifecycle.STALE
        assert restarted.case.revision == 1
        assert case_path.read_bytes() == stale_bytes
        with pytest.raises(MigrationCaseError, match="fresh case"):
            restarted.approve_low_risk()
        assert case_path.read_bytes() == stale_bytes
    finally:
        restarted.close()


@pytest.mark.anyio
async def test_source_change_before_card_generation_makes_zero_provider_calls(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    shutil.copytree(HERO_ROOT, source)
    case_path = tmp_path / "case.json"
    provider = NeverCalledProvider()
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=provider,
    )
    try:
        family_id = _meaning_family_id(workflow)
        meaning_family = workflow.family(family_id)
        _rewrite_same_size(source / meaning_family.original.relative_path)

        with pytest.raises(MigrationCaseError, match="content_changed"):
            await workflow.generate_card(family_id)

        assert provider.calls == 0
        assert workflow.cards_requested == 0
        assert workflow.cards == {}
        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.evidence_records == ()
        assert workflow.case.card_records == ()
        assert load_case(case_path) == workflow.case
    finally:
        workflow.close()


@pytest.mark.anyio
async def test_durable_card_reuse_after_restart_makes_zero_provider_calls(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_meaning_risk_package(source)
    case_path = tmp_path / "case.json"
    first_provider = CountingReplayProvider(_record_for_single_meaning_family(source))
    first = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=first_provider,
    )
    family_id = first.package.families[0].family_id
    expected_card = await first.generate_card(family_id)
    first.close()
    durable_before = case_path.read_bytes()
    revision_before = load_case(case_path).revision

    second_provider = NeverCalledProvider()
    restarted = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=second_provider,
    )
    try:
        assert await restarted.generate_card(family_id) == expected_card
        assert second_provider.calls == 0
        assert restarted.cache_hits == 1
        assert restarted.case is not None
        assert restarted.case.revision == revision_before
        assert case_path.read_bytes() == durable_before
    finally:
        restarted.close()


@pytest.mark.anyio
async def test_source_change_during_provider_response_persists_stale_without_card(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_meaning_risk_package(source)
    source_member = source / "objects" / "campaña.svg"
    case_path = tmp_path / "case.json"
    provider = SourceMutatingReplayProvider(
        source_member,
        _record_for_single_meaning_family(source),
    )
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=provider,
    )
    family_id = workflow.package.families[0].family_id
    try:
        with pytest.raises(MigrationCaseError, match="content_changed"):
            await workflow.generate_card(family_id)

        assert provider.calls == 1
        assert workflow.cards == {}
        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.evidence_records == ()
        assert workflow.case.card_records == ()
        assert workflow.case.decisions == ()
        assert load_case(case_path) == workflow.case
    finally:
        workflow.close()


def test_source_change_before_stage_creates_no_pending_output(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    output = tmp_path / "output"
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=output,
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    try:
        workflow.approve_low_risk()
        _rewrite_same_size(source / "objects" / "plain-note.txt")

        with pytest.raises(MigrationCaseError, match="content_changed"):
            workflow.stage()

        assert workflow.stage_result is None
        assert not output.exists() or tuple(output.iterdir()) == ()
        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.local_paths.stage_path is None
        assert workflow.case.local_paths.handoff_path is None
        assert load_case(case_path) == workflow.case
    finally:
        workflow.close()


def test_source_change_during_second_import_is_persisted_stale_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    original_import = workflow_module.import_package
    mutated = False

    def mutate_then_import(root: Path) -> Any:
        nonlocal mutated
        if not mutated:
            mutated = True
            _rewrite_same_size(source / "objects" / "plain-note.txt")
        return original_import(root)

    monkeypatch.setattr(workflow_module, "import_package", mutate_then_import)
    try:
        with pytest.raises(MigrationCaseError, match="content_changed"):
            workflow.approve_low_risk()

        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.STALE
        assert workflow.case.revision == 1
        assert workflow.case.stale_differences[0].kind is (
            SourceDifferenceKind.CONTENT_CHANGED
        )
        assert workflow.decisions == {}
        assert load_case(case_path) == workflow.case
    finally:
        workflow.close()


def test_source_change_during_restart_import_is_persisted_stale_on_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    initial = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    initial.close()
    original_import = workflow_module.import_package
    mutated = False

    def mutate_then_import(root: Path) -> Any:
        nonlocal mutated
        if not mutated:
            mutated = True
            _rewrite_same_size(source / "objects" / "plain-note.txt")
        return original_import(root)

    monkeypatch.setattr(workflow_module, "import_package", mutate_then_import)
    restarted = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    try:
        assert restarted.case is not None
        assert restarted.case.lifecycle is CaseLifecycle.STALE
        assert restarted.case.revision == 1
        assert restarted.case.stale_differences[0].kind is (
            SourceDifferenceKind.CONTENT_CHANGED
        )
        assert load_case(case_path) == restarted.case
    finally:
        restarted.close()


def test_external_durable_revision_conflict_rehydrates_and_blocks_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    try:
        assert workflow.case is not None
        external = MigrationCase.model_validate(
            {
                **workflow.case.model_dump(mode="python"),
                "revision": workflow.case.revision + 1,
                "case_name": "Externally advanced case",
            },
            strict=True,
        )
        case_path.write_bytes(canonical_case_bytes(external))

        with pytest.raises(CaseRevisionError, match="revision"):
            workflow.approve_low_risk()

        assert workflow.case == external
        assert workflow.case.case_name == "Externally advanced case"
        assert workflow.decisions == {}
        assert load_case(case_path) == external
    finally:
        workflow.close()


def test_same_revision_byte_replacement_rehydrates_and_blocks_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    try:
        assert workflow.case is not None
        replacement = MigrationCase.model_validate(
            {
                **workflow.case.model_dump(mode="python"),
                "case_name": "Same-revision replacement",
            },
            strict=True,
        )
        case_path.write_bytes(canonical_case_bytes(replacement))

        with pytest.raises(CaseRevisionError, match="durable authority changed"):
            workflow.approve_low_risk()

        assert workflow.case == replacement
        assert workflow.decisions == {}
        assert load_case(case_path) == replacement
    finally:
        workflow.close()


@pytest.mark.anyio
async def test_handoff_ready_case_rejects_before_provider_or_runtime_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_meaning_risk_package(source)
    provider = CountingReplayProvider(_record_for_single_meaning_family(source))
    workflow = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=tmp_path / "case.json",
        provider=provider,
    )
    try:
        family_id = workflow.package.families[0].family_id
        await workflow.generate_card(family_id)
        workflow.approve(family_id)
        workflow.stage()
        assert workflow.case is not None
        assert workflow.case.lifecycle is CaseLifecycle.HANDOFF_READY

        before = {
            "case": workflow.case,
            "cards": workflow.cards.copy(),
            "card_fingerprints": workflow.card_fingerprints.copy(),
            "decisions": workflow.decisions.copy(),
            "decision_timestamps": workflow.decision_timestamps.copy(),
            "decision_methods": workflow.decision_methods.copy(),
            "cards_requested": workflow.cards_requested,
            "cache_hits": workflow.cache_hits,
            "stage_result": workflow.stage_result,
        }
        provider_calls = provider.calls

        with pytest.raises(CaseFinalizedError, match="read-only"):
            await workflow.generate_card(family_id)

        assert provider.calls == provider_calls
        assert workflow.case == before["case"]
        assert workflow.cards == before["cards"]
        assert workflow.card_fingerprints == before["card_fingerprints"]
        assert workflow.decisions == before["decisions"]
        assert workflow.decision_timestamps == before["decision_timestamps"]
        assert workflow.decision_methods == before["decision_methods"]
        assert workflow.cards_requested == before["cards_requested"]
        assert workflow.cache_hits == before["cache_hits"]
        assert workflow.stage_result == before["stage_result"]
    finally:
        workflow.close()


def test_handoff_ready_case_remains_immutable_after_sender_source_changes(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write_low_risk_package(source)
    case_path = tmp_path / "case.json"
    output = tmp_path / "output"
    first = _make_workflow(
        source=source,
        output=output,
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    first.approve_low_risk()
    result = first.stage()
    assert first.case is not None
    assert first.case.lifecycle is CaseLifecycle.HANDOFF_READY
    first.close()
    finalized_bytes = case_path.read_bytes()

    _rewrite_same_size(source / "objects" / "plain-note.txt")
    restarted = _make_workflow(
        source=source,
        output=output,
        case_path=case_path,
        provider=NeverCalledProvider(),
    )
    try:
        assert restarted.case is not None
        assert restarted.case.lifecycle is CaseLifecycle.HANDOFF_READY
        assert restarted.case.receipt_fingerprint == result.receipt_fingerprint
        assert restarted.case.stale_differences == ()
        assert restarted.case.source_scan_blocker is None
        assert case_path.read_bytes() == finalized_bytes
        with pytest.raises(CaseFinalizedError, match="read-only"):
            restarted.approve_low_risk()
        assert case_path.read_bytes() == finalized_bytes
    finally:
        restarted.close()


def _alter_approved_target(payload: dict[str, Any]) -> None:
    decision = payload["decisions"][0]["decision"]
    role = next(iter(decision["resolved_targets"]))
    original_target = Path(decision["resolved_targets"][role])
    decision["resolved_targets"][role] = (
        original_target.parent / "NA-0001__forged__original.svg"
    ).as_posix()


def _alter_evidence_binding(payload: dict[str, Any]) -> None:
    payload["decisions"][0]["evidence_fingerprint"] = "0" * 64


def _alter_card_binding(payload: dict[str, Any]) -> None:
    payload["decisions"][0]["card_fingerprint"] = "0" * 64


@pytest.mark.anyio
@pytest.mark.parametrize(
    "malformation",
    (_alter_approved_target, _alter_evidence_binding, _alter_card_binding),
    ids=("approved-target", "evidence-binding", "card-binding"),
)
async def test_malformed_persisted_meaning_binding_blocks_reopen(
    tmp_path: Path,
    malformation: Callable[[dict[str, Any]], None],
) -> None:
    source = tmp_path / "source"
    shutil.copytree(HERO_ROOT, source)
    case_path = tmp_path / "case.json"
    first = _make_workflow(
        source=source,
        output=tmp_path / "output",
        case_path=case_path,
        provider=CountingReplayProvider(),
    )
    meaning_family_id = _meaning_family_id(first)
    await first.generate_card(meaning_family_id)
    first.approve(meaning_family_id)
    first.close()

    payload = json.loads(case_path.read_text(encoding="utf-8"))
    malformation(payload)
    case_path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    reopen_provider = NeverCalledProvider()

    with pytest.raises(MigrationCaseError):
        _make_workflow(
            source=source,
            output=tmp_path / "output",
            case_path=case_path,
            provider=reopen_provider,
        )

    assert reopen_provider.calls == 0
