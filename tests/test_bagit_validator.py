"""Focused tests for the read-only BagIt package validator adapter."""

from __future__ import annotations

from pathlib import Path

import bagit
import pytest

from name_atlas.ports import PackageValidator
from name_atlas.verification import BagItAdapterError, BagItPackageValidator


def _make_valid_bag(tmp_path: Path) -> Path:
    bag_root = tmp_path / "package"
    bag_root.mkdir()
    (bag_root / "record.txt").write_text("unchanged payload\n", encoding="utf-8")
    bagit.make_bag(
        str(bag_root),
        checksums=["sha256"],
        bag_info={"Source-Organization": "Name Atlas test fixture"},
    )
    return bag_root


def _read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_valid_bag_passes_without_mutation(tmp_path: Path) -> None:
    bag_root = _make_valid_bag(tmp_path)
    before = _read_tree(bag_root)

    validator = BagItPackageValidator()
    result = validator.validate(bag_root)

    assert isinstance(validator, PackageValidator)
    assert result.model_dump() == {
        "validator": "bagit",
        "valid": True,
        "messages": ("BagIt validation passed.",),
    }
    assert _read_tree(bag_root) == before


def test_payload_tampering_is_an_invalid_result_with_sanitized_message(
    tmp_path: Path,
) -> None:
    bag_root = _make_valid_bag(tmp_path)
    (bag_root / "data" / "record.txt").write_text(
        "changed!! payload\n", encoding="utf-8"
    )

    result = BagItPackageValidator().validate(bag_root)

    assert result.valid is False
    assert result.validator == "bagit"
    assert len(result.messages) == 1
    assert result.messages[0].startswith("BagIt validation failed:")
    assert str(tmp_path) not in result.messages[0]
    assert "record.txt" in result.messages[0]


@pytest.mark.parametrize(
    ("bag_root", "expected_message"),
    [
        ("not-a-path", "Bag root must be a pathlib.Path."),
        (Path("missing-bag"), "Bag root does not exist."),
    ],
)
def test_invalid_adapter_input_raises_typed_error(
    tmp_path: Path, bag_root: object, expected_message: str
) -> None:
    candidate = tmp_path / bag_root if isinstance(bag_root, Path) else bag_root

    with pytest.raises(BagItAdapterError, match=expected_message):
        BagItPackageValidator().validate(candidate)  # type: ignore[arg-type]


def test_file_root_raises_typed_error(tmp_path: Path) -> None:
    bag_root = tmp_path / "not-a-directory"
    bag_root.write_text("not a bag", encoding="utf-8")

    with pytest.raises(BagItAdapterError, match="Bag root is not a directory"):
        BagItPackageValidator().validate(bag_root)


def test_unexpected_library_failure_raises_typed_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bag_root = _make_valid_bag(tmp_path)

    def fail_unexpectedly(_bag: bagit.Bag, **_kwargs: object) -> bool:
        raise OSError("sensitive local path")

    monkeypatch.setattr(bagit.Bag, "validate", fail_unexpectedly)

    with pytest.raises(
        BagItAdapterError,
        match="unexpected adapter or I/O error",
    ) as exc_info:
        BagItPackageValidator().validate(bag_root)

    assert "sensitive local path" not in str(exc_info.value)
