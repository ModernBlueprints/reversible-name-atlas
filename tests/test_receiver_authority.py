"""Adversarial source-free receiver-authority reconstruction tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from name_atlas.artifacts import parse_path_map, write_path_map
from name_atlas.cases import (
    CardDisplayOrigin,
    CaseDecisionBinding,
    CaseDecisionCardRecord,
    CaseDecisionMethod,
    CaseEvidenceRecord,
    CaseLifecycle,
    MigrationCase,
    card_fingerprint,
    new_migration_case,
    oslo_tz,
)
from name_atlas.decision_cards import (
    ReplayUsage,
    build_evidence_packet,
    evidence_fingerprint,
)
from name_atlas.decisions import (
    approve_family,
    edit_family,
    proposals_after_decision,
)
from name_atlas.domain import (
    CandidateExplanation,
    DecisionCard,
    EvidencePacket,
    LinkedObservation,
)
from name_atlas.package_import import import_package
from name_atlas.proposals import build_proposals
from name_atlas.receipts import (
    CHANGE_RECEIPT_HTML_PATH,
    DECISION_LEDGER_PATH,
    FORWARD_PATH_MAP_PATH,
    REVERSE_PATH_MAP_PATH,
    VERIFICATION_REPORT_PATH,
    VERIFICATION_SUMMARY_PATH,
    ReceiptCore,
    decision_card_fingerprint,
    receipt_fingerprint,
)
from name_atlas.receiver_verifier import (
    ReceiptVerificationStatus,
    verify_receipt,
)
from name_atlas.staging import StagingError, stage_package
from name_atlas.verification import BagItPackageValidator


def _write_meaning_source(root: Path) -> None:
    (root / "objects").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "objects" / "campaña.svg").write_bytes(b"campaign")
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\nobjects/campaña.svg,MEAN-0001,Campaña\n",
        encoding="utf-8",
    )


def _complete_meaning_handoff(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_meaning_source(source)
    package = import_package(source)
    proposals = build_proposals(package.families)
    family = package.families[0]
    packet = build_evidence_packet(package, family, proposals)
    evidence_id = packet.metadata_evidence[0].evidence_id
    observation = LinkedObservation(
        text="The normalized descriptor may lose a written distinction.",
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
    decision = approve_family(
        family,
        proposals,
        semantic_card_available=True,
    )
    timestamp = datetime(2026, 7, 18, 3, 0, tzinfo=oslo_tz)
    packet_fingerprint = evidence_fingerprint(packet)
    card_digest = card_fingerprint(card)
    initial = new_migration_case(
        package,
        proposals,
        case_path=tmp_path / "case.json",
        output_root=output,
        case_name="Receiver authority case",
        now=timestamp,
    )
    payload = initial.model_dump(mode="python")
    payload.update(
        {
            "evidence_records": (
                CaseEvidenceRecord(
                    family_id=family.family_id,
                    packet=packet,
                    evidence_fingerprint=packet_fingerprint,
                ),
            ),
            "card_records": (
                CaseDecisionCardRecord(
                    family_id=family.family_id,
                    evidence_fingerprint=packet_fingerprint,
                    card=card,
                    card_fingerprint=card_digest,
                    display_origin=CardDisplayOrigin.RECORDED_REPLAY,
                    generated_at=timestamp,
                    usage=ReplayUsage(
                        input_tokens=0,
                        cached_input_tokens=0,
                        output_tokens=0,
                        reasoning_tokens=0,
                        total_tokens=0,
                        latency_ms=0.0,
                        estimated_cost_usd=0.0,
                    ),
                ),
            ),
            "decisions": (
                CaseDecisionBinding(
                    family_id=family.family_id,
                    decision=decision,
                    decision_method=CaseDecisionMethod.INDIVIDUAL_APPROVAL,
                    decision_timestamp=timestamp,
                    evidence_fingerprint=packet_fingerprint,
                    card_fingerprint=card_digest,
                ),
            ),
            "lifecycle": CaseLifecycle.READY_TO_STAGE,
        }
    )
    migration_case = MigrationCase.model_validate(payload, strict=True)
    result = stage_package(
        package,
        (decision,),
        output_root=output,
        package_validator=BagItPackageValidator(),
        migration_case=migration_case,
    )
    assert (
        verify_receipt(result.stage_root).status is ReceiptVerificationStatus.VERIFIED
    )
    return result.stage_root


def _complete_collision_handoff(
    tmp_path: Path,
    *,
    deferred_collision_resolution: bool = False,
) -> Path:
    source = tmp_path / "collision-source"
    output = tmp_path / "collision-output"
    (source / "objects").mkdir(parents=True)
    (source / "metadata").mkdir()
    (source / "objects" / "harbor-map.svg").write_bytes(b"upper")
    (source / "objects" / "harbor_map.svg").write_bytes(b"lower")
    (source / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\n"
        "objects/harbor-map.svg,CASE-010,Upper case identifier\n"
        "objects/harbor_map.svg,case-010,Lower case identifier\n",
        encoding="utf-8",
    )
    package = import_package(source)
    proposals = build_proposals(package.families)
    edited_family = next(
        family
        for family in package.families
        if family.canonical_identifier == "CASE-010"
    )
    approved_family = next(
        family
        for family in package.families
        if family.canonical_identifier == "case-010"
    )
    edited = edit_family(
        edited_family,
        proposals,
        descriptor=(
            "harbor-map" if deferred_collision_resolution else "harbor-map-north"
        ),
        semantic_card_available=False,
    )
    after_edit = proposals_after_decision(proposals, edited)
    if deferred_collision_resolution:
        second = edit_family(
            approved_family,
            after_edit,
            descriptor="harbor-map-south",
            semantic_card_available=False,
        )
        second_method = CaseDecisionMethod.HUMAN_EDIT
    else:
        second = approve_family(
            approved_family,
            after_edit,
            semantic_card_available=False,
        )
        second_method = CaseDecisionMethod.BATCH_APPROVAL
    created_at = datetime(2026, 7, 18, 2, 58, tzinfo=oslo_tz)
    edited_at = datetime(2026, 7, 18, 3, 0, tzinfo=oslo_tz)
    approved_at = datetime(2026, 7, 18, 3, 1, tzinfo=oslo_tz)
    initial = new_migration_case(
        package,
        proposals,
        case_path=tmp_path / "collision-case.json",
        output_root=output,
        case_name="Collision chronology case",
        now=created_at,
    )
    bindings = tuple(
        sorted(
            (
                CaseDecisionBinding(
                    family_id=edited.family_id,
                    decision=edited,
                    decision_method=CaseDecisionMethod.HUMAN_EDIT,
                    decision_timestamp=edited_at,
                    evidence_fingerprint=None,
                    card_fingerprint=None,
                ),
                CaseDecisionBinding(
                    family_id=second.family_id,
                    decision=second,
                    decision_method=second_method,
                    decision_timestamp=approved_at,
                    evidence_fingerprint=None,
                    card_fingerprint=None,
                ),
            ),
            key=lambda item: item.family_id,
        )
    )
    migration_case = MigrationCase.model_validate(
        {
            **initial.model_dump(mode="python"),
            "decisions": bindings,
            "lifecycle": CaseLifecycle.READY_TO_STAGE,
        },
        strict=True,
    )
    if deferred_collision_resolution:
        with pytest.raises(
            StagingError,
            match="deterministic_authority_mismatch",
        ):
            stage_package(
                package,
                (edited, second),
                output_root=output,
                package_validator=BagItPackageValidator(),
                migration_case=migration_case,
            )
        pending = tuple(output.glob(".*.pending"))
        assert len(pending) == 1
        return pending[0]
    result = stage_package(
        package,
        (edited, second),
        output_root=output,
        package_validator=BagItPackageValidator(),
        migration_case=migration_case,
    )
    assert (
        verify_receipt(result.stage_root).status is ReceiptVerificationStatus.VERIFIED
    )
    return result.stage_root


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _refresh_tagmanifest(bag: Path) -> None:
    tag_paths = sorted(
        path.relative_to(bag).as_posix()
        for path in bag.rglob("*")
        if path.is_file()
        and not path.relative_to(bag).as_posix().startswith("data/")
        and path.name != "tagmanifest-sha256.txt"
    )
    (bag / "tagmanifest-sha256.txt").write_text(
        "".join(
            f"{hashlib.sha256((bag / relative).read_bytes()).hexdigest()}  {relative}\n"
            for relative in tag_paths
        ),
        encoding="utf-8",
    )


def _reseal(bag: Path, changed_paths: tuple[str, ...]) -> None:
    receipt_path = bag / "name-atlas" / "change_receipt.json"
    html_path = bag / "name-atlas" / "change_receipt.html"
    envelope = json.loads(receipt_path.read_text(encoding="utf-8"))
    core_value = envelope["receipt"]
    commitments = {
        commitment["path"]: commitment
        for commitment in core_value["artifact_commitments"]
    }
    for relative_path in changed_paths:
        data = (bag / relative_path).read_bytes()
        commitments[relative_path]["size"] = len(data)
        commitments[relative_path]["sha256"] = hashlib.sha256(data).hexdigest()
    core = ReceiptCore.model_validate_json(
        json.dumps(core_value, ensure_ascii=False, allow_nan=False)
    )
    previous_fingerprint = envelope["receipt_fingerprint"]
    envelope["receipt_fingerprint"] = receipt_fingerprint(core)
    _write_json(receipt_path, envelope)
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace(
            previous_fingerprint,
            envelope["receipt_fingerprint"],
        ),
        encoding="utf-8",
    )
    _refresh_tagmanifest(bag)
    assert BagItPackageValidator().validate(bag).valid is True


def test_receiver_blocks_forbidden_card_authority_after_coherent_reseal(
    tmp_path: Path,
) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    review = ledger["decisions"][0]["meaning_review"]
    review["decision_card"]["uncertainty"] = "The proposal is approved."
    card = DecisionCard.model_validate_json(json.dumps(review["decision_card"]))
    review["card_fingerprint"] = decision_card_fingerprint(card)
    _write_json(ledger_path, ledger)
    _reseal(bag, (DECISION_LEDGER_PATH,))

    result = verify_receipt(bag)

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("deterministic_authority_mismatch",)


def test_receiver_blocks_invented_deterministic_transformation(
    tmp_path: Path,
) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["decisions"][0]["initial_proposals"][0]["transformation_steps"][0][
        "operation"
    ] = "invented_non_deterministic_step"
    _write_json(ledger_path, ledger)
    _reseal(bag, (DECISION_LEDGER_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("deterministic_authority_mismatch",)


def test_receiver_blocks_edited_target_not_derived_from_human_descriptor(
    tmp_path: Path,
) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = ledger["decisions"][0]
    entry["decision_method"] = "human_edit"
    entry["human_decision"]["action"] = "edited"
    entry["human_decision"]["human_input"] = "different-human-descriptor"
    _write_json(ledger_path, ledger)
    _reseal(bag, (DECISION_LEDGER_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("deterministic_authority_mismatch",)


def test_receiver_blocks_arbitrary_passing_producer_check(tmp_path: Path) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    report_path = bag / VERIFICATION_REPORT_PATH
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checks"] = [
        {
            "check_id": "everything_is_fine",
            "label": "Unsupported aggregate assertion",
            "passed": True,
            "detail": "This is not a recomputed producer check.",
        }
    ]
    _write_json(report_path, report)
    _reseal(bag, (VERIFICATION_REPORT_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("producer_report_inconsistent",)


def test_receiver_blocks_changed_producer_finding_prose(tmp_path: Path) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    report_path = bag / VERIFICATION_REPORT_PATH
    report = json.loads(report_path.read_text(encoding="utf-8"))
    check = next(
        item for item in report["checks"] if item["check_id"] == "payload_hashes_equal"
    )
    check["label"] = "Universal semantic correctness"
    check["detail"] = "Every name is semantically correct and institutionally approved."
    _write_json(report_path, report)
    _reseal(bag, (VERIFICATION_REPORT_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("producer_report_inconsistent",)


def test_receiver_blocks_human_decision_that_predates_meaning_card(
    tmp_path: Path,
) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = ledger["decisions"][0]
    entry["decided_at"] = "2026-07-18T02:59:59+02:00"
    _write_json(ledger_path, ledger)
    _reseal(bag, (DECISION_LEDGER_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("portable_artifact_schema_invalid",)


def test_receiver_blocks_approval_before_collision_resolving_edit(
    tmp_path: Path,
) -> None:
    bag = _complete_collision_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    approved_entry = next(
        entry
        for entry in ledger["decisions"]
        if entry["initial_proposals"][0]["canonical_identifier"] == "case-010"
    )
    approved_entry["decided_at"] = "2026-07-18T02:59:00+02:00"
    _write_json(ledger_path, ledger)
    _reseal(bag, (DECISION_LEDGER_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("deterministic_authority_mismatch",)


def test_receiver_blocks_edit_relying_on_a_later_collision_resolution(
    tmp_path: Path,
) -> None:
    bag = _complete_collision_handoff(
        tmp_path,
        deferred_collision_resolution=True,
    )

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("deterministic_authority_mismatch",)


def test_receiver_blocks_prohibited_receipt_claim_boundaries(tmp_path: Path) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    receipt_path = bag / "name-atlas" / "change_receipt.json"
    envelope = json.loads(receipt_path.read_text(encoding="utf-8"))
    envelope["receipt"]["claim_boundaries"] = [
        "This handoff is semantically correct, institutionally authorized, "
        "and compliant."
    ]
    canonical_core = json.dumps(
        envelope["receipt"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    envelope["receipt_fingerprint"] = hashlib.sha256(canonical_core).hexdigest()
    _write_json(receipt_path, envelope)
    _refresh_tagmanifest(bag)
    assert BagItPackageValidator().validate(bag).valid is True

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("receipt_schema_invalid",)


def test_receiver_blocks_family_identity_not_derived_from_source_contract(
    tmp_path: Path,
) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = ledger["decisions"][0]
    original_family_id = entry["family_id"]
    replacement_family_id = "f" * 64
    entry["family_id"] = replacement_family_id
    entry["human_decision"]["family_id"] = replacement_family_id
    for proposal in entry["initial_proposals"]:
        proposal["family_id"] = replacement_family_id
    review = entry["meaning_review"]
    review["evidence_packet"]["family_id"] = replacement_family_id
    packet = EvidencePacket.model_validate_json(json.dumps(review["evidence_packet"]))
    review["evidence_fingerprint"] = evidence_fingerprint(packet)
    _write_json(ledger_path, ledger)

    for relative_path, reverse in (
        (FORWARD_PATH_MAP_PATH, False),
        (REVERSE_PATH_MAP_PATH, True),
    ):
        map_path = bag / relative_path
        map_path.write_text(
            map_path.read_text(encoding="utf-8").replace(
                original_family_id,
                replacement_family_id,
            ),
            encoding="utf-8",
        )
        # Reparse and rewrite through the production serializer to retain the
        # exact canonical CSV contract after the identity substitution.
        rows = parse_path_map(map_path.read_bytes(), reverse=reverse)
        map_path.unlink()
        write_path_map(map_path, rows, reverse=reverse)

    _reseal(
        bag,
        (
            DECISION_LEDGER_PATH,
            FORWARD_PATH_MAP_PATH,
            REVERSE_PATH_MAP_PATH,
        ),
    )

    result = verify_receipt(bag)

    assert result.failed_check_ids == (
        "deterministic_authority_mismatch",
        "producer_report_inconsistent",
    )


def test_receiver_blocks_resealed_but_false_markdown_summary(tmp_path: Path) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    summary_path = bag / VERIFICATION_SUMMARY_PATH
    summary_path.write_text(
        "# Reversible Name Atlas verification summary\n\n"
        "- Content objects staged copy-only: 999\n",
        encoding="utf-8",
    )
    _reseal(bag, (VERIFICATION_SUMMARY_PATH,))

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("verification_summary_disagrees",)


def test_receiver_blocks_bagit_valid_false_offline_receipt(tmp_path: Path) -> None:
    bag = _complete_meaning_handoff(tmp_path)
    html_path = bag / CHANGE_RECEIPT_HTML_PATH
    html_path.write_text(
        html_path.read_text(encoding="utf-8").replace(
            "GPT-assisted decisions</dt><dd>1",
            "GPT-assisted decisions</dt><dd>999",
        ),
        encoding="utf-8",
    )
    _refresh_tagmanifest(bag)
    assert BagItPackageValidator().validate(bag).valid is True

    result = verify_receipt(bag)

    assert result.failed_check_ids == ("offline_receipt_disagrees",)
