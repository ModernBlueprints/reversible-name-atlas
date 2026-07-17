"""Structural boundaries for external decision and package services."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from name_atlas.domain import DecisionCard, EvidencePacket, PackageValidationResult


@runtime_checkable
class DecisionCardProvider(Protocol):
    """Generate one advisory card from a bounded evidence packet."""

    async def generate(self, packet: EvidencePacket) -> DecisionCard:
        """Return a validated advisory card without decision authority."""
        ...


@runtime_checkable
class PackageValidator(Protocol):
    """Validate a completed package without coupling to a BagIt library."""

    def validate(self, bag_root: Path) -> PackageValidationResult:
        """Return deterministic package-validation evidence."""
        ...
