"""The app server: builds the FastAPI app, mounts static files, sets up templates and the guard."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI, Request
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from research_pipeline import db
from research_pipeline.webapp import guard

HERE = Path(__file__).parent

# Autoescape is on for every template, so paper titles and abstracts cannot inject markup.
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(default=True))
)


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one database connection per request, closed afterwards."""
    conn = db.open_db(request.app.state.vault_db)
    try:
        yield conn
    finally:
        conn.close()


def create_app(vault_db: Path, allowed_hosts: set[str]) -> FastAPI:
    app = FastAPI(title="Research Feed", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.vault_db = vault_db
    app.state.allowed_hosts = allowed_hosts

    @app.middleware("http")
    async def guard_middleware(request: Request, call_next):
        refusal = guard.check(request, allowed_hosts)
        return refusal if refusal is not None else await call_next(request)

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    return app
