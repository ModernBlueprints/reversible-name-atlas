"""Strict portable receipt contracts and deterministic commitment helpers."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import stat
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from name_atlas.artifacts import (
    ControlFileProof,
    ProofStatus,
    VerificationCheck,
)
from name_atlas.decision_cards.evidence import evidence_fingerprint
from name_atlas.decision_cards.models import ReplayUsage
from name_atlas.decisions import HumanAction, HumanDecision
from name_atlas.domain import (
    ContentRole,
    DecisionCard,
    EvidencePacket,
    MemberKind,
    PackageValidationResult,
)
from name_atlas.proposals import PathProposal, RiskCategory, build_proposals
from name_atlas.source import (
    HASH_CHUNK_SIZE,
    ControlRole,
    SourceSnapshot,
    validate_relative_path,
)

if TYPE_CHECKING:
    from name_atlas.cases import (
        CaseDecisionBinding,
        CaseDecisionCardRecord,
        CaseEvidenceRecord,
        MigrationCase,
    )

oslo_tz = ZoneInfo("Europe/Oslo")

PORTABLE_SOURCE_SNAPSHOT_PATH = "name-atlas/source_snapshot.json"
DECISION_LEDGER_PATH = "name-atlas/decision_ledger.json"
FORWARD_PATH_MAP_PATH = "name-atlas/forward_path_map.csv"
REVERSE_PATH_MAP_PATH = "name-atlas/reverse_path_map.csv"
VERIFICATION_REPORT_PATH = "name-atlas/verification_report.json"
VERIFICATION_SUMMARY_PATH = "name-atlas/verification_summary.md"
CHANGE_RECEIPT_PATH = "name-atlas/change_receipt.json"
CHANGE_RECEIPT_HTML_PATH = "name-atlas/change_receipt.html"
ORIGINAL_METADATA_PATH = "name-atlas/original-control/metadata/metadata.csv"
ORIGINAL_NORMALIZATION_PATH = "name-atlas/original-control/normalization.csv"

_REQUIRED_COMMITTED_ARTIFACT_PATHS = frozenset(
    {
        PORTABLE_SOURCE_SNAPSHOT_PATH,
        ORIGINAL_METADATA_PATH,
        DECISION_LEDGER_PATH,
        FORWARD_PATH_MAP_PATH,
        REVERSE_PATH_MAP_PATH,
        VERIFICATION_REPORT_PATH,
        VERIFICATION_SUMMARY_PATH,
        "bagit.txt",
        "bag-info.txt",
        "manifest-sha256.txt",
    }
)
_OPTIONAL_COMMITTED_ARTIFACT_PATHS = frozenset({ORIGINAL_NORMALIZATION_PATH})
_EXCLUDED_RECEIPT_COMMITMENT_PATHS = frozenset(
    {
        CHANGE_RECEIPT_PATH,
        CHANGE_RECEIPT_HTML_PATH,
        "tagmanifest-sha256.txt",
    }
)
_FILE_URI = re.compile(
    r"(?<![A-Za-z0-9+.-])file:(?=/{1,3}|[A-Za-z]:[\\/])",
    flags=re.IGNORECASE,
)
_POSIX_ABSOLUTE_FRAGMENT = re.compile(r"(?<![A-Za-z0-9./])/(?!/)[^\s\"'<>|]+")
_WINDOWS_ABSOLUTE_FRAGMENT = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/][^\s\"'<>|]+|"
    r"\\\\[^\\/\s\"'<>|]+[\\/][^\s\"'<>|]+)"
)

RECEIPT_CLAIM_BOUNDARIES = (
    "Internal transaction consistency within the supported Name Atlas "
    "package contract.",
    "No sender identity, semantic correctness, compliance, or historical "
    "source authenticity is asserted.",
)


class ReceiptContractError(ValueError):
    """Portable receipt data or a commitment input violates its contract."""


class _StrictFrozenModel(BaseModel):
    """Immutable fail-closed base for serialized receipt contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _require_relative_posix(value: str) -> str:
    try:
        return validate_relative_path(value)
    except ValueError as exc:
        raise ValueError("Path must be normalized relative POSIX syntax.") from exc


def _require_case_id(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("case_id must be lowercase UUID4 hexadecimal text")
    try:
        parsed = UUID(hex=value)
    except ValueError as exc:
        raise ValueError("case_id must be lowercase UUID4 hexadecimal text") from exc
    if parsed.version != 4 or parsed.hex != value:
        raise ValueError("case_id must be lowercase UUID4 hexadecimal text")
    return value


def _require_oslo_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    oslo_value = value.astimezone(oslo_tz)
    if value.utcoffset() != oslo_value.utcoffset():
        raise ValueError("timestamp must use the Europe/Oslo offset")
    return value


class PortableSourceMember(_StrictFrozenModel):
    """One sender-path-neutral member in the committed source description."""

    relative_path: str = Field(min_length=1, max_length=4_096)
    role: ContentRole | ControlRole
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    kind: MemberKind

    _validate_path = field_validator("relative_path")(_require_relative_posix)

    @model_validator(mode="after")
    def role_matches_kind(self) -> PortableSourceMember:
        content = self.kind is MemberKind.CONTENT_OBJECT
        if content != isinstance(self.role, ContentRole):
            raise ValueError("Source member role does not match its member kind.")
        expected_location: dict[ContentRole | ControlRole, str] = {
            ContentRole.ORIGINAL: "objects/",
            ContentRole.ACCESS: "manualNormalization/access/",
            ContentRole.PRESERVATION: "manualNormalization/preservation/",
            ControlRole.METADATA: "metadata/metadata.csv",
            ControlRole.NORMALIZATION: "normalization.csv",
        }
        expected = expected_location[self.role]
        location_matches = (
            self.relative_path.startswith(expected)
            if isinstance(self.role, ContentRole)
            else self.relative_path == expected
        )
        if not location_matches:
            raise ValueError("Source member role does not match its package path.")
        return self


def portable_source_commitment(
    members: tuple[PortableSourceMember, ...],
) -> str:
    """Recompute the existing path-neutral source-member commitment."""

    serialized = [member.model_dump(mode="json") for member in members]
    return hashlib.sha256(_canonical_value_bytes(serialized)).hexdigest()


class PortableSourceSnapshot(_StrictFrozenModel):
    """Complete source description with no sender-local root."""

    schema_version: Literal["portable-source-snapshot.v1"] = (
        "portable-source-snapshot.v1"
    )
    members: tuple[PortableSourceMember, ...] = Field(min_length=1)
    commitment: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def require_canonical_complete_snapshot(self) -> PortableSourceSnapshot:
        paths = tuple(member.relative_path for member in self.members)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("Portable source members must be uniquely path-sorted.")
        if portable_source_commitment(self.members) != self.commitment:
            raise ValueError("Portable source commitment does not match its members.")
        return self


def portable_snapshot_from_source(snapshot: SourceSnapshot) -> PortableSourceSnapshot:
    """Project an existing immutable snapshot into its portable v1 contract."""

    members = tuple(
        PortableSourceMember.model_validate(member.model_dump(mode="python"))
        for member in snapshot.members
    )
    return PortableSourceSnapshot(members=members, commitment=snapshot.commitment)


class CardDisplayOrigin(StrEnum):
    """Truthful origin of the exact decision card shown to the human."""

    LIVE = "live"
    RECORDED_REPLAY = "recorded_replay"


def decision_card_fingerprint(
    card: DecisionCard,
    *,
    model: str = "gpt-5.6",
    card_schema: str = "decision-card.v1",
) -> str:
    """Bind an exact validated card to its model and schema identity."""

    value = {
        "card": card.model_dump(mode="json"),
        "card_schema": card_schema,
        "model": model,
    }
    return hashlib.sha256(_canonical_value_bytes(value)).hexdigest()


class MeaningReviewRecord(_StrictFrozenModel):
    """Exact GPT evidence and neutral card presented for one Meaning decision."""

    evidence_packet: EvidencePacket
    evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision_card: DecisionCard
    card_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: Literal["gpt-5.6"] = "gpt-5.6"
    card_schema: Literal["decision-card.v1"] = "decision-card.v1"
    display_origin: CardDisplayOrigin
    generated_at: datetime
    usage: ReplayUsage | None

    _validate_generated_at = field_validator("generated_at")(_require_oslo_timestamp)

    @model_validator(mode="after")
    def validate_bindings(self) -> MeaningReviewRecord:
        if evidence_fingerprint(self.evidence_packet) != self.evidence_fingerprint:
            raise ValueError("Meaning-review evidence fingerprint does not match.")
        expected_card = decision_card_fingerprint(
            self.decision_card,
            model=self.model,
            card_schema=self.card_schema,
        )
        if expected_card != self.card_fingerprint:
            raise ValueError("Meaning-review card fingerprint does not match.")
        return self


class DecisionMethod(StrEnum):
    """Human method that produced one complete resolved-target authority."""

    BATCH_APPROVAL = "batch_approval"
    INDIVIDUAL_APPROVAL = "individual_approval"
    HUMAN_EDIT = "human_edit"


class DecisionLedgerEntry(_StrictFrozenModel):
    """Complete proposal, review, and human-decision record for one family."""

    family_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_proposals: tuple[PathProposal, ...] = Field(min_length=1)
    decision_method: DecisionMethod
    human_decision: HumanDecision
    decided_at: datetime
    meaning_review: MeaningReviewRecord | None

    _validate_timestamp = field_validator("decided_at")(_require_oslo_timestamp)

    @classmethod
    def from_case_records(
        cls,
        *,
        proposals: tuple[PathProposal, ...],
        binding: CaseDecisionBinding,
        evidence_record: CaseEvidenceRecord | None,
        card_record: CaseDecisionCardRecord | None,
    ) -> Self:
        """Project exact local-case authority without inventing provenance."""

        if binding.decision_timestamp is None:
            raise ReceiptContractError(
                "A completed ledger entry requires its persisted decision timestamp."
            )
        if (evidence_record is None) != (card_record is None):
            raise ReceiptContractError(
                "Evidence and card case records must both be present or absent."
            )
        meaning_review = None
        if evidence_record is not None and card_record is not None:
            if (
                binding.evidence_fingerprint != evidence_record.evidence_fingerprint
                or binding.card_fingerprint != card_record.card_fingerprint
            ):
                raise ReceiptContractError(
                    "Case decision bindings do not match their evidence/card records."
                )
            meaning_review = MeaningReviewRecord(
                evidence_packet=evidence_record.packet,
                evidence_fingerprint=evidence_record.evidence_fingerprint,
                decision_card=card_record.card,
                card_fingerprint=card_record.card_fingerprint,
                model=card_record.model,
                card_schema=card_record.card_schema,
                display_origin=CardDisplayOrigin(card_record.display_origin.value),
                generated_at=card_record.generated_at,
                usage=card_record.usage,
            )
        if binding.decision_method is None:
            raise ReceiptContractError(
                "A completed ledger entry requires its persisted decision method."
            )
        try:
            method = DecisionMethod(binding.decision_method.value)
        except ValueError as exc:
            raise ReceiptContractError(
                "A refused decision cannot enter a completed receipt ledger."
            ) from exc
        return cls(
            family_id=binding.family_id,
            initial_proposals=proposals,
            decision_method=method,
            human_decision=binding.decision,
            decided_at=binding.decision_timestamp,
            meaning_review=meaning_review,
        )

    @model_validator(mode="after")
    def validate_family_authority(self) -> DecisionLedgerEntry:
        if self.human_decision.family_id != self.family_id or any(
            proposal.family_id != self.family_id for proposal in self.initial_proposals
        ):
            raise ValueError("Ledger entry contains a mismatched family ID.")
        roles = tuple(proposal.role for proposal in self.initial_proposals)
        if len(roles) != len(set(roles)):
            raise ValueError("Ledger entry contains duplicate proposal roles.")
        if not self.human_decision.export_ready:
            raise ValueError(
                "A completed decision ledger cannot contain unresolved state."
            )
        if set(self.human_decision.resolved_targets) != set(roles):
            raise ValueError("Resolved targets do not cover every proposal role.")
        for proposal in self.initial_proposals:
            _require_relative_posix(proposal.original_relative_path)
            _require_relative_posix(proposal.proposed_relative_path)
        for target in self.human_decision.resolved_targets.values():
            _require_relative_posix(target)

        if self.human_decision.action is HumanAction.EDITED:
            if self.decision_method is not DecisionMethod.HUMAN_EDIT:
                raise ValueError("Edited decisions require human_edit method.")
        else:
            if self.decision_method not in {
                DecisionMethod.BATCH_APPROVAL,
                DecisionMethod.INDIVIDUAL_APPROVAL,
            }:
                raise ValueError("Approved decisions require an approval method.")
            expected = {
                proposal.role: proposal.proposed_relative_path
                for proposal in self.initial_proposals
            }
            if dict(self.human_decision.resolved_targets) != expected:
                raise ValueError("Approved targets differ from the initial proposals.")

        has_meaning_risk = any(
            risk.category is RiskCategory.MEANING
            for proposal in self.initial_proposals
            for risk in proposal.risk_signals
        )
        if has_meaning_risk != (self.meaning_review is not None):
            raise ValueError(
                "Meaning-risk entries require review provenance and other entries "
                "must not fabricate it."
            )
        if self.meaning_review is not None:
            packet = self.meaning_review.evidence_packet
            if self.decided_at < self.meaning_review.generated_at:
                raise ValueError(
                    "A human decision cannot predate its displayed Meaning card."
                )
            if packet.family_id != self.family_id:
                raise ValueError("Meaning-review evidence has a mismatched family ID.")
            proposal_sources = {
                proposal.original_relative_path for proposal in self.initial_proposals
            }
            proposal_targets = {
                proposal.proposed_relative_path for proposal in self.initial_proposals
            }
            if set(packet.original_paths) != proposal_sources:
                raise ValueError("Meaning-review original paths differ from proposals.")
            if set(packet.proposed_paths) != proposal_targets:
                raise ValueError("Meaning-review proposed paths differ from proposals.")
        return self


class DecisionLedgerV2(_StrictFrozenModel):
    """Complete portable human-decision authority for one finalized case."""

    schema_version: Literal["decision-ledger.v2"] = "decision-ledger.v2"
    case_id: str
    decisions: tuple[DecisionLedgerEntry, ...] = Field(min_length=1)

    _validate_case_id = field_validator("case_id")(_require_case_id)

    @classmethod
    def from_case(cls, case: MigrationCase) -> Self:
        """Build the complete finalized ledger directly from durable case state."""

        bindings = {binding.family_id: binding for binding in case.decisions}
        family_ids = {family.family_id for family in case.families}
        if set(bindings) != family_ids or any(
            not binding.decision.export_ready for binding in bindings.values()
        ):
            raise ReceiptContractError(
                "A completed decision ledger requires every case family to be resolved."
            )
        evidence = {record.family_id: record for record in case.evidence_records}
        cards = {record.family_id: record for record in case.card_records}
        initial_proposals = build_proposals(case.families)
        entries: list[DecisionLedgerEntry] = []
        for family_id in sorted(bindings):
            proposals = tuple(
                proposal
                for proposal in initial_proposals
                if proposal.family_id == family_id
            )
            entries.append(
                DecisionLedgerEntry.from_case_records(
                    proposals=proposals,
                    binding=bindings[family_id],
                    evidence_record=evidence.get(family_id),
                    card_record=cards.get(family_id),
                )
            )
        return cls(case_id=case.case_id, decisions=tuple(entries))

    @model_validator(mode="after")
    def require_unique_canonical_order(self) -> DecisionLedgerV2:
        family_ids = tuple(decision.family_id for decision in self.decisions)
        if family_ids != tuple(sorted(family_ids)) or len(family_ids) != len(
            set(family_ids)
        ):
            raise ValueError("Decision ledger entries must be uniquely family-sorted.")
        return self


class VerificationReportV2(_StrictFrozenModel):
    """Path-neutral producer authority for deterministic findings."""

    schema_version: Literal["verification-report.v2"] = "verification-report.v2"
    status: ProofStatus
    claim: str | None
    generated_at: datetime
    source_snapshot_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    prestaging_snapshot_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    postcopy_snapshot_commitment: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )
    source_unchanged: bool | None
    content_object_count: int = Field(ge=0)
    content_bytes: int = Field(ge=0)
    control_files: tuple[ControlFileProof, ...]
    map_row_count: int = Field(ge=0)
    checks: tuple[VerificationCheck, ...] = Field(min_length=1)
    bagit_validation: PackageValidationResult
    artifact_paths: tuple[str, ...] = Field(min_length=1)
    blockers: tuple[str, ...]

    _validate_timestamp = field_validator("generated_at")(_require_oslo_timestamp)

    @field_validator("artifact_paths")
    @classmethod
    def require_portable_artifact_paths(
        cls, values: tuple[str, ...]
    ) -> tuple[str, ...]:
        for value in values:
            _require_relative_posix(value)
        if len(values) != len(set(values)):
            raise ValueError("Report artifact paths must be unique.")
        return values


class ArtifactCommitment(_StrictFrozenModel):
    """Raw exact-byte commitment for one receipt-authoritative artifact."""

    path: str = Field(min_length=1, max_length=4_096)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    _validate_path = field_validator("path")(_require_relative_posix)


class StagedDataMember(_StrictFrozenModel):
    """One regular payload member below data/ in the commitment hash domain."""

    path: str = Field(min_length=1, max_length=4_096)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    _validate_path = field_validator("path")(_require_relative_posix)


class ReceiptCore(_StrictFrozenModel):
    """Immutable non-self-referential core of a portable change receipt."""

    schema_version: Literal["portable-change-receipt.v1"] = "portable-change-receipt.v1"
    package_contract_id: Literal["name-atlas-linked-package.v1"] = (
        "name-atlas-linked-package.v1"
    )
    profile_id: Literal["repository-ready-identity.v1"] = "repository-ready-identity.v1"
    case_id: str
    source_snapshot_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_member_count: int = Field(ge=1)
    source_bytes: int = Field(ge=0)
    staged_data_commitment: str = Field(pattern=r"^[a-f0-9]{64}$")
    staged_data_file_count: int = Field(ge=1)
    staged_data_bytes: int = Field(ge=0)
    artifact_commitments: tuple[ArtifactCommitment, ...] = Field(min_length=10)
    map_row_count: int = Field(ge=1)
    decision_count: int = Field(ge=1)
    gpt_assisted_decision_count: int = Field(ge=0)
    human_decision_count: int = Field(ge=1)
    producer_bagit_validation: PackageValidationResult
    claim_boundaries: tuple[str, ...] = Field(min_length=1)
    verification_summary_path: Literal["name-atlas/verification_summary.md"] = (
        VERIFICATION_SUMMARY_PATH
    )
    receipt_html_path: Literal["name-atlas/change_receipt.html"] = (
        CHANGE_RECEIPT_HTML_PATH
    )

    _validate_case_id = field_validator("case_id")(_require_case_id)

    @model_validator(mode="after")
    def require_complete_acyclic_commitments(self) -> ReceiptCore:
        paths = tuple(item.path for item in self.artifact_commitments)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("Artifact commitments must be uniquely path-sorted.")
        path_set = set(paths)
        if not _REQUIRED_COMMITTED_ARTIFACT_PATHS.issubset(path_set):
            raise ValueError("Receipt omits a required artifact commitment.")
        if path_set.difference(
            _REQUIRED_COMMITTED_ARTIFACT_PATHS | _OPTIONAL_COMMITTED_ARTIFACT_PATHS
        ):
            raise ValueError("Receipt contains an unsupported artifact commitment.")
        if path_set.intersection(_EXCLUDED_RECEIPT_COMMITMENT_PATHS):
            raise ValueError("Receipt commitment graph contains a circular edge.")
        if not self.producer_bagit_validation.valid:
            raise ValueError("A completed receipt requires producer BagIt success.")
        if self.gpt_assisted_decision_count > self.decision_count:
            raise ValueError("GPT-assisted count exceeds decision count.")
        if self.human_decision_count != self.decision_count:
            raise ValueError("Every completed decision must be human-owned.")
        if self.claim_boundaries != RECEIPT_CLAIM_BOUNDARIES:
            raise ValueError(
                "Receipt claim boundaries do not match the frozen contract."
            )
        return self


class ReceiptEnvelope(_StrictFrozenModel):
    """Machine receipt whose fingerprint is outside its own hash domain."""

    receipt: ReceiptCore
    receipt_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_fingerprint(self) -> ReceiptEnvelope:
        if receipt_fingerprint(self.receipt) != self.receipt_fingerprint:
            raise ValueError("Receipt fingerprint does not match ReceiptCore.")
        return self


def canonical_receipt_core_bytes(core: ReceiptCore) -> bytes:
    """Return the exact no-newline ReceiptCore fingerprint hash domain."""

    return _canonical_value_bytes(core.model_dump(mode="json", exclude_none=False))


def receipt_fingerprint(core: ReceiptCore) -> str:
    """Return lowercase SHA-256 for one canonical ReceiptCore."""

    return hashlib.sha256(canonical_receipt_core_bytes(core)).hexdigest()


def build_receipt_envelope(core: ReceiptCore) -> ReceiptEnvelope:
    """Create a self-validating envelope without adding a circular hash edge."""

    return ReceiptEnvelope(receipt=core, receipt_fingerprint=receipt_fingerprint(core))


def render_offline_receipt(
    envelope: ReceiptEnvelope,
    ledger: DecisionLedgerV2,
    report: VerificationReportV2,
) -> bytes:
    """Render one deterministic, self-contained, non-authoritative receipt view."""

    core = envelope.receipt
    gpt_assisted_count = sum(
        entry.meaning_review is not None for entry in ledger.decisions
    )
    agreement_failures = (
        ledger.case_id != core.case_id,
        len(ledger.decisions) != core.decision_count,
        len(ledger.decisions) != core.human_decision_count,
        gpt_assisted_count != core.gpt_assisted_decision_count,
        report.status is not ProofStatus.VERIFIED,
        report.claim is None,
        report.source_snapshot_commitment != core.source_snapshot_commitment,
        report.content_object_count != core.map_row_count,
        report.map_row_count != core.map_row_count,
        report.bagit_validation != core.producer_bagit_validation,
        bool(report.blockers),
        not all(check.passed for check in report.checks),
    )
    if any(agreement_failures):
        raise ReceiptContractError(
            "Offline receipt inputs do not describe one finalized transaction."
        )

    def escaped(value: object) -> str:
        return html.escape(str(value), quote=True)

    bagit_messages = (
        "".join(
            f"<li>{escaped(message)}</li>"
            for message in core.producer_bagit_validation.messages
        )
        or "<li>No validator message was recorded.</li>"
    )
    report_checks = "".join(
        "<tr>"
        f"<td><code>{escaped(check.check_id)}</code></td>"
        f"<td>{escaped(check.label)}</td>"
        f"<td>{'PASS' if check.passed else 'BLOCKED'}</td>"
        f"<td>{escaped(check.detail)}</td>"
        "</tr>"
        for check in report.checks
    )
    control_proofs = (
        "".join(
            "<tr>"
            f"<td><code>{escaped(proof.logical_path)}</code></td>"
            f"<td><code>{escaped(proof.source_sha256)}</code></td>"
            f"<td><code>{escaped(proof.staged_sha256)}</code></td>"
            f"<td>{escaped(', '.join(proof.rewritten_fields) or 'None')}</td>"
            f"<td>{'YES' if proof.non_path_fields_unchanged else 'NO'}</td>"
            "</tr>"
            for proof in sorted(
                report.control_files, key=lambda item: item.logical_path
            )
        )
        or '<tr><td colspan="5">No declared control proofs.</td></tr>'
    )

    decision_sections: list[str] = []
    for entry in ledger.decisions:
        mappings = "".join(
            "<tr>"
            f"<td>{escaped(proposal.role.value)}</td>"
            f"<td><code>{escaped(proposal.original_relative_path)}</code></td>"
            f"<td><code>{escaped(proposal.proposed_relative_path)}</code></td>"
            f"<td><code>{escaped(entry.human_decision.resolved_targets[proposal.role])}</code></td>"
            "</tr>"
            for proposal in sorted(
                entry.initial_proposals,
                key=lambda item: (item.role.value, item.original_relative_path),
            )
        )
        meaning = ""
        if entry.meaning_review is not None:
            review = entry.meaning_review
            meaning = (
                '<div class="meaning"><h4>Meaning review</h4><dl>'
                f"<dt>Model</dt><dd><code>{escaped(review.model)}</code></dd>"
                "<dt>Display origin</dt>"
                f"<dd>{escaped(review.display_origin.value)}</dd>"
                "<dt>Question</dt>"
                f"<dd>{escaped(review.decision_card.discriminating_question)}</dd>"
                "<dt>Evidence fingerprint</dt>"
                f"<dd><code>{escaped(review.evidence_fingerprint)}</code></dd>"
                "<dt>Card fingerprint</dt>"
                f"<dd><code>{escaped(review.card_fingerprint)}</code></dd>"
                "</dl></div>"
            )
        decision_sections.append(
            '<section class="decision">'
            f"<h3>Family <code>{escaped(entry.family_id)}</code></h3>"
            "<dl>"
            f"<dt>Decision method</dt><dd>{escaped(entry.decision_method.value)}</dd>"
            "<dt>Human action</dt>"
            f"<dd>{escaped(entry.human_decision.action.value)}</dd>"
            f"<dt>Decided at</dt><dd>{escaped(entry.decided_at.isoformat())}</dd>"
            "</dl>"
            "<table><thead><tr><th>Role</th><th>Before</th>"
            "<th>Mechanical proposal</th><th>Resolved target</th></tr></thead>"
            f"<tbody>{mappings}</tbody></table>{meaning}</section>"
        )

    claim_boundaries = "".join(
        f"<li>{escaped(boundary)}</li>" for boundary in core.claim_boundaries
    )
    artifact_paths = "".join(
        f"<li><code>{escaped(path)}</code></li>" for path in report.artifact_paths
    )
    document = (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Reversible Name Atlas Portable Change Receipt</title>"
        "<style>"
        ":root{color-scheme:dark}body{font:15px/1.5 system-ui,sans-serif;"
        "background:#11151b;color:#e8edf3;max-width:1080px;margin:0 auto;padding:32px}"
        "h1,h2,h3,h4{line-height:1.2}code{color:#9ecbff;overflow-wrap:anywhere}"
        ".notice{border-left:4px solid #d6a84b;background:#211d15;padding:12px 16px}"
        ".verified{color:#72ca9b;font-weight:700}dl{display:grid;grid-template-columns:"
        "minmax(150px,220px) 1fr;gap:6px 16px}dt{font-weight:700}dd{margin:0}"
        "table{border-collapse:collapse;width:100%;margin:12px 0 24px}"
        "th,td{border:1px solid #384351;"
        "padding:8px;text-align:left;vertical-align:top}th{background:#202833}"
        ".decision,.proof{border:1px solid #384351;border-radius:6px;"
        "padding:16px;margin:16px 0}"
        "</style></head><body>"
        '<p class="verified">VERIFIED PORTABLE CHANGE RECEIPT</p>'
        "<h1>Reversible Name Atlas</h1>"
        '<p class="notice"><strong>Non-authoritative offline view.</strong> '
        "The machine receipt, decision ledger, verification report, maps, and "
        "committed artifacts remain authoritative. Re-run the independent verifier "
        "before relying on this handoff.</p>"
        "<h2>Receipt identity</h2><dl>"
        "<dt>Receipt fingerprint</dt>"
        f"<dd><code>{escaped(envelope.receipt_fingerprint)}</code></dd>"
        f"<dt>Case ID</dt><dd><code>{escaped(core.case_id)}</code></dd>"
        f"<dt>Receipt schema</dt><dd>{escaped(core.schema_version)}</dd>"
        f"<dt>Package contract</dt><dd>{escaped(core.package_contract_id)}</dd>"
        f"<dt>Profile</dt><dd>{escaped(core.profile_id)}</dd></dl>"
        "<h2>Committed transaction</h2><dl>"
        "<dt>Source commitment</dt>"
        f"<dd><code>{escaped(core.source_snapshot_commitment)}</code></dd>"
        f"<dt>Source members</dt><dd>{core.source_member_count}</dd>"
        f"<dt>Source bytes</dt><dd>{core.source_bytes}</dd>"
        "<dt>Staged-data commitment</dt>"
        f"<dd><code>{escaped(core.staged_data_commitment)}</code></dd>"
        f"<dt>Staged data members</dt><dd>{core.staged_data_file_count}</dd>"
        f"<dt>Staged data bytes</dt><dd>{core.staged_data_bytes}</dd>"
        f"<dt>Map rows</dt><dd>{core.map_row_count}</dd>"
        f"<dt>Decisions</dt><dd>{core.decision_count}</dd>"
        f"<dt>GPT-assisted decisions</dt><dd>{core.gpt_assisted_decision_count}</dd>"
        f"<dt>Human decisions</dt><dd>{core.human_decision_count}</dd></dl>"
        "<h2>Producer BagIt validation</h2>"
        "<p><strong>"
        f"{'PASS' if core.producer_bagit_validation.valid else 'BLOCKED'}"
        "</strong> · "
        f"{escaped(core.producer_bagit_validation.validator)}</p>"
        f"<ul>{bagit_messages}</ul>"
        f"<h2>Human decisions</h2>{''.join(decision_sections)}"
        '<section class="proof"><h2>Producer verification report</h2><dl>'
        f"<dt>Status</dt><dd>{escaped(report.status.value)}</dd>"
        f"<dt>Claim</dt><dd>{escaped(report.claim)}</dd>"
        f"<dt>Generated at</dt><dd>{escaped(report.generated_at.isoformat())}</dd></dl>"
        "<h3>Checks</h3><table><thead><tr><th>ID</th><th>Check</th><th>Result</th><th>Detail</th>"
        f"</tr></thead><tbody>{report_checks}</tbody></table>"
        "<h3>Declared control proofs</h3><table><thead><tr><th>Path</th>"
        "<th>Source SHA-256</th><th>Staged SHA-256</th><th>Rewritten fields</th>"
        "<th>Other fields unchanged</th></tr></thead>"
        f"<tbody>{control_proofs}</tbody></table></section>"
        f"<h2>Claim boundaries</h2><ul>{claim_boundaries}</ul>"
        f"<h2>Machine artifact paths</h2><ul>{artifact_paths}</ul>"
        "<h2>Independent verification</h2>"
        "<p><code>uv run name-atlas verify-receipt RECEIVED_BAG</code></p>"
        "</body></html>\n"
    )
    return document.encode("utf-8")


def canonical_artifact_json_bytes(value: BaseModel) -> bytes:
    """Render one portable JSON artifact deterministically with a final newline."""

    rendered = json.dumps(
        value.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    return f"{rendered}\n".encode()


def artifact_commitment(bag_root: Path, relative_path: str) -> ArtifactCommitment:
    """Hash exact bytes for one safe regular artifact below a bag root."""

    relative = _require_relative_posix(relative_path)
    data = read_regular_bytes(bag_root, relative)
    return ArtifactCommitment(
        path=relative,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def staged_data_members(bag_root: Path) -> tuple[StagedDataMember, ...]:
    """Enumerate and hash every ordinary member below data/ without following links."""

    data_root = _require_real_directory(bag_root, "data")
    members: list[StagedDataMember] = []
    pending = [data_root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise ReceiptContractError(
                "Staged data tree cannot be enumerated."
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ReceiptContractError(
                    "A staged data member cannot be inspected."
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ReceiptContractError(
                    "Staged data contains a symbolic link or special file."
                )
            relative = path.relative_to(data_root).as_posix()
            data = read_regular_bytes(data_root, relative)
            members.append(
                StagedDataMember(
                    path=relative,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
            )
    if not members:
        raise ReceiptContractError("Staged data contains no regular members.")
    return tuple(sorted(members, key=lambda member: member.path))


def staged_data_commitment(members: tuple[StagedDataMember, ...]) -> str:
    """Hash the canonical sorted complete staged-data member list."""

    paths = tuple(member.path for member in members)
    if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
        raise ReceiptContractError(
            "Staged data members must be uniquely path-sorted before commitment."
        )
    value = [member.model_dump(mode="json") for member in members]
    return hashlib.sha256(_canonical_value_bytes(value)).hexdigest()


def read_regular_bytes(root: Path, relative_path: str) -> bytes:
    """Read stable bytes from one in-scope regular file without following symlinks."""

    resolved_root = _require_real_directory(root)
    relative = _require_relative_posix(relative_path)
    current = resolved_root
    parts = PurePosixPath(relative).parts
    for segment in parts[:-1]:
        current = current / segment
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ReceiptContractError("Artifact parent cannot be inspected.") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ReceiptContractError("Artifact parent is not a real directory.")

    path = current / parts[-1]
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    chunks: list[bytes] = []
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReceiptContractError("Artifact is not a regular file.")
        size = 0
        while chunk := os.read(descriptor, HASH_CHUNK_SIZE):
            chunks.append(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if identity_before != identity_after or size != after.st_size:
            raise ReceiptContractError("Artifact changed while it was read.")
    except ReceiptContractError:
        raise
    except OSError as exc:
        raise ReceiptContractError("Artifact cannot be read safely.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return b"".join(chunks)


def contains_sender_local_path(value: object) -> bool:
    """Detect absolute sender-local paths and file URIs in portable data."""

    if isinstance(value, BaseModel):
        return contains_sender_local_path(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return any(contains_sender_local_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_sender_local_path(item) for item in value)
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    return (
        _FILE_URI.search(candidate) is not None
        or _POSIX_ABSOLUTE_FRAGMENT.search(candidate) is not None
        or _WINDOWS_ABSOLUTE_FRAGMENT.search(candidate) is not None
        or PurePosixPath(candidate).is_absolute()
        or PureWindowsPath(candidate).is_absolute()
    )


def _canonical_value_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_real_directory(root: Path, child: str | None = None) -> Path:
    if not isinstance(root, Path):
        raise ReceiptContractError("Receipt root must be a pathlib.Path.")
    candidate = root if child is None else root / child
    try:
        metadata = candidate.lstat()
    except OSError as exc:
        raise ReceiptContractError(
            "Required receipt directory cannot be inspected."
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReceiptContractError(
            "Required receipt directory is not a real directory."
        )
    try:
        return candidate.resolve(strict=True)
    except OSError as exc:
        raise ReceiptContractError(
            "Required receipt directory cannot be resolved."
        ) from exc
