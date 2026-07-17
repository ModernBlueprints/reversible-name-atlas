"""Deterministic repository-ready path proposals and mechanical risk signals."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from enum import StrEnum
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from name_atlas.domain import ContentRole, TransformationStep

if TYPE_CHECKING:
    from name_atlas.package_import import ObjectFamily, SourceMember

EXTENSION_PATTERN = re.compile(r"\.[a-z0-9]{1,16}\Z")
DESCRIPTOR_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
PROFILE_NAME = "Repository-ready identity profile"

ROLE_DIRECTORIES: dict[ContentRole, str] = {
    ContentRole.ORIGINAL: "objects",
    ContentRole.ACCESS: "manualNormalization/access",
    ContentRole.PRESERVATION: "manualNormalization/preservation",
}


class RiskCategory(StrEnum):
    """The four visible mechanical risk lanes."""

    POLICY = "Policy"
    COLLISION = "Collision"
    LINKS = "Links"
    MEANING = "Meaning"


class ProposalSource(StrEnum):
    """Authorized proposal origins."""

    REPOSITORY_READY_PROFILE = "repository_ready_profile"
    HUMAN_EDIT = "human_edit"


class ResolutionState(StrEnum):
    """Current human-resolution state of a proposal."""

    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REFUSED = "refused"
    UNRESOLVED = "unresolved"


class VerificationState(StrEnum):
    """Current deterministic verification state."""

    PENDING = "pending"
    VERIFIED = "verified"
    BLOCKED = "blocked"


class RiskSignal(BaseModel):
    """One deterministic risk observation linked to supplied evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: RiskCategory
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class PathProposal(BaseModel):
    """One role-specific path proposal under the fixed profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    family_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    canonical_identifier: str = Field(min_length=1, max_length=64)
    role: ContentRole
    original_relative_path: str = Field(min_length=1, max_length=1_024)
    proposed_relative_path: str = Field(min_length=1, max_length=1_024)
    proposal_source: ProposalSource
    transformation_steps: tuple[TransformationStep, ...] = Field(min_length=1)
    affected_references: tuple[str, ...] = Field(min_length=1)
    risk_signals: tuple[RiskSignal, ...]
    human_resolution_state: ResolutionState = ResolutionState.PENDING
    verification_state: VerificationState = VerificationState.PENDING
    evidence_ids: tuple[str, ...] = Field(min_length=1)

    @property
    def meaning_risks(self) -> tuple[RiskSignal, ...]:
        """Return Meaning signals only."""

        return tuple(
            risk for risk in self.risk_signals if risk.category is RiskCategory.MEANING
        )

    @property
    def mechanical_blockers(self) -> tuple[RiskSignal, ...]:
        """Return red mechanical blocker signals."""

        return tuple(
            risk
            for risk in self.risk_signals
            if risk.category
            in {RiskCategory.POLICY, RiskCategory.COLLISION, RiskCategory.LINKS}
        )


class DescriptorProjection(BaseModel):
    """Deterministic descriptor plus its complete visible transformation trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    descriptor: str
    steps: tuple[TransformationStep, ...]
    risks: tuple[RiskSignal, ...]


def project_descriptor(original_relative_path: str) -> DescriptorProjection:
    """Project the original filename stem under the frozen profile."""

    leaf = PurePosixPath(original_relative_path).name
    suffix = PurePosixPath(leaf).suffix
    if not suffix:
        raise ValueError(
            f"Content object has no final extension: {original_relative_path}"
        )
    stem = leaf[: -len(suffix)]
    evidence_id = f"path:{original_relative_path}"

    nfkd = unicodedata.normalize("NFKD", stem)
    steps = [TransformationStep(operation="unicode_nfkd", before=stem, after=nfkd)]
    risks: list[RiskSignal] = []

    without_marks_characters: list[str] = []
    for index, character in enumerate(nfkd):
        if unicodedata.combining(character):
            risks.append(
                RiskSignal(
                    category=RiskCategory.MEANING,
                    code="combining_mark_removed",
                    message=(
                        "Removed combining mark "
                        f"{unicodedata.name(character, 'UNKNOWN')} "
                        f"at normalized stem position {index}."
                    ),
                    evidence_ids=(evidence_id,),
                )
            )
            continue
        without_marks_characters.append(character)
    without_marks = "".join(without_marks_characters)
    steps.append(
        TransformationStep(
            operation="remove_combining_marks",
            before=nfkd,
            after=without_marks,
        )
    )

    projected: list[str] = []
    for index, character in enumerate(without_marks):
        if "A" <= character <= "Z":
            projected.append(character.lower())
        elif "a" <= character <= "z" or "0" <= character <= "9":
            projected.append(character)
        elif (
            character.isspace()
            or character in "._-"
            or unicodedata.category(character).startswith("P")
        ):
            projected.append("-")
        elif ord(character) > 127:
            risks.append(
                RiskSignal(
                    category=RiskCategory.MEANING,
                    code="non_ascii_codepoint_removed",
                    message=(
                        "Removed non-ASCII code point "
                        f"{unicodedata.name(character, 'UNKNOWN')} "
                        f"at normalized stem position {index}."
                    ),
                    evidence_ids=(evidence_id,),
                )
            )
        else:
            projected.append("-")
    mapped = "".join(projected)
    steps.append(
        TransformationStep(
            operation="ascii_lower_and_separator_mapping",
            before=without_marks,
            after=mapped,
        )
    )

    descriptor = re.sub(r"-+", "-", mapped).strip("-")
    steps.append(
        TransformationStep(
            operation="collapse_and_trim_separators",
            before=mapped,
            after=descriptor,
        )
    )
    if not descriptor or DESCRIPTOR_PATTERN.fullmatch(descriptor) is None:
        raise ValueError(
            "Original stem projects to an invalid empty descriptor: "
            f"{original_relative_path}"
        )
    return DescriptorProjection(
        descriptor=descriptor,
        steps=tuple(steps),
        risks=tuple(risks),
    )


def build_family_proposals(family: ObjectFamily) -> tuple[PathProposal, ...]:
    """Build one visible role-specific proposal for every family member."""

    descriptor = project_descriptor(family.original.relative_path)
    proposals: list[PathProposal] = []
    for member in _family_members(family):
        extension = PurePosixPath(member.relative_path).suffix.lower()
        if EXTENSION_PATTERN.fullmatch(extension) is None:
            raise ValueError(
                f"Unsupported final extension for {member.relative_path}: {extension!r}"
            )
        target = _target_path(
            identifier=family.canonical_identifier,
            descriptor=descriptor.descriptor,
            role=member.role,
            extension=extension,
        )
        reference_ids = _affected_references(family, member)
        evidence_ids = (f"path:{member.relative_path}", *reference_ids)
        steps = (
            *descriptor.steps,
            TransformationStep(
                operation="compose_role_target",
                before=member.relative_path,
                after=target,
            ),
        )
        proposals.append(
            PathProposal(
                family_id=family.family_id,
                canonical_identifier=family.canonical_identifier,
                role=member.role,
                original_relative_path=member.relative_path,
                proposed_relative_path=target,
                proposal_source=ProposalSource.REPOSITORY_READY_PROFILE,
                transformation_steps=steps,
                affected_references=reference_ids,
                risk_signals=descriptor.risks,
                evidence_ids=evidence_ids,
            )
        )
    return tuple(proposals)


def build_proposals(families: tuple[ObjectFamily, ...]) -> tuple[PathProposal, ...]:
    """Build all proposals and annotate exact/NFC/casefold collisions."""

    proposals = tuple(
        proposal for family in families for proposal in build_family_proposals(family)
    )
    return refresh_collision_signals(proposals)


def refresh_collision_signals(
    proposals: tuple[PathProposal, ...],
) -> tuple[PathProposal, ...]:
    """Recompute collision signals from the complete current proposal set."""

    collision_members: dict[int, list[RiskSignal]] = defaultdict(list)
    comparisons = {
        "exact": lambda value: value,
        "nfc": lambda value: unicodedata.normalize("NFC", value),
        "nfc_casefold": lambda value: unicodedata.normalize("NFC", value).casefold(),
    }
    for comparison_name, comparison in comparisons.items():
        groups: dict[str, list[int]] = defaultdict(list)
        for index, proposal in enumerate(proposals):
            groups[comparison(proposal.proposed_relative_path)].append(index)
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            paths = tuple(proposals[index].proposed_relative_path for index in indexes)
            for index in indexes:
                collision_members[index].append(
                    RiskSignal(
                        category=RiskCategory.COLLISION,
                        code=f"target_collision_{comparison_name}",
                        message=(
                            f"Target is not unique under {comparison_name} comparison: "
                            + ", ".join(paths)
                        ),
                        evidence_ids=tuple(
                            f"path:{proposals[item].original_relative_path}"
                            for item in indexes
                        ),
                    )
                )
    return tuple(
        proposal.model_copy(
            update={
                "risk_signals": (
                    *(
                        risk
                        for risk in proposal.risk_signals
                        if risk.category is not RiskCategory.COLLISION
                    ),
                    *collision_members.get(index, ()),
                ),
                "verification_state": (
                    VerificationState.BLOCKED
                    if collision_members.get(index)
                    or proposal.human_resolution_state
                    in {ResolutionState.REFUSED, ResolutionState.UNRESOLVED}
                    else (
                        VerificationState.VERIFIED
                        if proposal.human_resolution_state
                        in {ResolutionState.APPROVED, ResolutionState.EDITED}
                        else VerificationState.PENDING
                    )
                ),
            }
        )
        for index, proposal in enumerate(proposals)
    )


def edited_targets(
    family: ObjectFamily,
    descriptor: str,
) -> dict[ContentRole, str]:
    """Derive the complete immutable target map for an exact human descriptor."""

    if DESCRIPTOR_PATTERN.fullmatch(descriptor) is None:
        raise ValueError(
            "Edited descriptor must match [a-z0-9]+(?:-[a-z0-9]+)* exactly."
        )
    return {
        member.role: _target_path(
            identifier=family.canonical_identifier,
            descriptor=descriptor,
            role=member.role,
            extension=PurePosixPath(member.relative_path).suffix.lower(),
        )
        for member in _family_members(family)
    }


def _target_path(
    *,
    identifier: str,
    descriptor: str,
    role: ContentRole,
    extension: str,
) -> str:
    leaf = f"{identifier}__{descriptor}__{role.value}{extension}"
    return f"{ROLE_DIRECTORIES[role]}/{leaf}"


def _family_members(family: ObjectFamily) -> tuple[SourceMember, ...]:
    members = [family.original]
    if family.access is not None:
        members.append(family.access)
    if family.preservation is not None:
        members.append(family.preservation)
    return tuple(members)


def _affected_references(
    family: ObjectFamily,
    member: SourceMember,
) -> tuple[str, ...]:
    references = []
    if member.role is ContentRole.ORIGINAL:
        references.append(f"metadata:row:{family.metadata_row.row_number}:filename")
    if family.normalization_row_number is not None:
        references.append(
            f"normalization:row:{family.normalization_row_number}:{member.role.value}"
        )
    return tuple(references)
