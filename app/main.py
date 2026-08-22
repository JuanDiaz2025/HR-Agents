from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import agent_page, bookings, health, interviews, realtime_ws, webhooks
from app.config import get_settings
from app.db import init_db
from app.logging_config import configure_logging
from app.runtime import build_runtime

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    init_db()
    app.state.runtime = build_runtime(settings)
    log.info(
        "%s ready — env=%s public_base_url=%s voice=%s",
        settings.app_name,
        settings.env,
        settings.public_base_url,
        settings.voice_output_mode,
    )
    if settings.env == "prod" and settings.internal_api_key == "dev-internal-key":
        log.error("INTERNAL_API_KEY is still the default value in production")
    try:
        yield
    finally:
        await app.state.runtime.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description=(
            "AI screening interviewer for Google Meet: Recall.ai bot + Claude "
            "interview engine + Google Sheets / monday.com automation."
        ),
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(bookings.router)
    app.include_router(interviews.router)
    app.include_router(webhooks.router)
    app.include_router(realtime_ws.router)
    app.include_router(agent_page.router)
    return app


app = create_app()
