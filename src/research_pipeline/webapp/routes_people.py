"""The People page: your feed's status, the directory with Follow buttons, and the feeds you follow with their Mix ticks."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from research_pipeline import library_api
from research_pipeline.webapp.deps import get_conn, templates
from research_pipeline.webapp.routes_actions import Body, _error

router = APIRouter()
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
TOPICS_SHOWN = 4  # topic tags on a closed card; the rest are counted ("+10 more") and listed when it opens


def _own_handle(request: Request) -> str | None:
    share = request.app.state.share
    return share["handle"] if share else None


@router.get("/people")
def people(request: Request, conn: Conn):
    state = request.app.state
    ctx = {
        "nav": "people", "share": state.share,
        "directory": library_api.directory(conn, state.my_topics, _own_handle(request)),
        "follows": library_api.follows(conn), "topics_shown": TOPICS_SHOWN,
    }
    return templates.TemplateResponse(request, "people.html", ctx)


@router.post("/api/feed/{handle}/follow")
def follow(handle: str, request: Request, conn: Conn):
    try:
        library_api.follow_request(conn, handle, _own_handle(request))
    except ValueError as exc:
        if "own feed" in str(exc):
            return _error(400, str(exc))
        return _error(404, "That feed isn't in the directory.")
    return JSONResponse({"following": True})


@router.post("/api/feed/{handle}/mix")
def mix(handle: str, body: Body, conn: Conn):
    on = body.get("on")
    if not isinstance(on, bool):
        return _error(400, "On must be true or false.")
    wanted = handle.lower()
    feed = next((f for f in library_api.follows(conn) if f.handle.lower() == wanted), None)
    if feed is None:
        return _error(404, "You don't follow that feed.")
    library_api.set_in_mix(conn, feed.feed_id, on)
    return JSONResponse({"in_mix": on})
