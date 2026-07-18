"""Server-rendered stale Migration Case behavior."""

from html import escape
from pathlib import Path

import httpx
import pytest

from name_atlas.app import create_app
from name_atlas.cases import (
    CaseLifecycle,
    MigrationCaseStore,
    format_source_differences,
)
from name_atlas.config import RuntimeConfig
from name_atlas.domain import RunMode
from name_atlas.verification import BagItPackageValidator
from name_atlas.workflow import (
    UnavailableReplayDecisionCardProvider,
    WorkflowSession,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_durable_stale_case_blocks_every_workbench_surface(
    tmp_path: Path,
) -> None:
    source = tmp_path / "stale-source"
    case_path = tmp_path / "case.json"
    output_root = tmp_path / "output"
    _write_low_risk_package(source)

    initial = WorkflowSession(
        source_root=source,
        output_root=output_root,
        decision_card_provider=UnavailableReplayDecisionCardProvider(),
        package_validator=BagItPackageValidator(),
        case_path=case_path,
        case_name="Durable stale case",
    )
    initial.close()

    changed_member = source / "objects" / "plain note.txt"
    changed_member.write_text("plain source changed", encoding="utf-8")
    workflow = WorkflowSession(
        source_root=source,
        output_root=output_root,
        decision_card_provider=UnavailableReplayDecisionCardProvider(),
        package_validator=BagItPackageValidator(),
        case_path=case_path,
    )
    assert workflow.case is not None
    assert workflow.case.lifecycle is CaseLifecycle.STALE
    expected_differences = format_source_differences(workflow.case.stale_differences)
    assert expected_differences
    assert MigrationCaseStore(case_path).load().lifecycle is CaseLifecycle.STALE

    config = RuntimeConfig.from_environment(
        mode=RunMode.REPLAY,
        environ={},
        replay_record_configured=True,
    )
    transport = httpx.ASGITransport(app=create_app(config, workflow))
    route_labels = {
        "/atlas": "01 · Atlas · BLOCKED",
        "/decide": "02 · Decide · BLOCKED",
        "/stage": "03 · Stage · BLOCKED",
        "/verify": "04 · Verify · BLOCKED",
        "/handoff": "05 · Handoff · BLOCKED",
    }

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        root = await client.get("/")
        pages = {path: await client.get(path) for path in route_labels}
        health = await client.get("/healthz")
        direct_post = await client.post("/approve-low-risk")
        decide_after_post = await client.get("/decide")

    assert root.status_code == 303
    assert root.headers["location"] == "/atlas"
    for path, label in route_labels.items():
        assert pages[path].status_code == 200
        assert label in pages[path].text

    atlas_html = pages["/atlas"].text
    assert "Exact source differences" in atlas_html
    for difference in expected_differences:
        assert escape(difference) in atlas_html

    decide_html = pages["/decide"].text
    assert "Decision controls are unavailable for this stale case" in decide_html
    assert 'method="post"' not in decide_html
    assert "READY" not in pages["/stage"].text
    assert "cannot verify a current handoff" in pages["/verify"].text
    assert "No current receiver handoff can be issued" in pages["/handoff"].text

    assert health.json()["status"] == "blocked"
    assert health.json()["case_lifecycle"] == "stale"
    assert direct_post.status_code == 303
    assert direct_post.headers["location"] == "/decide"
    assert "Migration Case is stale" in decide_after_post.text
    assert MigrationCaseStore(case_path).load().lifecycle is CaseLifecycle.STALE
    workflow.close()


def _write_low_risk_package(root: Path) -> None:
    (root / "objects").mkdir(parents=True)
    (root / "metadata").mkdir()
    (root / "objects" / "plain note.txt").write_text("plain", encoding="utf-8")
    (root / "metadata" / "metadata.csv").write_text(
        "filename,dc.identifier,dc.title\nobjects/plain note.txt,LOW-0001,Plain note\n",
        encoding="utf-8",
    )
