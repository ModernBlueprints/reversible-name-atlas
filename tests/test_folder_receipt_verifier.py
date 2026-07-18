"""Independent generic-folder receipt verification tests."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

import name_atlas.folder_refactor.receipt_verifier as receipt_verifier_module
from name_atlas.folder_app import FolderRunPresentation
from name_atlas.folder_job_service import JobBackedFolderRunService
from name_atlas.folder_refactor.contracts import (
    FolderFile,
    FolderInventory,
    FolderPlan,
    FolderPlanEntry,
)
from name_atlas.folder_refactor.markdown_contracts import FolderReferenceGraph
from name_atlas.folder_refactor.markdown_links import build_reference_graph_from_reader
from name_atlas.folder_refactor.planner_contracts import (
    FolderPlannerTurnInput,
    FolderProviderResponse,
    ProviderToolResponse,
    SubmitPlanCall,
)
from name_atlas.folder_refactor.portable_artifacts import (
    ACCEPTED_PLAN_PATH,
    CHANGE_RECEIPT_PATH,
    canonical_portable_json_bytes,
    strict_json_object,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderReceiptVerificationStatus,
)
from name_atlas.folder_refactor.receipt_verifier import (
    FolderReceiptCandidateError,
    verify_folder_receipt,
)
from name_atlas.verification.bag_writer import BagItWriter
from name_atlas.verification.bagit_validator import BagItPackageValidator

REQUEST = "Organize this connected folder and keep every supported link working."


@dataclass(frozen=True, slots=True)
class VerifiedFolderFixture:
    """One real deterministic result and its untouched source."""

    source_root: Path
    result_root: Path
    presentation: FolderRunPresentation


class _ConnectedFolderPlanner:
    """Submit one complete deterministic plan that forces a link rewrite."""

    @property
    def provider_kind(self) -> Literal["deterministic"]:
        return "deterministic"

    async def exchange(
        self,
        turn_input: FolderPlannerTurnInput,
        /,
    ) -> FolderProviderResponse:
        destinations = {
            "assets/raw.bin": "handoff/assets/raw.bin",
            "materials/report.txt": "handoff/final/report.txt",
            "notes.md": "handoff/notes.md",
        }
        initial = turn_input.evidence_ledger.initial_evidence
        assert isinstance(initial, dict)
        files = initial["files"]
        assert isinstance(files, list)
        entries = tuple(
            FolderPlanEntry(
                file_id=item["file_id"],
                original_path=item["relative_path"],
                proposed_target=destinations[item["relative_path"]],
                rationale="Deterministic receipt-verifier integration fixture.",
                evidence_ids=("initial_inventory",),
            )
            for item in files
            if not item["protected"]
        )
        plan = FolderPlan(
            source_commitment=turn_input.source_commitment,
            request_fingerprint=turn_input.request_fingerprint,
            request_scope="rename_and_move_every_file",
            evidence_fingerprint=turn_input.evidence_ledger.evidence_fingerprint,
            result_folder_name="organized-copy",
            entries=entries,
            exclusions=(),
        )
        return ProviderToolResponse(
            provider_kind="deterministic",
            observable_output_items=(
                {
                    "type": "receipt_verifier_fixture_plan",
                    "response_turn": turn_input.response_turn,
                },
            ),
            tool_calls=(SubmitPlanCall(call_id="fixture-plan", plan=plan),),
        )


def create_verified_folder_fixture(tmp_path: Path) -> VerifiedFolderFixture:
    """Create one full copy, receipt, proof, and independent verification."""

    source = tmp_path / "source"
    output = tmp_path / "results"
    (source / "assets").mkdir(parents=True)
    (source / "materials").mkdir()
    (source / "empty" / "keep").mkdir(parents=True)
    output.mkdir()
    (source / ".env").write_bytes(b"DEMO_MODE=fixture\n")
    (source / "assets" / "raw.bin").write_bytes(b"\x00fixture\xff")
    (source / "materials" / "report.txt").write_text(
        "Approved report\n",
        encoding="utf-8",
    )
    (source / "notes.md").write_text(
        "[report](materials/report.txt)\n",
        encoding="utf-8",
    )
    service = JobBackedFolderRunService(
        job_path=tmp_path / "state" / "job.json",
        provider_factory=lambda _job: _ConnectedFolderPlanner(),
    )

    presentation = asyncio.run(
        service.plan_and_create_copy(
            source_root=source,
            output_parent=output,
            request=REQUEST,
        )
    )
    assert isinstance(presentation, FolderRunPresentation)
    assert presentation.supported_link_update_count == 1
    return VerifiedFolderFixture(
        source_root=source.resolve(strict=True),
        result_root=presentation.result_root,
        presentation=presentation,
    )


def test_source_free_verifier_survives_unrelated_absolute_location(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    relocated = tmp_path / "unrelated" / "received-copy"
    relocated.parent.mkdir()
    shutil.copytree(fixture.result_root, relocated)

    source_free = verify_folder_receipt(relocated)
    source_bound = verify_folder_receipt(relocated, source_root=fixture.source_root)

    assert source_free.status is FolderReceiptVerificationStatus.VERIFIED
    assert source_free.failed_check_ids == ()
    assert source_free.receipt_fingerprint is not None
    assert source_bound.status is FolderReceiptVerificationStatus.VERIFIED
    assert source_bound.receipt_fingerprint == source_free.receipt_fingerprint
    assert source_bound.checks[-1].check_id == "supplied_source_matches"


def test_optional_wrong_source_is_an_exact_receiver_blocker(tmp_path: Path) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    changed_source = tmp_path / "changed-source"
    shutil.copytree(fixture.source_root, changed_source)
    (changed_source / "materials" / "report.txt").write_text(
        "Changed after receipt creation\n",
        encoding="utf-8",
    )

    result = verify_folder_receipt(
        fixture.result_root,
        source_root=changed_source,
    )

    assert result.status is FolderReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("supplied_source_mismatch",)


def test_bagit_valid_accepted_plan_alteration_has_one_exact_failure(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    altered = tmp_path / "altered-result"
    shutil.copytree(fixture.result_root, altered)
    accepted_path = altered / ACCEPTED_PLAN_PATH
    payload = strict_json_object(accepted_path.read_bytes())
    mappings = payload["file_mappings"]
    assert isinstance(mappings, list)
    mappings[0]["target_path"] = "syntactically-valid/changed-target.bin"
    accepted_path.write_bytes(canonical_portable_json_bytes(payload))
    BagItWriter().finalize_tagmanifest(altered)

    assert BagItPackageValidator().validate(altered).valid is True
    result = verify_folder_receipt(altered)

    assert result.status is FolderReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("artifact_digest_mismatch:accepted_plan",)


def test_verifier_performs_no_writes(tmp_path: Path) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    before = _tree_snapshot(fixture.result_root)

    result = verify_folder_receipt(fixture.result_root)

    assert result.status is FolderReceiptVerificationStatus.VERIFIED
    assert _tree_snapshot(fixture.result_root) == before


def test_verifier_rebuilds_markdown_graph_through_sequential_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    observed_paths: list[str] = []

    def observed_builder(
        inventory: FolderInventory,
        reader: Callable[[FolderFile], bytes],
    ) -> FolderReferenceGraph:
        def observed_reader(source_file: FolderFile) -> bytes:
            observed_paths.append(source_file.relative_path)
            return reader(source_file)

        return build_reference_graph_from_reader(inventory, observed_reader)

    monkeypatch.setattr(
        receipt_verifier_module,
        "build_reference_graph_from_reader",
        observed_builder,
    )

    result = verify_folder_receipt(fixture.result_root)

    assert result.status is FolderReceiptVerificationStatus.VERIFIED
    assert observed_paths == ["notes.md"]


def test_malformed_receipt_is_blocked_after_ordinary_bagit_reseal(
    tmp_path: Path,
) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    altered = tmp_path / "malformed-receipt"
    shutil.copytree(fixture.result_root, altered)
    receipt_path = altered / CHANGE_RECEIPT_PATH
    payload = strict_json_object(receipt_path.read_bytes())
    receipt = payload["receipt"]
    assert isinstance(receipt, dict)
    receipt["schema_version"] = "folder-change-receipt.invalid"
    receipt_path.write_bytes(canonical_portable_json_bytes(payload))
    BagItWriter().finalize_tagmanifest(altered)

    result = verify_folder_receipt(altered)

    assert BagItPackageValidator().validate(altered).valid is True
    assert result.status is FolderReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("receipt_schema_invalid",)


def test_candidate_errors_are_distinct_from_receipt_blockers(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    ordinary_file = tmp_path / "ordinary-file"
    ordinary_file.write_bytes(b"not a result")

    with pytest.raises(FolderReceiptCandidateError):
        verify_folder_receipt(missing)
    with pytest.raises(FolderReceiptCandidateError):
        verify_folder_receipt(ordinary_file)

    target = tmp_path / "directory"
    target.mkdir()
    link = tmp_path / "linked-directory"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        return
    with pytest.raises(FolderReceiptCandidateError):
        verify_folder_receipt(link)


def test_internal_symlink_blocks_before_portable_authority_use(tmp_path: Path) -> None:
    fixture = create_verified_folder_fixture(tmp_path / "fixture")
    target = tmp_path / "outside"
    target.write_bytes(b"outside")
    link = fixture.result_root / "unexpected-link"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are unavailable on this platform.")

    result = verify_folder_receipt(fixture.result_root)

    assert result.status is FolderReceiptVerificationStatus.BLOCKED
    assert result.failed_check_ids == ("candidate_tree_unsupported",)


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, str], ...]:
    facts: list[tuple[str, str, int, str]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(metadata.st_mode):
            facts.append((relative, "directory", stat.S_IMODE(metadata.st_mode), ""))
        elif stat.S_ISREG(metadata.st_mode):
            facts.append(
                (
                    relative,
                    "file",
                    metadata.st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
        elif stat.S_ISLNK(metadata.st_mode):
            facts.append((relative, "symlink", 0, os.readlink(path)))
        else:
            facts.append((relative, "special", metadata.st_size, ""))
    return tuple(facts)
