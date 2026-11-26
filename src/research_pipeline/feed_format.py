"""The files a published rpipe feed is made of, and how to read them back.

A feed is three kinds of file: ``feed.json`` (JSON Feed 1.1 with an ``_rpipe`` extension, the
last 30 days), ``archive/YYYY-MM[-N].json`` (everything older, one file per month, split when a
month is too big) and ``feed.xml`` (Atom, so ordinary feed readers work too). This module only
builds and parses bytes; it knows nothing about git, GitHub, the database or the network, so
both the pipeline (which publishes and fetches) and the app (which shows followed posts) can
import it. Never published: votes, saves, skips, the shelf, notes; reasons only of the kinds in
``PUBLIC_REASON_KINDS``.
"""

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from research_pipeline.summary_models import PaperSummary

FORMAT = 1
RECENT_DAYS = 30
FEED_MAX_ITEMS = 300
FEED_MAX_BYTES = 5_000_000
ARCHIVE_MAX_ITEMS = 2000
ARCHIVE_MAX_BYTES = 20_000_000
STOPPED_AFTER = 7  # consecutive failed fetches before a feed counts as "stopped publishing"
PUBLIC_REASON_KINDS = ("topic", "upvotes", "author", "keyword")

JSON_FEED_VERSION = "https://jsonfeed.org/version/1.1"
TITLE_MAX, ABSTRACT_MAX, CLAIM_MAX = 500, 10_000, 2_000
_ATOM = "http://www.w3.org/2005/Atom"
Short = Annotated[str, Field(max_length=200)]
_ARCHIVE_PATH = r"^archive/\d{4}-\d{2}(-\d{1,4})?\.json$"


def clean_text(text: str) -> str:
    """Text from someone else's feed made safe to print: no control characters or ANSI escapes.

    Tabs and newlines become spaces; other control, format (zero-width, bidi override) and
    line/paragraph-separator characters are dropped. Used when storing downloaded names, topics
    and titles, and again before printing them in the terminal or the digest.
    """
    out = []
    for c in text:
        if c in "\t\n\r":
            out.append(" ")
        elif unicodedata.category(c) not in ("Cc", "Cf", "Zl", "Zp"):
            out.append(c)
    return "".join(out).strip()


class FeedError(Exception):
    """A feed or archive that can't be used at all; the message is plain enough to show."""


# ---- reasons ----

_TOPIC_REASON = re.compile(r"^topic: (.+?) \(\d+(?:\.\d+)?\)$")
_PREFIX_REASONS: list[tuple[str, Literal["upvotes", "author", "keyword", "shelf"]]] = [
    ("HF upvotes: ", "upvotes"), ("followed author: ", "author"),
    ("keyword: ", "keyword"), ("near your shelf: ", "shelf"),
]


def parse_reason(text: str) -> tuple[str, str, str | None] | None:
    """Split a stored triage reason into (kind, label, paper_id); None if not recognised.

    ``disliked similar: ...`` and anything unknown give None (only what drew a paper in is explained).
    """
    if m := _TOPIC_REASON.match(text):
        return "topic", m.group(1), None
    if text.startswith("liked similar: "):
        return "liked", "", text.removeprefix("liked similar: ")
    for prefix, kind in _PREFIX_REASONS:
        if text.startswith(prefix):
            return kind, text.removeprefix(prefix), None
    return None


def public_reasons(stored: list[str]) -> list[str]:
    """The stored reasons that are safe to publish (kinds in ``PUBLIC_REASON_KINDS``), in order."""
    out = []
    for text in stored:
        parsed = parse_reason(text)
        if parsed and parsed[0] in PUBLIC_REASON_KINDS:
            out.append(text)
    return out


def _norm_topic(topic: str) -> str:
    return " ".join(topic.lower().split())


def shared_topics(theirs: list[str], mine: list[str]) -> int:
    """How many topics two people have in common (ignoring case and spacing)."""
    return len({_norm_topic(t) for t in theirs} & {_norm_topic(t) for t in mine})


# ---- the shapes of the files ----


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class PaperBlock(_Model):
    id: str = Field(max_length=200)
    title: str = Field(max_length=TITLE_MAX)
    abstract: str = Field(max_length=ABSTRACT_MAX)
    authors: list[Short] = Field(max_length=200)
    url: str = Field(max_length=2000)
    published: str | None = None


def _claim_texts(summary: PaperSummary) -> list[str]:
    s, c = summary.summary, summary.critique
    claims = [s.problem, s.approach, s.results, s.limitations, *c.strengths, *c.weaknesses]
    if s.hook:
        claims.append(s.hook)
    texts = [cl.text for cl in claims]
    texts += [t for kn in s.key_numbers for t in (kn.value, kn.label)]
    return texts + list(c.questions)


class PostMeta(_Model):
    paper: PaperBlock
    rank: int
    summary: PaperSummary | None = None
    reasons: list[Annotated[str, Field(max_length=300)]] = Field(default=[], max_length=50)
    provenance: dict | None = None

    @field_validator("provenance")
    @classmethod
    def _provenance_small(cls, v: dict | None) -> dict | None:
        if v is not None and len(json.dumps(v)) > 2000:
            raise ValueError("provenance is too big")
        return v

    @field_validator("summary")
    @classmethod
    def _claims_not_huge(cls, v: PaperSummary | None) -> PaperSummary | None:
        if v is not None and any(len(t) > CLAIM_MAX for t in _claim_texts(v) + [v.summary.one_line]):
            raise ValueError("a claim is too long")
        return v


class Item(_Model):
    id: str = Field(max_length=200)
    url: str = Field(max_length=2000)
    title: str = Field(max_length=TITLE_MAX)
    summary: str = Field(max_length=ABSTRACT_MAX)
    date_published: str = Field(max_length=40)
    rpipe: PostMeta = Field(alias="_rpipe")


class ArchiveRef(_Model):
    path: str = Field(pattern=_ARCHIVE_PATH)
    count: int
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FeedMeta(_Model):
    format: StrictInt = Field(ge=1)
    display_name: str = Field(max_length=60)
    topics: list[Annotated[str, Field(max_length=100)]] = Field(default=[], max_length=30)
    generated_at: str = Field(max_length=40)
    active: bool = True
    archives: list[ArchiveRef] = Field(default=[], max_length=500)


class FeedFile(_Model):
    version: str = JSON_FEED_VERSION
    title: str = Field(max_length=200)
    home_page_url: str | None = Field(default=None, max_length=2000)
    feed_url: str | None = Field(default=None, max_length=2000)
    items: list[Item] = []
    rpipe: FeedMeta = Field(alias="_rpipe")


@dataclass
class Post:
    """One paper as the publisher hands it over: the paper, its digest date, rank, summary, reasons."""

    paper: PaperBlock
    digest_date: str  # YYYY-MM-DD
    rank: int
    summary: PaperSummary | None
    reasons: list[str]
    provenance: dict | None


# ---- building ----


def _dumps(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _excerpt(post: Post) -> str:
    if post.summary and post.summary.summary.one_line:
        return post.summary.summary.one_line
    text = " ".join(post.paper.abstract.split())
    return text if len(text) <= 300 else text[:299].rstrip() + "…"


def _clip(text: str, limit: int) -> str:
    """Cut text to ``limit`` characters, ending with an ellipsis when something was cut."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clip_strings(value, limit: int):
    """Every string inside a dumped summary, cut to ``limit``."""
    if isinstance(value, str):
        return _clip(value, limit)
    if isinstance(value, list):
        return [_clip_strings(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: _clip_strings(v, limit) for k, v in value.items()}
    return value


def _clamped(post: Post) -> Post:
    """The post with every field cut to the published caps, so publishing never fails on size."""
    p = post.paper
    paper = PaperBlock(
        id=_clip(p.id, 200), title=_clip(p.title, TITLE_MAX), abstract=_clip(p.abstract, ABSTRACT_MAX),
        authors=[_clip(a, 200) for a in p.authors[:200]], url=p.url[:2000], published=p.published,
    )
    summary = post.summary
    if summary is not None:
        summary = PaperSummary.model_validate(_clip_strings(summary.model_dump(), CLAIM_MAX))
        summary.summary.one_line = _clip(summary.summary.one_line, CLAIM_MAX)
    provenance = post.provenance
    if provenance is not None and len(json.dumps(provenance)) > 2000:
        provenance = None
    return Post(paper=paper, digest_date=post.digest_date, rank=post.rank, summary=summary,
                reasons=[_clip(r, 300) for r in post.reasons], provenance=provenance)


def _item(post: Post) -> Item:
    post = _clamped(post)
    meta = PostMeta(paper=post.paper, rank=post.rank, summary=post.summary,
                    reasons=public_reasons(post.reasons)[:50], provenance=post.provenance)
    return Item(id=post.paper.id, url=post.paper.url, title=post.paper.title, summary=_excerpt(post),
                date_published=f"{post.digest_date}T00:00:00Z", rpipe=meta)


def _item_json(item: Item) -> dict:
    return item.model_dump(by_alias=True, mode="json")


def _newest_first(posts: list[Post]) -> list[Post]:
    """Newest digest date first; within a day by rank, then paper id (a stable order)."""
    by_rank = sorted(posts, key=lambda p: (p.rank, p.paper.id))
    return sorted(by_rank, key=lambda p: p.digest_date, reverse=True)


def _archive_files(posts: list[Post]) -> dict[str, tuple[bytes, int]]:
    """``{path: (bytes, item count)}``: one file per month, split by the item and byte caps."""
    by_month: dict[str, list[Post]] = {}
    for post in _newest_first(posts):
        by_month.setdefault(post.digest_date[:7], []).append(post)
    files: dict[str, tuple[bytes, int]] = {}
    for month, month_posts in sorted(by_month.items()):
        chunks: list[list[dict]] = [[]]
        size = 0
        for post in month_posts:
            item = _item_json(_item(post))
            item_size = len(_dumps(item)) + 1
            if chunks[-1] and (len(chunks[-1]) >= ARCHIVE_MAX_ITEMS or size + item_size > ARCHIVE_MAX_BYTES - 1000):
                chunks.append([])
                size = 0
            chunks[-1].append(item)
            size += item_size
        for n, chunk in enumerate(chunks, start=1):
            path = f"archive/{month}.json" if len(chunks) == 1 else f"archive/{month}-{n}.json"
            files[path] = (_dumps({"version": JSON_FEED_VERSION, "items": chunk}), len(chunk))
    return files


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _xml_text(text: str) -> str:
    """Drop characters XML 1.0 can't hold (one would make the whole feed.xml invalid)."""
    return _XML_BAD.sub("", text)


def _atom(feed: FeedFile, updated: str) -> bytes:
    ET.register_namespace("", _ATOM)
    root = ET.Element(f"{{{_ATOM}}}feed")
    ET.SubElement(root, f"{{{_ATOM}}}title").text = _xml_text(feed.title)
    ET.SubElement(root, f"{{{_ATOM}}}id").text = _xml_text(feed.feed_url or "urn:rpipe:feed")
    ET.SubElement(root, f"{{{_ATOM}}}updated").text = updated
    if feed.feed_url:
        ET.SubElement(root, f"{{{_ATOM}}}link", rel="self", href=_xml_text(feed.feed_url))
    for item in feed.items:
        entry = ET.SubElement(root, f"{{{_ATOM}}}entry")
        ET.SubElement(entry, f"{{{_ATOM}}}id").text = _xml_text(f"urn:rpipe:{item.id}")
        ET.SubElement(entry, f"{{{_ATOM}}}title").text = _xml_text(item.title)
        ET.SubElement(entry, f"{{{_ATOM}}}link", href=_xml_text(item.url))
        ET.SubElement(entry, f"{{{_ATOM}}}updated").text = item.date_published
        ET.SubElement(entry, f"{{{_ATOM}}}summary").text = _xml_text(item.summary)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _readme(display_name: str, address: str | None) -> bytes:
    where = f" Its address is {address}." if address else ""
    text = (f"# {display_name}'s research feed\n\nThis repository is a research feed published by "
            f"rpipe (research-pipeline): papers {display_name} found worth "
            f"reading, with short summaries. Everything here is written by `rpipe` and rewritten "
            f"each night; do not edit it by hand.{where} Follow it with "
            f"`rpipe share follow <github handle>`.\n")
    return text.encode("utf-8")


def build_files(posts: list[Post], *, display_name: str, topics: list[str], active: bool,
                now: str, base_url: str | None) -> dict[str, bytes]:
    """Every file of the feed, as ``{path: bytes}``. Same input gives the same bytes.

    ``base_url`` is the public address of the folder holding ``feed.json`` (for example
    ``https://alex.github.io/rpipe-feed/``), or None when not known.
    """
    display_name = _clip(display_name, 60)
    topics = [_clip(t, 100) for t in topics[:30]]
    archives = _archive_files(posts)
    cutoff = (date.fromisoformat(now[:10]) - timedelta(days=RECENT_DAYS)).isoformat()
    recent = [p for p in _newest_first(posts) if p.digest_date >= cutoff][:FEED_MAX_ITEMS]
    # Stamped from the newest post, not from `now`, so unchanged posts give unchanged bytes
    # (and the nightly publish has nothing to push). `now` only sets the 30-day window.
    stamp = f"{max(p.digest_date for p in posts)}T00:00:00Z" if posts else now
    base = base_url.rstrip("/") + "/" if base_url else None
    feed = FeedFile(
        title=f"{display_name}'s research feed",
        home_page_url=base, feed_url=f"{base}feed.json" if base else None,
        items=[_item(p) for p in recent],
        rpipe=FeedMeta(
            format=FORMAT, display_name=display_name, topics=topics, generated_at=stamp, active=active,
            archives=[ArchiveRef(path=path, count=count, sha256=hashlib.sha256(data).hexdigest())
                      for path, (data, count) in archives.items()],
        ),
    )
    out = {
        "feed.json": _dumps(feed.model_dump(by_alias=True, mode="json")),
        "feed.xml": _atom(feed, stamp),
        "README.md": _readme(display_name, feed.feed_url),
    }
    out.update({path: data for path, (data, _) in archives.items()})
    return out


# ---- parsing (other people's files: never trusted) ----


def _load(data: bytes, max_bytes: int, what: str) -> dict:
    if len(data) > max_bytes:
        raise FeedError(f"This {what} is too big ({len(data):,} bytes; the limit is {max_bytes:,}).")
    try:
        doc = json.loads(data)
    except (ValueError, RecursionError) as exc:
        raise FeedError(f"This {what} isn't valid JSON.") from exc
    if not isinstance(doc, dict):
        raise FeedError(f"This {what} isn't a JSON object.")
    return doc


def _items(raw) -> tuple[list[Item], int]:
    if not isinstance(raw, list):
        return [], 0
    items, dropped = [], 0
    for entry in raw:
        try:
            items.append(Item.model_validate(entry))
        except ValidationError:
            dropped += 1
    return items, dropped


def parse_feed(data: bytes) -> tuple[FeedFile, int]:
    """Read a ``feed.json``. Returns the feed and how many invalid items were dropped."""
    doc = _load(data, FEED_MAX_BYTES, "feed")
    meta = doc.get("_rpipe")
    if not isinstance(meta, dict):
        raise FeedError("This isn't an rpipe feed (no _rpipe section).")
    fmt = meta.get("format")
    if isinstance(fmt, int) and fmt > FORMAT:
        raise FeedError("This feed needs a newer rpipe.")
    items, dropped = _items(doc.get("items"))
    try:
        feed = FeedFile.model_validate({**doc, "items": []})
    except ValidationError as exc:
        raise FeedError("This feed's header is invalid.") from exc
    feed.items = items
    return feed, dropped


def parse_archive(data: bytes) -> tuple[list[Item], int]:
    """Read an archive file. Returns the valid items and how many were dropped."""
    doc = _load(data, ARCHIVE_MAX_BYTES, "archive")
    if "items" not in doc:
        raise FeedError("This archive has no items.")
    return _items(doc["items"])
