"""The shelf: your own notes and writing in ``my_work/``, chunked and embedded locally.

Files are the source of truth; the database rows are a rebuildable index.
Nothing here talks to the network or the language model. See docs/guide/shelf.md.
"""

import difflib
import hashlib
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from research_pipeline import db
from research_pipeline.embed import Embedder

MIN_CHUNK_CHARS = 200
MAX_CHUNK_CHARS = 1500
EXTENSIONS = (".md", ".txt")
_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


# ---- chunking ----


def _split_long(text: str, limit: int) -> list[str]:
    """Pack sentences into pieces of at most ``limit`` chars; hard-cut any sentence longer than that."""
    pieces: list[str] = []
    cur = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > limit:  # no sentence boundary to use
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if cur and len(cur) + 1 + len(sentence) > limit:
            pieces.append(cur)
            cur = sentence
        else:
            cur = f"{cur} {sentence}" if cur else sentence
    if cur:
        pieces.append(cur)
    return pieces


def _section_bodies(paragraphs: list[str], limit: int) -> list[str]:
    """Merge short neighbouring paragraphs (under MIN, within ``limit``); split over-long ones."""
    bodies: list[str] = []
    cur = ""
    for para in paragraphs:
        for piece in _split_long(para, limit) if len(para) > limit else [para]:
            if cur and len(cur) < MIN_CHUNK_CHARS and len(cur) + 2 + len(piece) <= limit:
                cur = f"{cur}\n\n{piece}"
            else:
                if cur:
                    bodies.append(cur)
                cur = piece
    if cur:
        bodies.append(cur)
    return bodies


def chunk_text(name: str, text: str) -> list[str]:
    """Split a note into chunks to embed, each led by its name and nearest heading."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for block in re.split(r"\n\s*\n", text):
        lines: list[str] = []
        for line in block.splitlines():
            m = _HEADING.match(line.strip())
            if m:
                if lines:
                    sections[-1][1].append("\n".join(lines).strip())
                    lines = []
                sections.append((m.group(1), []))
            else:
                lines.append(line)
        if "".join(lines).strip():
            sections[-1][1].append("\n".join(lines).strip())
    chunks: list[str] = []
    for heading, paragraphs in sections:
        if not paragraphs:
            continue
        prefix = f"{name} / {heading}\n" if heading else f"{name}\n"
        limit = max(MAX_CHUNK_CHARS - len(prefix), 100)
        chunks += [prefix + body for body in _section_bodies(paragraphs, limit)]
    return chunks


# ---- sync ----


def _shelf_files(my_work: Path) -> tuple[dict[str, Path], list[str]]:
    """Indexable files by item name (file stem), and the names of files we skip."""
    files: dict[str, Path] = {}
    ignored: list[str] = []
    if not my_work.is_dir():
        return files, ignored
    for p in sorted(my_work.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        if p.suffix.lower() in EXTENSIONS and p.stem not in files:
            files[p.stem] = p
        else:
            ignored.append(p.name)
    return files, ignored


def _has_embeddings(conn: sqlite3.Connection, name: str, n_chunks: int, model: str) -> bool:
    ids = [db.shelf_embedding_id(name, i) for i in range(n_chunks)]
    found = 0
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        found += conn.execute(
            f"SELECT COUNT(*) FROM embeddings WHERE embedding_model = ? AND item_id IN ({','.join('?' * len(batch))})",
            (model, *batch),
        ).fetchone()[0]
    return found == n_chunks


def sync_shelf(conn: sqlite3.Connection, my_work: Path, embedder: Embedder) -> SyncResult:
    """Bring the database in line with ``my_work/``: add, re-index, or drop items. Commits."""
    res = SyncResult()
    files, res.ignored = _shelf_files(my_work)
    existing = {r["name"]: r for r in db.list_shelf_items(conn)}
    for name, path in files.items():
        try:
            data = path.read_bytes()
        except OSError as exc:  # unreadable or vanished mid-sync: skip it, keep the rest going
            res.ignored.append(f"{path.name} (could not be read: {exc.strerror or exc})")
            continue
        digest = hashlib.sha256(data).hexdigest()
        row = existing.get(name)
        if (
            row
            and row["content_hash"] == digest
            and _has_embeddings(conn, name, row["n_chunks"], embedder.model_name)
        ):
            continue
        chunks = chunk_text(name, data.decode("utf-8", errors="replace"))
        db.upsert_shelf_item(conn, name, digest, chunks)
        if chunks:
            vecs = np.asarray(embedder.embed(chunks), dtype=np.float32)
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings (item_id, embedding_model, vector, dim)"
                " VALUES (?, ?, ?, ?)",
                [
                    (db.shelf_embedding_id(name, i), embedder.model_name, v.tobytes(), len(v))
                    for i, v in enumerate(vecs)
                ],
            )
        (res.updated if row else res.added).append(name)
    for name in existing:
        if name not in files:
            db.delete_shelf_item(conn, name, commit=False)
            res.removed.append(name)
    conn.commit()
    return res


# ---- add / remove ----


def _taken_stems(my_work: Path) -> set[str]:
    return {p.stem.casefold() for p in my_work.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS}


def _unique(stem: str, taken: set[str], folder: Path | None = None, suffix: str = "") -> str:
    """First of ``stem``, ``stem-2``, ... whose casefolded name is not in ``taken``.

    With ``folder``, also skip any name whose file already exists there, so a case-insensitive
    filesystem (where ``Idea.md`` and ``idea.md`` are one file) can never be overwritten.
    """
    def free(cand: str) -> bool:
        return cand.casefold() not in taken and not (folder is not None and (folder / f"{cand}{suffix}").exists())

    if free(stem):
        return stem
    n = 2
    while not free(f"{stem}-{n}"):
        n += 1
    return f"{stem}-{n}"


def _existing_file(source: str) -> Path | None:
    try:
        p = Path(source).expanduser()
        return p if p.is_file() else None
    except (OSError, ValueError):  # a long note is not a valid path
        return None


def add_to_shelf(conn: sqlite3.Connection, my_work: Path, embedder: Embedder, source: str) -> str:
    """Add a file (copied) or a note (written as ``.md``) to the shelf; return the item name."""
    my_work.mkdir(parents=True, exist_ok=True)
    src = _existing_file(source)
    if src is not None:
        if src.suffix.lower() not in EXTENSIONS:
            raise ValueError(
                f"{src.name}: the shelf takes .md and .txt files only. Convert it to text, "
                "or paste the text with `rpipe shelf add \"your note\"`."
            )
        suffix = src.suffix.lower()
        name = _unique(src.stem, _taken_stems(my_work), my_work, suffix)
        with open(src, "rb") as fin, open(my_work / f"{name}{suffix}", "xb") as fout:  # "x": never overwrite
            shutil.copyfileobj(fin, fout)
    else:
        if not source.strip():
            raise ValueError("Nothing to add. Give a file path or the note text: `rpipe shelf add \"your note\"`.")
        slug = "-".join(re.findall(r"[a-z0-9]+", source.lower())[:6]) or "note"
        name = _unique(slug, _taken_stems(my_work), my_work, ".md")
        with open(my_work / f"{name}.md", "x", encoding="utf-8") as fh:  # "x": never overwrite
            fh.write(source.strip() + "\n")
    sync_shelf(conn, my_work, embedder)
    return name


def remove_from_shelf(conn: sqlite3.Connection, my_work: Path, name: str) -> list[str]:
    """Move every file of the item to ``my_work/.removed/`` (never deleting) and drop it from the index.

    Returns the names the files now have in ``.removed/``. All ``.md``/``.txt`` files with that
    stem move, otherwise a leftover ``a.txt`` would come back as item ``a`` at the next sync.
    """
    files, _ = _shelf_files(my_work)
    known = {r["name"] for r in db.list_shelf_items(conn)}
    if name not in files and name not in known:
        matches = difflib.get_close_matches(name, sorted(files.keys() | known), n=3)
        hint = f" Close matches: {', '.join(matches)}." if matches else ""
        raise KeyError(f"No shelf item named '{name}'.{hint} List them with `rpipe shelf list`.")
    moved: list[str] = []
    if my_work.is_dir():
        paths = [
            p for p in sorted(my_work.iterdir())
            if p.is_file() and not p.name.startswith(".") and p.stem == name and p.suffix.lower() in EXTENSIONS
        ]
        if paths:
            trash = my_work / ".removed"
            trash.mkdir(exist_ok=True)
            for path in paths:
                suffix = path.suffix.lower()
                taken = {p.stem.casefold() for p in trash.iterdir() if p.suffix.lower() == suffix}
                dest = _unique(path.stem, taken, trash, suffix)
                shutil.move(str(path), trash / f"{dest}{suffix}")
                moved.append(f"{dest}{suffix}")
    db.delete_shelf_item(conn, name)
    return moved
