"""Complete A3 proof transaction, counterfactual, and reconstruction."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Literal

import pytest

from name_atlas.folder_app import FolderRunPresentation
from name_atlas.folder_job_service import JobBackedFolderRunService
from name_atlas.folder_refactor.contracts import (
    FolderAcceptedPlan,
    FolderPlan,
    FolderPlanEntry,
)
from name_atlas.folder_refactor.job import (
    FolderJobLifecycle,
    FolderRefactorJobStore,
)
from name_atlas.folder_refactor.planner_contracts import (
    FolderPlannerTurnInput,
    PlannerInventoryFile,
    ProviderToolResponse,
    ReadTextExcerptCall,
    SubmitPlanCall,
)
from name_atlas.folder_refactor.planner_provider import (
    DETERMINISTIC_DEVELOPMENT_REQUEST,
)
from name_atlas.folder_refactor.portable_artifacts import (
    ACCEPTED_PLAN_PATH,
    CHANGE_LEDGER_PATH,
    CHANGE_RECEIPT_PATH,
    EVIDENCE_LEDGER_PATH,
    FORWARD_PATH_MAP_PATH,
    PROOF_AND_RESTORE_HTML_PATH,
    REFERENCE_GRAPH_PATH,
    REVERSE_PATH_MAP_PATH,
    SOURCE_SNAPSHOT_PATH,
    USER_REQUEST_PATH,
    VERIFICATION_REPORT_PATH,
)
from name_atlas.folder_refactor.receipt_contracts import (
    FolderReceiptVerificationStatus,
)
from name_atlas.folder_refactor.receipt_verifier import verify_folder_receipt
from name_atlas.folder_refactor.reconstruction import (
    FolderReconstructionError,
    restore_folder_receipt,
)
from name_atlas.folder_refactor.serialization import canonical_json_bytes
from name_atlas.verification import BagItPackageValidator
from name_atlas.verification.bag_writer import BagItWriter

REQUEST = DETERMINISTIC_DEVELOPMENT_REQUEST
RESULT_NAME = "northstar-result"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _AsymmetricPlanProvider:
    """Submit one complete path plan that forces a Markdown-link rewrite."""

    def __init__(self) -> None:
        self.invocation_count = 0

    @property
    def provider_kind(self) -> Literal["deterministic"]:
        return "deterministic"

    async def exchange(
        self,
        turn_input: FolderPlannerTurnInput,
        /,
    ) -> ProviderToolResponse:
        self.invocation_count += 1
        initial = turn_input.evidence_ledger.initial_evidence
        assert isinstance(initial, dict)
        raw_files = initial["files"]
        assert isinstance(raw_files, list)
        files = tuple(
            PlannerInventoryFile.model_validate_json(
                canonical_json_bytes(item),
                strict=True,
            )
            for item in raw_files
        )
        targets = {
            "assets/apollo final.txt": "handoff/final report.txt",
            "brief.md": "handoff/overview.md",
            "research/data.bin": "handoff/research/data.bin",
        }
        plan = FolderPlan(
            source_commitment=turn_input.source_commitment,
            request_fingerprint=turn_input.request_fingerprint,
            request_scope="rename_and_move_every_file",
            evidence_fingerprint=turn_input.evidence_ledger.evidence_fingerprint,
            result_folder_name=RESULT_NAME,
            entries=tuple(
                FolderPlanEntry(
                    file_id=item.file_id,
                    original_path=item.relative_path,
                    proposed_target=targets[item.relative_path],
                    rationale="Creates the bounded A3 connected-folder handoff.",
                    evidence_ids=("initial_inventory",),
                )
                for item in files
                if not item.protected
            ),
            exclusions=(),
        )
        return ProviderToolResponse(
            provider_kind="deterministic",
            observable_output_items=(
                {"type": "a3_asymmetric_plan", "response_turn": 1},
            ),
            tool_calls=(SubmitPlanCall(call_id="a3-complete-plan", plan=plan),),
        )


class _EvidenceBetweenSubmissionsProvider:
    """Reject, gather evidence, then submit a valid plan under its new fingerprint."""

    def __init__(self) -> None:
        self.invocation_count = 0

    @property
    def provider_kind(self) -> Literal["deterministic"]:
        return "deterministic"

    async def exchange(
        self,
        turn_input: FolderPlannerTurnInput,
        /,
    ) -> ProviderToolResponse:
        self.invocation_count += 1
        initial = turn_input.evidence_ledger.initial_evidence
        assert isinstance(initial, dict)
        raw_files = initial["files"]
        assert isinstance(raw_files, list)
        files = tuple(
            PlannerInventoryFile.model_validate_json(
                canonical_json_bytes(item),
                strict=True,
            )
            for item in raw_files
        )
        eligible = tuple(item for item in files if not item.protected)
        if turn_input.response_turn == 2:
            brief = next(item for item in eligible if item.relative_path == "brief.md")
            return ProviderToolResponse(
                provider_kind="deterministic",
                observable_output_items=(
                    {"type": "request_more_evidence", "response_turn": 2},
                ),
                tool_calls=(
                    ReadTextExcerptCall(
                        call_id="read-brief-after-rejection",
                        file_id=brief.file_id,
                        start_byte=0,
                        max_bytes=64,
                    ),
                ),
            )
        targets = {
            "assets/apollo final.txt": "handoff/final report.txt",
            "brief.md": "handoff/overview.md",
            "research/data.bin": "handoff/research/data.bin",
        }
        selected = eligible[:-1] if turn_input.response_turn == 1 else eligible
        plan = FolderPlan(
            source_commitment=turn_input.source_commitment,
            request_fingerprint=turn_input.request_fingerprint,
            request_scope="rename_and_move_every_file",
            evidence_fingerprint=turn_input.evidence_ledger.evidence_fingerprint,
            result_folder_name="northstar-repair-result",
            entries=tuple(
                FolderPlanEntry(
                    file_id=item.file_id,
                    original_path=item.relative_path,
                    proposed_target=targets[item.relative_path],
                    rationale="Creates the bounded repaired A3 handoff.",
                    evidence_ids=("initial_inventory",),
                )
                for item in selected
            ),
            exclusions=(),
        )
        return ProviderToolResponse(
            provider_kind="deterministic",
            observable_output_items=(
                {
                    "type": "repair_submission",
                    "response_turn": turn_input.response_turn,
                },
            ),
            tool_calls=(
                SubmitPlanCall(
                    call_id=f"submit-plan-{turn_input.response_turn}",
                    plan=plan,
                ),
            ),
        )


def _make_source(root: Path) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "research").mkdir()
    (root / "empty" / "keep").mkdir(parents=True)
    (root / "assets" / "apollo final.txt").write_bytes(b"approved\n")
    (root / "brief.md").write_bytes(
        b"Use the [approved report](<assets/apollo final.txt#summary>).\r\n"
    )
    (root / "research" / "data.bin").write_bytes(b"\x00\x01opaque\xff")
    (root / ".env.local").write_bytes(b"DEMO_ONLY=true\n")
    return root


def _tree_state(root: Path) -> dict[str, tuple[object, ...]]:
    state: dict[str, tuple[object, ...]] = {}
    for candidate in (root, *sorted(root.rglob("*"))):
        relative = "." if candidate == root else candidate.relative_to(root).as_posix()
        metadata = candidate.lstat()
        if candidate.is_dir():
            state[relative] = (
                "directory",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mtime_ns,
            )
        else:
            state[relative] = (
                "file",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                candidate.read_bytes(),
            )
    return state


def _portable_tree_bytes(root: Path) -> dict[str, bytes | None]:
    return {
        candidate.relative_to(root).as_posix(): (
            None if candidate.is_dir() else candidate.read_bytes()
        )
        for candidate in sorted(root.rglob("*"))
    }


@pytest.mark.anyio
async def test_complete_a3_transaction_verifies_blocks_alteration_and_reconstructs(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source")
    output = tmp_path / "results"
    output.mkdir()
    job_path = tmp_path / "jobs" / "a3.json"
    provider = _AsymmetricPlanProvider()
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )
    source_before = _tree_state(source)

    presentation = await service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )

    assert isinstance(presentation, FolderRunPresentation)
    assert presentation.independent_verification_passed is True
    assert presentation.reconstruction_available is True
    assert provider.invocation_count == 1
    assert _tree_state(source) == source_before
    assert presentation.result_root == output / RESULT_NAME
    assert (presentation.data_root / "handoff" / "overview.md").read_bytes() == (
        b"Use the [approved report](<final%20report.txt#summary>).\r\n"
    )
    assert (presentation.data_root / ".env.local").read_bytes() == (
        source / ".env.local"
    ).read_bytes()

    job = FolderRefactorJobStore(job_path).load()
    assert job.lifecycle is FolderJobLifecycle.VERIFIED
    assert job.receipt_fingerprint is not None
    assert job.change_ledger is not None
    proof_root = presentation.result_root / "name-atlas"
    expected_artifacts = {
        SOURCE_SNAPSHOT_PATH,
        USER_REQUEST_PATH,
        EVIDENCE_LEDGER_PATH,
        ACCEPTED_PLAN_PATH,
        REFERENCE_GRAPH_PATH,
        FORWARD_PATH_MAP_PATH,
        REVERSE_PATH_MAP_PATH,
        CHANGE_LEDGER_PATH,
        VERIFICATION_REPORT_PATH,
        CHANGE_RECEIPT_PATH,
        PROOF_AND_RESTORE_HTML_PATH,
    }
    assert expected_artifacts <= {
        candidate.relative_to(presentation.result_root).as_posix()
        for candidate in presentation.result_root.rglob("*")
        if candidate.is_file()
    }
    rewritten_file_id = next(
        entry.file_id
        for entry in job.change_ledger.entries
        if entry.original_path == "brief.md"
    )
    assert (
        proof_root / "original-content" / f"{rewritten_file_id}.bin"
    ).read_bytes() == (source / "brief.md").read_bytes()
    assert str(source.resolve()).encode() not in b"".join(
        candidate.read_bytes()
        for candidate in proof_root.rglob("*")
        if candidate.is_file()
        and candidate.suffix.casefold() in {".json", ".csv", ".html"}
    )

    positive = verify_folder_receipt(presentation.result_root)
    with_source = verify_folder_receipt(
        presentation.result_root,
        source_root=source,
    )
    assert positive.status is FolderReceiptVerificationStatus.VERIFIED
    assert with_source.status is FolderReceiptVerificationStatus.VERIFIED
    assert positive.receipt_fingerprint == job.receipt_fingerprint

    unrelated = tmp_path / "received-elsewhere" / "portable-result"
    unrelated.parent.mkdir()
    shutil.copytree(presentation.result_root, unrelated)
    unrelated_verification = verify_folder_receipt(unrelated)
    assert unrelated_verification.status is FolderReceiptVerificationStatus.VERIFIED
    assert unrelated_verification.receipt_fingerprint == job.receipt_fingerprint

    altered = tmp_path / "counterfactual" / "altered-result"
    altered.parent.mkdir()
    shutil.copytree(presentation.result_root, altered)
    accepted_path = altered / ACCEPTED_PLAN_PATH
    accepted_payload = json.loads(accepted_path.read_bytes())
    for mapping in accepted_payload["file_mappings"]:
        if mapping["original_path"] == "assets/apollo final.txt":
            mapping["target_path"] = "handoff/adjusted final report.txt"
            break
    altered_plan = FolderAcceptedPlan.model_validate_json(
        canonical_json_bytes(accepted_payload),
        strict=True,
    )
    accepted_path.write_bytes(canonical_json_bytes(altered_plan))
    BagItWriter().finalize_tagmanifest(altered)
    assert BagItPackageValidator().validate(altered).valid is True
    blocked = verify_folder_receipt(altered)
    assert blocked.status is FolderReceiptVerificationStatus.BLOCKED
    assert blocked.failed_check_ids == ("artifact_digest_mismatch:accepted_plan",)

    result_before_restore = _portable_tree_bytes(presentation.result_root)
    restore_destination = presentation.result_root.parent / "recreated-original"
    restore_report = restore_folder_receipt(
        presentation.result_root,
        restore_destination,
    )
    assert restore_report.destination == restore_destination.resolve()
    assert restore_report.restored_file_count == 4
    assert _portable_tree_bytes(restore_destination) == _portable_tree_bytes(source)
    assert _tree_state(source) == source_before
    assert _portable_tree_bytes(presentation.result_root) == result_before_restore

    with pytest.raises(FolderReconstructionError) as existing_error:
        restore_folder_receipt(presentation.result_root, restore_destination)
    assert existing_error.value.code == "destination_exists"


@pytest.mark.anyio
async def test_receipt_replays_each_submission_against_its_turn_evidence(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source")
    output = tmp_path / "results"
    output.mkdir()
    job_path = tmp_path / "jobs" / "repair.json"
    provider = _EvidenceBetweenSubmissionsProvider()
    service = JobBackedFolderRunService(
        job_path=job_path,
        provider_factory=lambda _job: provider,
    )

    presentation = await service.plan_and_create_copy(
        source_root=source,
        output_parent=output,
        request=REQUEST,
    )

    assert isinstance(presentation, FolderRunPresentation)
    assert provider.invocation_count == 3
    job = FolderRefactorJobStore(job_path).load()
    assert job.lifecycle is FolderJobLifecycle.VERIFIED
    assert job.planner_progress is not None
    assert job.planner_progress.plan_submissions == 2
    assert tuple(
        failure.code for failure in job.planner_progress.compiler_failures
    ) == ("missing_file_ids",)
    assert len(job.planner_progress.evidence_ledger.records) == 1
    verification = verify_folder_receipt(presentation.result_root)
    assert verification.status is FolderReceiptVerificationStatus.VERIFIED
