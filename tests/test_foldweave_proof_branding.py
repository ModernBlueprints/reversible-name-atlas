"""Schema-aware Foldweave proof-branding compatibility tests."""

from __future__ import annotations

import hashlib

from name_atlas.folder_refactor.connected_change.proof import (
    render_connected_proof_html,
)


def test_legacy_proof_bytes_keep_historical_name_atlas_title() -> None:
    proof = render_connected_proof_html("a" * 64, "b" * 64)

    assert len(proof) == 1024
    assert hashlib.sha256(proof).hexdigest() == (
        "727b861ac79ac733dfe72ac5e72c2d2e300ea529431dd10561e4fab10e89f99d"
    )
    assert b"<title>Name Atlas proof</title>" in proof
    assert b"Foldweave proof" not in proof


def test_foldweave_proof_bytes_use_active_product_title() -> None:
    proof = render_connected_proof_html(
        "a" * 64,
        "b" * 64,
        release_profile="foldweave",
    )

    assert b"<title>Foldweave proof</title>" in proof
    assert b"Name Atlas proof" not in proof
    assert b'<p class="status">' in proof
    assert b'class="verified"' not in proof
    assert b"box-shadow" not in proof
    assert b"border-bottom:1px solid" not in proof
    assert b"background:var(--surface)" in proof
