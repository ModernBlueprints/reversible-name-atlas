"""Deterministic Sofia/Martin fixtures for the Connected Change C0 gate."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path

CONNECTED_CHANGE_REQUEST = (
    "Prepare this Apollo project for Northstar handoff. Keep every file. Organize "
    "the approved and research materials into clear folders and keep every "
    "supported link working."
)
CONNECTED_CHANGE_RESULT_NAME = "northstar-shared"

_DUPLICATE_PRESENTATION = b"shared presentation bytes\n"
_COVER_PNG = b"\x89PNG\r\n\x1a\nnorthstar-cover\x00"
_PROTECTED_ENV = b"DEMO_MODE=northstar\n"


@dataclass(frozen=True, slots=True)
class ConnectedChangeFixture:
    """Equivalent projects with different paths and one deterministic origin map."""

    sofia_root: Path
    martin_root: Path
    target_paths: dict[str, str]
    request: str
    result_name: str


@dataclass(frozen=True, slots=True)
class SymmetricConnectedChangeFixture:
    """Two equivalent projects whose duplicate groups cannot be uniquely rebound."""

    origin_root: Path
    receiver_root: Path


TreeState = dict[str, tuple[str, int, int, int, int, bytes | None]]


def make_connected_change_fixture(tmp_path: Path) -> ConnectedChangeFixture:
    """Create the path-different but deterministically matchable Sofia/Martin pair."""

    sofia_root = tmp_path / "sofia-project"
    martin_root = tmp_path / "martin-project"
    _write_files(
        sofia_root,
        {
            ".env.local": _PROTECTED_ENV,
            "assets/approved-copy.txt": _DUPLICATE_PRESENTATION,
            "assets/research-copy.txt": _DUPLICATE_PRESENTATION,
            "media/cover.png": _COVER_PNG,
            "notes/client-brief.md": (
                b"Approved item: [document](../assets/approved-copy.txt#final)\r\n"
            ),
            "notes/research-log.md": (
                b"Research item: [document](../assets/research-copy.txt#draft)\r\n"
            ),
        },
    )
    _write_files(
        martin_root,
        {
            ".env.local": _PROTECTED_ENV,
            "originals/a-copy.txt": _DUPLICATE_PRESENTATION,
            "originals/b-copy.txt": _DUPLICATE_PRESENTATION,
            "incoming/cover-art.png": _COVER_PNG,
            "drafts/summary.md": (
                b"Approved item: [document](../originals/a-copy.txt#final)\r\n"
            ),
            "working/research.md": (
                b"Research item: [document](../originals/b-copy.txt#draft)\r\n"
            ),
        },
    )
    (sofia_root / "empty" / "keep").mkdir(parents=True)
    (martin_root / "empty" / "keep").mkdir(parents=True)

    return ConnectedChangeFixture(
        sofia_root=sofia_root,
        martin_root=martin_root,
        target_paths={
            ".env.local": ".env.local",
            "assets/approved-copy.txt": "deliverables/approved.txt",
            "assets/research-copy.txt": "research/supporting.txt",
            "media/cover.png": "assets/cover.png",
            "notes/client-brief.md": "notes/client-brief.md",
            "notes/research-log.md": "notes/research-log.md",
        },
        request=CONNECTED_CHANGE_REQUEST,
        result_name=CONNECTED_CHANGE_RESULT_NAME,
    )


def make_symmetric_fixture(tmp_path: Path) -> SymmetricConnectedChangeFixture:
    """Create a graph-symmetric pair that a conforming matcher must refuse."""

    origin_root = tmp_path / "symmetric-origin"
    receiver_root = tmp_path / "symmetric-receiver"
    _write_files(
        origin_root,
        {
            "left/note.md": b"See [copy](../payloads/one.txt).\n",
            "right/note.md": b"See [copy](../payloads/two.txt).\n",
            "payloads/one.txt": b"identical\n",
            "payloads/two.txt": b"identical\n",
        },
    )
    _write_files(
        receiver_root,
        {
            "drafts/a.md": b"See [copy](../copies/a.txt).\n",
            "drafts/b.md": b"See [copy](../copies/b.txt).\n",
            "copies/a.txt": b"identical\n",
            "copies/b.txt": b"identical\n",
        },
    )
    return SymmetricConnectedChangeFixture(
        origin_root=origin_root,
        receiver_root=receiver_root,
    )


def tree_state(root: Path) -> TreeState:
    """Capture identity, metadata, and bytes for source-immutability assertions."""

    state: TreeState = {}
    for candidate in (root, *_sorted_descendants(root)):
        relative = "." if candidate == root else candidate.relative_to(root).as_posix()
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            state[relative] = (
                "directory",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mtime_ns,
                metadata.st_size,
                None,
            )
        elif stat.S_ISREG(metadata.st_mode):
            state[relative] = (
                "file",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mtime_ns,
                metadata.st_size,
                candidate.read_bytes(),
            )
        else:
            raise ValueError(f"Unsupported fixture member: {candidate}")
    return state


def portable_tree(root: Path) -> dict[str, bytes | None]:
    """Capture portable directory presence and exact file bytes below ``root``."""

    result: dict[str, bytes | None] = {}
    for candidate in _sorted_descendants(root):
        relative = candidate.relative_to(root).as_posix()
        if candidate.is_dir():
            result[relative] = None
        elif candidate.is_file():
            result[relative] = candidate.read_bytes()
        else:
            raise ValueError(f"Unsupported fixture member: {candidate}")
    return result


def sha256_bytes(payload: bytes) -> str:
    """Return the lowercase SHA-256 digest used by fixture assertions."""

    return hashlib.sha256(payload).hexdigest()


def _write_files(root: Path, files: dict[str, bytes]) -> None:
    for relative_path, payload in files.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def _sorted_descendants(root: Path) -> tuple[Path, ...]:
    return tuple(sorted(root.rglob("*"), key=lambda item: item.as_posix()))
