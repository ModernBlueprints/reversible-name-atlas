"""Read-only adapter for the Library of Congress BagIt validator."""

from __future__ import annotations

import re
from pathlib import Path

import bagit

from name_atlas.domain import PackageValidationResult

_WHITESPACE_PATTERN = re.compile(r"\s+")
_BAG_PLACEHOLDER = "<bag>"


class BagItAdapterError(RuntimeError):
    """An unexpected failure prevented the adapter from validating a bag."""


class BagItPackageValidator:
    """Validate an existing BagIt package without mutating it."""

    def validate(self, bag_root: Path) -> PackageValidationResult:
        """Return sanitized BagIt evidence for an existing directory.

        A structurally invalid or fixity-invalid BagIt package is a normal
        validation result. Invalid adapter input and unexpected library or I/O
        failures are operational errors and therefore raise ``BagItAdapterError``.
        """

        validated_root = _require_bag_directory(bag_root)

        try:
            bag = bagit.Bag(str(validated_root))
            bag.validate(processes=1, fast=False, completeness_only=False)
        except bagit.BagError as exc:
            return PackageValidationResult(
                validator="bagit",
                valid=False,
                messages=(_validation_failure_message(exc, validated_root),),
            )
        except Exception as exc:
            raise BagItAdapterError(
                "BagIt validation failed because of an unexpected adapter or I/O error."
            ) from exc

        return PackageValidationResult(
            validator="bagit",
            valid=True,
            messages=("BagIt validation passed.",),
        )


def _require_bag_directory(bag_root: Path) -> Path:
    if not isinstance(bag_root, Path):
        raise BagItAdapterError("Bag root must be a pathlib.Path.")

    try:
        if not bag_root.exists():
            raise BagItAdapterError("Bag root does not exist.")
        if not bag_root.is_dir():
            raise BagItAdapterError("Bag root is not a directory.")
        return bag_root.resolve(strict=True)
    except BagItAdapterError:
        raise
    except OSError as exc:
        raise BagItAdapterError("Bag root could not be inspected.") from exc


def _validation_failure_message(exc: bagit.BagError, bag_root: Path) -> str:
    message = str(exc) or type(exc).__name__
    sanitized = _sanitize_message(message, bag_root)
    return f"BagIt validation failed: {sanitized}"


def _sanitize_message(message: str, bag_root: Path) -> str:
    replacements = {
        str(bag_root): _BAG_PLACEHOLDER,
        bag_root.as_posix(): _BAG_PLACEHOLDER,
    }
    sanitized = message
    for original, replacement in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        sanitized = sanitized.replace(original, replacement)
    return _WHITESPACE_PATTERN.sub(" ", sanitized).strip()
