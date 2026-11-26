"""The taps that change things: vote, save and ask for a summary. Every route is one library_api call."""

import sqlite3
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from research_pipeline import ids, library_api
from research_pipeline.db import UnknownPaperError
from research_pipeline.webapp.deps import get_conn

router = APIRouter(prefix="/api/paper")
Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
_NOT_FOUND = "That paper isn't in your library."
_BUSY = "Your library is busy. Try again in a moment."
_VOTES = {"up": 1, "down": -1, "clear": 0}


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _json_body(request: Request) -> dict:
    """The JSON object the page sent, or {} for anything else (the routes then answer 400)."""
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


Body = Annotated[dict, Depends(_json_body)]


def _run(paper_id: str, action: Callable[[str], dict]) -> JSONResponse:
    """Run one library_api call on the normalised id, turning its failures into the JSON errors the page shows."""
    try:
        pid = ids.normalise_paper_id(paper_id)
    except ValueError:
        return _error(404, _NOT_FOUND)
    try:
        return JSONResponse(action(pid))
    except UnknownPaperError:
        return _error(404, _NOT_FOUND)
    except sqlite3.OperationalError:
        return _error(503, _BUSY)


@router.post("/{paper_id}/vote")
def vote(paper_id: str, body: Body, conn: Conn):
    direction = body.get("direction")
    if direction not in _VOTES:
        return _error(400, "Direction must be up, down or clear.")

    def act(pid: str) -> dict:
        library_api.vote(conn, pid, direction)
        return {"vote": _VOTES[direction]}

    return _run(paper_id, act)


@router.post("/{paper_id}/save")
def save(paper_id: str, body: Body, conn: Conn):
    saved = body.get("saved")
    if not isinstance(saved, bool):
        return _error(400, "Saved must be true or false.")

    def act(pid: str) -> dict:
        library_api.save(conn, pid, saved)
        return {"saved": saved}

    return _run(paper_id, act)


@router.post("/{paper_id}/summarize")
def summarize(paper_id: str, conn: Conn):
    def act(pid: str) -> dict:
        library_api.request(conn, "summarize", {"paper_id": pid})
        return {"state": library_api.summarize_states(conn, [pid]).get(pid) or "pending"}

    return _run(paper_id, act)
