"""Build complete bounded evidence packets from deterministic family facts."""

from __future__ import annotations

from name_atlas.domain import EvidencePacket, EvidenceRef
from name_atlas.package_import import ObjectFamily, SourcePackage
from name_atlas.proposals import PathProposal

PROFILE_DESCRIPTION = (
    "Repository-ready identity profile: stable dc.identifier prefix; descriptor "
    "from the original stem by NFKD, combining-mark removal, ASCII lowercase, "
    "separator mapping, remaining non-ASCII removal, collapse and trim; exact "
    "role directories and lowercase retained final extensions. GPT is advisory "
    "and cannot approve, verify, choose, or set a target."
)


def build_evidence_packet(
    package: SourcePackage,
    family: ObjectFamily,
    proposals: tuple[PathProposal, ...],
) -> EvidencePacket:
    """Build the exact user-visible outbound text contract for one family."""

    selected = tuple(
        proposal for proposal in proposals if proposal.family_id == family.family_id
    )
    if len(selected) != len(family.members):
        raise ValueError("Evidence packet requires one proposal per family member.")

    path_evidence = tuple(
        EvidenceRef(
            evidence_id=f"path:source:{member.role.value}",
            label=f"Source {member.role.value} path",
            value=member.relative_path,
        )
        for member in family.members
    ) + tuple(
        EvidenceRef(
            evidence_id=f"path:proposed:{proposal.role.value}",
            label=f"Proposed {proposal.role.value} path",
            value=proposal.proposed_relative_path,
        )
        for proposal in selected
    )
    metadata_evidence = tuple(
        EvidenceRef(
            evidence_id=f"metadata:row:{family.metadata_row.row_number}:{column}",
            label=column,
            value=family.metadata_row.value(column),
        )
        for column in package.metadata_header
    )
    derivative_evidence = tuple(
        EvidenceRef(
            evidence_id=f"relationship:{member.role.value}",
            label=f"Declared {member.role.value} relationship",
            value=(
                f"{family.original.relative_path} -> {member.relative_path}"
                if member is not family.original
                else family.original.relative_path
            ),
        )
        for member in family.members
    )
    candidate_paths = tuple(
        dict.fromkeys(proposal.proposed_relative_path for proposal in selected)
    )
    neighboring_paths = tuple(
        other.original.relative_path
        for other in package.families
        if other.family_id != family.family_id
    )[:8]
    risk_signals = tuple(
        dict.fromkeys(
            f"{risk.category.value}:{risk.code}:{risk.message}"
            for proposal in selected
            for risk in proposal.risk_signals
        )
    )
    return EvidencePacket(
        family_id=family.family_id,
        original_paths=tuple(member.relative_path for member in family.members),
        proposed_paths=tuple(proposal.proposed_relative_path for proposal in selected),
        transformation_steps=tuple(
            step for proposal in selected for step in proposal.transformation_steps
        ),
        candidate_paths=candidate_paths,
        neighboring_paths=neighboring_paths,
        path_evidence=path_evidence,
        metadata_evidence=metadata_evidence,
        derivative_evidence=derivative_evidence,
        risk_signals=risk_signals,
        profile_description=PROFILE_DESCRIPTION,
    )
