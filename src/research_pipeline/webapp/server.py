"""The app server: builds the FastAPI app, mounts static files, sets up the guard and the 404 page."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

from research_pipeline.webapp import guard, routes_feed, routes_pages
from research_pipeline.webapp.deps import HERE, get_conn, templates

__all__ = ["HERE", "create_app", "get_conn", "templates"]  # get_conn/templates re-exported for existing callers


def create_app(vault_db: Path, allowed_hosts: set[str]) -> FastAPI:
    app = FastAPI(title="Research Feed", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.vault_db = vault_db
    app.state.allowed_hosts = allowed_hosts

    @app.middleware("http")
    async def guard_middleware(request: Request, call_next):
        refusal = guard.check(request, allowed_hosts)
        return refusal if refusal is not None else await call_next(request)

    @app.exception_handler(StarletteHTTPException)
    async def not_found_page(request: Request, exc: StarletteHTTPException):
        if exc.status_code != 404:
            return await http_exception_handler(request, exc)
        message = exc.detail if exc.detail != "Not Found" else None
        return templates.TemplateResponse(request, "404.html", {"nav": None, "message": message}, status_code=404)

    app.include_router(routes_feed.router)
    app.include_router(routes_pages.router)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    return app
