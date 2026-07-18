"""Server-rendered Start, Working, and Done shell for folder refactoring."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qs

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from name_atlas.folder_refactor.planner import DeterministicDevelopmentPlanner
from name_atlas.folder_refactor.transaction import run_folder_refactor

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
MAX_FORM_BODY_BYTES = 32_768
MAX_REQUEST_CHARACTERS = 8_000
PLANNER_LABEL = "Deterministic development planner — no API call"
WORKING_STAGES = (
    "Reading folder",
    "GPT-5.6 is planning",
    "Checking every file and destination",
    "Creating a separate result",
    "Updating supported links",
    "Verifying result",
)


class FolderWebLifecycle(StrEnum):
    """Server-owned A1 presentation states."""

    IDLE = "idle"
    PLANNING = "planning"
    VERIFIED = "verified"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class FolderRunPresentation:
    """Plain facts returned by an injected folder transaction service."""

    source_root: Path
    output_parent: Path
    result_root: Path
    data_root: Path
    source_file_count: int
    path_change_count: int
    source_unchanged: bool
    all_files_present_once: bool
    deterministic_proof_passed: bool
    supported_link_update_count: int = 0
    independent_verification_passed: bool = False
    reconstruction_available: bool = False
    technical_facts: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Reject presentation claims that are malformed or internally unsafe."""

        for field_name in ("source_root", "output_parent", "result_root", "data_root"):
            if not getattr(self, field_name).is_absolute():
                raise ValueError(f"{field_name} must be an absolute local path.")
        if self.source_file_count < 1:
            raise ValueError("A completed result must account for at least one file.")
        if not 0 <= self.path_change_count <= self.source_file_count:
            raise ValueError("Path-change count is outside the source-file count.")
        if self.supported_link_update_count < 0:
            raise ValueError("Supported-link update count cannot be negative.")
        if not (
            self.source_unchanged
            and self.all_files_present_once
            and self.deterministic_proof_passed
        ):
            raise ValueError("A Done presentation cannot contain a failed core proof.")
        if self.data_root != self.result_root / "data":
            raise ValueError("The user folder must be the result's data directory.")


@runtime_checkable
class FolderRunService(Protocol):
    """Execute one bounded folder transaction without giving the UI authority."""

    async def plan_and_create_copy(
        self,
        *,
        source_root: Path,
        output_parent: Path,
        request: str,
    ) -> FolderRunPresentation:
        """Return verified facts for one separately created result."""
        ...


@dataclass(frozen=True, slots=True)
class DeterministicFolderRunService:
    """Bridge the A1 browser shell to the complete generic transaction."""

    result_folder_name: str = "name-atlas-organized-copy"
    target_prefix: str = "organized"

    async def plan_and_create_copy(
        self,
        *,
        source_root: Path,
        output_parent: Path,
        request: str,
    ) -> FolderRunPresentation:
        """Run the truthful no-API A1 planner and expose verified facts only."""

        planner = DeterministicDevelopmentPlanner(
            result_folder_name=self.result_folder_name,
            target_prefix=self.target_prefix,
        )
        result = await run_folder_refactor(
            source_root=source_root,
            output_parent=output_parent,
            request=request,
            planner=planner,
        )
        checks = {check.check_id: check.passed for check in result.report.checks}
        deterministic_proof_passed = bool(checks) and all(checks.values())
        return FolderRunPresentation(
            source_root=source_root.resolve(strict=True),
            output_parent=output_parent.resolve(strict=True),
            result_root=result.result_root,
            data_root=result.data_root,
            source_file_count=result.report.file_count,
            path_change_count=result.report.path_change_count,
            supported_link_update_count=0,
            source_unchanged=checks.get("source_unchanged") is True,
            all_files_present_once=(
                checks.get("complete_file_bijection") is True
                and checks.get("payload_hashes_preserved") is True
            ),
            deterministic_proof_passed=deterministic_proof_passed,
            independent_verification_passed=False,
            reconstruction_available=False,
            technical_facts=(
                ("Source commitment", result.report.source_commitment),
                ("Staged data commitment", result.report.staged_data_commitment),
                ("Portable package", "BagIt validation passed"),
            ),
        )


@dataclass(slots=True)
class _FolderWebState:
    lifecycle: FolderWebLifecycle = FolderWebLifecycle.IDLE
    source_value: str = ""
    request_value: str = ""
    output_value: str = ""
    current_stage: int = 0
    completed_stage_count: int = 0
    result: FolderRunPresentation | None = None
    blocker: str | None = None
    notice: str | None = None
    worker: asyncio.Task[None] | None = field(default=None, repr=False)


class FolderFormError(ValueError):
    """The local Start form is incomplete or malformed."""


def create_folder_app(
    service: FolderRunService,
    *,
    initial_source: Path | None = None,
    initial_output_parent: Path | None = None,
    planner_label: str = PLANNER_LABEL,
) -> FastAPI:
    """Create the A1 loopback UI around an injected folder transaction service."""

    state = _FolderWebState(
        source_value=str(initial_source) if initial_source is not None else "",
        output_value=(
            str(initial_output_parent) if initial_output_parent is not None else ""
        ),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if state.worker is not None and not state.worker.done():
                state.worker.cancel()
                with suppress(asyncio.CancelledError):
                    await state.worker

    app = FastAPI(
        title="Reversible Name Atlas",
        description=(
            "Describe the change. Keep supported Markdown links. Prove the result."
        ),
        version="0.1.0-a1",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.folder_web_state = state
    app.state.folder_run_service = service
    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_ROOT / "static"),
        name="static",
    )

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return _redirect(_next_path(state))

    @app.get("/start", response_class=HTMLResponse, include_in_schema=False)
    async def start(request: Request) -> HTMLResponse:
        if state.lifecycle is not FolderWebLifecycle.IDLE:
            return _redirect(_next_path(state))
        return _render_start(
            request=request,
            state=state,
            planner_label=planner_label,
        )

    @app.post("/start", response_class=HTMLResponse, include_in_schema=False)
    async def start_job(request: Request) -> Response:
        if state.lifecycle is not FolderWebLifecycle.IDLE:
            return _redirect(_next_path(state))
        try:
            source_root, user_request, output_parent = await _parse_start_form(request)
        except FolderFormError as exc:
            state.blocker = str(exc)
            return _render_start(
                request=request,
                state=state,
                planner_label=planner_label,
                status_code=422,
            )

        state.source_value = str(source_root)
        state.request_value = user_request
        state.output_value = str(output_parent)
        state.lifecycle = FolderWebLifecycle.PLANNING
        state.current_stage = 0
        state.completed_stage_count = 0
        state.result = None
        state.blocker = None
        state.notice = None
        state.worker = asyncio.create_task(
            _run_job(
                state=state,
                service=service,
                source_root=source_root,
                output_parent=output_parent,
                user_request=user_request,
            ),
            name="name-atlas-a1-folder-job",
        )
        await asyncio.sleep(0)
        return _redirect("/working")

    @app.get("/working", response_class=HTMLResponse, include_in_schema=False)
    async def working(request: Request) -> Response:
        if state.lifecycle is FolderWebLifecycle.IDLE:
            return _redirect("/start")
        if state.lifecycle is FolderWebLifecycle.VERIFIED:
            return _redirect("/done")
        return TEMPLATES.TemplateResponse(
            request=request,
            name="folder/working.html",
            context=_base_context(state=state, planner_label=planner_label),
        )

    @app.get("/status", include_in_schema=False)
    async def status() -> JSONResponse:
        payload: dict[str, str | int | bool | None] = {
            "lifecycle": state.lifecycle.value,
            "current_stage": state.current_stage,
            "completed_stage_count": state.completed_stage_count,
            "stage_count": len(WORKING_STAGES),
            "done_url": (
                "/done" if state.lifecycle is FolderWebLifecycle.VERIFIED else None
            ),
            "blocked": state.lifecycle is FolderWebLifecycle.BLOCKED,
        }
        return JSONResponse(payload)

    @app.post("/clarify", include_in_schema=False)
    async def clarify() -> RedirectResponse:
        state.notice = (
            "This deterministic A1 transaction did not request clarification. "
            "The one-question GPT-5.6 path is added in the next integrated slice."
        )
        return _redirect(_next_path(state))

    @app.get("/done", response_class=HTMLResponse, include_in_schema=False)
    async def done(request: Request) -> Response:
        if state.lifecycle is not FolderWebLifecycle.VERIFIED or state.result is None:
            return _redirect(_next_path(state))
        return TEMPLATES.TemplateResponse(
            request=request,
            name="folder/done.html",
            context=_base_context(state=state, planner_label=planner_label),
        )

    @app.post("/verify-again", include_in_schema=False)
    async def verify_again() -> RedirectResponse:
        state.notice = (
            "Independent keyless verification is not part of this A1 walking slice. "
            "The displayed deterministic proof is the verification currently available."
        )
        return _redirect(_next_path(state))

    @app.post("/recreate-original", include_in_schema=False)
    async def recreate_original() -> RedirectResponse:
        state.notice = (
            "Original-layout reconstruction is not part of this A1 walking slice. "
            "The original source remains unchanged."
        )
        return _redirect(_next_path(state))

    return app


async def _run_job(
    *,
    state: _FolderWebState,
    service: FolderRunService,
    source_root: Path,
    output_parent: Path,
    user_request: str,
) -> None:
    state.current_stage = 1
    state.completed_stage_count = 1
    try:
        result = await service.plan_and_create_copy(
            source_root=source_root,
            output_parent=output_parent,
            request=user_request,
        )
    except Exception as exc:  # noqa: BLE001 - service errors are user-visible blockers
        state.blocker = _safe_error_text(exc)
        state.lifecycle = FolderWebLifecycle.BLOCKED
        return
    state.result = result
    state.current_stage = len(WORKING_STAGES) - 1
    state.completed_stage_count = len(WORKING_STAGES)
    state.lifecycle = FolderWebLifecycle.VERIFIED


async def _parse_start_form(request: Request) -> tuple[Path, str, Path]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type != "application/x-www-form-urlencoded":
        raise FolderFormError("The Start form must use URL-encoded local fields.")
    body = await request.body()
    if not body or len(body) > MAX_FORM_BODY_BYTES:
        raise FolderFormError("The Start form is empty or too large.")
    try:
        fields = parse_qs(
            body.decode("utf-8", errors="strict"),
            strict_parsing=True,
            max_num_fields=3,
            keep_blank_values=True,
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise FolderFormError("The Start form is not valid UTF-8 form data.") from exc
    expected = {"source_root", "user_request", "output_parent"}
    if set(fields) != expected or any(len(fields[key]) != 1 for key in expected):
        raise FolderFormError("Exactly the three displayed Start fields are required.")

    source_text = fields["source_root"][0].strip()
    request_text = fields["user_request"][0].strip()
    output_text = fields["output_parent"][0].strip()
    if not source_text or not request_text or not output_text:
        raise FolderFormError("Folder, request, and result location are all required.")
    if "\x00" in source_text or "\x00" in request_text or "\x00" in output_text:
        raise FolderFormError("Start fields cannot contain a NUL character.")
    if len(request_text) > MAX_REQUEST_CHARACTERS:
        raise FolderFormError(
            "The plain-English request cannot exceed "
            f"{MAX_REQUEST_CHARACTERS} characters."
        )
    source_root = Path(source_text).expanduser()
    output_parent = Path(output_text).expanduser()
    if not source_root.is_absolute() or not output_parent.is_absolute():
        raise FolderFormError(
            "Folder and result location must be absolute local paths."
        )
    return source_root, request_text, output_parent


def _base_context(
    *,
    state: _FolderWebState,
    planner_label: str,
) -> dict[str, object]:
    return {
        "state": state,
        "stages": WORKING_STAGES,
        "planner_label": planner_label,
        "notice": state.notice,
    }


def _render_start(
    *,
    request: Request,
    state: _FolderWebState,
    planner_label: str,
    status_code: int = 200,
) -> HTMLResponse:
    response = TEMPLATES.TemplateResponse(
        request=request,
        name="folder/start.html",
        context=_base_context(state=state, planner_label=planner_label),
    )
    response.status_code = status_code
    return response


def _next_path(state: _FolderWebState) -> str:
    if state.lifecycle is FolderWebLifecycle.IDLE:
        return "/start"
    if state.lifecycle is FolderWebLifecycle.VERIFIED:
        return "/done"
    return "/working"


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def _safe_error_text(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    if not text:
        return "The folder transaction was blocked without a usable explanation."
    return text[:1_000]
