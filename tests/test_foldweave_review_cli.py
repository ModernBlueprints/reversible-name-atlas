"""Semantic evidence for the reviewed Foldweave command surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from connected_change_fixtures import make_connected_change_fixture, tree_state

from name_atlas import cli, foldweave_launcher, foldweave_review_cli
from name_atlas.folder_refactor.connected_change.job_v2 import build_new_gpt_job_v2
from name_atlas.folder_refactor.connected_change.job_v3 import (
    FolderJobLifecycleV3,
    FolderRefactorJobV3Store,
)
from name_atlas.folder_refactor.connected_change.review_service import (
    FoldweaveReviewService,
    _v3_from_seed,
)
from name_atlas.foldweave_review_cli import (
    run_accept,
    run_prepare_application,
    run_preview,
)


def _origin_review(tmp_path: Path):
    fixture = make_connected_change_fixture(tmp_path / "projects")
    output = tmp_path / "origin-output"
    jobs = tmp_path / "jobs"
    output.mkdir()
    jobs.mkdir()
    job = FoldweaveReviewService().prepare_deterministic_origin_review(
        source_root=fixture.sofia_root,
        output_parent=output,
        job_path=jobs / "origin.json",
        request=fixture.request,
        result_folder_name=fixture.result_name,
        target_by_original_path=fixture.target_paths,
        idempotency_key="review-cli-origin",
    )
    return fixture, output, job


def test_preview_prints_exact_persisted_dto(
    tmp_path: Path,
    capsys,
) -> None:
    _fixture, _output, job = _origin_review(tmp_path)
    assert job.preview is not None

    assert run_preview([str(job.job_path), "--json"], environ={}) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed == job.preview.model_dump(mode="json")


def test_accept_rejects_unseen_preview_then_executes_exact_preview(
    tmp_path: Path,
    capsys,
) -> None:
    fixture, output, job = _origin_review(tmp_path)
    source_before = tree_state(fixture.sofia_root)
    assert job.preview is not None

    assert (
        run_accept(
            [
                str(job.job_path),
                "--preview-fingerprint",
                "0" * 64,
                "--idempotency-key",
                "review-cli-wrong",
            ],
            environ={},
        )
        == 1
    )
    assert "preview fingerprint differs" in capsys.readouterr().err
    assert tuple(output.iterdir()) == ()

    assert (
        run_accept(
            [
                str(job.job_path),
                "--preview-fingerprint",
                job.preview.preview_fingerprint,
                "--idempotency-key",
                "review-cli-accept",
            ],
            environ={},
        )
        == 0
    )
    accepted_output = capsys.readouterr().out
    accepted = FolderRefactorJobV3Store(job.job_path).inspect()
    assert accepted.lifecycle is FolderJobLifecycleV3.VERIFIED
    assert "LIFECYCLE verified" in accepted_output
    assert "RECEIPT " in accepted_output
    assert tree_state(fixture.sofia_root) == source_before


def test_apply_change_prepares_receiver_review_without_output_or_model(
    tmp_path: Path,
    capsys,
) -> None:
    fixture, output, origin = _origin_review(tmp_path)
    assert origin.preview is not None
    accepted = FoldweaveReviewService().accept(
        origin.job_path,
        expected_revision=origin.revision,
        preview_fingerprint=origin.preview.preview_fingerprint,
        candidate_fingerprint=origin.preview.compiled_candidate_fingerprint,
        output_parent=output,
        result_folder_name=fixture.result_name,
        idempotency_key="review-cli-origin-accept",
        channel="cli",
    )
    change_file, _fingerprint, _receipt = FoldweaveReviewService().get_change_file(
        accepted.job_path
    )
    receiver_output = tmp_path / "receiver-output"
    receiver_output.mkdir()
    receiver_job = tmp_path / "jobs" / "receiver.json"
    receiver_before = tree_state(fixture.martin_root)

    assert (
        run_prepare_application(
            [
                str(change_file),
                "--source",
                str(fixture.martin_root),
                "--output",
                str(receiver_output),
                "--job",
                str(receiver_job),
            ],
            environ={},
        )
        == 0
    )

    prepared = FolderRefactorJobV3Store(receiver_job).inspect()
    printed = capsys.readouterr().out
    assert prepared.lifecycle is FolderJobLifecycleV3.REVIEWING
    assert prepared.preview is not None
    assert prepared.preview.proposal_basis == "imported_change_file"
    assert "LIFECYCLE reviewing" in printed
    assert tuple(receiver_output.iterdir()) == ()
    assert tree_state(fixture.martin_root) == receiver_before


def test_launcher_exposes_reviewed_commands(capsys) -> None:
    assert foldweave_launcher.run(["--help"]) == 0
    output = capsys.readouterr().out
    for command in (
        "run",
        "apply-change",
        "preview",
        "revise",
        "accept",
        "verify-receipt",
        "restore-receipt",
    ):
        assert command in output


@pytest.mark.parametrize("command", ["verify-receipt", "restore-receipt"])
def test_primary_receipt_help_uses_foldweave_command_name(
    command: str,
    capsys,
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        foldweave_launcher.run([command, "--help"])

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert output.startswith(f"usage: foldweave {command}")
    assert f"usage: name-atlas {command}" not in output


def test_legacy_alias_keeps_its_historical_command_name(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.run(["verify-receipt", "--help"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("usage: name-atlas verify-receipt")


@pytest.mark.parametrize("lifecycle", ["planning", "reviewing", "verified"])
def test_run_refuses_unrelated_existing_job_before_provider_construction(
    lifecycle: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    fixture = make_connected_change_fixture(tmp_path / "projects")
    output = tmp_path / "output"
    jobs = tmp_path / "jobs"
    output.mkdir()
    jobs.mkdir()
    job_path = jobs / "existing.json"
    service = FoldweaveReviewService()
    if lifecycle == "planning":
        seed = build_new_gpt_job_v2(
            source_root=fixture.sofia_root,
            output_parent=output,
            job_path=job_path,
            user_request=fixture.request,
            idempotency_key="another-planning-owner",
        )
        with FolderRefactorJobV3Store(job_path).writer() as writer:
            writer.save_new(
                _v3_from_seed(seed, lifecycle=FolderJobLifecycleV3.PLANNING)
            )
    else:
        existing = service.prepare_deterministic_origin_review(
            source_root=fixture.sofia_root,
            output_parent=output,
            job_path=job_path,
            request=fixture.request,
            result_folder_name=fixture.result_name,
            target_by_original_path=fixture.target_paths,
            idempotency_key="another-review-owner",
        )
        if lifecycle == "verified":
            assert existing.preview is not None
            existing = service.accept(
                existing.job_path,
                expected_revision=existing.revision,
                preview_fingerprint=existing.preview.preview_fingerprint,
                candidate_fingerprint=(existing.preview.compiled_candidate_fingerprint),
                output_parent=output,
                result_folder_name=fixture.result_name,
                idempotency_key="another-review-owner-accept",
                channel="cli",
            )
            assert existing.lifecycle is FolderJobLifecycleV3.VERIFIED

    provider_constructions = 0

    def forbidden_provider(**_kwargs):
        nonlocal provider_constructions
        provider_constructions += 1
        raise AssertionError("Conflicting job must fail before provider construction.")

    monkeypatch.setattr(foldweave_review_cli, "_initial_provider", forbidden_provider)
    result = foldweave_review_cli.run_prepare_origin(
        [
            "--mode",
            "live",
            "--source",
            str(fixture.sofia_root),
            "--output",
            str(output),
            "--job",
            str(job_path),
            "--request",
            "A different request must never reuse this job.",
        ],
        environ={},
    )

    assert result == 1
    assert provider_constructions == 0
    assert "existing job is bound to another" in capsys.readouterr().err
