"""Thin in-memory coordinator for the connected Atlas, Decisions, and Proof flow."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from name_atlas.artifacts import StageArtifacts
from name_atlas.decision_cards import (
    DecisionCardProviderError,
    RecordedDecisionCard,
    ReplayRecordInvalidError,
    build_evidence_packet,
    canonical_evidence_text,
)
from name_atlas.decisions import (
    DecisionError,
    HumanDecision,
    approve_family,
    edit_family,
    proposals_after_decision,
    refuse_family,
    unresolved_family,
)
from name_atlas.domain import DecisionCard, EvidencePacket, PackageValidationResult
from name_atlas.package_import import ObjectFamily, SourcePackage, import_package
from name_atlas.proposals import PathProposal, RiskCategory, build_proposals
from name_atlas.staging import StageResult, stage_package


class _DecisionCardProvider(Protocol):
    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        """Return one bounded card or raise a typed fail-closed error."""


class _PackageValidator(Protocol):
    def validate(self, bag_root: Path) -> PackageValidationResult:
        """Validate a staged package without mutating it."""


class UnavailableReplayDecisionCardProvider:
    """Replay boundary used before a real validated recording exists."""

    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        del packet
        raise ReplayRecordInvalidError(
            "No validated recorded GPT-5.6 response exists for this evidence yet."
        )


class WorkflowSession:
    """Coordinate domain modules without owning their calculations."""

    def __init__(
        self,
        *,
        source_root: Path,
        output_root: Path,
        decision_card_provider: _DecisionCardProvider,
        package_validator: _PackageValidator,
        replay_record_path: Path | None = None,
    ) -> None:
        self.package: SourcePackage = import_package(source_root)
        self.proposals: tuple[PathProposal, ...] = build_proposals(
            self.package.families
        )
        self.output_root = output_root
        self.decision_card_provider = decision_card_provider
        self.package_validator = package_validator
        self.replay_record_path = replay_record_path
        self.cards: dict[str, DecisionCard] = {}
        self.card_errors: dict[str, str] = {}
        self.decisions: dict[str, HumanDecision] = {}
        self.stage_result: StageResult | None = None
        self.cards_requested = 0

    def family(self, family_id: str) -> ObjectFamily:
        """Return one known stable family or raise a bounded user error."""

        try:
            return next(
                family
                for family in self.package.families
                if family.family_id == family_id
            )
        except StopIteration as exc:
            raise DecisionError(f"Unknown family ID: {family_id}") from exc

    def evidence_packet(self, family_id: str) -> EvidencePacket:
        """Return the complete outbound packet that the UI previews."""

        if not self.family_requires_card(family_id):
            raise DecisionError(
                "This family has no mechanically flagged Meaning risk; "
                "GPT-5.6 is not called."
            )
        return build_evidence_packet(
            self.package,
            self.family(family_id),
            self.proposals,
        )

    def family_requires_card(self, family_id: str) -> bool:
        """Return whether mechanics found a Meaning risk for this family."""

        self.family(family_id)
        return any(
            proposal.meaning_risks
            for proposal in self.proposals
            if proposal.family_id == family_id
        )

    async def generate_card(self, family_id: str) -> DecisionCard:
        """Run the selected provider only after the explicit UI action."""

        packet = self.evidence_packet(family_id)
        self.cards_requested += 1
        self.card_errors.pop(family_id, None)
        try:
            card = await self.decision_card_provider.generate(packet)
        except DecisionCardProviderError as exc:
            self.cards.pop(family_id, None)
            self.decisions[family_id] = unresolved_family(family_id)
            self.card_errors[family_id] = str(exc)
            raise
        self.cards[family_id] = card
        self.decisions.pop(family_id, None)
        self._persist_live_record_if_available()
        return card

    def approve(self, family_id: str) -> HumanDecision:
        """Apply the human's explicit atomic approval."""

        family = self.family(family_id)
        decision = approve_family(
            family,
            self.proposals,
            semantic_card_available=family_id in self.cards,
        )
        self._store_decision(decision)
        return decision

    def edit(self, family_id: str, descriptor: str) -> HumanDecision:
        """Apply one exact human descriptor to every role in the family."""

        family = self.family(family_id)
        other_targets = tuple(
            proposal.proposed_relative_path
            for proposal in self.proposals
            if proposal.family_id != family_id
        )
        decision = edit_family(
            family,
            self.proposals,
            descriptor=descriptor,
            semantic_card_available=family_id in self.cards,
            other_resolved_targets=other_targets,
        )
        self._store_decision(decision)
        return decision

    def approve_low_risk(self) -> tuple[HumanDecision, ...]:
        """Apply one explicit batch action to every currently eligible family."""

        decisions: list[HumanDecision] = []
        for family in self.package.families:
            existing = self.decisions.get(family.family_id)
            if existing is not None and existing.export_ready:
                continue
            selected = tuple(
                proposal
                for proposal in self.proposals
                if proposal.family_id == family.family_id
            )
            if self.family_requires_card(family.family_id) or any(
                proposal.mechanical_blockers for proposal in selected
            ):
                continue
            decisions.append(
                approve_family(
                    family,
                    self.proposals,
                    semantic_card_available=False,
                )
            )
        if not decisions:
            raise DecisionError("No unresolved low-risk families are eligible.")
        for decision in decisions:
            self._store_decision(decision)
        return tuple(decisions)

    def refuse(self, family_id: str) -> HumanDecision:
        """Record an explicit refusal and block complete export."""

        self.family(family_id)
        decision = refuse_family(family_id)
        self._store_decision(decision)
        return decision

    def stage(self) -> StageResult:
        """Run the copy-only transaction from stored family decisions."""

        ordered_decisions = tuple(
            self.decisions.get(
                family.family_id,
                unresolved_family(family.family_id),
            )
            for family in self.package.families
        )
        self.stage_result = stage_package(
            self.package,
            ordered_decisions,
            output_root=self.output_root,
            package_validator=self.package_validator,
        )
        return self.stage_result

    def view_model(self) -> dict[str, object]:
        """Build the exact connected view from current domain and proof objects."""

        risk_counts = {
            category.value: len(
                {
                    proposal.family_id
                    for proposal in self.proposals
                    if any(risk.category is category for risk in proposal.risk_signals)
                }
            )
            for category in RiskCategory
        }
        families = []
        for family in self.package.families:
            proposals = tuple(
                proposal
                for proposal in self.proposals
                if proposal.family_id == family.family_id
            )
            requires_card = self.family_requires_card(family.family_id)
            packet = self.evidence_packet(family.family_id) if requires_card else None
            decision = self.decisions.get(family.family_id)
            card = self.cards.get(family.family_id)
            families.append(
                {
                    "family": family,
                    "proposals": proposals,
                    "packet": packet,
                    "outbound_text": (
                        canonical_evidence_text(packet) if packet is not None else None
                    ),
                    "requires_card": requires_card,
                    "card": card,
                    "card_error": self.card_errors.get(family.family_id),
                    "decision": decision,
                    "ready": bool(decision and decision.export_ready),
                }
            )
        ready_count = sum(bool(item["ready"]) for item in families)
        proof: StageArtifacts | None = (
            self.stage_result.artifacts if self.stage_result is not None else None
        )
        return {
            "source_root": str(self.package.root),
            "snapshot": self.package.snapshot,
            "family_count": len(self.package.families),
            "content_count": len(self.package.content_members),
            "proposal_count": len(self.proposals),
            "risk_counts": {
                category.value: risk_counts[category.value] for category in RiskCategory
            },
            "families": families,
            "ready_count": ready_count,
            "export_ready": ready_count == len(families),
            "cards_requested": self.cards_requested,
            "proof": proof,
            "stage_root": (
                str(self.stage_result.stage_root)
                if self.stage_result is not None
                else None
            ),
        }

    def _store_decision(self, decision: HumanDecision) -> None:
        self.decisions[decision.family_id] = decision
        self.proposals = proposals_after_decision(self.proposals, decision)
        self.stage_result = None

    def _persist_live_record_if_available(self) -> None:
        if self.replay_record_path is None:
            return
        record = getattr(self.decision_card_provider, "last_record", None)
        if not isinstance(record, RecordedDecisionCard):
            return
        self.replay_record_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.replay_record_path.with_suffix(".json.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        try:
            payload = f"{record.model_dump_json(indent=2)}\n".encode()
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, self.replay_record_path)
