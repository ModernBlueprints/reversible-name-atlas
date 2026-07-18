"""Deterministic Name Atlas target-path validation."""

from __future__ import annotations

import unicodedata
from collections.abc import Collection
from pathlib import PurePosixPath

MAX_COMPONENT_BYTES = 240
MAX_TARGET_PATH_BYTES = 1_024
FORBIDDEN_COMPONENT_CHARACTERS = frozenset('<>:"/\\|?*')
RESERVED_BASENAMES = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{number}" for number in range(1, 10)),
        *(f"lpt{number}" for number in range(1, 10)),
    }
)


class TargetPathError(ValueError):
    """One proposed result path violates the fixed naming profile."""


def validate_result_folder_name(value: str) -> str:
    """Validate GPT's single-component outer result-folder proposal."""

    if "/" in value or "\\" in value:
        raise TargetPathError("Result folder name must be exactly one component.")
    _validate_component(value)
    return value


def validate_target_path(
    value: str,
    *,
    original_path: str,
    protected: bool,
) -> str:
    """Validate one relative target and the exact suffix-preservation rule."""

    if not isinstance(value, str) or not value:
        raise TargetPathError("Target path must be a non-empty string.")
    if value.startswith("/") or "\\" in value:
        raise TargetPathError(f"Target must be a relative POSIX path: {value!r}")
    if len(value.encode("utf-8")) > MAX_TARGET_PATH_BYTES:
        raise TargetPathError(
            f"Target path exceeds {MAX_TARGET_PATH_BYTES} UTF-8 bytes: {value!r}"
        )
    segments = value.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise TargetPathError(f"Target contains an empty or dot segment: {value!r}")
    if PurePosixPath(value).as_posix() != value:
        raise TargetPathError(f"Target is not normalized POSIX syntax: {value!r}")
    if protected and value != original_path:
        raise TargetPathError(
            f"Protected file must remain at its exact original path: {original_path}"
        )
    for component in segments:
        _validate_component(component, allow_leading_dot=protected)
    if not protected:
        original_suffix = protected_suffix(PurePosixPath(original_path).name)
        target_suffix = protected_suffix(PurePosixPath(value).name)
        if target_suffix != original_suffix:
            raise TargetPathError(
                "Target must preserve the exact original protected suffix: "
                f"{original_path!r} -> {value!r}"
            )
    return value


def protected_suffix(basename: str) -> str:
    """Return the exact substring beginning at the first non-leading period."""

    index = basename.find(".", 1)
    return "" if index < 0 else basename[index:]


def normalized_path_keys(value: str) -> tuple[str, str, str]:
    """Return exact, NFC, and Unicode-casefold comparison keys."""

    nfc = unicodedata.normalize("NFC", value)
    return value, nfc, nfc.casefold()


def validate_complete_target_tree(
    file_targets: Collection[str],
    empty_directories: Collection[str],
) -> None:
    """Reject path collisions and file/directory conflicts under all profiles."""

    targets = tuple(file_targets)
    empty_paths = tuple(empty_directories)
    _require_unique_paths(targets, "file target")
    _require_unique_paths(empty_paths, "empty directory")

    directory_nodes: list[str] = list(empty_paths)
    for path in (*targets, *empty_paths):
        parts = PurePosixPath(path).parts
        directory_nodes.extend(
            PurePosixPath(*parts[:index]).as_posix() for index in range(1, len(parts))
        )

    for comparison_index, comparison_label in enumerate(
        ("exact", "NFC", "Unicode casefold")
    ):
        empty_keys = {
            normalized_path_keys(directory)[comparison_index]: directory
            for directory in empty_paths
        }
        directory_keys = {
            normalized_path_keys(directory)[comparison_index]: directory
            for directory in directory_nodes
        }
        for target in targets:
            key = normalized_path_keys(target)[comparison_index]
            if key in directory_keys:
                raise TargetPathError(
                    "File target conflicts with a required directory under "
                    f"{comparison_label} comparison: {target!r}, "
                    f"{directory_keys[key]!r}"
                )
            target_parts = PurePosixPath(target).parts
            for index in range(1, len(target_parts)):
                ancestor = PurePosixPath(*target_parts[:index]).as_posix()
                ancestor_key = normalized_path_keys(ancestor)[comparison_index]
                if ancestor_key in empty_keys:
                    raise TargetPathError(
                        "File target would make a preserved empty directory nonempty "
                        f"under {comparison_label} comparison: {target!r}, "
                        f"{empty_keys[ancestor_key]!r}"
                    )

    prefix_spellings: dict[str, str] = {}
    for directory in directory_nodes:
        folded = normalized_path_keys(directory)[2]
        prior = prefix_spellings.setdefault(folded, directory)
        if prior != directory:
            raise TargetPathError(
                "Directory prefix has inconsistent Unicode-casefold spelling: "
                f"{prior!r}, {directory!r}"
            )


def _require_unique_paths(values: Collection[str], label: str) -> None:
    for key_index, key_label in enumerate(("exact", "NFC", "Unicode casefold")):
        observed: dict[str, str] = {}
        for value in values:
            key = normalized_path_keys(value)[key_index]
            prior = observed.get(key)
            if prior is not None:
                raise TargetPathError(
                    f"Conflicting {label}s under {key_label} comparison: "
                    f"{prior!r}, {value!r}"
                )
            observed[key] = value


def _validate_component(value: str, *, allow_leading_dot: bool = False) -> None:
    if not isinstance(value, str) or not value:
        raise TargetPathError("Path component must be non-empty.")
    if value in {".", ".."}:
        raise TargetPathError("Dot path components are unsupported.")
    if unicodedata.normalize("NFC", value) != value:
        raise TargetPathError(f"Path component is not Unicode NFC: {value!r}")
    leading_is_invalid = value[0] == " " or (value[0] == "." and not allow_leading_dot)
    if leading_is_invalid or value[-1] in {" ", "."}:
        raise TargetPathError(
            f"Path component cannot begin or end with space or dot: {value!r}"
        )
    if any(character in FORBIDDEN_COMPONENT_CHARACTERS for character in value):
        raise TargetPathError(
            f"Path component contains a forbidden character: {value!r}"
        )
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        raise TargetPathError(f"Path component contains a control character: {value!r}")
    if len(value.encode("utf-8")) > MAX_COMPONENT_BYTES:
        raise TargetPathError(
            f"Path component exceeds {MAX_COMPONENT_BYTES} UTF-8 bytes: {value!r}"
        )
    basename_before_period = value.split(".", 1)[0].casefold()
    if basename_before_period in RESERVED_BASENAMES:
        raise TargetPathError(f"Path component uses a reserved basename: {value!r}")
