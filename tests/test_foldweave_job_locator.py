from __future__ import annotations

import os
from pathlib import Path

import pytest
from connected_change_fixtures import make_connected_change_fixture

from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderRefactorJobV3,
    canonical_job_v3_bytes,
)
from name_atlas.folder_refactor.connected_change.review_service import (
    FoldweaveReviewService,
)
from name_atlas.folder_refactor.serialization import canonical_json_bytes
from name_atlas.foldweave_host_service import FoldweaveHostPlanningService
from name_atlas.foldweave_job_locator import (
    FoldweaveJobLocator,
    FoldweaveJobLocatorError,
)
from name_atlas.foldweave_paths import FoldweavePaths


def _copy_job(job: FolderRefactorJobV3, destination: Path) -> FolderRefactorJobV3:
    copied = FolderRefactorJobV3.model_validate(
        {
            **job.model_dump(mode="python"),
            "job_path": destination.resolve(strict=False),
        },
        strict=True,
    )
    destination.write_bytes(canonical_job_v3_bytes(copied))
    return copied


def _write_pre_final_job(job: FolderRefactorJobV3, destination: Path) -> bytes:
    copied = FolderRefactorJobV3.model_validate(
        {
            **job.model_dump(mode="python"),
            "job_path": destination.resolve(strict=False),
        },
        strict=True,
    )
    payload = copied.model_dump(mode="json")
    payload.pop("operation_idempotency")
    persisted = canonical_json_bytes(payload) + b"\n"
    destination.write_bytes(persisted)
    return persisted


def _review_job(tmp_path: Path, *, label: str) -> FolderRefactorJobV3:
    fixture = make_connected_change_fixture(tmp_path / f"projects-{label}")
    output = tmp_path / f"output-{label}"
    seed_jobs = tmp_path / "seed-jobs"
    output.mkdir()
    seed_jobs.mkdir(exist_ok=True)
    return FoldweaveReviewService().prepare_deterministic_origin_review(
        source_root=fixture.sofia_root,
        output_parent=output,
        job_path=seed_jobs / f"seed-{label}.json",
        request=fixture.request,
        result_folder_name=fixture.result_name,
        target_by_original_path=fixture.target_paths,
        idempotency_key=f"locator-{label}",
    )


def test_locator_resolves_active_filename_by_embedded_job_id(
    tmp_path: Path,
) -> None:
    deterministic_review_job = _review_job(tmp_path, label="first")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    active = jobs / "active.json"
    copied = _copy_job(deterministic_review_job, active)

    located = FoldweaveJobLocator(jobs).resolve(copied.job_id)

    assert located.path == active
    assert located.job == copied


def test_locator_discovers_uuid_and_active_authorities(
    tmp_path: Path,
) -> None:
    deterministic_review_job = _review_job(tmp_path, label="first")
    another_review_job = _review_job(tmp_path, label="second")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    first = _copy_job(deterministic_review_job, jobs / "active.json")
    second = _copy_job(another_review_job, jobs / f"{another_review_job.job_id}.json")

    discovered = FoldweaveJobLocator(jobs).discover()

    assert {item.job.job_id for item in discovered} == {first.job_id, second.job_id}


def test_locator_resolves_current_job_with_unrelated_pre_final_record(
    tmp_path: Path,
) -> None:
    current_job = _review_job(tmp_path, label="current")
    pre_final_job = _review_job(tmp_path, label="pre-final")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    current = _copy_job(current_job, jobs / f"{current_job.job_id}.json")
    preserved = _write_pre_final_job(
        pre_final_job,
        jobs / f"{pre_final_job.job_id}.json",
    )

    located = FoldweaveJobLocator(jobs).resolve(current.job_id)

    assert located.job == current
    assert (jobs / f"{pre_final_job.job_id}.json").read_bytes() == preserved


def test_locator_returns_fresh_start_for_matching_pre_final_record(
    tmp_path: Path,
) -> None:
    pre_final_job = _review_job(tmp_path, label="pre-final")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    destination = jobs / f"{pre_final_job.job_id}.json"
    preserved = _write_pre_final_job(pre_final_job, destination)

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(jobs).resolve(pre_final_job.job_id)

    assert error.value.code == "job_requires_fresh_start"
    assert destination.read_bytes() == preserved


def test_locator_rejects_duplicate_id_across_current_and_pre_final_records(
    tmp_path: Path,
) -> None:
    job = _review_job(tmp_path, label="duplicate")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    _copy_job(job, jobs / "current.json")
    _write_pre_final_job(job, jobs / "pre-final.json")

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(jobs).inspect_registry()

    assert error.value.code == "duplicate_job_id"


def test_locator_rejects_unknown_invalid_v3_shape(
    tmp_path: Path,
) -> None:
    job = _review_job(tmp_path, label="unknown-invalid")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    destination = jobs / f"{job.job_id}.json"
    copied = FolderRefactorJobV3.model_validate(
        {
            **job.model_dump(mode="python"),
            "job_path": destination.resolve(strict=False),
        },
        strict=True,
    )
    payload = copied.model_dump(mode="json")
    payload.pop("operation_idempotency")
    payload.pop("user_request")
    destination.write_bytes(canonical_json_bytes(payload) + b"\n")

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(jobs).inspect_registry()

    assert error.value.code == "job_authority_invalid"


def test_locator_rejects_duplicate_embedded_job_ids(
    tmp_path: Path,
) -> None:
    deterministic_review_job = _review_job(tmp_path, label="first")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    _copy_job(deterministic_review_job, jobs / "active.json")
    _copy_job(deterministic_review_job, jobs / "duplicate.json")

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(jobs).discover()

    assert error.value.code == "duplicate_job_id"


def test_locator_rejects_symlink_job_candidate(
    tmp_path: Path,
) -> None:
    deterministic_review_job = _review_job(tmp_path, label="first")
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    outside = tmp_path / "outside.json"
    _copy_job(deterministic_review_job, outside)
    os.symlink(outside, jobs / "active.json")

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(jobs).discover()

    assert error.value.code == "job_authority_invalid"


def test_locator_rejects_noncanonical_or_historical_candidate(
    tmp_path: Path,
) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "active.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(jobs).discover()

    assert error.value.code == "job_authority_invalid"


def test_locator_rejects_malformed_job_id(tmp_path: Path) -> None:
    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(tmp_path / "jobs").resolve("not-a-job-id")

    assert error.value.code == "job_id_invalid"


def test_locator_rejects_hex_text_that_is_not_uuid4(tmp_path: Path) -> None:
    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(tmp_path / "jobs").resolve("a" * 32)

    assert error.value.code == "job_id_invalid"


def test_locator_rejects_symlink_jobs_root(tmp_path: Path) -> None:
    real_jobs = tmp_path / "real-jobs"
    real_jobs.mkdir()
    linked_jobs = tmp_path / "linked-jobs"
    os.symlink(real_jobs, linked_jobs)

    with pytest.raises(FoldweaveJobLocatorError) as error:
        FoldweaveJobLocator(linked_jobs).discover()

    assert error.value.code == "jobs_root_invalid"


def test_host_service_resolves_native_active_job_by_embedded_id(
    tmp_path: Path,
) -> None:
    deterministic_review_job = _review_job(tmp_path, label="first")
    state_root = tmp_path / "state"
    jobs = state_root / "jobs"
    jobs.mkdir(parents=True)
    copied = _copy_job(deterministic_review_job, jobs / "active.json")

    observed = FoldweaveHostPlanningService(
        paths=FoldweavePaths(state_root=state_root),
    ).status(copied.job_id)

    assert observed == copied
