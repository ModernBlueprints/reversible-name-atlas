"""Focused portable receipt and independent receiver-verifier tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from name_atlas.artifacts import (
    PathMapRow,
    ProofStatus,
    render_verification_summary,
    write_path_map,
)
from name_atlas.cases import (
    CaseDecisionBinding,
    CaseDecisionMethod,
)
from name_atlas.cases import (
    card_fingerprint as case_card_fingerprint,
)
from name_atlas.decisions import approve_family
from name_atlas.domain import (
    ContentRole,
    DecisionCard,
    LinkedObservation,
    PackageValidationResult,
)
from name_atlas.package_import import import_package
from name_atlas.proposals import build_proposals
from name_atlas.receipts import (
    CHANGE_RECEIPT_PATH,
    DECISION_LEDGER_PATH,
    PORTABLE_SOURCE_SNAPSHOT_PATH,
    RECEIPT_CLAIM_BOUNDARIES,
    VERIFICATION_REPORT_PATH,
    VERIFICATION_SUMMARY_PATH,
    ArtifactCommitment,
    DecisionLedgerEntry,
    DecisionLedgerV2,
    DecisionMethod,
    ReceiptContractError,
    ReceiptCore,
    VerificationReportV2,
    artifact_commitment,
    build_receipt_envelope,
    canonical_artifact_json_bytes,
    canonical_receipt_core_bytes,
    decision_card_fingerprint,
    oslo_tz,
    portable_snapshot_from_source,
    receipt_fingerprint,
    render_offline_receipt,
    staged_data_commitment,
    staged_data_members,
)
from name_atlas.receiver_verifier import (
    ReceiptVerificationStatus,
    verify_receipt,
)
from name_atlas.verification import BagItPackageValidator
from name_atlas.verification.staged_proof import verify_staged_artifacts

CASE_ID = "12345678123442348123456789abcdef"
COMMITTED_ARTIFACT_PATHS = (
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _render_manifest(root: Path, relative_paths: tuple[str, ...]) -> str:
    return "".join(
        f"{_sha256(root / relative_path)}  {relative_path}\n"
        for relative_path in sorted(relative_paths)
    )


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


def _source_package(parent: Path) -> Path:
    source = parent / "source"
    (source / "objects").mkdir(parents=True)
    (source / "metadata").mkdir()
    (source / "objects" / "photo-one.txt").write_bytes(b"payload bytes\n")
    (source / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\nobjects/photo-one.txt,NA-0001,Photo one\n",
        encoding="utf-8",
    )
    return source


def _complete_handoff(parent: Path) -> tuple[Path, Path, ReceiptCore]:
    source = _source_package(parent)
    package = import_package(source)
    proposals = build_proposals(package.families)
    decision = approve_family(
        package.families[0],
        proposals,
        semantic_card_available=False,
    )
    target = decision.resolved_targets[ContentRole.ORIGINAL]
    content_member = package.families[0].original
    map_row = PathMapRow(
        family_id=package.families[0].family_id,
        canonical_identifier=package.families[0].canonical_identifier,
        role=ContentRole.ORIGINAL,
        source_path=content_member.relative_path,
        target_path=target,
        size=content_member.size,
        sha256=content_member.sha256,
    )

    bag = parent / "handoff"
    (bag / "data" / "objects").mkdir(parents=True)
    (bag / "data" / "metadata").mkdir(parents=True)
    (bag / "name-atlas" / "original-control" / "metadata").mkdir(parents=True)
    (bag / "data" / target).write_bytes(
        (source / content_member.relative_path).read_bytes()
    )
    staged_metadata = (
        f"filename,dc.identifier,dc.title\n{target},NA-0001,Photo one\n"
    ).encode()
    (bag / "data" / "metadata" / "metadata.csv").write_bytes(staged_metadata)
    original_metadata = (source / "metadata" / "metadata.csv").read_bytes()
    (bag / "name-atlas" / "original-control" / "metadata" / "metadata.csv").write_bytes(
        original_metadata
    )

    portable_snapshot = portable_snapshot_from_source(package.snapshot)
    (bag / "name-atlas" / "source_snapshot.json").write_bytes(
        canonical_artifact_json_bytes(portable_snapshot)
    )
    ledger = DecisionLedgerV2(
        case_id=CASE_ID,
        decisions=(
            DecisionLedgerEntry(
                family_id=decision.family_id,
                initial_proposals=proposals,
                decision_method=DecisionMethod.BATCH_APPROVAL,
                human_decision=decision,
                decided_at=datetime(2026, 7, 18, 1, 0, tzinfo=oslo_tz),
                meaning_review=None,
            ),
        ),
    )
    (bag / "name-atlas" / "decision_ledger.json").write_bytes(
        canonical_artifact_json_bytes(ledger)
    )
    write_path_map(
        bag / "name-atlas" / "forward_path_map.csv",
        (map_row,),
        reverse=False,
    )
    write_path_map(
        bag / "name-atlas" / "reverse_path_map.csv",
        (map_row,),
        reverse=True,
    )
    (bag / "name-atlas" / "verification_summary.md").write_bytes(
        render_verification_summary(
            content_objects=1,
            content_bytes=content_member.size,
        )
    )

    payload_paths = (
        f"data/{target}",
        "data/metadata/metadata.csv",
    )
    payload_bytes = sum((bag / path).stat().st_size for path in payload_paths)
    (bag / "bagit.txt").write_text(
        "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n",
        encoding="utf-8",
    )
    (bag / "bag-info.txt").write_text(
        "Bagging-Date: 2026-07-18\n"
        "Bag-Software-Agent: Reversible Name Atlas 0.1.0\n"
        f"Payload-Oxum: {payload_bytes}.2\n",
        encoding="utf-8",
    )
    (bag / "manifest-sha256.txt").write_text(
        _render_manifest(bag, payload_paths), encoding="utf-8"
    )

    deterministic_proof = verify_staged_artifacts(
        package,
        (map_row,),
        {decision.family_id: decision},
        pending_root=bag,
        expected_source_snapshot=(
            bag / "name-atlas" / "source_snapshot.json"
        ).read_bytes(),
        expected_decision_ledger=(
            bag / "name-atlas" / "decision_ledger.json"
        ).read_bytes(),
    )
    report = VerificationReportV2(
        status=ProofStatus.VERIFIED,
        claim="Verified round-trip integrity within the supported package contract",
        generated_at=datetime(2026, 7, 18, 1, 0, tzinfo=oslo_tz),
        source_snapshot_commitment=package.snapshot.commitment,
        prestaging_snapshot_commitment=package.snapshot.commitment,
        postcopy_snapshot_commitment=package.snapshot.commitment,
        source_unchanged=True,
        content_object_count=1,
        content_bytes=content_member.size,
        control_files=deterministic_proof.control_files,
        map_row_count=1,
        checks=deterministic_proof.checks,
        bagit_validation=PackageValidationResult(
            validator="bagit",
            valid=True,
            messages=("BagIt validation passed.",),
        ),
        artifact_paths=(
            "name-atlas/source_snapshot.json",
            "name-atlas/decision_ledger.json",
            "name-atlas/forward_path_map.csv",
            "name-atlas/reverse_path_map.csv",
            "name-atlas/verification_report.json",
            "name-atlas/verification_summary.md",
            "name-atlas/change_receipt.json",
            "name-atlas/change_receipt.html",
            "name-atlas/original-control/metadata/metadata.csv",
            "bagit.txt",
            "bag-info.txt",
            "manifest-sha256.txt",
            "tagmanifest-sha256.txt",
        ),
        blockers=(),
    )
    (bag / "name-atlas" / "verification_report.json").write_bytes(
        canonical_artifact_json_bytes(report)
    )

    commitments = tuple(
        artifact_commitment(bag, relative_path)
        for relative_path in COMMITTED_ARTIFACT_PATHS
    )
    data_members = staged_data_members(bag)
    core = ReceiptCore(
        case_id=CASE_ID,
        source_snapshot_commitment=package.snapshot.commitment,
        source_member_count=len(package.snapshot.members),
        source_bytes=sum(member.size for member in package.snapshot.members),
        staged_data_commitment=staged_data_commitment(data_members),
        staged_data_file_count=len(data_members),
        staged_data_bytes=sum(member.size for member in data_members),
        artifact_commitments=commitments,
        map_row_count=1,
        decision_count=1,
        gpt_assisted_decision_count=0,
        human_decision_count=1,
        producer_bagit_validation=PackageValidationResult(
            validator="bagit",
            valid=True,
            messages=("BagIt validation passed.",),
        ),
        claim_boundaries=RECEIPT_CLAIM_BOUNDARIES,
    )
    envelope = build_receipt_envelope(core)
    (bag / "name-atlas" / "change_receipt.json").write_bytes(
        canonical_artifact_json_bytes(envelope)
    )
    (bag / "name-atlas" / "change_receipt.html").write_bytes(
        render_offline_receipt(envelope, ledger, report)
    )
    tag_paths = tuple(
        path.relative_to(bag).as_posix()
        for path in bag.rglob("*")
        if path.is_file()
        and not path.relative_to(bag).as_posix().startswith("data/")
        and path.name != "tagmanifest-sha256.txt"
    )
    (bag / "tagmanifest-sha256.txt").write_text(
        _render_manifest(bag, tag_paths), encoding="utf-8"
    )
    assert BagItPackageValidator().validate(bag).valid
    return source, bag, core


def _refresh_tagmanifest(bag: Path) -> None:
    tag_paths = tuple(
        path.relative_to(bag).as_posix()
        for path in bag.rglob("*")
        if path.is_file()
        and not path.relative_to(bag).as_posix().startswith("data/")
        and path.name != "tagmanifest-sha256.txt"
    )
    (bag / "tagmanifest-sha256.txt").write_text(
        _render_manifest(bag, tag_paths), encoding="utf-8"
    )


def _reseal_receipt(
    bag: Path,
    *,
    changed_paths: tuple[str, ...] = (),
    core_updates: dict[str, object] | None = None,
) -> None:
    """Recommit selected fixture facts without altering unrelated evidence."""

    receipt_path = bag / CHANGE_RECEIPT_PATH
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
    core_value.update(core_updates or {})
    core = ReceiptCore.model_validate_json(
        json.dumps(core_value, ensure_ascii=False, allow_nan=False)
    )
    envelope["receipt"] = core.model_dump(mode="json", exclude_none=False)
    envelope["receipt_fingerprint"] = receipt_fingerprint(core)
    _write_json(receipt_path, envelope)
    _refresh_tagmanifest(bag)
    assert BagItPackageValidator().validate(bag).valid is True


def test_receipt_core_fingerprint_is_canonical_and_non_circular(
    tmp_path: Path,
) -> None:
    _source, _bag, core = _complete_handoff(tmp_path)

    canonical = canonical_receipt_core_bytes(core)
    envelope = build_receipt_envelope(core)

    assert not canonical.endswith(b"\n")
    assert b"receipt_fingerprint" not in canonical
    assert envelope.receipt_fingerprint == hashlib.sha256(canonical).hexdigest()
    assert receipt_fingerprint(core) == envelope.receipt_fingerprint
    assert set(item.path for item in core.artifact_commitments).isdisjoint(
        {
            "name-atlas/change_receipt.json",
            "name-atlas/change_receipt.html",
            "tagmanifest-sha256.txt",
        }
    )


def test_ledger_entry_projects_exact_persisted_case_decision_method(
    tmp_path: Path,
) -> None:
    package = import_package(_source_package(tmp_path))
    proposals = build_proposals(package.families)
    decision = approve_family(
        package.families[0],
        proposals,
        semantic_card_available=False,
    )
    timestamp = datetime(2026, 7, 18, 1, 0, tzinfo=oslo_tz)
    binding = CaseDecisionBinding(
        family_id=decision.family_id,
        decision=decision,
        decision_method=CaseDecisionMethod.INDIVIDUAL_APPROVAL,
        decision_timestamp=timestamp,
        evidence_fingerprint=None,
        card_fingerprint=None,
    )

    entry = DecisionLedgerEntry.from_case_records(
        proposals=proposals,
        binding=binding,
        evidence_record=None,
        card_record=None,
    )

    assert entry.decision_method is DecisionMethod.INDIVIDUAL_APPROVAL
    assert entry.decided_at == timestamp

    missing_method = binding.model_copy(update={"decision_method": None})
    with pytest.raises(ReceiptContractError, match="persisted decision method"):
        DecisionLedgerEntry.from_case_records(
            proposals=proposals,
            binding=missing_method,
            evidence_record=None,
            card_record=None,
        )


def test_case_and_receipt_use_the_same_card_fingerprint_domain() -> None:
    card = DecisionCard(
        possible_interpretations=(
            LinkedObservation(text="Interpretation", evidence_ids=("path:one",)),
        ),
        possible_meaning_loss=(
            LinkedObservation(text="Meaning risk", evidence_ids=("path:one",)),
        ),
        uncertainty="Uncertain",
        why_the_distinction_matters="The path may communicate different meaning.",
        discriminating_question="Which meaning should the human preserve?",
        candidate_explanations=(),
    )

    assert decision_card_fingerprint(card) == case_card_fingerprint(card)


def test_receipt_core_rejects_a_circular_or_unknown_commitment(tmp_path: Path) -> None:
    _source, _bag, core = _complete_handoff(tmp_path)
    circular = ArtifactCommitment(
        path="name-atlas/change_receipt.json",
        size=1,
        sha256="a" * 64,
    )

    with pytest.raises(ValidationError, match="unsupported artifact commitment"):
        ReceiptCore.model_validate(
            {
                **core.model_dump(mode="python"),
                "artifact_commitments": tuple(
                    sorted(
                        (*core.artifact_commitments, circular),
                        key=lambda item: item.path,
                    )
                ),
            }
        )


def test_source_free_verifier_passes_moved_bag_without_writes(tmp_path: Path) -> None:
    source, bag, core = _complete_handoff(tmp_path)
    moved = tmp_path / "unrelated" / "received-bag"
    moved.parent.mkdir()
    shutil.copytree(bag, moved)
    before = _read_tree(moved)

    result = verify_receipt(moved)
    source_result = verify_receipt(moved, source_root=source)

    assert result.status is ReceiptVerificationStatus.VERIFIED
    assert result.failed_check_ids == ()
    assert result.receipt_fingerprint == receipt_fingerprint(core)
    assert source_result.status is ReceiptVerificationStatus.VERIFIED
    assert source_result.checks[-1].check_id == "supplied_source_matches"
    assert _read_tree(moved) == before


def test_bagit_valid_altered_ledger_has_exact_receipt_digest_blocker(
    tmp_path: Path,
) -> None:
    _source, bag, _core = _complete_handoff(tmp_path)
    ledger_path = bag / DECISION_LEDGER_PATH
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["decisions"][0]["human_decision"]["resolved_targets"]["original"] = (
        "objects/NA-0001__different__original.txt"
    )
    _write_json(ledger_path, ledger)
    _refresh_tagmanifest(bag)

    assert BagItPackageValidator().validate(bag).valid is True
    result = verify_receipt(bag)

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("artifact_digest_mismatch:decision_ledger",)
    failure = next(check for check in result.checks if not check.passed)
    assert "name-atlas/decision_ledger.json" in failure.detail


@pytest.mark.parametrize(
    ("mutation", "expected_blocker"),
    (
        ("malformed_receipt", "receipt_schema_invalid"),
        ("duplicate_receipt_key", "receipt_schema_invalid"),
        ("unknown_receipt_schema", "receipt_schema_invalid"),
        ("fingerprint_mismatch", "receipt_fingerprint_mismatch"),
        (
            "missing_committed_summary",
            "artifact_missing_or_unreadable:verification_summary",
        ),
        (
            "changed_non_ledger_commitment",
            "artifact_digest_mismatch:verification_summary",
        ),
    ),
)
def test_receipt_and_raw_commitment_negative_matrix_has_stable_blockers(
    tmp_path: Path,
    mutation: str,
    expected_blocker: str,
) -> None:
    _source, bag, _core = _complete_handoff(tmp_path)
    receipt_path = bag / CHANGE_RECEIPT_PATH
    summary_path = bag / VERIFICATION_SUMMARY_PATH

    if mutation == "malformed_receipt":
        receipt_path.write_bytes(b'{"receipt":')
    elif mutation == "duplicate_receipt_key":
        receipt_bytes = receipt_path.read_bytes()
        receipt_path.write_bytes(
            b'{"receipt_fingerprint":"'
            + (b"0" * 64)
            + b'",'
            + receipt_bytes.lstrip()[1:]
        )
    elif mutation == "unknown_receipt_schema":
        envelope = json.loads(receipt_path.read_text(encoding="utf-8"))
        envelope["receipt"]["schema_version"] = "portable-change-receipt.v999"
        _write_json(receipt_path, envelope)
    elif mutation == "fingerprint_mismatch":
        envelope = json.loads(receipt_path.read_text(encoding="utf-8"))
        envelope["receipt_fingerprint"] = "0" * 64
        _write_json(receipt_path, envelope)
    elif mutation == "missing_committed_summary":
        summary_path.unlink()
    elif mutation == "changed_non_ledger_commitment":
        summary_path.write_bytes(summary_path.read_bytes() + b"Changed view.\n")
    else:  # pragma: no cover - the parameter table is exhaustive
        raise AssertionError(f"Unsupported test mutation: {mutation}")

    _refresh_tagmanifest(bag)
    assert BagItPackageValidator().validate(bag).valid is True

    result = verify_receipt(bag)

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == (expected_blocker,)


def test_malformed_recommitted_portable_artifact_has_stable_blocker(
    tmp_path: Path,
) -> None:
    _source, bag, _core = _complete_handoff(tmp_path)
    snapshot_path = bag / PORTABLE_SOURCE_SNAPSHOT_PATH
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["unexpected_field"] = "not allowed by the strict schema"
    _write_json(snapshot_path, snapshot)
    _reseal_receipt(bag, changed_paths=(PORTABLE_SOURCE_SNAPSHOT_PATH,))

    result = verify_receipt(bag)

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("portable_artifact_schema_invalid",)


def test_recommitted_payload_manifest_cannot_mask_staged_data_change(
    tmp_path: Path,
) -> None:
    _source, bag, _core = _complete_handoff(tmp_path)
    payload_path = next((bag / "data" / "objects").iterdir())
    payload_path.write_bytes(b"x" * payload_path.stat().st_size)
    payload_paths = tuple(
        path.relative_to(bag).as_posix()
        for path in (bag / "data").rglob("*")
        if path.is_file()
    )
    (bag / "manifest-sha256.txt").write_text(
        _render_manifest(bag, payload_paths),
        encoding="utf-8",
    )
    _reseal_receipt(bag, changed_paths=("manifest-sha256.txt",))

    result = verify_receipt(bag)

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("staged_data_commitment_mismatch",)


@pytest.mark.parametrize(
    ("mutation", "expected_blocker"),
    (
        ("source_count", "source_snapshot_receipt_mismatch"),
        ("decision_count", "receipt_counts_inconsistent"),
        ("producer_claim", "producer_report_inconsistent"),
    ),
)
def test_coherently_recommitted_cross_artifact_mismatches_are_blocked(
    tmp_path: Path,
    mutation: str,
    expected_blocker: str,
) -> None:
    _source, bag, core = _complete_handoff(tmp_path)

    if mutation == "source_count":
        _reseal_receipt(
            bag,
            core_updates={"source_member_count": core.source_member_count + 1},
        )
    elif mutation == "decision_count":
        _reseal_receipt(
            bag,
            core_updates={
                "decision_count": core.decision_count + 1,
                "human_decision_count": core.human_decision_count + 1,
            },
        )
    elif mutation == "producer_claim":
        report_path = bag / VERIFICATION_REPORT_PATH
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["claim"] = "A different producer claim."
        _write_json(report_path, report)
        _reseal_receipt(bag, changed_paths=(VERIFICATION_REPORT_PATH,))
    else:  # pragma: no cover - the parameter table is exhaustive
        raise AssertionError(f"Unsupported test mutation: {mutation}")

    result = verify_receipt(bag)

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == (expected_blocker,)


def test_unreadable_optional_source_has_stable_blocker(tmp_path: Path) -> None:
    _source, bag, _core = _complete_handoff(tmp_path)

    result = verify_receipt(bag, source_root=tmp_path / "missing-source")

    assert result.status is ReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("supplied_source_unreadable",)


def test_keyless_cli_subprocess_verifies_moved_bag_and_blocks_wrong_source(
    tmp_path: Path,
) -> None:
    source, bag, core = _complete_handoff(tmp_path)
    received = tmp_path / "receiver" / "portable-handoff"
    received.parent.mkdir()
    shutil.copytree(bag, received)
    project_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    command = (
        "uv",
        "run",
        "--no-sync",
        "name-atlas",
        "verify-receipt",
        str(received),
    )
    original_bag_bytes = _read_tree(received)

    verified = subprocess.run(
        command,
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert verified.returncode == 0
    assert verified.stdout == f"VERIFIED {receipt_fingerprint(core)}\n"
    assert verified.stderr == ""
    assert _read_tree(received) == original_bag_bytes

    source_verified = subprocess.run(
        (*command, "--source", str(source)),
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert source_verified.returncode == 0
    assert source_verified.stdout == f"VERIFIED {receipt_fingerprint(core)}\n"
    assert source_verified.stderr == ""
    assert _read_tree(received) == original_bag_bytes

    wrong_source = tmp_path / "different-source"
    shutil.copytree(source, wrong_source)
    payload = next((wrong_source / "objects").iterdir())
    payload.write_bytes(payload.read_bytes() + b"different")
    blocked = subprocess.run(
        (*command, "--source", str(wrong_source)),
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert blocked.returncode == 1
    assert blocked.stdout == "BLOCKED supplied_source_mismatch\n"
    assert blocked.stderr == ""
    assert _read_tree(received) == original_bag_bytes
