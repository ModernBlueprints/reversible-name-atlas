"""A3 browser actions for independent proof and exact reconstruction."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from name_atlas.folder_app import FolderRunPresentation, create_folder_app
from name_atlas.folder_refactor.receipt_contracts import (
    FolderReceiptVerification,
    FolderReceiptVerificationCheck,
    FolderReceiptVerificationStatus,
    FolderRestoreCheck,
    FolderRestoreReport,
)

oslo_tz = ZoneInfo("Europe/Oslo")
JOB_ID = "123e4567e89b42d3a456426614174000"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _A3ActionService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.verification_calls = 0
        self.block_verification = False
        self.reconstruction_calls: list[Path] = []

    async def plan_and_create_copy(
        self,
        *,
        source_root: Path,
        output_parent: Path,
        request: str,
    ) -> FolderRunPresentation:
        assert source_root == self.root / "source"
        assert output_parent == self.root / "results"
        assert request == "Organize this connected folder."
        result_root = output_parent / "organized-result"
        return FolderRunPresentation(
            source_root=source_root,
            output_parent=output_parent,
            result_root=result_root,
            data_root=result_root / "data",
            source_file_count=4,
            path_change_count=3,
            supported_link_count=2,
            supported_link_update_count=1,
            source_unchanged=True,
            all_files_present_once=True,
            deterministic_proof_passed=True,
            independent_verification_passed=True,
            reconstruction_available=True,
            technical_facts=(("Receipt fingerprint", "f" * 64),),
        )

    def verify_again(self) -> FolderReceiptVerification:
        self.verification_calls += 1
        if self.block_verification:
            return FolderReceiptVerification(
                status=FolderReceiptVerificationStatus.BLOCKED,
                job_id=JOB_ID,
                receipt_fingerprint="f" * 64,
                checks=(
                    FolderReceiptVerificationCheck(
                        check_id="artifact_digest_mismatch:accepted_plan",
                        passed=False,
                        detail="The accepted plan no longer matches its receipt.",
                    ),
                ),
                failed_check_ids=("artifact_digest_mismatch:accepted_plan",),
            )
        return FolderReceiptVerification(
            status=FolderReceiptVerificationStatus.VERIFIED,
            job_id=JOB_ID,
            receipt_fingerprint="f" * 64,
            checks=(
                FolderReceiptVerificationCheck(
                    check_id="receipt_consistency",
                    passed=True,
                    detail="The complete portable result is internally consistent.",
                ),
            ),
        )

    def recreate_original(self, destination: Path) -> FolderRestoreReport:
        self.reconstruction_calls.append(destination)
        return FolderRestoreReport(
            receipt_fingerprint="f" * 64,
            source_commitment="a" * 64,
            destination=destination,
            completed_at=datetime.now(oslo_tz),
            restored_file_count=4,
            restored_bytes=24,
            restored_empty_directory_count=1,
            checks=(
                FolderRestoreCheck(
                    check_id="restored_snapshot_equal",
                    detail="Every reconstructed path and byte matches the snapshot.",
                ),
            ),
        )


def _csrf(response: httpx.Response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _wait_for_verified(client: httpx.AsyncClient) -> None:
    for _ in range(50):
        if (await client.get("/status")).json()["lifecycle"] == "verified":
            return
        await asyncio.sleep(0)
    raise AssertionError("Folder result did not reach verified state.")


@pytest.mark.anyio
async def test_done_runs_keyless_verifier_and_reconstruction_actions(
    tmp_path: Path,
) -> None:
    service = _A3ActionService(tmp_path)
    app = create_folder_app(service)
    transport = httpx.ASGITransport(app=app)
    destination = tmp_path / "recreated-original"

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        csrf_token = _csrf(await client.get("/start"))
        started = await client.post(
            "/start",
            data={
                "source_root": str(tmp_path / "source"),
                "user_request": "Organize this connected folder.",
                "output_parent": str(tmp_path / "results"),
                "csrf_token": csrf_token,
            },
        )
        await _wait_for_verified(client)
        done = await client.get("/done")
        invalid_verify = await client.post(
            "/verify-again",
            data={"csrf_token": "wrong"},
        )
        verified = await client.post(
            "/verify-again",
            data={"csrf_token": csrf_token},
        )
        verified_done = await client.get("/done")
        restored = await client.post(
            "/recreate-original",
            data={
                "csrf_token": csrf_token,
                "restore_destination": str(destination),
            },
        )
        restored_done = await client.get("/done")

    assert started.status_code == 303
    assert done.status_code == 200
    assert "Independent receipt verification" in done.text
    assert "Passed without GPT or an API key" in done.text
    assert 'action="/verify-again"' in done.text
    assert 'action="/recreate-original"' in done.text
    assert "Creates another folder matching the original paths and bytes" in done.text
    assert invalid_verify.status_code == 422
    assert service.verification_calls == 1
    assert verified.status_code == 303
    assert "Independent keyless verification passed" in verified_done.text
    assert restored.status_code == 303
    assert service.reconstruction_calls == [destination]
    assert f"Original layout recreated and verified at {destination}" in (
        restored_done.text
    )


@pytest.mark.anyio
async def test_result_actions_reject_relative_reconstruction_destination(
    tmp_path: Path,
) -> None:
    service = _A3ActionService(tmp_path)
    transport = httpx.ASGITransport(app=create_folder_app(service))

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        csrf_token = _csrf(await client.get("/start"))
        await client.post(
            "/start",
            data={
                "source_root": str(tmp_path / "source"),
                "user_request": "Organize this connected folder.",
                "output_parent": str(tmp_path / "results"),
                "csrf_token": csrf_token,
            },
        )
        await _wait_for_verified(client)
        response = await client.post(
            "/recreate-original",
            data={
                "csrf_token": csrf_token,
                "restore_destination": "relative-destination",
            },
        )

    assert response.status_code == 422
    assert "must be absolute" in response.text
    assert service.reconstruction_calls == []


@pytest.mark.anyio
async def test_blocked_reverification_replaces_positive_browser_authority(
    tmp_path: Path,
) -> None:
    service = _A3ActionService(tmp_path)
    transport = httpx.ASGITransport(app=create_folder_app(service))

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        csrf_token = _csrf(await client.get("/start"))
        await client.post(
            "/start",
            data={
                "source_root": str(tmp_path / "source"),
                "user_request": "Organize this connected folder.",
                "output_parent": str(tmp_path / "results"),
                "csrf_token": csrf_token,
            },
        )
        await _wait_for_verified(client)
        service.block_verification = True
        rerun = await client.post(
            "/verify-again",
            data={"csrf_token": csrf_token},
        )
        blocked = await client.get(rerun.headers["location"])
        done = await client.get("/done", follow_redirects=False)
        restore = await client.post(
            "/recreate-original",
            data={
                "csrf_token": csrf_token,
                "restore_destination": str(tmp_path / "never-created"),
            },
        )

    assert rerun.status_code == 303
    assert rerun.headers["location"] == "/working"
    assert blocked.status_code == 200
    assert "Transaction blocked" in blocked.text
    assert "artifact_digest_mismatch:accepted_plan" in blocked.text
    assert "Deterministic checks passed" not in blocked.text
    assert done.status_code == 303
    assert done.headers["location"] == "/working"
    assert restore.status_code == 409
    assert service.reconstruction_calls == []
