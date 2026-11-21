"""Command line interface."""

import json
import sqlite3
import sys
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from research_pipeline.collect import run_collect
from research_pipeline.db import finish_run, open_db, start_run
from research_pipeline.digest import write_digest
from research_pipeline.embed import Embedder, SentenceTransformerEmbedder
from research_pipeline.models import Config, PaperMeta, Profile
from research_pipeline import profile_edit
from research_pipeline.seed_input import find_seed_candidates, parse_seed_input
from research_pipeline.seeds import SeedFetchError, cache_seed, resolve_seed_texts
from research_pipeline.sources.arxiv import ArxivSource
from research_pipeline.sources.base import Source, SourceError
from research_pipeline.sources.hf_daily import HfDailySource
from research_pipeline.triage import run_triage
from research_pipeline.vault import (
    DEFAULT_CATEGORIES,
    ConfigError,
    init_vault,
    load_config,
    load_profile,
    resolve_vault,
)

app = typer.Typer(help="Nightly research paper pipeline.", no_args_is_help=True)

VaultOption = Annotated[
    Path | None,
    typer.Option(help="Vault folder (default: $RESEARCH_VAULT or ~/research-vault)."),
]


@dataclass
class Pipeline:
    """The pieces that touch the outside world; tests swap them for stubs."""

    sources: list[Source]
    make_embedder: Callable[[], Embedder]  # called only by run and triage (may raise RuntimeError)
    seed_fetcher: Callable[[list[str]], list[PaperMeta]]


def build_pipeline(config: Config) -> Pipeline:
    """Real sources, the real embedding model, and arXiv lookup for seed papers."""
    arxiv = ArxivSource(config.arxiv_categories, max_results=config.arxiv_max_results)
    return Pipeline(
        sources=[arxiv, HfDailySource()],
        make_embedder=lambda: SentenceTransformerEmbedder(config.embedding_model),
        seed_fetcher=arxiv.fetch_by_ids,
    )


@app.callback()
def main() -> None:
    """Nightly research paper pipeline."""


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


@app.command()
def init(
    vault: Annotated[
        Path | None, typer.Option(help="Vault folder (default: $RESEARCH_VAULT or ~/research-vault).")
    ] = None,
) -> None:
    """Create the vault folders, config.yaml, profile.yaml and database (safe to re-run)."""
    path = resolve_vault(vault)
    topics = categories = None
    if _is_tty():
        entered = typer.prompt("Topics you care about (comma-separated, blank for examples)", default="")
        topics = _split(entered) or None
        entered = typer.prompt("arXiv categories (comma-separated)", default=", ".join(DEFAULT_CATEGORIES))
        categories = _split(entered) or None
    existed = {n for n in ("config.yaml", "profile.yaml") if (path / n).exists()}
    init_vault(path, topics=topics, categories=categories)
    typer.echo(f"Vault ready at {path}")
    for name in ("config.yaml", "profile.yaml"):
        typer.echo(f"  {name}: {'kept existing' if name in existed else 'created'}")
    typer.echo("  vault.sqlite, raw/, markdown/, summaries/, my_work/, gold/, digests/, cache/ ready")
    typer.echo("\nNext steps:")
    typer.echo("  1. Add 10-20 papers you like: rpipe profile add-seed <arXiv link, id or title>")
    typer.echo("  2. Check your profile: rpipe profile show")
    typer.echo("  3. Change topics, authors, keywords, top-N or categories: rpipe profile --help")


@dataclass
class _Context:
    vault: Path
    config: Config
    conn: sqlite3.Connection


def _fail(message: str) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code=1)


def _open(vault: Path | None) -> _Context:
    path = resolve_vault(vault)
    if not (path / "config.yaml").exists():
        raise _fail(f"No vault found at {path} (no config.yaml). Run `rpipe init` first.")
    try:
        config = load_config(path / "config.yaml")
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    return _Context(path, config, open_db(path / "vault.sqlite"))


def _load_profile(ctx: _Context) -> Profile:
    try:
        return load_profile(ctx.vault / "profile.yaml")
    except ConfigError as exc:
        raise _fail(str(exc)) from exc


def _embedder(pipeline: Pipeline, model_name: str) -> Embedder:
    try:
        return pipeline.make_embedder()
    except RuntimeError as exc:  # e.g. the `embed` extra is not installed (message has the hint)
        raise _fail(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - failed download, broken torch install, ...
        raise _fail(f"could not load embedding model {model_name}: {exc}") from exc


def _seed_texts(
    ctx: _Context, profile: Profile, pipeline: Pipeline, errors: list[str]
) -> dict[str, str]:
    """Seed texts for triage; a failed lookup is appended to `errors`, never fatal."""
    cache = ctx.vault / "cache" / "seeds.json"
    try:
        texts = resolve_seed_texts(ctx.conn, cache, profile.seeds, pipeline.seed_fetcher)
    except SeedFetchError as exc:
        errors.append(str(exc))
        return exc.texts
    unresolved = [s for s in profile.seeds if s not in texts]
    if unresolved:
        errors.append(f"seeds: no paper found for {', '.join(unresolved)}")
    return texts


def health_line(day: date, counts: dict[str, int], errors: list[str]) -> str:
    detail = "; ".join(" ".join(e.split()) for e in errors) or "none"
    return (
        f"Run {day.isoformat()}: collected {counts.get('inserted', 0)} new, "
        f"{counts.get('updated', 0)} updated · kept {counts.get('triaged', 0)} · "
        f"skipped {counts.get('skipped', 0)} · errors: {detail}"
    )


def _report(counts: dict[str, int], errors: list[str]) -> None:
    typer.echo("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for e in errors:
        typer.echo(f"  error: {e}", err=True)


@app.command()
def run(vault: VaultOption = None) -> None:
    """Collect, triage and write today's digest (the nightly command)."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    pipeline = build_pipeline(ctx.config)
    embedder = _embedder(pipeline, ctx.config.embedding_model)
    run_id = start_run(ctx.conn)
    counts, errors = run_collect(ctx.conn, pipeline.sources, run_id)
    seeds = _seed_texts(ctx, profile, pipeline, errors)
    counts |= run_triage(ctx.conn, embedder, profile, seeds)
    today = date.today()
    out = write_digest(ctx.conn, ctx.vault, today, health_line(today, counts, errors))
    finish_run(ctx.conn, run_id, counts, errors)
    typer.echo(f"Digest written to {out}")
    _report(counts, errors)


@app.command()
def collect(vault: VaultOption = None) -> None:
    """Fetch new papers from every source into the database."""
    ctx = _open(vault)
    pipeline = build_pipeline(ctx.config)
    counts, errors = run_collect(ctx.conn, pipeline.sources)
    typer.echo("Collected:")
    _report(counts, errors)


@app.command()
def triage(vault: VaultOption = None) -> None:
    """Rank `new` papers against your profile; keep the top N."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    pipeline = build_pipeline(ctx.config)
    embedder = _embedder(pipeline, ctx.config.embedding_model)
    errors: list[str] = []
    seeds = _seed_texts(ctx, profile, pipeline, errors)
    counts = run_triage(ctx.conn, embedder, profile, seeds)
    typer.echo("Triaged:")
    _report(counts, errors)


@app.command()
def digest(
    vault: VaultOption = None,
    date_: Annotated[
        str | None, typer.Option("--date", help="Digest date, YYYY-MM-DD (default: today).")
    ] = None,
) -> None:
    """Write the markdown digest from papers already triaged."""
    ctx = _open(vault)
    try:
        day = date.fromisoformat(date_) if date_ else date.today()
    except ValueError as exc:
        raise _fail(f"--date must be YYYY-MM-DD, got {date_!r}") from exc
    out = write_digest(ctx.conn, ctx.vault, day, f"Digest {day.isoformat()}: written from stored papers (no collection run)")
    typer.echo(f"Digest written to {out}")


# ---------------------------------------------------------------- profile commands

profile_app = typer.Typer(help="View and change your profile without editing files.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")


def make_arxiv(config: Config) -> ArxivSource:
    """The arXiv client used by `profile add-seed`; tests replace this with a stub."""
    return ArxivSource(config.arxiv_categories, max_results=config.arxiv_max_results)


@contextmanager
def _clean_errors() -> Iterator[None]:
    """Show bad input and invalid files as one message and exit 1, never a traceback."""
    try:
        yield
    except (ValueError, ConfigError) as exc:
        raise _fail(str(exc)) from exc


def _seed_titles(vault: Path) -> dict[str, str]:
    try:
        cache = json.loads((vault / "cache" / "seeds.json").read_text("utf-8"))
        return {k: v.split("\n", 1)[0] for k, v in cache.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _numbered(items: list[str]) -> str:
    return "\n".join(f"  {i}. {x}" for i, x in enumerate(items, 1)) or "  (none)"


@profile_app.command("show")
def profile_show(vault: VaultOption = None) -> None:
    """Print your topics, seeds, authors, keywords, top-N and categories."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    titles = _seed_titles(ctx.vault)
    seeds = [f"{s}  {titles.get(s, '(title not cached)')}" for s in profile.seeds]
    typer.echo(f"Topics:\n{_numbered(profile.topics)}")
    typer.echo(f"Seeds:\n{_numbered(seeds)}")
    typer.echo(f"Followed authors:\n{_numbered(profile.followed_authors)}")
    typer.echo(f"Keywords:\n{_numbered(profile.keywords)}")
    typer.echo(f"Top N: {profile.top_n}")
    typer.echo(f"arXiv categories: {', '.join(ctx.config.arxiv_categories)}")


def _a(word: str) -> str:
    return ("an " if word[0] in "aeiou" else "a ") + word  # "an author", "a topic"


def _add_command(kind: str, add: Callable[[Path, str], bool]) -> None:
    def command(text: Annotated[str, typer.Argument(help=f"The {kind} to add.")], vault: VaultOption = None) -> None:
        ctx = _open(vault)
        with _clean_errors():
            changed = add(ctx.vault, text)
        shown = profile_edit.normalise_text(text)
        typer.echo(f"Added {kind}: {shown}" if changed else f'"{shown}" is already in your profile ({kind}s).')

    command.__doc__ = f"Add {_a(kind)} to your profile."
    profile_app.command(f"add-{kind}")(command)


def _remove_command(kind: str, remove: Callable[[Path, str], bool]) -> None:
    def command(
        text_or_number: Annotated[str, typer.Argument(help=f"The {kind} text, or its number from `rpipe profile show`.")],
        vault: VaultOption = None,
    ) -> None:
        ctx = _open(vault)
        with _clean_errors():
            changed = remove(ctx.vault, text_or_number)
        if changed:
            typer.echo(f"Removed {kind}: {text_or_number.strip()}")
        else:
            typer.echo(f'"{text_or_number.strip()}" is not in your profile ({kind}s). Nothing was changed.')

    command.__doc__ = f"Remove a {kind} by its text or its number from `rpipe profile show`."
    profile_app.command(f"remove-{kind}")(command)


_add_command("topic", profile_edit.add_topic)
_add_command("author", profile_edit.add_author)
_add_command("keyword", profile_edit.add_keyword)
_remove_command("topic", profile_edit.remove_topic)
_remove_command("author", profile_edit.remove_author)
_remove_command("keyword", profile_edit.remove_keyword)
_remove_command("seed", profile_edit.remove_seed)


def _describe(meta: PaperMeta) -> str:
    first = meta.authors[0] if meta.authors else "unknown author"
    year = meta.published.year if meta.published else "year unknown"
    return f"{meta.title} ({first}, {year})"


def _choose(candidates: list[PaperMeta], yes: bool) -> PaperMeta | None:
    if yes:
        return candidates[0]
    if len(candidates) == 1:
        typer.echo(_describe(candidates[0]))
        return candidates[0] if typer.confirm("Add this paper?", default=True) else None
    for i, meta in enumerate(candidates, 1):
        typer.echo(f"  {i}. {_describe(meta)}")
    n = len(candidates)
    pick = typer.prompt(f"Pick 1-{n} or 0 to cancel", type=int)
    if not 0 <= pick <= n:
        raise _fail(f"{pick} is not between 0 and {n}. Nothing was changed.")
    return candidates[pick - 1] if pick else None


@profile_app.command("add-seed")
def profile_add_seed(
    text: Annotated[str, typer.Argument(help="An arXiv link, an arXiv id, or (part of) a title.")],
    vault: VaultOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask; take the only (or first) match.")] = False,
) -> None:
    """Add a seed paper from an arXiv link, id or title search."""
    ctx = _open(vault)
    if not text.strip():
        raise _fail("Seed input is empty: give an arXiv link, id or title.")
    profile = _load_profile(ctx)
    kind, value = parse_seed_input(text)
    if kind == "id" and value in profile.seeds:
        typer.echo(f"{value} is already in your profile.")
        return
    try:
        candidates = find_seed_candidates(text, make_arxiv(ctx.config))
    except SourceError as exc:
        raise _fail(f"Could not reach arXiv: {exc}. Nothing was changed.") from exc
    if not candidates:
        raise _fail(f'No paper found for "{text.strip()}". Try the arXiv link or id.')
    chosen = _choose(candidates, yes)
    if chosen is None:
        typer.echo("Cancelled. Nothing was changed.")
        return
    with _clean_errors():
        if chosen.id in profile.seeds:
            typer.echo(f"{chosen.id} is already in your profile.")
            return
        cache_seed(ctx.vault / "cache" / "seeds.json", chosen)
        changed = profile_edit.add_seed_id(ctx.vault, chosen.id)
    typer.echo(f"Added seed {chosen.id}: {chosen.title}" if changed else f"{chosen.id} is already in your profile.")


@profile_app.command("set-top-n")
def profile_set_top_n(
    n: Annotated[int, typer.Argument(help="Papers kept per digest (at least 1).")], vault: VaultOption = None
) -> None:
    """Set how many papers each digest keeps."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_top_n(ctx.vault, n)
    typer.echo(f"top_n is now {n}.")


@profile_app.command("set-categories")
def profile_set_categories(
    categories: Annotated[list[str], typer.Argument(help="arXiv categories, e.g. cs.CR stat.ML hep-th.")],
    vault: VaultOption = None,
) -> None:
    """Replace the arXiv categories the pipeline collects from."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_categories(ctx.vault, categories)
    typer.echo(f"arXiv categories are now: {', '.join(c.strip() for c in categories)}")
