"""Shared by the app server and its routers: the template environment and the per-request database."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.templating import Jinja2Templates

from research_pipeline import db

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
