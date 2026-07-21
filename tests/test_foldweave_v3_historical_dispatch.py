"""Strict historical job dispatch at the Foldweave v3 boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from connected_change_fixtures import make_connected_change_fixture

from name_atlas.folder_refactor.connected_change.job_v2 import (
    CapsuleAppliedJobAuthorityV2,
    FolderJobLifecycleV2,
    FolderJobVerifiedArtifactsV2,
    FolderRefactorJobV2,
    GptPlannedJobAuthorityV2,
    GptPlannerCheckpointV2,
    JobInputStalenessV2,
    LegacyFolderJobV1Evidence,
    build_new_capsule_job_v2,
    build_new_gpt_job_v2,
    canonical_job_v2_bytes,
    evolve_job_v2,
    expected_final_result_path_v2,
)
from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderJobV3LoadError,
    FolderRefactorJobV3,
    FolderRefactorJobV3Store,
    LegacyV2NonterminalJobError,
    UnsupportedPreFinalFolderJobV3,
    build_execution_authorization,
    canonical_job_v3_bytes,
    load_folder_job_record_v3,
    load_folder_job_routing_record_v3,
    parse_job_v3_routing_bytes,
)
from name_atlas.folder_refactor.connected_change.review_service import (
    FoldweaveReviewService,
)
from name_atlas.folder_refactor.connected_change.service import (
    create_connected_change_origin,
    prepare_connected_change_application,
)
from name_atlas.folder_refactor.serialization import canonical_json_bytes


def test_v3_routing_classifies_only_exact_pre_final_shapes_without_mutation(
    tmp_path: Path,
) -> None:
    job = _build_review_v3_job(tmp_path / "routing")
    current_bytes = canonical_job_v3_bytes(job)

    assert parse_job_v3_routing_bytes(current_bytes, expected_path=job.job_path) == job

    payload = job.model_dump(mode="json")
    payload.pop("operation_idempotency")
    pre_final_bytes = canonical_json_bytes(payload) + b"\n"
    job.job_path.write_bytes(pre_final_bytes)

    classified = load_folder_job_routing_record_v3(job.job_path)

    assert isinstance(classified, UnsupportedPreFinalFolderJobV3)
    assert classified.job_id == job.job_id
    assert classified.idempotency == job.idempotency
    with pytest.raises(FolderJobV3LoadError, match="corrupt"):
        FolderRefactorJobV3Store(job.job_path).inspect()
    assert job.job_path.read_bytes() == pre_final_bytes

    authorization = build_execution_authorization(
        job=job,
        expected_job_revision=job.revision,
        preview_fingerprint=job.preview.preview_fingerprint,
        candidate_fingerprint=job.preview.compiled_candidate_fingerprint,
        output_parent=job.output_parent,
        result_folder_name=job.candidate_plan.result_folder_name,
        idempotency_key="pre-final-routing-authorization",
        channel="cli",
    )
    verified_shape = job.model_dump(mode="json")
    verified_shape.pop("operation_idempotency")
    verified_shape["execution_authorization"] = authorization.model_dump(mode="json")
    verified_shape["execution_authorization"].pop("output_parent_fingerprint")
    verified_bytes = canonical_json_bytes(verified_shape) + b"\n"

    classified_verified = parse_job_v3_routing_bytes(
        verified_bytes,
        expected_path=job.job_path,
    )

    assert isinstance(classified_verified, UnsupportedPreFinalFolderJobV3)


def test_v3_routing_rejects_any_additional_unknown_failure(
    tmp_path: Path,
) -> None:
    job = _build_review_v3_job(tmp_path / "unknown")
    payload = job.model_dump(mode="json")
    payload.pop("operation_idempotency")
    payload.pop("user_request")
    invalid_bytes = canonical_json_bytes(payload) + b"\n"

    with pytest.raises(FolderJobV3LoadError, match="corrupt"):
        parse_job_v3_routing_bytes(invalid_bytes, expected_path=job.job_path)


def _build_review_v3_job(tmp_path: Path) -> FolderRefactorJobV3:
    fixture = make_connected_change_fixture(tmp_path / "projects")
    output = tmp_path / "output"
    output.mkdir(parents=True)
    return FoldweaveReviewService().prepare_deterministic_origin_review(
        source_root=fixture.sofia_root,
        output_parent=output,
        job_path=tmp_path / "jobs" / "review.json",
        request=fixture.request,
        result_folder_name=fixture.result_name,
        target_by_original_path=fixture.target_paths,
        idempotency_key="historical-routing-review",
    )


@pytest.mark.parametrize(
    "lifecycle",
    tuple(FolderJobLifecycleV2),
    ids=lambda lifecycle: lifecycle.value,
)
def test_v3_dispatch_accepts_only_terminal_v2_evidence_without_mutation(
    tmp_path: Path,
    lifecycle: FolderJobLifecycleV2,
) -> None:
    """Every v2 lifecycle is classified explicitly at the v3 boundary."""

    job = _build_v2_job_for_lifecycle(tmp_path, lifecycle)
    job.job_path.parent.mkdir(parents=True, exist_ok=True)
    persisted_bytes = canonical_job_v2_bytes(job)
    job.job_path.write_bytes(persisted_bytes)

    if lifecycle.terminal:
        record = load_folder_job_record_v3(job.job_path)
        assert isinstance(record, FolderRefactorJobV2)
        assert record == job
        with pytest.raises(
            FolderJobV3LoadError,
            match=r"Historical v1/v2 jobs are read-only; create a fresh v3 job\.",
        ):
            FolderRefactorJobV3Store(job.job_path).inspect()
    else:
        with pytest.raises(
            LegacyV2NonterminalJobError,
            match=(
                r"Nonterminal folder-refactor-job\.v2 state cannot be resumed as "
                r"v3; create a fresh FolderRefactorJobV3 from the unchanged source\."
            ),
        ):
            load_folder_job_record_v3(job.job_path)
        with pytest.raises(LegacyV2NonterminalJobError):
            FolderRefactorJobV3Store(job.job_path).inspect()

    assert job.job_path.read_bytes() == persisted_bytes


def test_v3_dispatch_preserves_existing_v1_rules_and_bytes(tmp_path: Path) -> None:
    """The v2 gate does not reinterpret or rewrite historical v1 records."""

    from name_atlas.folder_refactor.job import (
        FolderJobLifecycle,
        FolderRefactorJob,
        build_new_job,
        canonical_job_bytes,
    )

    source = tmp_path / "legacy-source"
    output = tmp_path / "legacy-output"
    source.mkdir()
    output.mkdir()
    (source / "note.txt").write_text("legacy\n", encoding="utf-8")
    job_path = tmp_path / "legacy-state" / "job.json"
    legacy = build_new_job(
        source_root=source,
        output_parent=output,
        job_path=job_path,
        user_request="Organize the historical project.",
    )
    job_path.parent.mkdir()

    nonterminal_bytes = canonical_job_bytes(legacy)
    job_path.write_bytes(nonterminal_bytes)
    with pytest.raises(FolderJobV3LoadError):
        load_folder_job_record_v3(job_path)
    assert job_path.read_bytes() == nonterminal_bytes

    terminal = FolderRefactorJob.model_validate(
        {
            **legacy.model_dump(mode="python"),
            "lifecycle": FolderJobLifecycle.BLOCKED,
            "blocker_code": "historical_blocker",
            "blocker_message": "Historical v1 job stopped.",
        },
        strict=True,
    )
    terminal_bytes = canonical_job_bytes(terminal)
    job_path.write_bytes(terminal_bytes)
    record = load_folder_job_record_v3(job_path)

    assert isinstance(record, LegacyFolderJobV1Evidence)
    assert record.lifecycle == "blocked"
    assert job_path.read_bytes() == terminal_bytes


def _build_v2_job_for_lifecycle(
    tmp_path: Path,
    lifecycle: FolderJobLifecycleV2,
) -> FolderRefactorJobV2:
    if lifecycle in {FolderJobLifecycleV2.EXECUTING, FolderJobLifecycleV2.VERIFIED}:
        return _build_executing_or_verified_v2_job(tmp_path, lifecycle)

    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    (source / "note.txt").write_text("unchanged\n", encoding="utf-8")
    job = build_new_gpt_job_v2(
        source_root=source,
        output_parent=output,
        job_path=tmp_path / "state" / "job.json",
        user_request="Organize this project.",
        idempotency_key=f"historical-{lifecycle.value}",
    )
    if lifecycle is FolderJobLifecycleV2.PLANNING:
        return job
    if lifecycle is FolderJobLifecycleV2.AWAITING_CLARIFICATION:
        return evolve_job_v2(
            job,
            authority=GptPlannedJobAuthorityV2(
                planner_checkpoint=GptPlannerCheckpointV2(
                    status="awaiting_clarification",
                    clarification_question="Which reviewed structure should be used?",
                )
            ),
            lifecycle=lifecycle,
        )
    if lifecycle is FolderJobLifecycleV2.STALE:
        return evolve_job_v2(
            job,
            lifecycle=lifecycle,
            staleness=JobInputStalenessV2(
                source_scan_error="Historical source could not be rescanned."
            ),
        )
    if lifecycle is FolderJobLifecycleV2.BLOCKED:
        blocker_code = "historical_blocker"
        blocker_message = "Historical v2 job stopped."
        return evolve_job_v2(
            job,
            authority=GptPlannedJobAuthorityV2(
                planner_checkpoint=GptPlannerCheckpointV2(
                    status="blocked",
                    blocker_code=blocker_code,
                    blocker_message=blocker_message,
                )
            ),
            lifecycle=lifecycle,
            blocker_code=blocker_code,
            blocker_message=blocker_message,
        )
    raise AssertionError(f"Unhandled v2 lifecycle: {lifecycle.value}")


def _build_executing_or_verified_v2_job(
    tmp_path: Path,
    lifecycle: FolderJobLifecycleV2,
) -> FolderRefactorJobV2:
    fixture = make_connected_change_fixture(tmp_path / "projects")
    origin_output = tmp_path / "origin-output"
    receiver_output = tmp_path / "receiver-output"
    origin_output.mkdir()
    receiver_output.mkdir()
    origin = create_connected_change_origin(
        source_root=fixture.sofia_root,
        output_parent=origin_output,
        request=fixture.request,
        result_folder_name=fixture.result_name,
        target_by_original_path=fixture.target_paths,
    )
    job = build_new_capsule_job_v2(
        source_root=fixture.martin_root,
        output_parent=receiver_output,
        job_path=tmp_path / "state" / "job.json",
        change_file_path=origin.change_file_path,
        idempotency_key=f"historical-{lifecycle.value}",
    )
    prepared = prepare_connected_change_application(
        change_file_path=origin.change_file_path,
        source_root=fixture.martin_root,
    )
    executing = evolve_job_v2(
        job,
        authority=CapsuleAppliedJobAuthorityV2(
            change_file_binding=job.authority.change_file_binding,
            match_report=prepared.match_report,
            execution_origin=prepared.execution_origin,
        ),
        accepted_plan=prepared.accepted_plan,
        lifecycle=FolderJobLifecycleV2.EXECUTING,
    )
    if lifecycle is FolderJobLifecycleV2.EXECUTING:
        return executing
    return evolve_job_v2(
        executing,
        lifecycle=FolderJobLifecycleV2.VERIFIED,
        final_result_path=expected_final_result_path_v2(executing),
        verified_artifacts=FolderJobVerifiedArtifactsV2(
            receipt_fingerprint="a" * 64,
            organized_tree_commitment="b" * 64,
            change_ledger_fingerprint="c" * 64,
            verification_fingerprint="d" * 64,
        ),
    )
