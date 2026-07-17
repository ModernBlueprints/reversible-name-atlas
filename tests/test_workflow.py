"""Focused connected M1 transaction through Atlas, Decisions, and Proof."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from name_atlas.app import create_app
from name_atlas.config import RuntimeConfig
from name_atlas.decisions import DecisionError
from name_atlas.domain import (
    CandidateExplanation,
    DecisionCard,
    EvidencePacket,
    LinkedObservation,
    RunMode,
)
from name_atlas.verification import BagItPackageValidator
from name_atlas.workflow import WorkflowSession

HERO_ROOT = Path(__file__).parents[1] / "sample_data" / "hero"


class FakeDecisionCardProvider:
    """Deterministic test double that has no human or staging authority."""

    def __init__(self) -> None:
        self.packets: list[EvidencePacket] = []

    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        self.packets.append(packet)
        evidence_id = packet.metadata_evidence[2].evidence_id
        observation = LinkedObservation(
            text="The source spelling may distinguish the intended Spanish term.",
            evidence_ids=(evidence_id,),
        )
        return DecisionCard(
            possible_interpretations=(observation,),
            possible_meaning_loss=(observation,),
            uncertainty="The bounded evidence cannot establish semantic intent.",
            why_the_distinction_matters=(
                "The descriptor remains visible after repository ingest."
            ),
            discriminating_question=(
                "Which supplied descriptor preserves the archivist's intended meaning?"
            ),
            candidate_explanations=(
                CandidateExplanation(
                    candidate_path=packet.candidate_paths[0],
                    explanation="This supplied candidate follows the fixed profile.",
                    evidence_ids=(evidence_id,),
                ),
            ),
        )


def _write_low_risk_package(root: Path) -> None:
    (root / "objects").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "objects" / "poster.svg").write_text("poster", encoding="utf-8")
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\n"
        "objects/poster.svg,LOW-0001,Ordinary poster\n",
        encoding="utf-8",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_connected_walking_skeleton_requires_model_then_human_then_proof(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    shutil.copytree(HERO_ROOT, source)
    source_before = {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }
    provider = FakeDecisionCardProvider()
    workflow = WorkflowSession(
        source_root=source,
        output_root=tmp_path / "output",
        decision_card_provider=provider,
        package_validator=BagItPackageValidator(),
    )
    family_id = next(
        family.family_id
        for family in workflow.package.families
        if family.canonical_identifier == "NA-0001"
    )
    collision_family_id = next(
        family.family_id
        for family in workflow.package.families
        if family.canonical_identifier == "CASE-010"
    )
    config = RuntimeConfig.from_environment(mode=RunMode.REPLAY, environ={})
    transport = httpx.ASGITransport(app=create_app(config, workflow))

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=True,
    ) as client:
        initial = await client.get("/")
        premature_stage = await client.post("/stage")
        generated = await client.post(f"/families/{family_id}/generate")
        approved = await client.post(f"/families/{family_id}/approve")
        first_batch = await client.post("/approve-low-risk")
        edited_collision = await client.post(
            f"/families/{collision_family_id}/edit",
            content=b"descriptor=harbor-map-north",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        second_batch = await client.post("/approve-low-risk")
        staged = await client.post("/stage")

    assert initial.status_code == 200
    assert "Campaña poster" in initial.text
    assert "What GPT-5.6 will see" in initial.text
    assert "Blocked by decisions" in premature_stage.text
    assert "GPT is advisory" in generated.text
    assert len(provider.packets) == 1
    assert workflow.decisions[family_id].export_ready is True
    assert "Stored state:" in approved.text
    assert "low-risk families" in first_batch.text
    assert "Human descriptor stored" in edited_collision.text
    assert "low-risk families" in second_batch.text
    assert "Verified round-trip integrity within the supported package contract" in (
        staged.text
    )
    assert workflow.stage_result is not None
    assert workflow.stage_result.artifacts.report.map_row_count == 28
    assert BagItPackageValidator().validate(workflow.stage_result.stage_root).valid
    assert {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    } == source_before


@pytest.mark.anyio
async def test_low_risk_batch_approval_makes_no_provider_call(
    tmp_path: Path,
) -> None:
    source = tmp_path / "low-risk"
    _write_low_risk_package(source)
    provider = FakeDecisionCardProvider()
    workflow = WorkflowSession(
        source_root=source,
        output_root=tmp_path / "output",
        decision_card_provider=provider,
        package_validator=BagItPackageValidator(),
    )
    family_id = workflow.package.families[0].family_id

    view = workflow.view_model()
    item = view["families"][0]  # type: ignore[index]
    assert item["requires_card"] is False
    assert item["packet"] is None
    with pytest.raises(DecisionError, match="no mechanically flagged Meaning risk"):
        await workflow.generate_card(family_id)

    decisions = workflow.approve_low_risk()

    assert len(decisions) == 1
    assert decisions[0].export_ready
    assert provider.packets == []
