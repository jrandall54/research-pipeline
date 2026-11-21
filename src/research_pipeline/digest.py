"""Digest stage: render the day's kept papers as a markdown file in the vault."""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from research_pipeline.db import mark_in_digest
from research_pipeline.models import Status

MAX_AUTHORS = 5
_ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})$")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _link_text(title: str) -> str:
    return _one_line(title).replace("[", r"\[").replace("]", r"\]")


def _link_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _authors(raw: str) -> str:
    names = json.loads(raw)
    shown = ", ".join(names[:MAX_AUTHORS])
    return f"{shown} et al." if len(names) > MAX_AUTHORS else shown


def _render(n: int, p: sqlite3.Row) -> str:
    lines = [f"## {n}. [{_link_text(p['title'])}]({_link_url(p['url'])})", ""]
    authors = _authors(p["authors"])
    if authors:
        lines += [f"**Authors:** {authors}", ""]
    lines += [f"**Why it matched** (score {p['triage_score'] or 0.0:.2f})", ""]
    lines += [f"- {r}" for r in json.loads(p["triage_reasons"] or "[]")]
    lines += ["", p["abstract"].strip() or "_No abstract available._", ""]
    links = [f"[abs]({_link_url(p['url'])})"]
    if _ARXIV_ID.match(p["id"]):
        links.append(f"[PDF](https://arxiv.org/pdf/{p['id']})")
    if p["hf_upvotes"]:
        links.append(f"HF upvotes: {p['hf_upvotes']}")
    links += [f"[repo]({_link_url(r)})" for r in json.loads(p["repos"])]
    lines += [" · ".join(links), ""]
    return "\n".join(lines)


def write_digest(
    conn: sqlite3.Connection, vault: Path, digest_date: date, run_summary: str
) -> Path:
    """Write `<vault>/digests/YYYY-MM-DD.md` and mark its papers `in_digest`.

    Includes `triaged` papers plus papers already `in_digest` for this same date,
    so a same-day re-run rewrites a complete file. See docs/guide/digest.md.
    """
    papers = conn.execute(
        "SELECT * FROM papers WHERE status = ? OR (status = ? AND digest_date = ?)"
        " ORDER BY triage_score DESC, id ASC",
        (Status.triaged.value, Status.in_digest.value, digest_date.isoformat()),
    ).fetchall()

    parts = [run_summary.strip(), "", f"# Research digest — {digest_date.isoformat()}", ""]
    if papers:
        parts += [_render(i, p) for i, p in enumerate(papers, 1)]
    else:
        parts += ["Nothing new matched your profile today.", ""]

    out = vault / "digests" / f"{digest_date.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    mark_in_digest(conn, [p["id"] for p in papers], digest_date)
    return out
