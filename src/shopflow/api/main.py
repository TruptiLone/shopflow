import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shopflow.api.routers import carts, catalog, health, orders
from shopflow.core.config import get_settings
from shopflow.core.db import dispose_engine, get_engine
from shopflow.core.errors import DomainError
from shopflow.core.logging import configure_logging, request_id_var
from shopflow.ports.cache import NoopCache
from shopflow.ports.search import PostgresSearch

log = logging.getLogger("shopflow.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    get_engine()
    # Adapters are wired here so later phases swap them (Redis, OpenSearch) in one place.
    app.state.cache = NoopCache()
    app.state.search = PostgresSearch()
    log.info("startup env=%s", settings.environment)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Shopflow API",
        version="0.1.0",
        description="E-commerce backend on the Olist dataset: catalog, cart and orders.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
        finally:
            request_id_var.reset(token)
        return response

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code
        )

    app.include_router(health.router)
    for r in (catalog.router, carts.router, orders.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
