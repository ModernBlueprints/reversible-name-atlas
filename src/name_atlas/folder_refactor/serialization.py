"""Canonical serialization helpers for portable folder-refactor records."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json_bytes(value: BaseModel | Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes with no trailing newline."""

    serializable = (
        value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    )
    return json.dumps(
        serializable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | Any) -> str:
    """Hash one canonically serialized value."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def request_fingerprint(request: str) -> str:
    """Bind the exact user request to the planner and accepted plan."""

    if not isinstance(request, str) or not request.strip():
        raise ValueError("The folder-change request must contain non-whitespace text.")
    payload = {
        "domain": "name-atlas:folder-user-request:v1",
        "request": request,
    }
    return canonical_sha256(payload)
