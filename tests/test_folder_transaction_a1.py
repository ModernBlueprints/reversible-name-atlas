"""A1 end-to-end tests for the generic copy-only folder transaction."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from name_atlas.folder_refactor import transaction as transaction_module
from name_atlas.folder_refactor.contracts import (
    FolderAcceptedPlan,
    FolderInventory,
    FolderPlannerOutcome,
    FolderVerificationReport,
)
from name_atlas.folder_refactor.planner import DeterministicDevelopmentPlanner
from name_atlas.folder_refactor.serialization import canonical_sha256
from name_atlas.folder_refactor.transaction import (
    FolderTransactionError,
    required_free_bytes,
    run_folder_refactor,
)
from name_atlas.verification import BagItPackageValidator

REQUEST = "Organize this folder into a clear handoff while keeping every file."
RESULT_NAME = "organized-copy"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_source(root: Path) -> Path:
    (root / "docs").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / ".git").mkdir()
    (root / "empty" / "keep-me").mkdir(parents=True)
    (root / "docs" / "brief.txt").write_bytes(b"Project brief\r\nLine two\r\n")
    (root / "assets" / "payload.bin").write_bytes(b"\x00\x01opaque-payload\xff\x10")
    (root / ".env").write_bytes(b"EXAMPLE_ONLY=not-a-secret\n")
    (root / ".git" / "config").write_bytes(b"[core]\n\tbare = false\n")
    return root


def _planner() -> DeterministicDevelopmentPlanner:
    return DeterministicDevelopmentPlanner(
        result_folder_name=RESULT_NAME,
        target_prefix="organized",
    )


def _file_bytes(root: Path) -> dict[str, bytes]:
    return {
        candidate.relative_to(root).as_posix(): candidate.read_bytes()
        for candidate in sorted(root.rglob("*"))
        if candidate.is_file()
    }


def _source_state(root: Path) -> dict[str, tuple[object, ...]]:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_no_pending_result(output_parent: Path) -> None:
    assert not tuple(output_parent.glob(f".{RESULT_NAME}.pending-*"))


@pytest.mark.anyio
async def test_walking_transaction_creates_separate_valid_bag_with_exact_proof(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    output_parent.mkdir()
    source_before = _source_state(source)

    result = await run_folder_refactor(
        source_root=source,
        output_parent=output_parent,
        request=REQUEST,
        planner=_planner(),
    )

    assert result.result_root == output_parent / RESULT_NAME
    assert result.data_root == result.result_root / "data"
    assert _source_state(source) == source_before
    assert not result.result_root.is_relative_to(source)
    assert not source.is_relative_to(result.result_root)

    source_files = _file_bytes(source)
    result_files = _file_bytes(result.data_root)
    assert len(result_files) == len(source_files) == 4
    assert set(result_files) == {
        ".env",
        ".git/config",
        "organized/assets/payload.bin",
        "organized/docs/brief.txt",
    }
    for mapping in result.accepted_plan.file_mappings:
        assert result_files[mapping.target_path] == source_files[mapping.original_path]
    assert (result.data_root / "empty" / "keep-me").is_dir()
    assert not any((result.data_root / "empty" / "keep-me").iterdir())

    assert (result.data_root / ".env").read_bytes() == source_files[".env"]
    assert (result.data_root / ".git" / "config").read_bytes() == (
        source_files[".git/config"]
    )
    protected = {
        mapping.original_path: mapping
        for mapping in result.accepted_plan.file_mappings
        if mapping.protected
    }
    assert set(protected) == {".env", ".git/config"}
    assert all(
        mapping.target_path == original_path and not mapping.planner_supplied
        for original_path, mapping in protected.items()
    )

    root_entries = {candidate.name for candidate in result.result_root.iterdir()}
    assert root_entries == {
        "bag-info.txt",
        "bagit.txt",
        "data",
        "manifest-sha256.txt",
        "name-atlas",
        "tagmanifest-sha256.txt",
    }
    assert BagItPackageValidator().validate(result.result_root).valid is True
    assert (result.result_root / "bagit.txt").read_text(encoding="utf-8") == (
        "BagIt-Version: 1.0\nTag-File-Character-Encoding: UTF-8\n"
    )

    proof_root = result.result_root / "name-atlas"
    expected_proof = {
        "accepted_plan.json",
        "source_snapshot.json",
        "user_request.json",
        "verification_report.json",
    }
    assert expected_proof <= {
        path.relative_to(proof_root).as_posix()
        for path in proof_root.rglob("*")
        if path.is_file()
    }
    inventory = FolderInventory.model_validate_json(
        (proof_root / "source_snapshot.json").read_bytes(),
        strict=True,
    )
    accepted_plan = FolderAcceptedPlan.model_validate_json(
        (proof_root / "accepted_plan.json").read_bytes(),
        strict=True,
    )
    report = FolderVerificationReport.model_validate_json(
        (proof_root / "verification_report.json").read_bytes(),
        strict=True,
    )
    request_record = json.loads(
        (proof_root / "user_request.json").read_text(encoding="utf-8")
    )
    assert accepted_plan == result.accepted_plan
    assert report == result.report
    assert request_record["request"] == REQUEST
    assert report.source_commitment == inventory.source_commitment
    assert report.file_count == len(source_files)
    assert report.protected_file_count == 2
    assert report.empty_directory_count == 1
    checks = {check.check_id: check.passed for check in report.checks}
    required_checks = {
        "bagit_validation",
        "complete_file_bijection",
        "empty_directories_preserved",
        "payload_hashes_preserved",
        "protected_paths_preserved",
        "result_is_separate",
        "source_unchanged",
    }
    assert required_checks <= checks.keys()
    assert all(checks[check_id] for check_id in required_checks)

    actual_payload_records = sorted(
        (
            {
                "path": relative_path,
                "sha256": _sha256(result.data_root / relative_path),
                "size": (result.data_root / relative_path).stat().st_size,
            }
            for relative_path in result_files
        ),
        key=lambda record: record["path"],
    )
    assert report.staged_data_commitment == canonical_sha256(actual_payload_records)
    absolute_source = str(source.resolve())
    for artifact in expected_proof:
        artifact_text = (proof_root / artifact).read_text(encoding="utf-8")
        assert absolute_source not in artifact_text


@pytest.mark.anyio
async def test_capacity_formula_and_insufficient_space_block_before_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    output_parent.mkdir()
    source_bytes = sum(len(payload) for payload in _file_bytes(source).values())
    required = required_free_bytes(
        source_bytes=source_bytes,
        rewritten_markdown_original_bytes=0,
    )
    assert required == source_bytes + 256 * 1024 * 1024
    planner = _planner()
    monkeypatch.setattr(
        transaction_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=required - 1),
    )

    with pytest.raises(FolderTransactionError, match="Insufficient free space"):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=planner,
        )

    assert planner.invocation_count == 0
    assert not any(output_parent.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize("layout", ["output_inside_source", "source_inside_output"])
async def test_source_and_output_overlap_block_before_planning(
    tmp_path: Path,
    layout: str,
) -> None:
    if layout == "output_inside_source":
        source = _make_source(tmp_path / "source")
        output_parent = source / "results"
        output_parent.mkdir()
    else:
        output_parent = tmp_path / "workspace"
        source = _make_source(output_parent / "source")
    source_before = _source_state(source)
    planner = _planner()

    with pytest.raises(
        FolderTransactionError,
        match="cannot contain one another",
    ):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=planner,
        )

    assert planner.invocation_count == 0
    assert _source_state(source) == source_before


class _ReplacingPlanner(DeterministicDevelopmentPlanner):
    def __init__(self, source_file: Path) -> None:
        super().__init__(
            result_folder_name=RESULT_NAME,
            target_prefix="organized",
        )
        self._source_file = source_file

    async def plan(
        self,
        *,
        request: str,
        inventory: FolderInventory,
        evidence_fingerprint: str,
    ) -> FolderPlannerOutcome:
        outcome = await super().plan(
            request=request,
            inventory=inventory,
            evidence_fingerprint=evidence_fingerprint,
        )
        replacement = self._source_file.with_name(".same-bytes-replacement.tmp")
        replacement.write_bytes(self._source_file.read_bytes())
        os.replace(replacement, self._source_file)
        return outcome


class _ReplacingDirectoryPlanner(DeterministicDevelopmentPlanner):
    def __init__(self, source_directory: Path) -> None:
        super().__init__(
            result_folder_name=RESULT_NAME,
            target_prefix="organized",
        )
        self._source_directory = source_directory

    async def plan(
        self,
        *,
        request: str,
        inventory: FolderInventory,
        evidence_fingerprint: str,
    ) -> FolderPlannerOutcome:
        outcome = await super().plan(
            request=request,
            inventory=inventory,
            evidence_fingerprint=evidence_fingerprint,
        )
        replacement = self._source_directory.with_name("replacement-directory")
        replacement.mkdir()
        for child in self._source_directory.iterdir():
            child.rename(replacement / child.name)
        self._source_directory.rmdir()
        replacement.rename(self._source_directory)
        return outcome


@pytest.mark.anyio
async def test_same_byte_source_replacement_during_planning_blocks_promotion(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    output_parent.mkdir()
    payload = source / "docs" / "brief.txt"
    payload_before = payload.read_bytes()

    with pytest.raises(
        FolderTransactionError,
        match="Source folder changed after planning",
    ):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=_ReplacingPlanner(payload),
        )

    assert payload.read_bytes() == payload_before
    assert not any(output_parent.iterdir())


@pytest.mark.anyio
async def test_directory_replacement_during_planning_blocks_promotion(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    output_parent.mkdir()
    source_before_files = _file_bytes(source)

    with pytest.raises(
        FolderTransactionError,
        match="Source folder changed after planning",
    ):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=_ReplacingDirectoryPlanner(source / "docs"),
        )

    assert _file_bytes(source) == source_before_files
    assert not any(output_parent.iterdir())


@pytest.mark.anyio
async def test_source_replacement_between_rescan_and_copy_blocks_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    output_parent.mkdir()
    real_copy = transaction_module._copy_verified_file
    race_was_injected = False

    def replace_then_copy(**kwargs: object) -> tuple[int, str]:
        nonlocal race_was_injected
        source_path = kwargs["source"]
        assert isinstance(source_path, Path)
        if not race_was_injected:
            race_was_injected = True
            replacement = source_path.with_name(".copy-race.tmp")
            replacement.write_bytes(source_path.read_bytes())
            os.replace(replacement, source_path)
        return real_copy(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        transaction_module,
        "_copy_verified_file",
        replace_then_copy,
    )

    with pytest.raises(
        FolderTransactionError,
        match="Source member was replaced or changed",
    ):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=_planner(),
        )

    assert race_was_injected is True
    assert not (output_parent / RESULT_NAME).exists()
    _assert_no_pending_result(output_parent)


@pytest.mark.anyio
async def test_preexisting_final_destination_is_never_overwritten(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    final_root = output_parent / RESULT_NAME
    final_root.mkdir(parents=True)
    marker = final_root / "belongs-to-someone-else.txt"
    marker.write_bytes(b"preserve me")
    source_before = _source_state(source)

    with pytest.raises(FolderTransactionError, match="Final result already exists"):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=_planner(),
        )

    assert marker.read_bytes() == b"preserve me"
    assert _source_state(source) == source_before
    _assert_no_pending_result(output_parent)


@pytest.mark.anyio
async def test_competing_destination_at_promotion_is_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path / "source")
    output_parent = tmp_path / "results"
    output_parent.mkdir()
    real_promote = transaction_module.promote_directory_no_replace

    def inject_competing_destination(pending: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "competitor.txt").write_bytes(b"do not replace")
        real_promote(pending, destination)

    monkeypatch.setattr(
        transaction_module,
        "promote_directory_no_replace",
        inject_competing_destination,
    )

    with pytest.raises(FolderTransactionError):
        await run_folder_refactor(
            source_root=source,
            output_parent=output_parent,
            request=REQUEST,
            planner=_planner(),
        )

    final_root = output_parent / RESULT_NAME
    assert _file_bytes(final_root) == {"competitor.txt": b"do not replace"}
    _assert_no_pending_result(output_parent)
