"""Loopback-only FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from name_atlas.config import RuntimeConfig

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=PACKAGE_ROOT / "templates")


def create_app(config: RuntimeConfig) -> FastAPI:
    """Create the local application with safe startup diagnostics."""

    app = FastAPI(
        title="Reversible Name Atlas",
        description="Refactor the collection. Preserve every identity.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )
    app.state.runtime_config = config
    app.mount(
        "/static",
        StaticFiles(directory=PACKAGE_ROOT / "static"),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "config": config,
                "diagnostics": config.safe_diagnostics(),
            },
        )

    @app.get("/healthz")
    async def health() -> dict[str, str | bool]:
        ready = config.mode.value == "replay" or config.api_key_configured
        return {
            "status": "ready" if ready else "blocked",
            "mode": config.mode.value,
            "model": config.model,
            "api_key_configured": config.api_key_configured,
        }

    return app
