"""C2 acceptance for restart-safe v2 clarification planning."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Literal

import httpx
import pytest
from connected_change_fixtures import portable_tree, tree_state

from name_atlas.connected_web_service import ConnectedBrowserRunService
from name_atlas.folder_app import FolderWebLifecycle, create_folder_app
from name_atlas.folder_refactor.connected_change.contracts import (
    GptPlannedExecutionOrigin,
)
from name_atlas.folder_refactor.connected_change.job_service import (
    ConnectedChangeJobService,
    ConnectedChangeJobServiceError,
)
from name_atlas.folder_refactor.connected_change.job_v2 import (
    FolderJobLifecycleV2,
    FolderJobV2RevisionError,
    FolderRefactorJobV2,
    FolderRefactorJobV2Store,
    GptPlannedJobAuthorityV2,
    GptPlannerCheckpointV2,
    evolve_job_v2,
    parse_job_v2_bytes,
)
from name_atlas.folder_refactor.connected_change.planning import (
    ConnectedOriginPlanningService,
)
from name_atlas.folder_refactor.connected_change.receipt import (
    EXECUTION_ORIGIN_PATH,
)
from name_atlas.folder_refactor.connected_change.receipt_contracts import (
    FolderReceiptEnvelopeV2,
)
from name_atlas.folder_refactor.connected_change.verification import (
    ConnectedReceiptVerificationStatus,
)
from name_atlas.folder_refactor.contracts import FolderPlan, FolderPlanEntry
from name_atlas.folder_refactor.planner_contracts import (
    FolderPlannerTurnInput,
    FolderProviderResponse,
    PlannerInventoryFile,
    ProviderToolResponse,
    RequestClarificationCall,
    SubmitPlanCall,
)
from name_atlas.folder_refactor.planner_orchestrator import create_planner_progress
from name_atlas.folder_refactor.portable_artifacts import (
    CHANGE_RECEIPT_PATH,
    EVIDENCE_LEDGER_PATH,
    parse_portable_model,
    read_regular_bytes,
)
from name_atlas.folder_refactor.receipt_contracts import FolderEvidenceLedger
from name_atlas.folder_refactor.serialization import canonical_sha256

REQUEST = "Prepare the approved presentation and keep every file."
QUESTION = "Which report should be treated as the approved handoff file?"
ANSWER = "Use the client-approved report."
SECOND_QUESTION = "Which cover should accompany that report?"


class _StatelessClarifyingProvider:
    """Choose the next response solely from the persisted turn input."""

    def __init__(
        self,
        *,
        job_path: Path | None = None,
        ask_second_question: bool = False,
    ) -> None:
        self._job_path = job_path
        self._ask_second_question = ask_second_question
        self.invocation_count = 0
        self.received_inputs: list[FolderPlannerTurnInput] = []
        self.persisted_answer_seen: str | None = None

    @property
    def provider_kind(self) -> Literal["deterministic"]:
        return "deterministic"

    async def exchange(
        self,
        turn_input: FolderPlannerTurnInput,
        /,
    ) -> FolderProviderResponse:
        self.invocation_count += 1
        self.received_inputs.append(turn_input)
        if turn_input.clarification_answer is None:
            return ProviderToolResponse(
                provider_kind="deterministic",
                observable_output_items=(
                    {
                        "type": "durable_clarification_question",
                        "response_turn": turn_input.response_turn,
                    },
                ),
                tool_calls=(
                    RequestClarificationCall(
                        call_id="approved-report-question",
                        question=QUESTION,
                        missing_facts=("approved_report",),
                        evidence_ids=("initial_inventory",),
                    ),
                ),
            )

        assert turn_input.clarification_question == QUESTION
        assert turn_input.clarification_answer == ANSWER
        self._assert_answer_was_persisted()
        if self._ask_second_question:
            return ProviderToolResponse(
                provider_kind="deterministic",
                observable_output_items=(
                    {
                        "type": "forbidden_second_question",
                        "response_turn": turn_input.response_turn,
                    },
                ),
                tool_calls=(
                    RequestClarificationCall(
                        call_id="second-question",
                        question=SECOND_QUESTION,
                        missing_facts=("approved_cover",),
                        evidence_ids=("initial_inventory",),
                    ),
                ),
            )

        return ProviderToolResponse(
            provider_kind="deterministic",
            observable_output_items=(
                {
                    "type": "durable_clarification_plan",
                    "response_turn": turn_input.response_turn,
                },
            ),
            tool_calls=(
                SubmitPlanCall(
                    call_id="clarified-complete-plan",
                    plan=_complete_plan(turn_input),
                ),
            ),
        )

    def _assert_answer_was_persisted(self) -> None:
        if self._job_path is None:
            return
        persisted = parse_job_v2_bytes(
            self._job_path.read_bytes(),
            expected_path=self._job_path.resolve(),
        )
        assert persisted.lifecycle is FolderJobLifecycleV2.PLANNING
        assert isinstance(persisted.authority, GptPlannedJobAuthorityV2)
        checkpoint = persisted.authority.planner_checkpoint
        assert checkpoint.clarification_question == QUESTION
        assert checkpoint.clarification_answer == ANSWER
        assert checkpoint.progress is not None
        assert checkpoint.progress.clarification_answer == ANSWER
        self.persisted_answer_seen = checkpoint.clarification_answer


class _NeverCalledProvider:
    """Fail a test if a terminal or stale job attempts provider continuation."""

    def __init__(self) -> None:
        self.invocation_count = 0

    @property
    def provider_kind(self) -> Literal["deterministic"]:
        return "deterministic"

    async def exchange(
        self,
        turn_input: FolderPlannerTurnInput,
        /,
    ) -> FolderProviderResponse:
        del turn_input
        self.invocation_count += 1
        raise AssertionError("Provider continuation was forbidden for this job state.")


class _SourceMutatingProvider(_StatelessClarifyingProvider):
    """Change one source member after exchange but before checkpoint persistence."""

    def __init__(self, source_file: Path) -> None:
        super().__init__()
        self._source_file = source_file

    async def exchange(
        self,
        turn_input: FolderPlannerTurnInput,
        /,
    ) -> FolderProviderResponse:
        response = await super().exchange(turn_input)
        self._source_file.write_bytes(b"Changed during provider exchange.\n")
        return response


def _source_and_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    output = tmp_path / "results"
    source.mkdir()
    output.mkdir()
    (source / "notes.md").write_bytes(
        b"Approved material: [report](report.txt#approved).\r\n"
    )
    (source / "report.txt").write_bytes(b"Client-approved report.\n")
    (source / ".env.local").write_bytes(b"DEMO_MODE=connected\n")
    (source / "empty" / "keep").mkdir(parents=True)
    return source, output, tmp_path / "jobs" / "origin.json"


def _complete_plan(turn_input: FolderPlannerTurnInput) -> FolderPlan:
    initial = turn_input.evidence_ledger.initial_evidence
    assert isinstance(initial, dict)
    raw_files = initial.get("files")
    assert isinstance(raw_files, list)
    files = tuple(
        PlannerInventoryFile.model_validate(item, strict=True) for item in raw_files
    )
    targets = {
        "notes.md": "notes/project-notes.md",
        "report.txt": "deliverables/approved-report.txt",
    }
    return FolderPlan(
        source_commitment=turn_input.source_commitment,
        request_fingerprint=turn_input.request_fingerprint,
        request_scope="rename_and_move_every_file",
        evidence_fingerprint=turn_input.evidence_ledger.evidence_fingerprint,
        result_folder_name="clarified-result",
        entries=tuple(
            FolderPlanEntry(
                file_id=item.file_id,
                original_path=item.relative_path,
                proposed_target=targets[item.relative_path],
                rationale="Uses the exact user clarification for the handoff layout.",
                evidence_ids=("initial_inventory",),
            )
            for item in files
            if not item.protected
        ),
        exclusions=(),
    )


async def _start_waiting_job(
    *,
    source: Path,
    output: Path,
    job_path: Path,
) -> tuple[FolderRefactorJobV2, _StatelessClarifyingProvider]:
    provider = _StatelessClarifyingProvider()
    waiting = await ConnectedOriginPlanningService().start(
        source_root=source,
        output_parent=output,
        job_path=job_path,
        request=REQUEST,
        idempotency_key="connected-planning-clarification",
        provider=provider,
    )
    assert waiting.lifecycle is FolderJobLifecycleV2.AWAITING_CLARIFICATION
    assert isinstance(waiting.authority, GptPlannedJobAuthorityV2)
    assert waiting.authority.planner_checkpoint.clarification_question == QUESTION
    assert provider.invocation_count == 1
    assert provider.received_inputs[0].response_turn == 1
    return waiting, provider


def _csrf(response: httpx.Response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _wait_for_browser_lifecycle(
    client: httpx.AsyncClient,
    expected: str,
) -> dict[str, object]:
    for _ in range(500):
        response = await client.get("/status")
        assert response.status_code == 200
        payload = response.json()
        if payload["lifecycle"] == expected:
            return payload
        if payload["lifecycle"] == "blocked":
            raise AssertionError((await client.get("/working")).text)
        await asyncio.sleep(0.02)
    raise AssertionError(f"Browser did not reach {expected}.")


@pytest.mark.anyio
async def test_standard_browser_asks_once_restarts_and_finishes_same_job(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    providers: list[_StatelessClarifyingProvider] = []

    def provider_factory() -> _StatelessClarifyingProvider:
        provider = _StatelessClarifyingProvider(job_path=job_path)
        providers.append(provider)
        return provider

    first_service = ConnectedBrowserRunService(
        job_path=job_path,
        planner_provider_factory=provider_factory,
    )
    first_app = create_folder_app(first_service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app),
        base_url="http://testserver",
    ) as client:
        start = await client.get("/start")
        csrf_token = _csrf(start)
        started = await client.post(
            "/start",
            data={
                "source_root": str(source),
                "user_request": REQUEST,
                "output_parent": str(output),
                "evidence_disclosure_acknowledged": "true",
                "csrf_token": csrf_token,
            },
        )
        waiting = await _wait_for_browser_lifecycle(
            client,
            "awaiting_clarification",
        )
        working = await client.get("/working")

    assert started.status_code == 303
    assert waiting["lifecycle"] == "awaiting_clarification"
    assert QUESTION in working.text
    assert len(providers) == 1
    assert providers[0].invocation_count == 1
    waiting_bytes = job_path.read_bytes()

    restarted_service = ConnectedBrowserRunService(
        job_path=job_path,
        planner_provider_factory=provider_factory,
    )
    restarted_app = create_folder_app(restarted_service)
    assert len(providers) == 1
    assert job_path.read_bytes() == waiting_bytes
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=restarted_app),
        base_url="http://testserver",
    ) as client:
        restarted_working = await client.get("/working")
        answered = await client.post(
            "/clarify",
            data={"answer": ANSWER, "csrf_token": _csrf(restarted_working)},
        )
        verified = await _wait_for_browser_lifecycle(client, "verified")
        done = await client.get("/done")

    assert answered.status_code == 303
    assert verified["lifecycle"] == "verified"
    assert "Your new folder is ready" in done.text
    assert len(providers) == 2
    assert providers[1].invocation_count == 1
    assert providers[1].persisted_answer_seen == ANSWER


@pytest.mark.anyio
async def test_restart_safe_question_answer_verifies_and_is_idempotent(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    source_before = tree_state(source)
    waiting, first_provider = await _start_waiting_job(
        source=source,
        output=output,
        job_path=job_path,
    )
    waiting_bytes = job_path.read_bytes()

    factory_calls = 0

    def provider_factory() -> _NeverCalledProvider:
        nonlocal factory_calls
        factory_calls += 1
        return _NeverCalledProvider()

    restarted_browser = ConnectedBrowserRunService(
        job_path=job_path,
        planner_provider_factory=provider_factory,
    )
    checkpoint = restarted_browser.web_checkpoint()

    assert checkpoint is not None
    assert checkpoint.lifecycle is FolderWebLifecycle.AWAITING_CLARIFICATION
    assert checkpoint.clarification is not None
    assert checkpoint.clarification.question == QUESTION
    assert checkpoint.clarification.continuation_token == waiting.job_id
    assert factory_calls == 0
    assert first_provider.invocation_count == 1
    assert job_path.read_bytes() == waiting_bytes

    answering_provider = _StatelessClarifyingProvider(job_path=job_path)
    verified = await ConnectedOriginPlanningService().answer(
        job_path,
        continuation_token=waiting.job_id,
        answer=ANSWER,
        provider=answering_provider,
    )

    assert verified.lifecycle is FolderJobLifecycleV2.VERIFIED
    assert verified.final_result_path is not None
    assert answering_provider.invocation_count == 1
    assert answering_provider.persisted_answer_seen == ANSWER
    second_input = answering_provider.received_inputs[0]
    assert second_input.response_turn == 2
    assert second_input.clarification_question == QUESTION
    assert second_input.clarification_answer == ANSWER
    assert len(second_input.prior_turns) == 1
    assert tree_state(source) == source_before

    result_root = verified.final_result_path
    evidence_bytes = read_regular_bytes(result_root, EVIDENCE_LEDGER_PATH)
    evidence = parse_portable_model(evidence_bytes, FolderEvidenceLedger)
    origin = parse_portable_model(
        read_regular_bytes(result_root, EXECUTION_ORIGIN_PATH),
        GptPlannedExecutionOrigin,
    )
    receipt = parse_portable_model(
        read_regular_bytes(result_root, CHANGE_RECEIPT_PATH),
        FolderReceiptEnvelopeV2,
    )
    assert evidence.clarification_question == QUESTION
    assert evidence.clarification_answer == ANSWER
    assert origin.clarification_question == QUESTION
    assert origin.clarification_answer == ANSWER
    assert origin.evidence_fingerprint == evidence.evidence_fingerprint
    assert receipt.receipt.evidence_fingerprint == evidence.evidence_fingerprint
    commitments = {item.path: item for item in receipt.receipt.artifact_commitments}
    evidence_commitment = commitments[EVIDENCE_LEDGER_PATH]
    assert evidence_commitment.size == len(evidence_bytes)
    assert evidence_commitment.sha256 == hashlib.sha256(evidence_bytes).hexdigest()
    assert receipt.receipt.execution_origin_fingerprint == canonical_sha256(origin)
    verification = ConnectedChangeJobService().verify_result(job_path)
    assert verification.status is ConnectedReceiptVerificationStatus.VERIFIED

    terminal_job_bytes = job_path.read_bytes()
    result_before = portable_tree(result_root)
    duplicate_provider = _NeverCalledProvider()
    with pytest.raises(
        ConnectedChangeJobServiceError,
        match="clarification_not_active",
    ):
        await ConnectedOriginPlanningService().answer(
            job_path,
            continuation_token=waiting.job_id,
            answer=ANSWER,
            provider=duplicate_provider,
        )
    assert duplicate_provider.invocation_count == 0
    assert job_path.read_bytes() == terminal_job_bytes
    assert portable_tree(result_root) == result_before
    assert tuple(output.iterdir()) == (result_root,)


@pytest.mark.anyio
async def test_second_clarification_blocks_without_creating_output(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    waiting, _first_provider = await _start_waiting_job(
        source=source,
        output=output,
        job_path=job_path,
    )
    second_provider = _StatelessClarifyingProvider(
        job_path=job_path,
        ask_second_question=True,
    )

    blocked = await ConnectedOriginPlanningService().answer(
        job_path,
        continuation_token=waiting.job_id,
        answer=ANSWER,
        provider=second_provider,
    )

    assert blocked.lifecycle is FolderJobLifecycleV2.BLOCKED
    assert blocked.blocker_code == "second_clarification_not_allowed"
    assert blocked.final_result_path is None
    assert blocked.pending_result_path is None
    assert second_provider.invocation_count == 1
    assert second_provider.persisted_answer_seen == ANSWER
    assert not tuple(output.iterdir())
    assert ConnectedChangeJobService().status(job_path) == blocked


@pytest.mark.anyio
async def test_source_change_before_answer_stales_without_provider_continuation(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    waiting, _first_provider = await _start_waiting_job(
        source=source,
        output=output,
        job_path=job_path,
    )
    (source / "report.txt").write_bytes(b"Changed after the question.\n")
    forbidden_provider = _NeverCalledProvider()

    stale = await ConnectedOriginPlanningService().answer(
        job_path,
        continuation_token=waiting.job_id,
        answer=ANSWER,
        provider=forbidden_provider,
    )

    assert stale.lifecycle is FolderJobLifecycleV2.STALE
    assert stale.staleness is not None
    assert forbidden_provider.invocation_count == 0
    assert stale.final_result_path is None
    assert stale.pending_result_path is None
    assert not tuple(output.iterdir())
    assert ConnectedChangeJobService().status(job_path) == stale


@pytest.mark.anyio
async def test_writer_rejects_rewinding_durable_clarification_history(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    waiting, _provider = await _start_waiting_job(
        source=source,
        output=output,
        job_path=job_path,
    )
    waiting_bytes = job_path.read_bytes()
    fresh_progress = create_planner_progress(
        waiting.source_inventory,
        waiting.user_request,
        job_id=waiting.job_id,
        provider_kind="deterministic",
    )
    rewind = evolve_job_v2(
        waiting,
        authority=GptPlannedJobAuthorityV2(
            planner_checkpoint=GptPlannerCheckpointV2.from_progress(fresh_progress)
        ),
        lifecycle=FolderJobLifecycleV2.PLANNING,
    )

    with (
        FolderRefactorJobV2Store(job_path).writer() as writer,
        pytest.raises(FolderJobV2RevisionError, match="append-only"),
    ):
        writer.save(rewind, expected_current=waiting)

    assert job_path.read_bytes() == waiting_bytes
    persisted = ConnectedChangeJobService().status(job_path)
    assert persisted == waiting
    assert isinstance(persisted.authority, GptPlannedJobAuthorityV2)
    assert persisted.authority.planner_checkpoint.response_turn_count == 1
    assert persisted.authority.planner_checkpoint.clarification_question == QUESTION


@pytest.mark.anyio
async def test_source_change_during_provider_exchange_returns_durable_stale_job(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    provider = _SourceMutatingProvider(source / "report.txt")

    stale = await ConnectedOriginPlanningService().start(
        source_root=source,
        output_parent=output,
        job_path=job_path,
        request=REQUEST,
        idempotency_key="source-change-during-provider",
        provider=provider,
    )

    assert stale.lifecycle is FolderJobLifecycleV2.STALE
    assert stale.staleness is not None
    assert provider.invocation_count == 1
    assert tuple(
        (item.kind, item.member_kind, item.relative_path)
        for item in stale.staleness.source_differences
    ) == (("content_changed", "regular_file", "report.txt"),)
    assert stale.final_result_path is None
    assert stale.pending_result_path is None
    assert not tuple(output.iterdir())
    assert ConnectedChangeJobService().status(job_path) == stale

    forbidden_provider = _NeverCalledProvider()
    repeated = await ConnectedOriginPlanningService().resume(
        job_path,
        provider=forbidden_provider,
    )
    assert repeated == stale
    assert forbidden_provider.invocation_count == 0


@pytest.mark.anyio
async def test_browser_startup_rehydrates_waiting_job_staleness_without_provider(
    tmp_path: Path,
) -> None:
    source, output, job_path = _source_and_paths(tmp_path)
    waiting, _provider = await _start_waiting_job(
        source=source,
        output=output,
        job_path=job_path,
    )
    assert waiting.lifecycle is FolderJobLifecycleV2.AWAITING_CLARIFICATION
    (source / "report.txt").write_bytes(b"Changed before browser restart.\n")
    factory_calls = 0

    def provider_factory() -> _NeverCalledProvider:
        nonlocal factory_calls
        factory_calls += 1
        return _NeverCalledProvider()

    restarted_service = ConnectedBrowserRunService(
        job_path=job_path,
        planner_provider_factory=provider_factory,
    )
    restarted_app = create_folder_app(restarted_service)
    state = restarted_app.state.folder_web_state
    persisted = ConnectedChangeJobService().status(job_path)

    assert state.lifecycle is FolderWebLifecycle.BLOCKED
    assert state.blocker is not None
    assert "stale" in state.blocker.lower()
    assert persisted.lifecycle is FolderJobLifecycleV2.STALE
    assert persisted.staleness is not None
    assert persisted.staleness.source_differences[0].relative_path == "report.txt"
    assert factory_calls == 0
    assert not tuple(output.iterdir())
