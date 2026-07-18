"""AI-first generic-folder refactoring domain."""

from typing import TYPE_CHECKING, Any

from name_atlas.folder_refactor.compiler import compile_plan
from name_atlas.folder_refactor.contracts import (
    AcceptedFileMapping,
    FolderAcceptedPlan,
    FolderEmptyDirectory,
    FolderFile,
    FolderInventory,
    FolderPlan,
    FolderPlanEntry,
    FolderVerificationReport,
    PlanOutcome,
)
from name_atlas.folder_refactor.inventory import (
    FolderScan,
    FolderScanError,
    scan_folder,
)
from name_atlas.folder_refactor.transaction import (
    FolderRunResult,
    FolderTransactionError,
    run_folder_refactor,
)

if TYPE_CHECKING:
    from name_atlas.folder_refactor.planner import (
        DeterministicDevelopmentPlanner,
        FolderPlanner,
    )


def __getattr__(name: str) -> Any:
    """Load origin-only planner symbols without affecting receiver imports."""

    if name in {"DeterministicDevelopmentPlanner", "FolderPlanner"}:
        from name_atlas.folder_refactor import planner

        return getattr(planner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AcceptedFileMapping",
    "DeterministicDevelopmentPlanner",
    "FolderAcceptedPlan",
    "FolderEmptyDirectory",
    "FolderFile",
    "FolderInventory",
    "FolderPlan",
    "FolderPlanEntry",
    "FolderPlanner",
    "FolderRunResult",
    "FolderScan",
    "FolderScanError",
    "FolderTransactionError",
    "FolderVerificationReport",
    "PlanOutcome",
    "compile_plan",
    "run_folder_refactor",
    "scan_folder",
]
