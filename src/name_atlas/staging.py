"""Copy-only pending staging transaction and deterministic integrity proof."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
import unicodedata
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from name_atlas.artifacts import (
    ControlFileProof,
    PathMapRow,
    ProofStatus,
    StageArtifacts,
    VerificationCheck,
    VerificationReport,
    replace_verification_report,
    write_decision_ledger,
    write_path_map,
    write_source_snapshot,
    write_summary,
    write_verification_report,
)
from name_atlas.decisions import HumanDecision
from name_atlas.domain import ContentRole, PackageValidationResult
from name_atlas.package_import import ObjectFamily, SourcePackage, import_package
from name_atlas.proposals import DESCRIPTOR_PATTERN, EXTENSION_PATTERN
from name_atlas.source import (
    HASH_CHUNK_SIZE,
    SourceMember,
    snapshot_tree,
    validate_relative_path,
)
from name_atlas.verification.bag_writer import BagItWriter

oslo_tz = ZoneInfo("Europe/Oslo")
VERIFIED_CLAIM = "Verified round-trip integrity within the supported package contract"


class _PackageValidator(Protocol):
    def validate(self, bag_root: Path) -> PackageValidationResult:
        """Validate a completed pending package without mutating it."""


class StagingError(RuntimeError):
    """The package could not be exposed as an exportable final stage."""


class StageResult(BaseModel):
    """Completed exportable stage and its exact proof artifact model."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    stage_root: Path
    artifacts: StageArtifacts


def stage_package(
    package: SourcePackage,
    decisions: tuple[HumanDecision, ...],
    *,
    output_root: Path,
    package_validator: _PackageValidator,
) -> StageResult:
    """Create, verify, and promote one new copy-only BagIt stage."""

    decision_by_family = _validate_decisions(package, decisions)
    prestaging = import_package(package.root)
    if prestaging.snapshot != package.snapshot:
        raise StagingError("Source package changed after the initial snapshot.")

    output_candidate = output_root.resolve(strict=False)
    if output_candidate == package.root or package.root in output_candidate.parents:
        raise StagingError("Stage output must be outside the immutable source package.")
    output_root.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(oslo_tz)
    final_root, pending_root = _allocate_stage_paths(
        output_root,
        generated_at=generated_at,
        snapshot_commitment=package.snapshot.commitment,
    )
    (pending_root / "data").mkdir(parents=True)
    (pending_root / "name-atlas").mkdir()

    map_rows: list[PathMapRow] = []
    try:
        for family in package.families:
            decision = decision_by_family[family.family_id]
            for member in family.members:
                target = decision.resolved_targets[member.role]
                _copy_content_member(
                    package.root / member.relative_path,
                    pending_root / "data" / target,
                    member,
                )
                map_rows.append(
                    PathMapRow(
                        family_id=family.family_id,
                        canonical_identifier=family.canonical_identifier,
                        role=member.role,
                        source_path=member.relative_path,
                        target_path=target,
                        size=member.size,
                        sha256=member.sha256,
                    )
                )

        control_proofs = _write_control_files(
            package,
            decision_by_family,
            pending_root=pending_root,
        )
        postcopy = snapshot_tree(package.root)
        if postcopy != package.snapshot:
            raise StagingError("Source package changed during copy-only staging.")

        ordered_maps = tuple(
            sorted(map_rows, key=lambda row: (row.family_id, row.role.value))
        )
        checks = _deterministic_checks(
            package,
            ordered_maps,
            decision_by_family,
            pending_root=pending_root,
        )
        if not all(check.passed for check in checks):
            raise StagingError("One or more deterministic proof checks failed.")

        proof_root = pending_root / "name-atlas"
        artifact_paths = (
            "name-atlas/source_snapshot.json",
            "name-atlas/decision_ledger.json",
            "name-atlas/forward_path_map.csv",
            "name-atlas/reverse_path_map.csv",
            "name-atlas/verification_report.json",
            "name-atlas/verification_summary.md",
            "bagit.txt",
            "bag-info.txt",
            "manifest-sha256.txt",
            "tagmanifest-sha256.txt",
        )
        write_source_snapshot(proof_root / "source_snapshot.json", package.snapshot)
        ordered_decisions = tuple(
            decision_by_family[family.family_id] for family in package.families
        )
        write_decision_ledger(proof_root / "decision_ledger.json", ordered_decisions)
        write_path_map(proof_root / "forward_path_map.csv", ordered_maps, reverse=False)
        write_path_map(proof_root / "reverse_path_map.csv", ordered_maps, reverse=True)
        write_summary(
            proof_root / "verification_summary.md",
            content_objects=len(ordered_maps),
            content_bytes=sum(row.size for row in ordered_maps),
        )
        provisional_validation = PackageValidationResult(
            validator="bagit",
            valid=False,
            messages=("BagIt validation has not run yet.",),
        )
        report = _report(
            status=ProofStatus.BLOCKED,
            claim=None,
            generated_at=generated_at,
            final_root=final_root,
            package=package,
            postcopy_commitment=postcopy.commitment,
            maps=ordered_maps,
            control_proofs=control_proofs,
            checks=checks,
            bagit_validation=provisional_validation,
            artifact_paths=artifact_paths,
            blockers=("BagIt validation has not run yet.",),
        )
        report_path = proof_root / "verification_report.json"
        write_verification_report(report_path, report)

        writer = BagItWriter()
        writer.write(pending_root, bagging_date=generated_at.date())
        first_validation = package_validator.validate(pending_root)
        final_status = (
            ProofStatus.VERIFIED if first_validation.valid else ProofStatus.BLOCKED
        )
        report = _report(
            status=final_status,
            claim=VERIFIED_CLAIM if first_validation.valid else None,
            generated_at=generated_at,
            final_root=final_root,
            package=package,
            postcopy_commitment=postcopy.commitment,
            maps=ordered_maps,
            control_proofs=control_proofs,
            checks=checks,
            bagit_validation=first_validation,
            artifact_paths=artifact_paths,
            blockers=() if first_validation.valid else first_validation.messages,
        )
        replace_verification_report(report_path, report)
        writer.refresh_tagmanifest(pending_root)
        final_validation = package_validator.validate(pending_root)
        if first_validation.valid and not final_validation.valid:
            report = _report(
                status=ProofStatus.BLOCKED,
                claim=None,
                generated_at=generated_at,
                final_root=final_root,
                package=package,
                postcopy_commitment=postcopy.commitment,
                maps=ordered_maps,
                control_proofs=control_proofs,
                checks=checks,
                bagit_validation=final_validation,
                artifact_paths=artifact_paths,
                blockers=final_validation.messages,
            )
            replace_verification_report(report_path, report)
            writer.refresh_tagmanifest(pending_root)
        if not first_validation.valid or not final_validation.valid:
            raise StagingError(
                f"BagIt validation failed; preserved pending stage at {pending_root}."
            )
        if snapshot_tree(package.root) != package.snapshot:
            raise StagingError("Source package changed before final promotion.")
        if final_root.exists():
            raise StagingError("Final stage path appeared before promotion.")
        os.rename(pending_root, final_root)
    except Exception:
        raise

    artifacts = StageArtifacts(
        forward_map=ordered_maps,
        reverse_map=ordered_maps,
        report=report,
    )
    return StageResult(stage_root=final_root, artifacts=artifacts)


def _validate_decisions(
    package: SourcePackage,
    decisions: tuple[HumanDecision, ...],
) -> dict[str, HumanDecision]:
    decision_by_family = {decision.family_id: decision for decision in decisions}
    if len(decision_by_family) != len(decisions):
        raise StagingError("A family has duplicate decision records.")
    expected_ids = {family.family_id for family in package.families}
    if set(decision_by_family) != expected_ids:
        raise StagingError("Every family must have exactly one decision record.")
    for family in package.families:
        decision = decision_by_family[family.family_id]
        expected_roles = {member.role for member in family.members}
        if (
            not decision.export_ready
            or set(decision.resolved_targets) != expected_roles
        ):
            raise StagingError(
                f"Family {family.family_id} has no complete resolved target map."
            )
        for member in family.members:
            _validate_resolved_target(
                family,
                member,
                decision.resolved_targets[member.role],
            )
    _require_globally_unique_targets(
        tuple(
            target
            for decision in decisions
            for target in decision.resolved_targets.values()
        )
    )
    return decision_by_family


def _validate_resolved_target(
    family: ObjectFamily,
    member: SourceMember,
    target: str,
) -> None:
    try:
        validate_relative_path(target)
    except ValueError as exc:
        raise StagingError(
            f"Resolved target is not a safe relative path: {target!r}"
        ) from exc
    path = PurePosixPath(target)
    expected_directory = {
        ContentRole.ORIGINAL: "objects",
        ContentRole.ACCESS: "manualNormalization/access",
        ContentRole.PRESERVATION: "manualNormalization/preservation",
    }[member.role]
    if path.parent.as_posix() != expected_directory:
        raise StagingError(
            f"Resolved {member.role.value} target uses the wrong directory: {target}"
        )
    source_extension = PurePosixPath(member.relative_path).suffix.lower()
    if (
        path.suffix != source_extension
        or EXTENSION_PATTERN.fullmatch(path.suffix) is None
    ):
        raise StagingError(
            f"Resolved target does not retain the lowercased final extension: {target}"
        )
    stem = path.name[: -len(path.suffix)]
    try:
        identifier, descriptor, role = stem.split("__")
    except ValueError as exc:
        raise StagingError(
            f"Resolved target does not match the fixed profile: {target}"
        ) from exc
    if (
        identifier != family.canonical_identifier
        or DESCRIPTOR_PATTERN.fullmatch(descriptor) is None
        or role != member.role.value
    ):
        raise StagingError(
            f"Resolved target does not match the fixed profile: {target}"
        )


def _allocate_stage_paths(
    output_root: Path,
    *,
    generated_at: datetime,
    snapshot_commitment: str,
) -> tuple[Path, Path]:
    prefix = generated_at.strftime("name-atlas-%Y%m%dT%H%M%S%z")
    base = f"{prefix}-{snapshot_commitment[:8]}"
    for counter in range(1_000):
        suffix = "" if counter == 0 else f"-{counter}"
        final_root = output_root / f"{base}{suffix}"
        pending_root = output_root / f".{base}{suffix}.pending"
        if not final_root.exists() and not pending_root.exists():
            pending_root.mkdir()
            return final_root, pending_root
    raise StagingError("No unused stage path is available.")


def _copy_content_member(source: Path, destination: Path, member: SourceMember) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    digest = hashlib.sha256()
    size = 0
    try:
        source_descriptor = os.open(source, source_flags)
        source_stat = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise StagingError(f"Source is not a regular file: {member.relative_path}")
        destination_descriptor = os.open(destination, destination_flags, 0o600)
        while chunk := os.read(source_descriptor, HASH_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                view = view[written:]
        os.fsync(destination_descriptor)
    except OSError as exc:
        raise StagingError(f"Copy failed for {member.relative_path}.") from exc
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)
    if size != member.size or digest.hexdigest() != member.sha256:
        raise StagingError(
            f"Copied payload does not match source: {member.relative_path}"
        )


def _write_control_files(
    package: SourcePackage,
    decision_by_family: dict[str, HumanDecision],
    *,
    pending_root: Path,
) -> tuple[ControlFileProof, ...]:
    family_by_row = {
        family.metadata_row.row_number: family for family in package.families
    }
    metadata_rows = []
    for row in package.metadata_rows:
        values = list(row.values)
        family = family_by_row[row.row_number]
        values[0] = decision_by_family[family.family_id].resolved_targets[
            ContentRole.ORIGINAL
        ]
        metadata_rows.append(tuple(values))
    metadata_bytes = _render_csv((package.metadata_header, *metadata_rows))
    metadata_target = pending_root / "data" / "metadata" / "metadata.csv"
    _write_new_bytes(metadata_target, metadata_bytes)
    metadata_source = _member_by_path(package, "metadata/metadata.csv")
    proofs = [
        ControlFileProof(
            logical_path="metadata/metadata.csv",
            source_sha256=metadata_source.sha256,
            staged_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
            rewritten_fields=tuple(
                f"row:{row.row_number}:filename" for row in package.metadata_rows
            ),
            non_path_fields_unchanged=True,
        )
    ]

    if package.normalization_present:
        family_by_normalization_row = {
            family.normalization_row_number: family
            for family in package.families
            if family.normalization_row_number is not None
        }
        normalization_values = []
        rewritten_fields = []
        for row in package.normalization_rows:
            family = family_by_normalization_row[row.row_number]
            targets = decision_by_family[family.family_id].resolved_targets
            normalization_values.append(
                (
                    targets[ContentRole.ORIGINAL],
                    targets.get(ContentRole.ACCESS, ""),
                    targets.get(ContentRole.PRESERVATION, ""),
                )
            )
            rewritten_fields.extend(
                (
                    f"row:{row.row_number}:original",
                    f"row:{row.row_number}:access",
                    f"row:{row.row_number}:preservation",
                )
            )
        normalization_bytes = _render_csv(tuple(normalization_values))
        _write_new_bytes(
            pending_root / "data" / "normalization.csv", normalization_bytes
        )
        normalization_source = _member_by_path(package, "normalization.csv")
        proofs.append(
            ControlFileProof(
                logical_path="normalization.csv",
                source_sha256=normalization_source.sha256,
                staged_sha256=hashlib.sha256(normalization_bytes).hexdigest(),
                rewritten_fields=tuple(rewritten_fields),
                non_path_fields_unchanged=True,
            )
        )
    return tuple(proofs)


def _render_csv(rows: tuple[tuple[str, ...], ...]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue().encode()


def _write_new_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _member_by_path(package: SourcePackage, path: str) -> SourceMember:
    return next(
        member for member in package.snapshot.members if member.relative_path == path
    )


def _deterministic_checks(
    package: SourcePackage,
    maps: tuple[PathMapRow, ...],
    decisions: dict[str, HumanDecision],
    *,
    pending_root: Path,
) -> tuple[VerificationCheck, ...]:
    staged_hashes_match = all(
        _stream_sha256(pending_root / "data" / row.target_path) == row.sha256
        for row in maps
    )
    target_paths = tuple(row.target_path for row in maps)
    reverse_round_trip = (
        len(maps) == len(package.content_members)
        and len({row.source_path for row in maps}) == len(maps)
        and len(set(target_paths)) == len(maps)
    )
    references_resolve = all(
        target in set(target_paths)
        for decision in decisions.values()
        for target in decision.resolved_targets.values()
    )
    profile_valid = all(_target_profile_valid(row) for row in maps)
    return (
        VerificationCheck(
            check_id="source_snapshot_equal",
            label="Source snapshot unchanged before staging",
            passed=True,
            detail=package.snapshot.commitment,
        ),
        VerificationCheck(
            check_id="payload_hashes_equal",
            label="Every staged content-object hash equals its source",
            passed=staged_hashes_match,
            detail=f"{len(maps)} content objects compared by SHA-256.",
        ),
        VerificationCheck(
            check_id="declared_references_resolve",
            label="Every rewritten declared reference resolves",
            passed=references_resolve,
            detail="Metadata and normalization targets use one stored family map.",
        ),
        VerificationCheck(
            check_id="target_profile_valid",
            label="Every target satisfies the repository-ready profile",
            passed=profile_valid,
            detail="Identifier, descriptor, role, directory, and extension checked.",
        ),
        VerificationCheck(
            check_id="forward_reverse_inverse",
            label="Forward and reverse logical maps are complete inverses",
            passed=reverse_round_trip,
            detail=f"{len(maps)} source and target logical paths round-trip.",
        ),
        VerificationCheck(
            check_id="target_uniqueness",
            label="Targets are unique under exact, NFC, and casefold comparison",
            passed=_targets_are_unique(target_paths),
            detail="Three independent target comparison sets evaluated.",
        ),
    )


def _target_profile_valid(row: PathMapRow) -> bool:
    expected_directory = {
        ContentRole.ORIGINAL: "objects",
        ContentRole.ACCESS: "manualNormalization/access",
        ContentRole.PRESERVATION: "manualNormalization/preservation",
    }[row.role]
    path = Path(row.target_path)
    if path.parent.as_posix() != expected_directory:
        return False
    extension = path.suffix
    if EXTENSION_PATTERN.fullmatch(extension) is None:
        return False
    stem = path.name[: -len(extension)]
    try:
        identifier, descriptor, role = stem.split("__")
    except ValueError:
        return False
    return (
        identifier == row.canonical_identifier
        and DESCRIPTOR_PATTERN.fullmatch(descriptor) is not None
        and role == row.role.value
    )


def _targets_are_unique(targets: tuple[str, ...]) -> bool:
    comparisons = (
        targets,
        tuple(unicodedata.normalize("NFC", target) for target in targets),
        tuple(unicodedata.normalize("NFC", target).casefold() for target in targets),
    )
    return all(len(values) == len(set(values)) for values in comparisons)


def _require_globally_unique_targets(targets: tuple[str, ...]) -> None:
    if not _targets_are_unique(targets):
        raise StagingError("Resolved targets collide under exact, NFC, or casefold.")


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report(
    *,
    status: ProofStatus,
    claim: str | None,
    generated_at: datetime,
    final_root: Path,
    package: SourcePackage,
    postcopy_commitment: str,
    maps: tuple[PathMapRow, ...],
    control_proofs: tuple[ControlFileProof, ...],
    checks: tuple[VerificationCheck, ...],
    bagit_validation: PackageValidationResult,
    artifact_paths: tuple[str, ...],
    blockers: tuple[str, ...],
) -> VerificationReport:
    return VerificationReport(
        status=status,
        claim=claim,
        generated_at=generated_at,
        staged_location=str(final_root),
        source_snapshot_commitment=package.snapshot.commitment,
        prestaging_snapshot_commitment=package.snapshot.commitment,
        postcopy_snapshot_commitment=postcopy_commitment,
        source_unchanged=postcopy_commitment == package.snapshot.commitment,
        content_object_count=len(maps),
        content_bytes=sum(row.size for row in maps),
        control_files=control_proofs,
        map_row_count=len(maps),
        checks=checks,
        bagit_validation=bagit_validation,
        artifact_paths=artifact_paths,
        blockers=blockers,
    )
