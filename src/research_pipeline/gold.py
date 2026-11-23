"""The gold set: papers you know well, with the facts a correct summary must get right.

Stored as `<vault>/gold/gold.json`. See docs/guide/evaluation.md.
"""

import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError

from research_pipeline.db import get_fulltext
from research_pipeline.profile_edit import normalise_arxiv_id, normalise_text
from research_pipeline.prompts import CHARS_PER_TOKEN, is_back_matter
from research_pipeline.vault import ConfigError

GOLD_TARGET = 10


class GoldPaper(BaseModel):
    paper_id: str
    title: str
    key_facts: list[str]  # what a correct summary must get right, written by the owner
    unreviewed: bool = False  # True when `rpipe gold auto` saved Claude's drafts without a human check


def _path(vault: Path) -> Path:
    return vault / "gold" / "gold.json"


def _key(paper_id: str) -> str:
    """Comparison key: the bare arXiv id if it is one (so 2307.15043v2 == 2307.15043)."""
    try:
        return normalise_arxiv_id(paper_id)
    except ValueError:
        return paper_id.strip()


def load_gold(vault: Path) -> list[GoldPaper]:
    path = _path(vault)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as e:
        raise ConfigError(f"{path}: cannot read file ({e.strerror or e})") from e
    try:
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("expected a list of papers")
        return [GoldPaper.model_validate(item) for item in data]
    except (ValueError, ValidationError) as e:  # JSONDecodeError and pydantic errors are ValueErrors
        raise ConfigError(f"{path}: not a valid gold set: {e}") from e


def _save(vault: Path, papers: list[GoldPaper]) -> None:
    """Write atomically: temp file in the same folder, then rename over."""
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps([p.model_dump() for p in papers], indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def has_gold(papers: list[GoldPaper], paper_id: str) -> bool:
    return any(_key(p.paper_id) == _key(paper_id) for p in papers)


def add_gold(vault: Path, paper: GoldPaper) -> bool:
    """Add a paper; False (nothing written) if one with the same id is already there."""
    papers = load_gold(vault)
    if has_gold(papers, paper.paper_id):
        return False
    paper = paper.model_copy(update={"paper_id": _key(paper.paper_id)})
    _save(vault, [*papers, paper])
    return True


def remove_gold(vault: Path, paper_id: str) -> bool:
    papers = load_gold(vault)
    kept = [p for p in papers if _key(p.paper_id) != _key(paper_id)]
    if len(kept) == len(papers):
        return False
    _save(vault, kept)
    return True


def add_fact(vault: Path, paper_id: str, fact: str) -> bool:
    """Append a key fact to a paper in the gold set; False if the paper is not there."""
    papers = load_gold(vault)
    for p in papers:
        if _key(p.paper_id) == _key(paper_id):
            p.key_facts.append(normalise_text(fact))
            _save(vault, papers)
            return True
    return False


def _fact_target(papers: list[GoldPaper], paper_id: str, number: int) -> tuple[GoldPaper | None, int]:
    """Find the paper and the 0-based fact index for a 1-based fact number (as `rpipe gold list` shows)."""
    for p in papers:
        if _key(p.paper_id) == _key(paper_id):
            if not 1 <= number <= len(p.key_facts):
                raise ValueError(f"{p.paper_id} has {len(p.key_facts)} fact(s); there is no fact {number}.")
            return p, number - 1
    return None, 0


def edit_fact(vault: Path, paper_id: str, number: int, text: str) -> str | None:
    """Replace fact `number` (1-based) of a paper; returns the old text, None if the paper is not there.

    Raises ValueError for a number out of range or empty text. Nothing is written on error.
    """
    text = normalise_text(text)
    if not text:
        raise ValueError("The fact is empty.")
    papers = load_gold(vault)
    p, i = _fact_target(papers, paper_id, number)
    if p is None:
        return None
    old = p.key_facts[i]
    p.key_facts[i] = text
    _save(vault, papers)
    return old


def remove_fact(vault: Path, paper_id: str, number: int) -> str | None:
    """Delete fact `number` (1-based); returns the removed text, None if the paper is not there.

    Raises ValueError for a number out of range, or if it is the paper's only fact
    (a gold paper needs at least one; use `rpipe gold remove` to drop the paper).
    """
    papers = load_gold(vault)
    p, i = _fact_target(papers, paper_id, number)
    if p is None:
        return None
    if len(p.key_facts) == 1:
        raise ValueError(f"That is the only fact for {p.paper_id}; a gold paper needs at least one. Remove the paper instead: rpipe gold remove {p.paper_id}")
    old = p.key_facts.pop(i)
    _save(vault, papers)
    return old


@dataclass(frozen=True)
class Suggestion:
    paper_id: str
    title: str
    tokens: int  # approximate length of the text a summary is built from (back matter excluded)


def suggest_papers(conn: sqlite3.Connection, gold: list[GoldPaper], count: int = GOLD_TARGET) -> list[Suggestion]:
    """Pick `count` summarized arXiv papers with full text, spread evenly across lengths.

    Always includes the shortest and the longest (when count >= 2); a single pick is the median.
    """
    rows = conn.execute(
        "SELECT p.id, p.title FROM papers p"
        " WHERE EXISTS (SELECT 1 FROM summaries s WHERE s.paper_id = p.id)"
        " AND EXISTS (SELECT 1 FROM fulltexts f WHERE f.paper_id = p.id)"
        " ORDER BY p.id"
    ).fetchall()
    candidates: list[Suggestion] = []
    for row in rows:
        try:
            normalise_arxiv_id(row["id"])  # gold draft fetches from arXiv, so only arXiv ids are useful
        except ValueError:
            continue
        if has_gold(gold, row["id"]):
            continue
        sections = get_fulltext(conn, row["id"]) or []
        chars = sum(len(s.text) for s in sections if not is_back_matter(s.title))
        candidates.append(Suggestion(row["id"], row["title"], chars // CHARS_PER_TOKEN))
    candidates.sort(key=lambda s: (s.tokens, s.paper_id))
    n = len(candidates)
    if n <= count:
        return candidates
    if count == 1:
        return [candidates[(n - 1) // 2]]
    indices = sorted({round(i * (n - 1) / (count - 1)) for i in range(count)})
    return [candidates[i] for i in indices]
