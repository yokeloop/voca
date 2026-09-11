from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from voca.application.sessions import SessionService
from voca.bootstrap.settings import Settings, load_settings
from voca.infrastructure.observability.logging import configure_logging
from voca.infrastructure.sqlite import SQLiteSessionStore
from voca.presentation.control import create_control_router
from voca.presentation.http import create_http_router, internal_error


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
            app.state.service = SessionService(store, diagnostics)
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
    app.add_exception_handler(Exception, internal_error)
    app.include_router(create_http_router())
    app.include_router(create_control_router())
    return app
