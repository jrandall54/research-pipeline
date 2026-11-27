"""Digest stage: render the day's kept papers as a markdown file in the vault."""

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from research_pipeline.db import get_provenance, get_summary, mark_in_digest
from research_pipeline.library import current_votes, is_saved
from research_pipeline.models import Status
from research_pipeline.summarize import Claim, PaperSummary

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


def _claim(c: Claim, unverified: list[str], label: str = "") -> str:
    flag = "⚠ unverified " if c.text in unverified else ""
    cited = ", ".join(_one_line(s) for s in c.sections)
    return f"- {label}{flag}{_one_line(c.text)} [§ {cited}]"


def render_summary(ps: PaperSummary) -> list[str]:
    s, c, bad = ps.summary, ps.critique, ps.unverified
    lines = [f"**Summary:** {_one_line(s.one_line)}", ""]  # what the paper is about comes first (Phase 9.4)
    if s.hook is not None:
        cited = ", ".join(_one_line(x) for x in s.hook.sections)
        lines += [f"**Hook:** {_one_line(s.hook.text)} [§ {cited}]", ""]
    lines += [
        _claim(s.problem, bad, "**Problem:** "),
        _claim(s.approach, bad, "**Approach:** "),
        _claim(s.results, bad, "**Results:** "),
        _claim(s.limitations, bad, "**Limitations:** "),
        "",
    ]
    if c.strengths:
        lines += ["**Strengths**", ""] + [_claim(x, bad) for x in c.strengths] + [""]
    if c.weaknesses:
        lines += ["**Weaknesses**", ""] + [_claim(x, bad) for x in c.weaknesses] + [""]
    if c.questions:
        lines += ["**Questions to ask**", ""] + [f"- {_one_line(q)}" for q in c.questions] + [""]
    notes = []
    if ps.basis == "abstract":
        notes.append("Summary based on the abstract only (full text unavailable)")
    if ps.body_cut:
        notes.append("the paper was too long for the model, so part of it was left out")
    if notes:
        lines += [f"_{'; '.join(notes)}._", ""]
    return lines


def _provenance_line(prov: dict | None) -> list[str]:
    """The small italic footer; any missing field is left out, and no fields means no line."""
    if not prov:
        return []
    parts = []
    if prov.get("backend"):
        parts.append(str(prov["backend"]))
    if prov.get("model"):
        parts.append(str(prov["model"]))
    if prov.get("prompt_version"):
        parts.append(f"prompt {prov['prompt_version']}")
    if prov.get("paper_version") is not None:
        parts.append(f"paper v{prov['paper_version']}")
    day = str(prov.get("created_at") or "")[:10]
    if day:
        parts.append(day)
    return [f"_{' · '.join(parts)}_", ""] if parts else []


def _id_line(paper_id: str, saved: bool, vote: int) -> str:
    marks = (["★ saved"] if saved else []) + (["👍"] if vote > 0 else ["👎"] if vote < 0 else [])
    return " · ".join([f"id: {paper_id}", *marks])


def _render(
    n: int,
    p: sqlite3.Row,
    ps: PaperSummary | None = None,
    saved: bool = False,
    vote: int = 0,
    prov: dict | None = None,
) -> str:
    lines = [f"## {n}. [{_link_text(p['title'])}]({_link_url(p['url'])})", ""]
    lines += [_id_line(p["id"], saved, vote), ""]
    authors = _authors(p["authors"])
    if authors:
        lines += [f"**Authors:** {authors}", ""]
    lines += [f"**Why it matched** (score {p['triage_score'] or 0.0:.2f})", ""]
    lines += [f"- {r}" for r in json.loads(p["triage_reasons"] or "[]")]
    lines += ["", p["abstract"].strip() or "_No abstract available._", ""]
    if ps is not None:
        lines += render_summary(ps) + _provenance_line(prov)
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

    Includes `triaged`, `fetched` and `summarized` papers (with their summary when they have one) plus papers already `in_digest` for this same date,
    so a same-day re-run rewrites a complete file. See docs/guide/digest.md.
    """
    papers = conn.execute(
        "SELECT * FROM papers WHERE status IN (?, ?, ?) OR (status = ? AND digest_date = ?)"
        " ORDER BY triage_score DESC, id ASC",
        (
            Status.triaged.value,
            Status.fetched.value,
            Status.summarized.value,
            Status.in_digest.value,
            digest_date.isoformat(),
        ),
    ).fetchall()

    parts = [run_summary.strip(), "", f"# Research digest — {digest_date.isoformat()}", ""]
    if papers:
        votes = {r["paper_id"]: r["vote"] for r in current_votes(conn)}
        parts += [
            _render(i, p, get_summary(conn, p["id"]), is_saved(conn, p["id"]), votes.get(p["id"], 0),
                    get_provenance(conn, p["id"]))
            for i, p in enumerate(papers, 1)
        ]
        first = papers[0]["id"]
        parts += ["---", "", f"`rpipe vote {first} up|down · rpipe save {first}`", ""]
    else:
        parts += ["Nothing new matched your profile today.", ""]

    out = vault / "digests" / f"{digest_date.isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    mark_in_digest(conn, [p["id"] for p in papers], digest_date)
    return out
