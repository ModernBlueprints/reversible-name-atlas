"""Deterministic compilation of a complete GPT folder plan."""

from __future__ import annotations

from collections.abc import Collection

from name_atlas.folder_refactor.contracts import (
    AcceptedFileMapping,
    FolderAcceptedPlan,
    FolderInventory,
    FolderPlan,
)
from name_atlas.folder_refactor.naming import (
    TargetPathError,
    validate_complete_target_tree,
    validate_result_folder_name,
    validate_target_path,
)
from name_atlas.folder_refactor.serialization import request_fingerprint


class PlanCompilationError(ValueError):
    """The submitted plan is incomplete or mechanically invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def compile_plan(
    inventory: FolderInventory,
    request: str,
    plan: FolderPlan,
    *,
    known_evidence_ids: Collection[str],
    evidence_fingerprint: str,
) -> FolderAcceptedPlan:
    """Compile a complete planner submission into an immutable accepted map."""

    expected_request_fingerprint = request_fingerprint(request)
    if plan.source_commitment != inventory.source_commitment:
        _reject("source_commitment_mismatch", "Plan targets a different source.")
    if plan.request_fingerprint != expected_request_fingerprint:
        _reject("request_fingerprint_mismatch", "Plan targets a different request.")
    if plan.evidence_fingerprint != evidence_fingerprint:
        _reject("evidence_fingerprint_mismatch", "Plan targets different evidence.")
    if plan.exclusions:
        _reject("plan_exclusions_forbidden", "A plan cannot exclude source files.")
    try:
        result_folder_name = validate_result_folder_name(plan.result_folder_name)
    except TargetPathError as exc:
        _reject("invalid_result_folder_name", str(exc))

    by_id = {item.file_id: item for item in inventory.files}
    eligible_by_id = {
        item.file_id: item for item in inventory.files if not item.protected
    }
    seen_ids: set[str] = set()
    mappings: list[AcceptedFileMapping] = []
    known_evidence = frozenset(known_evidence_ids)
    for entry in plan.entries:
        if entry.file_id in seen_ids:
            _reject("duplicate_file_id", f"Duplicate planner entry: {entry.file_id}")
        seen_ids.add(entry.file_id)
        source_file = by_id.get(entry.file_id)
        if source_file is None:
            _reject("unknown_file_id", f"Unknown planner file ID: {entry.file_id}")
        if source_file.protected:
            _reject(
                "protected_file_in_plan",
                "Planner attempted to control protected file: "
                f"{source_file.relative_path}",
            )
        if entry.original_path != source_file.relative_path:
            _reject(
                "original_path_mismatch",
                f"Entry path does not match file ID: {entry.original_path}",
            )
        unknown_evidence = set(entry.evidence_ids) - known_evidence
        if unknown_evidence:
            _reject(
                "unknown_evidence_id",
                f"Entry cites unknown evidence: {sorted(unknown_evidence)!r}",
            )
        target = _validated_target(
            entry.proposed_target,
            original_path=source_file.relative_path,
            protected=False,
        )
        mappings.append(
            AcceptedFileMapping(
                file_id=source_file.file_id,
                original_path=source_file.relative_path,
                target_path=target,
                protected=False,
                planner_supplied=True,
            )
        )

    missing_ids = sorted(set(eligible_by_id) - seen_ids)
    if missing_ids:
        missing_paths = [eligible_by_id[item].relative_path for item in missing_ids]
        _reject("missing_file_ids", f"Plan omitted source files: {missing_paths!r}")

    for source_file in inventory.files:
        if not source_file.protected:
            continue
        target = _validated_target(
            source_file.relative_path,
            original_path=source_file.relative_path,
            protected=True,
        )
        mappings.append(
            AcceptedFileMapping(
                file_id=source_file.file_id,
                original_path=source_file.relative_path,
                target_path=target,
                protected=True,
                planner_supplied=False,
            )
        )

    mappings.sort(key=lambda item: item.original_path)
    empty_directories = tuple(
        item.relative_path for item in inventory.empty_directories
    )
    for directory in empty_directories:
        _validated_target(directory, original_path=directory, protected=True)
    try:
        validate_complete_target_tree(
            [mapping.target_path for mapping in mappings],
            empty_directories,
        )
    except TargetPathError as exc:
        _reject("invalid_target_tree", str(exc))
    return FolderAcceptedPlan(
        source_commitment=inventory.source_commitment,
        request_fingerprint=expected_request_fingerprint,
        evidence_fingerprint=evidence_fingerprint,
        result_folder_name=result_folder_name,
        file_mappings=tuple(mappings),
        empty_directories=empty_directories,
    )


def _validated_target(value: str, *, original_path: str, protected: bool) -> str:
    try:
        return validate_target_path(
            value,
            original_path=original_path,
            protected=protected,
        )
    except TargetPathError as exc:
        _reject("invalid_target_path", str(exc))


def _reject(code: str, message: str) -> None:
    raise PlanCompilationError(code, message)
