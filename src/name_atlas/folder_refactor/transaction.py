"""Copy-only A1 walking transaction for accepted generic-folder plans."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import stat
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from name_atlas.folder_refactor.compiler import PlanCompilationError, compile_plan
from name_atlas.folder_refactor.contracts import (
    FolderAcceptedPlan,
    FolderVerificationCheck,
    FolderVerificationReport,
    PlanOutcome,
)
from name_atlas.folder_refactor.inventory import (
    HASH_CHUNK_SIZE,
    FolderScan,
    FolderScanError,
    LocalFileIdentity,
    inventory_evidence_ids,
    scan_folder,
)
from name_atlas.folder_refactor.planner import (
    FolderPlanner,
    initial_evidence_fingerprint,
)
from name_atlas.folder_refactor.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    request_fingerprint,
)
from name_atlas.ports import PackageValidator
from name_atlas.verification.bag_writer import BagItWriter, BagItWriteResult
from name_atlas.verification.bagit_validator import BagItPackageValidator
from name_atlas.verification.promotion import promote_directory_no_replace

SOURCE_SNAPSHOT_PATH = Path("name-atlas/source_snapshot.json")
USER_REQUEST_PATH = Path("name-atlas/user_request.json")
ACCEPTED_PLAN_PATH = Path("name-atlas/accepted_plan.json")
VERIFICATION_REPORT_PATH = Path("name-atlas/verification_report.json")
MINIMUM_FREE_MARGIN_BYTES = 256 * 1024 * 1024


class FolderTransactionError(RuntimeError):
    """The folder transaction cannot safely produce an accepted result."""


class FolderBagWriter(Protocol):
    """Create and refresh the deterministic BagIt container boundary."""

    def write(self, pending_root: Path) -> BagItWriteResult:
        """Create initial BagIt metadata and manifests."""
        ...

    def refresh_tagmanifest(self, pending_root: Path) -> BagItWriteResult:
        """Refresh the tag manifest after final report replacement."""
        ...


@dataclass(frozen=True, slots=True)
class FolderRunResult:
    """Local pointers and portable proof for one completed walking transaction."""

    result_root: Path
    data_root: Path
    accepted_plan: FolderAcceptedPlan
    report: FolderVerificationReport


async def run_folder_refactor(
    *,
    source_root: Path,
    output_parent: Path,
    request: str,
    planner: FolderPlanner,
    bag_writer: FolderBagWriter | None = None,
    package_validator: PackageValidator | None = None,
) -> FolderRunResult:
    """Plan, compile, copy, prove, and promote one generic folder result."""

    try:
        request_fingerprint(request)
        initial_scan = scan_folder(source_root)
        resolved_output_parent = _preflight_output_parent(
            source_root=initial_scan.source_root,
            output_parent=output_parent,
            source_bytes=initial_scan.inventory.total_bytes,
            rewritten_markdown_original_bytes=0,
        )
        evidence_fingerprint = initial_evidence_fingerprint(initial_scan.inventory)
        outcome = await planner.plan(
            request=request,
            inventory=initial_scan.inventory,
            evidence_fingerprint=evidence_fingerprint,
        )
        if not isinstance(outcome, PlanOutcome):
            raise FolderTransactionError(
                "A1 requires a complete plan outcome; clarification and blocking "
                "are implemented in A2."
            )
        post_plan_scan = scan_folder(initial_scan.source_root)
        _require_same_source(initial_scan, post_plan_scan, "after planning")
        accepted_plan = compile_plan(
            initial_scan.inventory,
            request,
            outcome.plan,
            known_evidence_ids=inventory_evidence_ids(initial_scan.inventory),
            evidence_fingerprint=evidence_fingerprint,
        )
        selected_bag_writer = BagItWriter() if bag_writer is None else bag_writer
        selected_validator = (
            BagItPackageValidator() if package_validator is None else package_validator
        )
        return _execute_accepted_plan(
            initial_scan=initial_scan,
            output_parent=resolved_output_parent,
            request=request,
            accepted_plan=accepted_plan,
            bag_writer=selected_bag_writer,
            package_validator=selected_validator,
        )
    except (FolderScanError, PlanCompilationError, ValueError) as exc:
        raise FolderTransactionError(str(exc)) from exc


def required_free_bytes(
    *,
    source_bytes: int,
    rewritten_markdown_original_bytes: int,
) -> int:
    """Return the exact deterministic capacity requirement."""

    if source_bytes < 0 or rewritten_markdown_original_bytes < 0:
        raise ValueError("Capacity inputs cannot be negative.")
    margin = max(MINIMUM_FREE_MARGIN_BYTES, math.ceil(source_bytes * 0.10))
    return source_bytes + rewritten_markdown_original_bytes + margin


def _preflight_output_parent(
    *,
    source_root: Path,
    output_parent: Path,
    source_bytes: int,
    rewritten_markdown_original_bytes: int,
) -> Path:
    if not isinstance(output_parent, Path):
        raise FolderTransactionError("Result location must be a pathlib.Path.")
    try:
        metadata = output_parent.lstat()
    except OSError as exc:
        raise FolderTransactionError(
            f"Result location must be an existing directory: {output_parent}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise FolderTransactionError(
            f"Result location must be a non-symlink directory: {output_parent}"
        )
    try:
        resolved_output = output_parent.resolve(strict=True)
    except OSError as exc:
        raise FolderTransactionError(
            f"Result location cannot be resolved: {output_parent}"
        ) from exc
    if _contains(source_root, resolved_output) or _contains(
        resolved_output,
        source_root,
    ):
        raise FolderTransactionError(
            "Source folder and result location cannot contain one another."
        )
    if not os.access(resolved_output, os.W_OK | os.X_OK):
        raise FolderTransactionError(
            f"Result location is not writable: {output_parent}"
        )
    required = required_free_bytes(
        source_bytes=source_bytes,
        rewritten_markdown_original_bytes=rewritten_markdown_original_bytes,
    )
    available = shutil.disk_usage(resolved_output).free
    if available < required:
        raise FolderTransactionError(
            "Insufficient free space: "
            f"required {required} bytes; available {available}."
        )
    return resolved_output


def _execute_accepted_plan(
    *,
    initial_scan: FolderScan,
    output_parent: Path,
    request: str,
    accepted_plan: FolderAcceptedPlan,
    bag_writer: FolderBagWriter,
    package_validator: PackageValidator,
) -> FolderRunResult:
    final_root = output_parent / accepted_plan.result_folder_name
    if os.path.lexists(final_root):
        raise FolderTransactionError(f"Final result already exists: {final_root}")
    pending_root = output_parent / (
        f".{accepted_plan.result_folder_name}.pending-{uuid.uuid4().hex}"
    )
    if os.path.lexists(pending_root):
        raise FolderTransactionError(f"Pending result already exists: {pending_root}")

    by_file_id = {item.file_id: item for item in initial_scan.inventory.files}
    identity_by_path = {
        item.relative_path: item for item in initial_scan.local_file_identities
    }
    try:
        data_root = pending_root / "data"
        proof_root = pending_root / "name-atlas"
        data_root.mkdir(parents=True, exist_ok=False)
        proof_root.mkdir(parents=True, exist_ok=False)
        for mapping in accepted_plan.file_mappings:
            source_file = by_file_id[mapping.file_id]
            source_identity = identity_by_path[source_file.relative_path]
            destination = data_root / mapping.target_path
            _ensure_directory_chain(data_root, destination.parent)
            _copy_verified_file(
                source=initial_scan.source_root / source_file.relative_path,
                destination=destination,
                expected=source_identity,
                expected_digest=source_file.sha256,
            )
        for relative_directory in accepted_plan.empty_directories:
            (data_root / relative_directory).mkdir(parents=True, exist_ok=False)

        _write_portable_json(SOURCE_SNAPSHOT_PATH, initial_scan.inventory, pending_root)
        _write_portable_json(
            USER_REQUEST_PATH,
            {
                "schema_version": "folder-user-request.v1",
                "request": request,
                "request_fingerprint": request_fingerprint(request),
            },
            pending_root,
        )
        _write_portable_json(ACCEPTED_PLAN_PATH, accepted_plan, pending_root)

        copied_records = _verify_staged_payloads(
            data_root=data_root,
            initial_scan=initial_scan,
            accepted_plan=accepted_plan,
        )
        staged_source_scan = scan_folder(initial_scan.source_root)
        _require_same_source(initial_scan, staged_source_scan, "during staging")
        staged_data_commitment = canonical_sha256(copied_records)
        path_change_count = sum(
            mapping.original_path != mapping.target_path
            for mapping in accepted_plan.file_mappings
        )
        provisional_report = _build_report(
            initial_scan=initial_scan,
            accepted_plan=accepted_plan,
            staged_data_commitment=staged_data_commitment,
            path_change_count=path_change_count,
            data_root=data_root,
            bagit_validated=False,
        )
        _write_portable_json(
            VERIFICATION_REPORT_PATH,
            provisional_report,
            pending_root,
        )
        bag_writer.write(pending_root)
        initial_package_result = package_validator.validate(pending_root)
        if not initial_package_result.valid:
            raise FolderTransactionError(
                "Initial BagIt validation blocked the result: "
                + "; ".join(initial_package_result.messages)
            )

        report = _build_report(
            initial_scan=initial_scan,
            accepted_plan=accepted_plan,
            staged_data_commitment=staged_data_commitment,
            path_change_count=path_change_count,
            data_root=data_root,
            bagit_validated=True,
        )
        _replace_portable_json(VERIFICATION_REPORT_PATH, report, pending_root)
        bag_writer.refresh_tagmanifest(pending_root)
        final_package_result = package_validator.validate(pending_root)
        if not final_package_result.valid:
            raise FolderTransactionError(
                "Final BagIt validation blocked the result: "
                + "; ".join(final_package_result.messages)
            )

        final_source_scan = scan_folder(initial_scan.source_root)
        _require_same_source(initial_scan, final_source_scan, "before promotion")
        promote_directory_no_replace(pending_root, final_root)
        return FolderRunResult(
            result_root=final_root,
            data_root=final_root / "data",
            accepted_plan=accepted_plan,
            report=report,
        )
    except Exception as exc:
        if pending_root.exists():
            shutil.rmtree(pending_root)
        if isinstance(exc, FolderTransactionError):
            raise
        raise FolderTransactionError(f"Copy transaction blocked: {exc}") from exc


def _build_report(
    *,
    initial_scan: FolderScan,
    accepted_plan: FolderAcceptedPlan,
    staged_data_commitment: str,
    path_change_count: int,
    data_root: Path,
    bagit_validated: bool,
) -> FolderVerificationReport:
    checks = [
        FolderVerificationCheck(
            check_id="source_unchanged",
            passed=True,
            detail="The source commitment and local file identities are unchanged.",
        ),
        FolderVerificationCheck(
            check_id="complete_file_bijection",
            passed=len(accepted_plan.file_mappings)
            == len(initial_scan.inventory.files),
            detail="Every source file has exactly one accepted result path.",
        ),
        FolderVerificationCheck(
            check_id="payload_hashes_preserved",
            passed=True,
            detail="Every staged payload size and SHA-256 matches its source.",
        ),
        FolderVerificationCheck(
            check_id="protected_paths_preserved",
            passed=all(
                not mapping.protected or mapping.original_path == mapping.target_path
                for mapping in accepted_plan.file_mappings
            ),
            detail="Every protected file remains at its original relative path.",
        ),
        FolderVerificationCheck(
            check_id="empty_directories_preserved",
            passed=all(
                (data_root / path).is_dir() for path in accepted_plan.empty_directories
            ),
            detail="Every explicit empty directory remains at its original path.",
        ),
        FolderVerificationCheck(
            check_id="result_is_separate",
            passed=not _contains(initial_scan.source_root, data_root)
            and not _contains(data_root, initial_scan.source_root),
            detail="The verified result is outside the source tree.",
        ),
    ]
    if bagit_validated:
        checks.append(
            FolderVerificationCheck(
                check_id="bagit_validation",
                passed=True,
                detail="The portable result passed the independent BagIt validator.",
            )
        )
    return FolderVerificationReport(
        source_commitment=initial_scan.inventory.source_commitment,
        request_fingerprint=accepted_plan.request_fingerprint,
        accepted_plan_fingerprint=canonical_sha256(accepted_plan),
        result_folder_name=accepted_plan.result_folder_name,
        staged_data_commitment=staged_data_commitment,
        file_count=len(accepted_plan.file_mappings),
        path_change_count=path_change_count,
        protected_file_count=sum(
            mapping.protected for mapping in accepted_plan.file_mappings
        ),
        empty_directory_count=len(accepted_plan.empty_directories),
        checks=tuple(checks),
    )


def _verify_staged_payloads(
    *,
    data_root: Path,
    initial_scan: FolderScan,
    accepted_plan: FolderAcceptedPlan,
) -> list[dict[str, str | int]]:
    source_by_id = {item.file_id: item for item in initial_scan.inventory.files}
    expected_by_target = {
        mapping.target_path: source_by_id[mapping.file_id]
        for mapping in accepted_plan.file_mappings
    }
    records: list[dict[str, str | int]] = []
    seen: set[str] = set()

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FolderTransactionError(
                "Staged data directory cannot be enumerated."
            ) from exc
        for entry in entries:
            candidate = Path(entry.path)
            relative_path = candidate.relative_to(data_root).as_posix()
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FolderTransactionError(
                    f"Staged member cannot be inspected: {relative_path}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise FolderTransactionError(
                    f"Staged result contains a symlink: {relative_path}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                visit(candidate)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise FolderTransactionError(
                    f"Staged result contains a special file: {relative_path}"
                )
            expected = expected_by_target.get(relative_path)
            if expected is None:
                raise FolderTransactionError(
                    f"Staged result contains an unexpected payload: {relative_path}"
                )
            if relative_path in seen:
                raise FolderTransactionError(
                    f"Staged result contains a duplicate payload: {relative_path}"
                )
            size, digest = _hash_staged_file(candidate, relative_path)
            if size != expected.size or digest != expected.sha256:
                raise FolderTransactionError(
                    f"Staged payload does not match source: {relative_path}"
                )
            seen.add(relative_path)
            records.append({"path": relative_path, "size": size, "sha256": digest})

    visit(data_root)
    missing = sorted(set(expected_by_target) - seen)
    if missing:
        raise FolderTransactionError(
            f"Staged result is missing accepted payloads: {missing!r}"
        )
    for relative_directory in accepted_plan.empty_directories:
        directory = data_root / relative_directory
        try:
            metadata = directory.lstat()
            with os.scandir(directory) as entries:
                has_member = next(entries, None) is not None
        except OSError as exc:
            raise FolderTransactionError(
                f"Explicit empty directory is missing: {relative_directory}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise FolderTransactionError(
                f"Explicit empty directory is invalid: {relative_directory}"
            )
        if has_member:
            raise FolderTransactionError(
                f"Explicit empty directory is not empty: {relative_directory}"
            )
    return sorted(records, key=lambda item: str(item["path"]))


def _hash_staged_file(path: Path, relative_path: str) -> tuple[int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FolderTransactionError(
            f"Staged payload cannot be opened: {relative_path}"
        ) from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FolderTransactionError(
                f"Staged payload is not a regular file: {relative_path}"
            )
        size = 0
        while chunk := os.read(descriptor, HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_identity != after_identity or size != after.st_size:
            raise FolderTransactionError(
                f"Staged payload changed while being verified: {relative_path}"
            )
    finally:
        os.close(descriptor)
    return size, digest.hexdigest()


def _ensure_directory_chain(root: Path, destination_parent: Path) -> None:
    try:
        relative = destination_parent.relative_to(root)
    except ValueError as exc:
        raise FolderTransactionError("Result target escapes data directory.") from exc
    current = root
    for component in relative.parts:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except OSError as exc:
                raise FolderTransactionError(
                    f"Result directory cannot be created: {relative.as_posix()}"
                ) from exc
            metadata = current.lstat()
        except OSError as exc:
            raise FolderTransactionError(
                f"Result directory cannot be inspected: {relative.as_posix()}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise FolderTransactionError(
                f"Result target parent is not a real directory: {relative.as_posix()}"
            )


def _copy_verified_file(
    *,
    source: Path,
    destination: Path,
    expected: LocalFileIdentity,
    expected_digest: str,
) -> tuple[int, str]:
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        destination_flags |= os.O_NOFOLLOW
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as exc:
        raise FolderTransactionError(
            f"Source file cannot be opened for copying: {expected.relative_path}"
        ) from exc
    try:
        before = os.fstat(source_descriptor)
        _require_expected_identity(before, expected)
        try:
            destination_descriptor = os.open(destination, destination_flags, 0o644)
        except OSError as exc:
            raise FolderTransactionError(
                f"Result file cannot be created exclusively: {destination}"
            ) from exc
        digest = hashlib.sha256()
        copied_size = 0
        try:
            while chunk := os.read(source_descriptor, HASH_CHUNK_SIZE):
                digest.update(chunk)
                copied_size += len(chunk)
                _write_all(destination_descriptor, chunk)
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        after = os.fstat(source_descriptor)
        _require_expected_identity(after, expected)
    finally:
        os.close(source_descriptor)
    copied_digest = digest.hexdigest()
    if copied_size != expected.size or copied_digest != expected_digest:
        raise FolderTransactionError(
            f"Copied payload does not match source: {expected.relative_path}"
        )
    return copied_size, copied_digest


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("Result write made no progress.")
        view = view[written:]


def _require_expected_identity(
    metadata: os.stat_result,
    expected: LocalFileIdentity,
) -> None:
    actual = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    wanted = (
        expected.device,
        expected.inode,
        expected.size,
        expected.modified_ns,
    )
    if actual != wanted or metadata.st_nlink > 1 or not stat.S_ISREG(metadata.st_mode):
        raise FolderTransactionError(
            f"Source member was replaced or changed: {expected.relative_path}"
        )


def _require_same_source(
    initial: FolderScan,
    current: FolderScan,
    boundary: str,
) -> None:
    if (
        initial.inventory.source_commitment != current.inventory.source_commitment
        or initial.local_file_identities != current.local_file_identities
        or initial.local_directory_identities != current.local_directory_identities
    ):
        raise FolderTransactionError(f"Source folder changed {boundary}.")


def _write_portable_json(relative_path: Path, value: object, root: Path) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise FolderTransactionError(
            f"Portable proof artifact cannot be written: {relative_path.as_posix()}"
        ) from exc


def _replace_portable_json(relative_path: Path, value: object, root: Path) -> None:
    path = root / relative_path
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise FolderTransactionError(
                "Portable proof artifact is not replaceable: "
                f"{relative_path.as_posix()}"
            )
        payload = canonical_json_bytes(value)
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except FolderTransactionError:
        raise
    except OSError as exc:
        raise FolderTransactionError(
            f"Portable proof artifact cannot be finalized: {relative_path.as_posix()}"
        ) from exc
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
