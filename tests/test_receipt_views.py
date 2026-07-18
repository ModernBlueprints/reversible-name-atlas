"""Focused deterministic portable-view and path-neutrality tests."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from name_atlas.artifacts import (
    ControlFileProof,
    ProofStatus,
    VerificationCheck,
    render_verification_summary,
    write_summary,
)
from name_atlas.decision_cards import evidence_fingerprint
from name_atlas.decisions import HumanAction, HumanDecision
from name_atlas.domain import (
    ContentRole,
    DecisionCard,
    EvidencePacket,
    EvidenceRef,
    LinkedObservation,
    PackageValidationResult,
    TransformationStep,
)
from name_atlas.proposals import (
    PathProposal,
    ProposalSource,
    RiskCategory,
    RiskSignal,
)
from name_atlas.receipts import (
    RECEIPT_CLAIM_BOUNDARIES,
    ArtifactCommitment,
    CardDisplayOrigin,
    DecisionLedgerEntry,
    DecisionLedgerV2,
    DecisionMethod,
    MeaningReviewRecord,
    ReceiptContractError,
    ReceiptCore,
    ReceiptEnvelope,
    VerificationReportV2,
    build_receipt_envelope,
    contains_sender_local_path,
    decision_card_fingerprint,
    render_offline_receipt,
)

oslo_tz = ZoneInfo("Europe/Oslo")
FAMILY_ID = "a" * 64
CASE_ID = "123e4567e89b42d3a456426614174000"
SOURCE_COMMITMENT = "b" * 64
STAGED_COMMITMENT = "c" * 64


def _portable_view_models() -> tuple[
    ReceiptEnvelope,
    DecisionLedgerV2,
    VerificationReportV2,
]:
    timestamp = datetime(2026, 7, 18, 12, 0, tzinfo=oslo_tz)
    original = "objects/campaña.jpg"
    proposed = "objects/NA-0001__campana__original.jpg"
    transformation = TransformationStep(
        operation="remove_combining_mark",
        before="campaña",
        after="campana",
    )
    meaning_risk = RiskSignal(
        category=RiskCategory.MEANING,
        code="combining_mark_removed",
        message="Possible <meaning & loss>",
        evidence_ids=(f"path:{original}",),
    )
    proposal = PathProposal(
        family_id=FAMILY_ID,
        canonical_identifier="NA-0001",
        role=ContentRole.ORIGINAL,
        original_relative_path=original,
        proposed_relative_path=proposed,
        proposal_source=ProposalSource.REPOSITORY_READY_PROFILE,
        transformation_steps=(transformation,),
        affected_references=("metadata:row:2:filename",),
        risk_signals=(meaning_risk,),
        evidence_ids=(f"path:{original}",),
    )
    packet = EvidencePacket(
        family_id=FAMILY_ID,
        original_paths=(original,),
        proposed_paths=(proposed,),
        transformation_steps=(transformation,),
        candidate_paths=(proposed,),
        path_evidence=(
            EvidenceRef(
                evidence_id="path:source:original",
                label="Source original path",
                value=original,
            ),
        ),
        risk_signals=("Meaning:combining_mark_removed",),
        profile_description="Repository-ready identity profile",
    )
    card = DecisionCard(
        possible_interpretations=(
            LinkedObservation(
                text="The spelling may carry meaning.",
                evidence_ids=("path:source:original",),
            ),
        ),
        possible_meaning_loss=(
            LinkedObservation(
                text="The tilde may be meaningful.",
                evidence_ids=("path:source:original",),
            ),
        ),
        uncertainty="The supplied evidence does not decide intent.",
        why_the_distinction_matters=(
            "A receiving archivist may interpret it differently."
        ),
        discriminating_question="<Which & why?>",
        candidate_explanations=(),
    )
    review = MeaningReviewRecord(
        evidence_packet=packet,
        evidence_fingerprint=evidence_fingerprint(packet),
        decision_card=card,
        card_fingerprint=decision_card_fingerprint(card),
        display_origin=CardDisplayOrigin.RECORDED_REPLAY,
        generated_at=timestamp,
        usage=None,
    )
    decision = HumanDecision(
        family_id=FAMILY_ID,
        action=HumanAction.APPROVED,
        human_input=None,
        resolved_targets={ContentRole.ORIGINAL: proposed},
    )
    ledger = DecisionLedgerV2(
        case_id=CASE_ID,
        decisions=(
            DecisionLedgerEntry(
                family_id=FAMILY_ID,
                initial_proposals=(proposal,),
                decision_method=DecisionMethod.INDIVIDUAL_APPROVAL,
                human_decision=decision,
                decided_at=timestamp,
                meaning_review=review,
            ),
        ),
    )
    bagit_result = PackageValidationResult(
        validator="bagit",
        valid=True,
        messages=("BagIt <passed & checked>.",),
    )
    report = VerificationReportV2(
        status=ProofStatus.VERIFIED,
        claim="Verified <within & boundaries>",
        generated_at=timestamp,
        source_snapshot_commitment=SOURCE_COMMITMENT,
        prestaging_snapshot_commitment=SOURCE_COMMITMENT,
        postcopy_snapshot_commitment=SOURCE_COMMITMENT,
        source_unchanged=True,
        content_object_count=1,
        content_bytes=17,
        control_files=(
            ControlFileProof(
                logical_path="metadata/metadata.csv",
                source_sha256="d" * 64,
                staged_sha256="e" * 64,
                rewritten_fields=("row:2:<filename>",),
                non_path_fields_unchanged=True,
            ),
        ),
        map_row_count=1,
        checks=(
            VerificationCheck(
                check_id="payload_hashes_equal",
                label="Payloads <match>",
                passed=True,
                detail="Compared 1 object & control.",
            ),
        ),
        bagit_validation=bagit_result,
        artifact_paths=(
            "name-atlas/change_receipt.json",
            "name-atlas/decision_ledger.json",
            "name-atlas/verification_report.json",
        ),
        blockers=(),
    )
    committed_paths = (
        "bag-info.txt",
        "bagit.txt",
        "manifest-sha256.txt",
        "name-atlas/decision_ledger.json",
        "name-atlas/forward_path_map.csv",
        "name-atlas/original-control/metadata/metadata.csv",
        "name-atlas/reverse_path_map.csv",
        "name-atlas/source_snapshot.json",
        "name-atlas/verification_report.json",
        "name-atlas/verification_summary.md",
    )
    core = ReceiptCore(
        case_id=CASE_ID,
        source_snapshot_commitment=SOURCE_COMMITMENT,
        source_member_count=2,
        source_bytes=42,
        staged_data_commitment=STAGED_COMMITMENT,
        staged_data_file_count=2,
        staged_data_bytes=43,
        artifact_commitments=tuple(
            ArtifactCommitment(path=path, size=index, sha256=f"{index:x}" * 64)
            for index, path in enumerate(committed_paths, start=1)
        ),
        map_row_count=1,
        decision_count=1,
        gpt_assisted_decision_count=1,
        human_decision_count=1,
        producer_bagit_validation=bagit_result,
        claim_boundaries=RECEIPT_CLAIM_BOUNDARIES,
    )
    return build_receipt_envelope(core), ledger, report


def test_verification_summary_renderer_is_exact_and_writer_delegates(
    tmp_path: Path,
) -> None:
    expected = (
        b"# Reversible Name Atlas verification summary\n\n"
        b"- Content objects staged copy-only: 3\n"
        b"- Content bytes staged: 128\n"
        b"- Complete deterministic and BagIt results: `verification_report.json`\n"
        b"- Forward and reverse logical maps: exact content-object inverses\n"
        b"- Source payload bytes are not stored in proof artifacts\n"
    )

    rendered = render_verification_summary(content_objects=3, content_bytes=128)
    output = tmp_path / "verification_summary.md"
    write_summary(output, content_objects=3, content_bytes=128)

    assert rendered == expected
    assert output.read_bytes() == expected


def test_offline_receipt_is_deterministic_escaped_and_machine_aligned() -> None:
    envelope, ledger, report = _portable_view_models()

    first = render_offline_receipt(envelope, ledger, report)
    second = render_offline_receipt(envelope, ledger, report)
    text = first.decode("utf-8")

    assert first == second
    assert first.startswith(b"<!doctype html>\n")
    assert first.endswith(b"</body></html>\n")
    assert envelope.receipt_fingerprint in text
    assert CASE_ID in text
    assert SOURCE_COMMITMENT in text
    assert STAGED_COMMITMENT in text
    assert "name-atlas-linked-package.v1" in text
    assert "repository-ready-identity.v1" in text
    assert "individual_approval" in text
    assert "objects/campaña.jpg" in text
    assert "objects/NA-0001__campana__original.jpg" in text
    assert "recorded_replay" in text
    assert "uv run name-atlas verify-receipt RECEIVED_BAG" in text
    assert "&lt;Which &amp; why?&gt;" in text
    assert "BagIt &lt;passed &amp; checked&gt;." in text
    assert "row:2:&lt;filename&gt;" in text
    assert "<Which & why?>" not in text
    assert RECEIPT_CLAIM_BOUNDARIES[0] in text
    assert "/Users/" not in text
    assert "file://" not in text.casefold()


def test_offline_receipt_refuses_disagreeing_machine_authorities() -> None:
    envelope, ledger, report = _portable_view_models()
    mismatched = report.model_copy(update={"map_row_count": 2})

    with pytest.raises(ReceiptContractError, match="one finalized transaction"):
        render_offline_receipt(envelope, ledger, mismatched)


def test_sender_local_path_detection_covers_portable_platforms() -> None:
    assert contains_sender_local_path("/opt/name-atlas/case.json")
    assert contains_sender_local_path("/srv/archive/source")
    assert contains_sender_local_path(r"C:\Users\archivist\case.json")
    assert contains_sender_local_path(r"\\server\share\handoff")
    assert contains_sender_local_path("FILE:///tmp/handoff")
    assert contains_sender_local_path({"nested": ("objects/relative.jpg", "/root/x")})
    assert contains_sender_local_path("Local source: /opt/name-atlas/source")
    assert contains_sender_local_path("case=/srv/archive/case.json")

    assert not contains_sender_local_path("name-atlas/change_receipt.json")
    assert not contains_sender_local_path("objects/relative.jpg")
    assert not contains_sender_local_path("See https://example.com/archive/path")
    assert not contains_sender_local_path("One file: recorded in the receipt")
    assert not contains_sender_local_path(
        "Refactor the collection. Hand over the proof."
    )
