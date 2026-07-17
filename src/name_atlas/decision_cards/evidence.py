"""Canonical outbound evidence serialization and post-parse validation."""

import hashlib
import json
import re
from collections.abc import Iterable

from pydantic import ValidationError

from name_atlas.domain import DecisionCard, EvidencePacket

from .errors import (
    AuthorityClaimError,
    InvalidEvidencePacketError,
    MalformedDecisionCardError,
    UnknownCandidatePathError,
    UnknownEvidenceIdError,
)
from .models import (
    DECISION_CARD_SCHEMA_VERSION,
    MODEL_ALIAS,
    CanonicalEvidenceEnvelope,
)

_AUTHORITY_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"\b(?:proposal|path|rename|candidate|choice|target)\s+"
        r"(?:is|was|has been)\s+(?:approved|verified|correct|safe|exportable)\b",
        r"\b(?:safe|correct)\s+to\s+"
        r"(?:proceed|rename|export|approve|accept|use)\b",
        r"\b(?:I|we)\s+(?:approve|verify|certify)\b",
        r"\b(?:should|can)\s+be\s+(?:approved|treated as verified)\b",
        r"\bfinal[_ ]target\b",
        r"\bexportable\b",
    )
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def validate_evidence_packet(packet: EvidencePacket) -> None:
    """Reject ambiguous IDs and duplicate mechanically supplied paths."""

    evidence_ids = [
        ref.evidence_id
        for ref in (
            *packet.path_evidence,
            *packet.metadata_evidence,
            *packet.derivative_evidence,
        )
    ]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise InvalidEvidencePacketError(
            "Evidence IDs must be unique across the complete packet."
        )
    if len(packet.candidate_paths) != len(set(packet.candidate_paths)):
        raise InvalidEvidencePacketError(
            "Mechanically supplied candidate paths must be unique."
        )


def canonical_evidence_text(packet: EvidencePacket) -> str:
    """Return the exact canonical UTF-8 text sent in a live request."""

    validate_evidence_packet(packet)
    envelope = CanonicalEvidenceEnvelope(
        model=MODEL_ALIAS,
        schema_version=DECISION_CARD_SCHEMA_VERSION,
        packet=packet,
    )
    return _canonical_json(envelope.model_dump(mode="json"))


def evidence_fingerprint(packet: EvidencePacket) -> str:
    """Hash the full canonical packet, model alias, and schema version."""

    return hashlib.sha256(canonical_evidence_text(packet).encode("utf-8")).hexdigest()


def _card_text(card: DecisionCard) -> Iterable[str]:
    for observation in (
        *card.possible_interpretations,
        *card.possible_meaning_loss,
    ):
        yield observation.text
    yield card.uncertainty
    yield card.why_the_distinction_matters
    yield card.discriminating_question
    for explanation in card.candidate_explanations:
        yield explanation.explanation


def _referenced_evidence_ids(card: DecisionCard) -> Iterable[str]:
    for observation in (
        *card.possible_interpretations,
        *card.possible_meaning_loss,
    ):
        yield from observation.evidence_ids
    for explanation in card.candidate_explanations:
        yield from explanation.evidence_ids


def validate_decision_card(
    value: DecisionCard | object,
    packet: EvidencePacket,
) -> DecisionCard:
    """Validate parsed output against authority and submitted-evidence limits."""

    try:
        candidate = (
            value.model_dump(mode="python")
            if isinstance(value, DecisionCard)
            else value
        )
        card = DecisionCard.model_validate(candidate)
    except ValidationError as exc:
        raise MalformedDecisionCardError(
            "GPT-5.6 output did not match the DecisionCard contract."
        ) from exc

    allowed_evidence_ids = {
        ref.evidence_id
        for ref in (
            *packet.path_evidence,
            *packet.metadata_evidence,
            *packet.derivative_evidence,
        )
    }
    unknown_evidence_ids = set(_referenced_evidence_ids(card)).difference(
        allowed_evidence_ids
    )
    if unknown_evidence_ids:
        raise UnknownEvidenceIdError(
            "DecisionCard referenced evidence IDs absent from the outbound packet."
        )

    allowed_candidates = set(packet.candidate_paths)
    unknown_candidates = {
        explanation.candidate_path for explanation in card.candidate_explanations
    }.difference(allowed_candidates)
    if unknown_candidates:
        raise UnknownCandidatePathError(
            "DecisionCard referenced candidate paths absent from the outbound packet."
        )

    for text in _card_text(card):
        if any(pattern.search(text) for pattern in _AUTHORITY_PATTERNS):
            raise AuthorityClaimError(
                "DecisionCard prose implied approval or deterministic authority."
            )

    return card
