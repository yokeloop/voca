from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from voca.adapters.control import create_control_router
from voca.adapters.sqlite import SQLiteSessionStore
from voca.config import Settings, load_settings
from voca.core.session import SessionService
from voca.diagnostics import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        diagnostics = configure_logging(
            configuration.log_dir, configuration.log_level, configuration.log_levels
        )
        diagnostics.log_event("app", "app.startup", "started")
        store: SQLiteSessionStore | None = None
        try:
            store = SQLiteSessionStore.open(configuration.db_path, diagnostics)
            app.state.diagnostics = diagnostics
            app.state.store = store
            app.state.service = SessionService(store)
            diagnostics.log_event("app", "app.startup", "succeeded")
            yield
        except BaseException:
            diagnostics.log_event(
                "app", "app.startup", "failed", reason="startup_failed"
            )
            raise
        finally:
            if store is not None:
                store.close()
            diagnostics.log_event("app", "app.shutdown", "succeeded")
            diagnostics.close()

    app = FastAPI(lifespan=lifespan)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, error: Exception) -> JSONResponse:
        del error
        diagnostics = getattr(request.app.state, "diagnostics", None)
        if diagnostics is not None:
            diagnostics.log_event(
                "app", "http.session_create", "failed", reason="internal_error"
            )
        return JSONResponse(
            {
                "error": {
                    "component": "app",
                    "code": "internal_error",
                    "message": "Internal error",
                }
            },
            status_code=500,
        )

    app.include_router(create_control_router())
    return app
