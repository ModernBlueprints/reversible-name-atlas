"""Structural boundary tests."""

from pathlib import Path

from name_atlas.domain import (
    CandidateExplanation,
    DecisionCard,
    EvidencePacket,
    LinkedObservation,
    PackageValidationResult,
)
from name_atlas.ports import DecisionCardProvider, PackageValidator


class FakeDecisionCardProvider:
    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        observation = LinkedObservation(
            text="A bounded interpretation.",
            evidence_ids=("metadata:title",),
        )
        return DecisionCard(
            possible_interpretations=(observation,),
            possible_meaning_loss=(observation,),
            uncertainty="The supplied evidence is not semantic truth.",
            why_the_distinction_matters="The human must preserve intended meaning.",
            discriminating_question="Which descriptor preserves the intended name?",
            candidate_explanations=(
                CandidateExplanation(
                    candidate_path=packet.candidate_paths[0],
                    explanation="A mechanically supplied candidate.",
                    evidence_ids=("metadata:title",),
                ),
            ),
        )


class FakePackageValidator:
    def validate(self, bag_root: Path) -> PackageValidationResult:
        return PackageValidationResult(
            validator="bagit",
            valid=bag_root.name == "valid",
        )


def test_provider_and_validator_are_structural_protocols() -> None:
    assert isinstance(FakeDecisionCardProvider(), DecisionCardProvider)
    assert isinstance(FakePackageValidator(), PackageValidator)
