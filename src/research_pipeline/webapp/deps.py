"""Shared by the app server and its routers: the template environment and the per-request database."""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.templating import Jinja2Templates

from research_pipeline import db
from research_pipeline.webapp import diagram_svg, present

HERE = Path(__file__).parent

# Autoescape is on for every template, so paper titles and abstracts cannot inject markup.
templates = Jinja2Templates(
    env=Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(default=True))
)
# Splits a summary field into points, so no field shows as one long block (Phase 8.1).
templates.env.filters["sentences"] = present.sentences
# Draws a paper's stored diagram as inline SVG (Phase 9).
templates.env.filters["diagram_svg"] = diagram_svg.render


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: one database connection per request, closed afterwards.

    Opening, using and closing can land on different worker threads, so the same-thread check is
    off; the request still uses the connection strictly one step at a time.
    """
    conn = db.open_db(request.app.state.vault_db, check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
