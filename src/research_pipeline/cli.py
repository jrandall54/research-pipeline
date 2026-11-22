"""Command line interface."""

import gc
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from research_pipeline.collect import run_collect
from research_pipeline.db import SchemaTooNewError, UnknownPaperError, current_votes, finish_run, get_paper, list_runs, list_saved, previous_run_unfinished, record_vote, set_saved, open_db, papers_given_up_on, papers_to_summarize, start_run
from research_pipeline.digest import write_digest
from research_pipeline.evaluate import Grade, eval_exists, latest_day, load_eval, report as eval_report, run_eval, save_grade, ungraded
from research_pipeline.fulltext import fetch_fulltext
from research_pipeline.gold import GOLD_TARGET, GoldPaper, add_fact, add_gold, edit_fact, has_gold, load_gold, remove_fact, remove_gold
from research_pipeline.llm import LLM, LlmError, OllamaClient
from research_pipeline.run_lock import RunLocked, run_lock
from research_pipeline.embed import Embedder, SentenceTransformerEmbedder
from research_pipeline.models import Config, PaperMeta, Profile, Section
from research_pipeline import ollama_fit, onboarding, profile_edit, schedule, serve as serve_mod
from research_pipeline.seed_input import find_seed_candidates, parse_seed_input
from research_pipeline.seeds import SeedFetchError, cache_seed, resolve_seed_texts
from research_pipeline.sources.arxiv import ArxivSource
from research_pipeline.sources.base import Source, SourceError
from research_pipeline.sources.hf_daily import HfDailySource
from research_pipeline.summarize import run_summarize
from research_pipeline.triage import run_triage
from research_pipeline.vault import (
    DEFAULT_TOPICS,
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


def _progress(line: str) -> None:
    """Short progress lines (e.g. which model is being probed) go to stderr."""
    typer.echo(f"  {line}", err=True)


def _release_gpu_memory() -> None:
    """Hand back GPU memory the embedding model held (never imports torch just for this)."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


@dataclass
class Pipeline:
    """The pieces that touch the outside world; tests swap them for stubs."""

    sources: list[Source]
    make_embedder: Callable[[], Embedder]  # called only by run and triage (may raise RuntimeError)
    seed_fetcher: Callable[[list[str]], list[PaperMeta]]
    make_llm: Callable[[], LLM]  # called only by run and summarize (may raise LlmError)
    fulltext_fetcher: Callable[[str], list[Section]]


def build_pipeline(config: Config) -> Pipeline:
    """Real sources, the real embedding model, and arXiv lookup for seed papers."""
    arxiv = ArxivSource(config.arxiv_categories, max_results=config.arxiv_max_results)
    return Pipeline(
        sources=[arxiv, HfDailySource()],
        make_embedder=lambda: SentenceTransformerEmbedder(config.embedding_model),
        seed_fetcher=arxiv.fetch_by_ids,
        make_llm=lambda: build_llm(config, config.vault_path, log=_progress),
        fulltext_fetcher=fetch_fulltext,
    )


def build_llm(
    config: Config, vault: Path, log: Callable[[str], None] = lambda s: None
) -> OllamaClient:
    """The real local model client, with `auto` settings resolved to a concrete model and
    context (cached in the vault). Raises LlmError if none can be chosen; tests replace it."""
    fit = ollama_fit.resolve(config.llm_url, vault, config.llm_model, config.llm_num_ctx, log=log)
    return OllamaClient(config.llm_url, fit.model, fit.num_ctx)


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
    """Create the vault, then (in a terminal) walk you through topics, categories and seed papers."""
    path = resolve_vault(vault)
    existed = {n for n in ("config.yaml", "profile.yaml") if (path / n).exists()}
    try:
        init_vault(path)
    except SchemaTooNewError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"Vault ready at {path}")
    for name in ("config.yaml", "profile.yaml"):
        typer.echo(f"  {name}: {'kept existing' if name in existed else 'created'}")
    typer.echo("  vault.sqlite, raw/, markdown/, summaries/, my_work/, gold/, digests/, cache/ ready")
    if not _is_tty():
        typer.echo("\nNext steps:")
        typer.echo("  1. Add 10-20 papers you like: rpipe profile add-seed <arXiv link, id or title>")
        typer.echo("  2. Check your profile: rpipe profile show")
        typer.echo("  3. Change topics, authors, keywords, top-N or categories: rpipe profile --help")
        return
    try:
        _wizard(path, fresh_profile="profile.yaml" not in existed, fresh_config="config.yaml" not in existed)
    except (typer.Abort, KeyboardInterrupt, EOFError):
        typer.echo("\nSaved what you entered so far. Re-run rpipe init to continue.")
        raise typer.Exit(code=130) from None


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
    # The folder actually opened is the vault, even if it was moved since `rpipe init`.
    config = config.model_copy(update={"vault_path": path})
    try:
        conn = open_db(path / "vault.sqlite")
    except SchemaTooNewError as exc:
        raise _fail(str(exc)) from exc
    return _Context(path, config, conn)


def _load_profile(ctx: _Context) -> Profile:
    try:
        return load_profile(ctx.vault / "profile.yaml")
    except ConfigError as exc:
        raise _fail(str(exc)) from exc


def _load_embedder(pipeline: Pipeline, model_name: str) -> Embedder:
    """Build the embedder; any failure becomes a RuntimeError whose message says what to do."""
    try:
        return pipeline.make_embedder()
    except RuntimeError:  # e.g. the `embed` extra is not installed (message has the hint)
        raise
    except Exception as exc:  # noqa: BLE001 - failed download, broken torch install, ...
        raise RuntimeError(f"could not load embedding model {model_name}: {exc}") from exc


def _embedder(pipeline: Pipeline, model_name: str) -> Embedder:
    try:
        return _load_embedder(pipeline, model_name)
    except RuntimeError as exc:
        raise _fail(str(exc)) from exc


@contextmanager
def _sigterm_as_interrupt():
    """While active, SIGTERM (systemd stop, `kill`) behaves like Ctrl-C so `finally` blocks run."""
    def handler(signum, frame):
        raise KeyboardInterrupt

    old = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, old if old is not None else signal.SIG_DFL)


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


def health_line(
    day: date, counts: dict[str, int], errors: list[str], previous_unfinished: bool = False
) -> str:
    detail = "; ".join(" ".join(e.split()) for e in errors) or "none"
    extras = ""
    if counts.get("abstract_only"):
        extras += f" · abstract-only {counts['abstract_only']}"
    if counts.get("truncated"):
        extras += f" · truncated {counts['truncated']}"
    if counts.get("summary_failed"):
        extras += f" · summary failures {counts['summary_failed']}"
    return (
        f"Run {day.isoformat()}: collected {counts.get('inserted', 0)} new, "
        f"{counts.get('updated', 0)} updated · kept {counts.get('triaged', 0)} · "
        f"skipped {counts.get('skipped', 0)} · summarized {counts.get('summarized', 0)}"
        f"{extras} · errors: {detail}"
        f"{' · previous run did not finish' if previous_unfinished else ''}"
    )


def _ready_llm(pipeline: Pipeline) -> LLM:
    """The model client, checked: raises LlmError (with a fix hint) if it cannot be used."""
    llm = pipeline.make_llm()
    check = getattr(llm, "check", None)
    if check is not None:
        check()
    return llm


def _report(counts: dict[str, int], errors: list[str]) -> None:
    typer.echo("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for e in errors:
        typer.echo(f"  error: {e}", err=True)


RUN_LOCKED_EXIT = 75  # EX_TEMPFAIL: "try again later"; the next night proceeds normally


@app.command()
def run(vault: VaultOption = None) -> None:
    """Collect, triage, summarize and write today's digest (the nightly command)."""
    ctx = _open(vault)
    try:
        with run_lock(ctx.vault):
            _run_locked(ctx)
    except RunLocked as exc:
        typer.echo(f"{exc}; nothing was started. Wait for it to finish (see `rpipe runs`).", err=True)
        raise typer.Exit(code=RUN_LOCKED_EXIT) from exc


def _run_locked(ctx: _Context) -> None:
    profile = _load_profile(ctx)
    pipeline = build_pipeline(ctx.config)
    run_id = start_run(ctx.conn)  # before anything can fail, so every failure leaves a runs row
    counts: dict[str, int] = {}
    errors: list[str] = []
    crashed = False
    out = None
    with _sigterm_as_interrupt():
        try:  # finish_run runs exactly once however this block ends (crash, Ctrl-C, SIGTERM, success)
            stage = "embed"
            try:
                embedder = _load_embedder(pipeline, ctx.config.embedding_model)
                stage = "collect"
                counts, errors = run_collect(ctx.conn, pipeline.sources, run_id)
                stage = "triage"
                seeds = _seed_texts(ctx, profile, pipeline, errors)
                counts |= run_triage(ctx.conn, embedder, profile, seeds)
                # Triage is done with the embedder; free its GPU memory before the model probe
                # measures what is free (the nightly summaries run without it).
                del embedder
                _release_gpu_memory()
                stage = "summarize"
                try:
                    llm = _ready_llm(pipeline)
                except Exception as exc:  # noqa: BLE001 - no model must never stop the digest
                    errors.append(f"summaries skipped, model unavailable: {exc}")
                else:
                    sum_counts, sum_errors = run_summarize(
                        ctx.conn, llm, pipeline.fulltext_fetcher, llm.num_ctx
                    )
                    counts |= sum_counts
                    errors += sum_errors
            except KeyboardInterrupt:
                errors.append(f"{stage}: interrupted")
                raise  # the user wants out: no digest, keep the conventional interrupt exit
            except Exception as exc:  # noqa: BLE001 - record it, then still try to write the digest
                crashed = True
                errors.append(f"{stage}: {' '.join(str(exc).split()) or type(exc).__name__}")
            today = date.today()
            unfinished = previous_run_unfinished(ctx.conn, run_id)
            try:
                out = write_digest(
                    ctx.conn, ctx.vault, today, health_line(today, counts, errors, unfinished)
                )
            except KeyboardInterrupt:
                errors.append("digest: interrupted")
                raise
            except Exception as exc:  # noqa: BLE001
                crashed = True
                errors.append(f"digest: {' '.join(str(exc).split()) or type(exc).__name__}")
        finally:
            in_flight = sys.exc_info()[0] is not None  # an error is already propagating
            try:
                finish_run(ctx.conn, run_id, counts, errors)
            except Exception as exc:  # noqa: BLE001
                if not in_flight:
                    raise  # nothing else went wrong, so this failure is the story
                # Never let this mask the original error that is already propagating.
                typer.echo(f"warning: could not record the run: {exc}", err=True)
    if out is not None:
        typer.echo(f"Digest written to {out}")
    _report(counts, errors)
    if crashed:
        raise typer.Exit(code=1)


@app.command()
def runs(
    limit: Annotated[int, typer.Option("--limit", min=1, help="How many runs to show.")] = 10,
    vault: VaultOption = None,
) -> None:
    """List recent runs, newest first: when, how long, what happened, how many errors."""
    ctx = _open(vault)
    rows = list_runs(ctx.conn, limit)
    if not rows:
        typer.echo("No runs yet. Run `rpipe run`.")
        return
    for row in rows:
        typer.echo(format_run(row))


def format_run(row: sqlite3.Row) -> str:
    counts = json.loads(row["counts"]) if row["counts"] else {}
    errors = json.loads(row["errors"]) if row["errors"] else []
    if row["finished_at"]:
        seconds = (
            datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"])
        ).total_seconds()
        took = f"{seconds:.0f}s"
    else:
        took = "did not finish"
    summary = (
        f"new {counts.get('inserted', 0)}, kept {counts.get('triaged', 0)}, "
        f"summarized {counts.get('summarized', 0)}"
        if counts
        else "no counts"
    )
    return f"{row['started_at']}  {took}  {summary}  errors {len(errors)}"


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
def summarize(vault: VaultOption = None) -> None:
    """Summarize and critique kept papers (and today's digest papers lacking a summary) with the local model."""
    ctx = _open(vault)
    pipeline = build_pipeline(ctx.config)
    if not papers_to_summarize(ctx.conn, date.today()) and not papers_given_up_on(ctx.conn, date.today()):
        typer.echo("Nothing to summarize: no triaged papers, and today's digest has none missing a summary. Run `rpipe run` or `rpipe triage` first.")
        return
    try:
        llm = _ready_llm(pipeline)
    except Exception as exc:  # noqa: BLE001
        raise _fail(f"model unavailable: {exc}") from exc
    counts, errors = run_summarize(ctx.conn, llm, pipeline.fulltext_fetcher, llm.num_ctx)
    typer.echo("Summarized:")
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
    typer.echo(f"Feedback half-life: {profile.feedback_halflife_days:g} days")
    typer.echo(f"arXiv categories: {_categories_text(ctx.config.arxiv_categories)}")


def _categories_text(cats: list[str]) -> str:
    return "all computer science (cs.*)" if cats == ["all"] else ", ".join(cats)


CATEGORY_HELP = (
    "An arXiv category is the subject tag every paper carries (cs.CR = security). "
    "Only papers in your categories are collected.\n"
    "Common ones: cs.CR security, cs.LG machine learning, cs.AI AI, cs.CL language/LLMs, "
    "cs.OS operating systems, cs.SE software engineering, cs.DC distributed computing, "
    "cs.NI networking, stat.ML statistical ML.\n"
    "Full list: https://arxiv.org/category_taxonomy"
)


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

    command.__doc__ = f"Remove {_a(kind)} by its text or its number from `rpipe profile show`."
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


def _pick_index(labels: list[str]) -> int | None:
    """Show a numbered list and ask for a choice; return the 0-based index, or None for cancel."""
    for i, label in enumerate(labels, 1):
        typer.echo(f"  {i}. {label}")
    n = len(labels)
    pick = typer.prompt(f"Pick 1-{n} or 0 to cancel", type=int)
    if not 0 <= pick <= n:
        raise _SeedProblem(f"{pick} is not between 0 and {n}. Nothing was changed.")
    return pick - 1 if pick else None


def _choose(candidates: list[PaperMeta], yes: bool) -> PaperMeta | None:
    if yes:
        return candidates[0]
    if len(candidates) == 1:
        typer.echo(_describe(candidates[0]))
        return candidates[0] if typer.confirm("Add this paper?", default=True) else None
    index = _pick_index([_describe(m) for m in candidates])
    return None if index is None else candidates[index]


def _close(source: object) -> None:
    """Close an arXiv client if it can be closed (test stubs may not)."""
    close = getattr(source, "close", None)
    if callable(close):
        close()


class _SeedProblem(Exception):
    """A seed could not be added; the message is shown to the user as one line."""


def _add_seed(vault: Path, arxiv: ArxivSource, text: str, yes: bool = False) -> str:
    """Look up, confirm and save one seed. Returns the message to show; raises _SeedProblem.

    Shared by `profile add-seed` and the `init` wizard, which pass ONE arXiv client for the
    whole run so its 3 s spacing between requests holds. The cache is written first and the
    profile second, so a failure in either step never leaves a profile entry without its text.
    """
    if not text.strip():
        raise _SeedProblem("Seed input is empty: give an arXiv link, id or title.")
    try:
        profile = load_profile(vault / "profile.yaml")
    except ConfigError as exc:
        raise _SeedProblem(str(exc)) from exc
    try:
        kind, value = parse_seed_input(text)
    except ValueError as exc:
        raise _SeedProblem(str(exc)) from exc
    if kind == "id" and profile_edit.has_seed(profile.seeds, value):
        return f"{value} is already in your profile."
    try:
        candidates = find_seed_candidates(text, arxiv)
    except SourceError as exc:
        raise _SeedProblem(f"Could not reach arXiv: {exc}. Nothing was changed.") from exc
    if not candidates:
        raise _SeedProblem(f'No paper found for "{text.strip()}". Try the arXiv link or id.')
    chosen = _choose(candidates, yes)
    if chosen is None:
        return "Cancelled. Nothing was changed."
    if profile_edit.has_seed(profile.seeds, chosen.id):
        return f"{chosen.id} is already in your profile."
    try:
        cache_seed(vault / "cache" / "seeds.json", chosen)
        changed = profile_edit.add_seed_id(vault, chosen.id)
    except (ValueError, ConfigError, OSError) as exc:
        raise _SeedProblem(f"Could not save the seed: {exc}. Nothing was changed.") from exc
    return f"Added seed {chosen.id}: {chosen.title}" if changed else f"{chosen.id} is already in your profile."


@profile_app.command("add-seed")
def profile_add_seed(
    text: Annotated[str, typer.Argument(help="An arXiv link, an arXiv id, or (part of) a title.")],
    vault: VaultOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask; take the only (or first) match.")] = False,
) -> None:
    """Add a seed paper from an arXiv link, id or title search."""
    ctx = _open(vault)
    arxiv = make_arxiv(ctx.config)
    try:
        typer.echo(_add_seed(ctx.vault, arxiv, text, yes))
    except _SeedProblem as exc:
        raise _fail(str(exc)) from exc
    finally:
        _close(arxiv)


LLM_DOWN = "Local model unavailable; add topics by hand with `rpipe profile add-topic`"


def _onboarding_llm(config: Config) -> LLM:
    """The checked local model for onboarding (same resolution as `run`); tests replace it."""
    return _ready_llm(build_pipeline(config))


def _suggest_flow(vault: Path, config: Config, description: str, yes: bool) -> bool:
    """Propose topics and seeds from `description`, adding each one the user approves.

    Returns False (changing nothing) if the local model is unavailable. Raises ValueError for
    a too-short description, before any model call."""
    description = onboarding.check_description(description)
    try:
        proposal = onboarding.propose(_onboarding_llm(config), description)
    except (LlmError, OSError) as exc:
        typer.echo(f"{LLM_DOWN} ({exc})")
        return False
    current = load_profile(vault / "profile.yaml").topics
    was_template = [profile_edit.normalise_text(t).casefold() for t in current] == [t.casefold() for t in DEFAULT_TOPICS]
    # While the topics are only the template examples, a proposal equal to an example is still offered.
    existing = set() if was_template else {profile_edit.normalise_text(t).casefold() for t in current}
    topics = []
    for t in proposal.topics:
        key = profile_edit.normalise_text(t).casefold()
        if key and key not in existing:
            existing.add(key)
            topics.append(profile_edit.normalise_text(t))
    if topics:
        typer.echo("Proposed topics:\n" + _numbered(topics))
    else:
        typer.echo("All proposed topics are already in your profile.")
    added: set[str] = set()
    for t in topics:
        if yes or typer.confirm(f"Add topic '{t}'?", default=True):
            try:
                changed = profile_edit.add_topic(vault, t)
                added.add(t.casefold())
                typer.echo(f"Added topic: {t}" if changed else f"{t} is already in your profile.")
            except (ValueError, ConfigError, OSError) as exc:
                typer.echo(f"Skipped: {exc}")
    if was_template and added:  # your own topics replace the template examples, as in the wizard
        for example in DEFAULT_TOPICS:
            if example.casefold() not in added:
                try:
                    profile_edit.remove_topic(vault, example)
                except (ValueError, ConfigError, OSError) as exc:
                    typer.echo(f"Skipped removing example topic: {exc}")
    if not proposal.seed_queries:
        return True
    arxiv = make_arxiv(config)
    try:
        try:
            resolved = onboarding.resolve_seeds(proposal.seed_queries, arxiv)
        except SourceError as exc:
            typer.echo(f"Could not reach arXiv: {exc}. No seeds were added.")
            return True
        found = [m for _, m in resolved if m is not None]
        typer.echo(f"Found {len(found)} of {len(resolved)} proposed papers on arXiv (the rest were dropped).")
        for meta in found:
            if yes or typer.confirm(f"Add seed {_describe(meta)}?", default=True):
                try:
                    typer.echo(_add_seed(vault, arxiv, meta.id, yes=True))
                except _SeedProblem as exc:
                    typer.echo(f"Skipped: {exc}")
    finally:
        _close(arxiv)
    return True


@profile_app.command("suggest")
def profile_suggest(
    description: Annotated[
        str | None, typer.Argument(help="A few sentences about your research (asked for if omitted).")
    ] = None,
    vault: VaultOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Accept every proposed topic and paper without asking.")] = False,
) -> None:
    """Let the local model propose topics and seed papers from a description; you approve each."""
    ctx = _open(vault)
    if description is None:
        description = typer.prompt("Describe your research in a few sentences")
    try:
        _suggest_flow(ctx.vault, ctx.config, description, yes)
    except (ValueError, ConfigError) as exc:
        raise _fail(str(exc)) from exc


@profile_app.command("set-top-n")
def profile_set_top_n(
    n: Annotated[int, typer.Argument(help="Papers kept per digest (at least 1).")], vault: VaultOption = None
) -> None:
    """Set how many papers each digest keeps."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_top_n(ctx.vault, n)
    typer.echo(f"top_n is now {n}.")


@profile_app.command("set-feedback-halflife")
def profile_set_feedback_halflife(
    days: Annotated[str, typer.Argument(help="Days until a vote or save counts half as much (1 to 3650).")],
    vault: VaultOption = None,
) -> None:
    """Set how fast old votes and saves fade from your implicit profile."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_feedback_halflife(ctx.vault, days)
    typer.echo(f"feedback_halflife_days is now {float(days):g}.")


@profile_app.command("set-categories")
def profile_set_categories(
    categories: Annotated[list[str], typer.Argument(help="arXiv categories, e.g. cs.CR stat.ML, or the single word all.")],
    vault: VaultOption = None,
) -> None:
    """Replace the arXiv categories the pipeline collects from.

    An arXiv category is the subject tag every paper carries (cs.CR = security).
    Only papers in your categories are collected.

    Common ones: cs.CR security, cs.LG machine learning, cs.AI AI, cs.CL language/LLMs,
    cs.OS operating systems, cs.SE software engineering, cs.DC distributed computing,
    cs.NI networking, stat.ML statistical ML. Full list: https://arxiv.org/category_taxonomy

    Not sure yet? Use `all` (alone) to collect every computer-science category (cs.*).
    That fetches far more papers each night (the arxiv_max_results cap still applies).
    Physics, maths and stat categories are not included: list those explicitly.
    """
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_categories(ctx.vault, categories)
    cleaned = ["all" if c.strip().lower() == "all" else c.strip() for c in categories]
    typer.echo(f"arXiv categories are now: {_categories_text(cleaned)}")


# ---------------------------------------------------------------- gold commands

gold_app = typer.Typer(help="Manage the gold set: papers with the facts a correct summary must contain.", no_args_is_help=True)
app.add_typer(gold_app, name="gold")


def _ask_facts() -> list[str]:
    """Read key facts one per line until a blank line. Ctrl-C/EOF aborts before anything is saved."""
    typer.echo("Key facts a correct summary must get right (specific and checkable).")
    typer.echo("Type one per line; press Enter on an empty line to finish. Aim for 3 or more.")
    facts: list[str] = []
    while True:
        line = profile_edit.normalise_text(typer.prompt(f"Fact {len(facts) + 1}", default="", show_default=False))
        if not line:
            return facts
        facts.append(line)


@gold_app.command("add")
def gold_add(
    text: Annotated[str | None, typer.Argument(help="An arXiv link, id or (part of) a title.")] = None,
    vault: VaultOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask; take the only (or first) match.")] = False,
) -> None:
    """Add a paper to the gold set, then enter the key facts about it."""
    ctx = _open(vault)
    if text is None:
        text = typer.prompt("arXiv link, id or title")
    if not text.strip():
        raise _fail("Input is empty: give an arXiv link, id or title.")
    with _clean_errors():
        existing = load_gold(ctx.vault)
        try:
            kind, value = parse_seed_input(text)
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        if kind == "id" and has_gold(existing, value):
            typer.echo(f"{value} is already in your gold set. Add facts with: rpipe gold add-fact {value} \"...\"")
            return
        arxiv = make_arxiv(ctx.config)
        try:
            try:
                candidates = find_seed_candidates(text, arxiv)
            except SourceError as exc:
                raise _fail(f"Could not reach arXiv: {exc}. Nothing was changed.") from exc
            if not candidates:
                raise _fail(f'No paper found for "{text.strip()}". Try the arXiv link or id.')
            try:
                chosen = _choose(candidates, yes)
            except _SeedProblem as exc:
                raise _fail(str(exc)) from exc
        finally:
            _close(arxiv)
        if chosen is None:
            typer.echo("Cancelled. Nothing was changed.")
            return
        if has_gold(existing, chosen.id):
            typer.echo(f"{chosen.id} is already in your gold set.")
            return
        facts = _ask_facts()
        if not facts:
            typer.echo("At least one key fact is needed, so nothing was saved. Run the command again.")
            return
        if len(facts) < 3:
            typer.echo(f"Saved with {len(facts)} fact(s); 3 or more make a better test (rpipe gold add-fact).")
        add_gold(ctx.vault, GoldPaper(paper_id=chosen.id, title=chosen.title, key_facts=facts))
        typer.echo(f"Added {chosen.id}: {chosen.title}")


@gold_app.command("list")
def gold_list(vault: VaultOption = None) -> None:
    """Show the gold set and its key facts."""
    ctx = _open(vault)
    with _clean_errors():
        papers = load_gold(ctx.vault)
    typer.echo(f"Gold set: {len(papers)}/{GOLD_TARGET} papers")
    for p in papers:
        typer.echo(f"\n{profile_edit.normalise_text(p.paper_id)}  {profile_edit.normalise_text(p.title)}")
        for n, fact in enumerate(p.key_facts, 1):
            typer.echo(f"  {n}. {profile_edit.normalise_text(fact)}")


@gold_app.command("remove")
def gold_remove(
    paper_id: Annotated[str, typer.Argument(help="The arXiv id of the paper to remove.")],
    vault: VaultOption = None,
) -> None:
    """Remove a paper from the gold set."""
    ctx = _open(vault)
    with _clean_errors():
        if not remove_gold(ctx.vault, paper_id):
            raise _fail(f"{paper_id} is not in your gold set.")
    typer.echo(f"Removed {paper_id}.")


@gold_app.command("add-fact")
def gold_add_fact(
    paper_id: Annotated[str, typer.Argument(help="The arXiv id of a paper in the gold set.")],
    fact: Annotated[str, typer.Argument(help="A specific, checkable fact a correct summary must contain.")],
    vault: VaultOption = None,
) -> None:
    """Add one more key fact to a paper already in the gold set."""
    ctx = _open(vault)
    fact = profile_edit.normalise_text(fact)
    if not fact:
        raise _fail("The fact is empty.")
    with _clean_errors():
        if not add_fact(ctx.vault, paper_id, fact):
            raise _fail(f"{paper_id} is not in your gold set. Add it first: rpipe gold add {paper_id}")
    typer.echo(f"Added a fact to {paper_id}.")


def _pick_fact(vault: Path, paper_id: str | None, number: int | None, verb: str) -> tuple[str, int, str]:
    """Resolve (paper id, fact number, fact text), prompting for whatever was omitted. Validates before any write."""
    papers = load_gold(vault)
    if paper_id is None:
        paper_id = typer.prompt("Paper id")
    matches = [p for p in papers if has_gold([p], paper_id)]
    if not matches:
        raise _fail(f"{paper_id} is not in your gold set. Add it first: rpipe gold add {paper_id}")
    paper = matches[0]
    if number is None:
        typer.echo(f"{paper.paper_id}  {paper.title}")
        for n, fact in enumerate(paper.key_facts, 1):
            typer.echo(f"  {n}. {fact}")
        number = typer.prompt(f"Number of the fact to {verb}", type=int)
    if not 1 <= number <= len(paper.key_facts):
        raise _fail(f"{paper.paper_id} has {len(paper.key_facts)} fact(s); there is no fact {number}. See: rpipe gold list")
    return paper.paper_id, number, paper.key_facts[number - 1]


@gold_app.command("edit-fact")
def gold_edit_fact(
    paper_id: Annotated[str | None, typer.Argument(help="The arXiv id of a paper in the gold set.")] = None,
    number: Annotated[int | None, typer.Argument(help="Which fact, as numbered by `rpipe gold list`.")] = None,
    text: Annotated[str | None, typer.Argument(help="The replacement fact.")] = None,
    vault: VaultOption = None,
) -> None:
    """Replace one key fact of a gold paper (prompts for anything you leave out)."""
    ctx = _open(vault)
    with _clean_errors():
        paper_id, number, _old = _pick_fact(ctx.vault, paper_id, number, "edit")
        if text is None:
            text = typer.prompt("New text for the fact")
        text = profile_edit.normalise_text(text)
        if not text:
            raise _fail("The fact is empty.")
        try:
            old = edit_fact(ctx.vault, paper_id, number, text)
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        if old is None:
            raise _fail(f"{paper_id} is not in your gold set. Add it first: rpipe gold add {paper_id}")
    typer.echo(f"Updated fact {number} of {paper_id}.")


@gold_app.command("remove-fact")
def gold_remove_fact(
    paper_id: Annotated[str | None, typer.Argument(help="The arXiv id of a paper in the gold set.")] = None,
    number: Annotated[int | None, typer.Argument(help="Which fact, as numbered by `rpipe gold list`.")] = None,
    vault: VaultOption = None,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation.")] = False,
) -> None:
    """Remove one key fact from a gold paper (asks first unless --yes)."""
    ctx = _open(vault)
    with _clean_errors():
        paper_id, number, fact = _pick_fact(ctx.vault, paper_id, number, "remove")
        if not yes and not typer.confirm(f'Remove fact {number} of {paper_id} ("{fact}")?', default=False):
            typer.echo("Cancelled. Nothing was changed.")
            return
        try:
            removed = remove_fact(ctx.vault, paper_id, number)
        except ValueError as exc:
            raise _fail(str(exc)) from exc
        if removed is None:
            raise _fail(f"{paper_id} is not in your gold set. Add it first: rpipe gold add {paper_id}")
    typer.echo(f"Removed fact {number} of {paper_id}.")


eval_app = typer.Typer(help="Check summary accuracy against your gold set: run, grade, report.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")


def _latest_eval(vault: Path) -> date:
    day = latest_day(vault)
    if day is None:
        raise _fail("No eval yet. Run `rpipe eval run` first (it needs a gold set: `rpipe gold add`).")
    return day


@eval_app.command("run")
def eval_run(vault: VaultOption = None) -> None:
    """Summarize every gold-set paper with the local model and write a report to grade."""
    ctx = _open(vault)
    with _clean_errors():
        papers = load_gold(ctx.vault)
    if not papers:
        raise _fail("Your gold set is empty. Add papers you know well with `rpipe gold add` first.")
    if len(papers) < GOLD_TARGET:
        typer.echo(f"Warning: the gold set has {len(papers)} papers; {GOLD_TARGET} make a fair test (rpipe gold add).")
    pipeline = build_pipeline(ctx.config)
    try:
        llm = _ready_llm(pipeline)
    except Exception as exc:  # noqa: BLE001
        raise _fail(f"model unavailable: {exc}") from exc

    def abstract_for(paper_id: str) -> str:
        found = pipeline.seed_fetcher([paper_id])
        return found[0].abstract if found else ""

    today = date.today()
    if eval_exists(ctx.vault, today):
        typer.echo(f"Replacing today's earlier eval ({today.isoformat()}); its grades are cleared.")
    typer.echo(f"Summarizing {len(papers)} paper(s); this can take a while...")
    with _clean_errors():
        md = run_eval(ctx.vault, llm, pipeline.fulltext_fetcher, llm.num_ctx, today, abstract_for)
    typer.echo(f"Report written: {md}")
    typer.echo("Next: rpipe eval grade")


@eval_app.command("grade")
def eval_grade(vault: VaultOption = None) -> None:
    """Grade each summary of the latest eval against your key facts (y/n). Grades are saved one by one."""
    ctx = _open(vault)
    day = _latest_eval(ctx.vault)
    with _clean_errors():
        data = load_eval(ctx.vault, day)
        gold = {g.paper_id: g for g in load_gold(ctx.vault)}
    todo = ungraded(data)
    if not todo:
        typer.echo("Nothing left to grade. See the result with: rpipe eval report")
        return
    typer.echo(f"Grading eval {day.isoformat()}: {len(todo)} paper(s) to go. Ctrl-C stops; grades given are kept.")
    try:
        for e in todo:
            assert e.summary is not None
            typer.echo(f"\n{e.paper_id}  {e.title}")
            for fact in gold[e.paper_id].key_facts if e.paper_id in gold else []:
                typer.echo(f"  key fact: {fact}")
            s = e.summary.summary
            typer.echo(f"\n  {s.one_line}")
            for label, c in (("Problem", s.problem), ("Approach", s.approach), ("Results", s.results), ("Limitations", s.limitations)):
                typer.echo(f"  {label}: {c.text} [{', '.join(c.sections)}]")
            while True:
                answer = typer.prompt("Is this summary accurate? [y/n]").strip().lower()
                if answer in ("y", "n"):
                    break
                typer.echo("Please answer y or n.")
            note = typer.prompt("Note (optional)", default="", show_default=False).strip()
            with _clean_errors():
                save_grade(ctx.vault, day, Grade(paper_id=e.paper_id, accurate=answer == "y", note=note))
    except (typer.Abort, KeyboardInterrupt, EOFError):
        typer.echo("\nStopped. Grades already given are saved; run `rpipe eval grade` to continue.")
        return
    typer.echo("\nAll graded. See the result with: rpipe eval report")


@eval_app.command("report")
def eval_report_cmd(vault: VaultOption = None) -> None:
    """Show the accuracy result of the latest eval against the pass mark."""
    ctx = _open(vault)
    day = _latest_eval(ctx.vault)
    with _clean_errors():
        typer.echo(eval_report(ctx.vault, day))


model_app = typer.Typer(help="Show, change and check the local summary model.", no_args_is_help=True)
app.add_typer(model_app, name="model")


@model_app.command("show")
def model_show(vault: VaultOption = None) -> None:
    """Show which local model and server are configured."""
    ctx = _open(vault)
    cfg = ctx.config
    typer.echo(f"model: {cfg.llm_model}")
    typer.echo(f"server: {cfg.llm_url}")
    typer.echo(f"context window (num_ctx): {cfg.llm_num_ctx}")
    if cfg.llm_model == "auto" or cfg.llm_num_ctx == "auto":
        try:
            last = ollama_fit.cached_fit(cfg.llm_url, ctx.vault, cfg.llm_model, cfg.llm_num_ctx)
        except ollama_fit.ProbeTimeout as exc:
            typer.echo(f"last choice: unknown. Ollama did not answer in time: {exc}")
            return
        if last is None:
            typer.echo("last choice: not probed yet for these settings and installed models; "
                       "it is probed on next use (or now with `rpipe model fit`)")
        else:
            typer.echo(f"last choice: {last.model} with a context of {last.num_ctx} tokens")


@model_app.command("set")
def model_set(
    name: Annotated[str, typer.Argument(help="Ollama model name, e.g. qwen3:14b, or auto to choose the best installed.")],
    vault: VaultOption = None,
) -> None:
    """Choose which Ollama model writes summaries (install it first: ollama pull NAME)."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_llm_model(ctx.vault, name)
    typer.echo(f"model is now {name.strip()}. Check it works with `rpipe model check`.")


@model_app.command("set-url")
def model_set_url(
    url: Annotated[str, typer.Argument(help="Ollama server address, e.g. http://localhost:11434.")],
    vault: VaultOption = None,
) -> None:
    """Point the pipeline at a different Ollama server (must start with http:// or https://)."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_llm_url(ctx.vault, url)
    typer.echo(f"server is now {url.strip()}. Check it works with `rpipe model check`.")


@model_app.command("set-context")
def model_set_context(
    n: Annotated[str, typer.Argument(help="Context window in tokens (at least 8192), or auto to use the largest that fits.")],
    vault: VaultOption = None,
) -> None:
    """Set how much text (in tokens) the model can read at once; larger needs more memory."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_llm_num_ctx(ctx.vault, n)
    typer.echo("context window is now auto." if n.strip().lower() == "auto"
               else f"context window is now {int(n)} tokens.")


@model_app.command("fit")
def model_fit(vault: VaultOption = None) -> None:
    """Probe Ollama now: choose the best installed model and the largest context that fits in GPU memory."""
    ctx = _open(vault)
    cfg = ctx.config
    try:
        if cfg.llm_model != "auto" and cfg.llm_num_ctx != "auto":
            typer.echo(f"pinned: {cfg.llm_model} at {cfg.llm_num_ctx} tokens (nothing to probe)")
            typer.echo(build_llm(cfg, ctx.vault).check())
            return
        typer.echo("Probing Ollama: this loads and unloads models and can take a few minutes.")
        fit = ollama_fit.resolve(
            cfg.llm_url, ctx.vault, cfg.llm_model, cfg.llm_num_ctx, force=True, log=_progress,
        )
    except (LlmError, OSError, ValueError) as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"Using model {fit.model} with a context of {fit.num_ctx} tokens")


@model_app.command("check")
def model_check(vault: VaultOption = None) -> None:
    """Check that Ollama is running and the model is installed. With `auto` settings this may probe the GPU (slow the first time)."""
    ctx = _open(vault)
    try:
        typer.echo(build_llm(ctx.config, ctx.vault, log=_progress).check())
    except (LlmError, OSError, ValueError) as exc:
        raise _fail(str(exc)) from exc


# ---------------------------------------------------------------- init wizard


def _ask(prompt: str) -> str:
    """One line of input; blank returns "". Ctrl-C or end of input raises typer.Abort."""
    return typer.prompt(prompt, default="", show_default=False).strip()


def _wizard_topics(vault: Path, fresh_profile: bool) -> None:
    profile = load_profile(vault / "profile.yaml")
    # "Still the template" is decided from the content, not from whether this run created the
    # file: an interrupted first run or a non-terminal `init` leaves the examples in place.
    is_template = [profile_edit.normalise_text(t).casefold() for t in profile.topics] == [
        t.casefold() for t in DEFAULT_TOPICS
    ]
    if not fresh_profile:
        label = "Your topics (these are the template examples, not topics of your own)" if is_template else "Your topics"
        typer.echo(f"{label}:\n{_numbered(profile.topics)}")
        if not typer.confirm("Add more topics?", default=False):
            return
    else:
        typer.echo("Topics are short phrases for what you care about, e.g. prompt injection attacks on LLM agents.")
    examples = list(DEFAULT_TOPICS) if is_template else []
    entered: set[str] = set()
    while text := _ask("Topic (blank to finish)"):
        try:
            profile_edit.add_topic(vault, text)
        except (ValueError, ConfigError, OSError) as exc:
            typer.echo(f"Skipped: {exc}")
            continue
        typer.echo(f"Added topic: {profile_edit.normalise_text(text)}")
        entered.add(profile_edit.normalise_text(text).casefold())
        # Once you enter a topic of your own, template examples you did not type are dropped.
        # A topic you typed is never removed, even if it matches an example.
        if entered <= {e.casefold() for e in examples}:
            continue
        for example in examples:
            if example.casefold() in entered:
                continue
            try:
                profile_edit.remove_topic(vault, example)
            except (ValueError, ConfigError, OSError) as exc:
                typer.echo(f"Skipped removing example topic: {exc}")
        examples = [e for e in examples if e.casefold() in entered]


def _wizard_categories(vault: Path, config: Config, fresh_config: bool) -> None:
    current = ", ".join(config.arxiv_categories)
    if not fresh_config:
        typer.echo(f"arXiv categories: {_categories_text(config.arxiv_categories)}")
        if not typer.confirm("Change categories?", default=False):
            return
    typer.echo(CATEGORY_HELP)
    typer.echo(
        "Press Enter to keep the default, or type all if you are not sure (collects "
        "every computer-science category (cs.*); more to fetch and rank). Change it any time with "
        "rpipe profile set-categories."
    )
    entered = typer.prompt("arXiv categories (comma-separated)", default=current)
    cats = ["all" if c.lower() == "all" else c for c in _split(entered)]
    if cats and cats != config.arxiv_categories:
        try:
            profile_edit.set_categories(vault, cats)
        except (ValueError, ConfigError, OSError) as exc:
            typer.echo(f"Skipped: {exc}")


def _wizard_seeds(vault: Path, config: Config) -> None:
    typer.echo(
        "Seed papers are papers you like; the ranker finds new papers similar to them."
    )
    arxiv = make_arxiv(config)  # one client for the whole run, so lookups stay 3 s apart
    try:
        while text := _ask("Paper link, arXiv id or title (blank to finish)"):
            try:
                typer.echo(_add_seed(vault, arxiv, text))
            except _SeedProblem as exc:
                typer.echo(f"Skipped: {exc}")
    finally:
        _close(arxiv)
    if len(load_profile(vault / "profile.yaml").seeds) < 5:
        typer.echo("Tip: 10-20 seeds work best; add more later with rpipe profile add-seed")


def _wizard_describe(vault: Path, config: Config) -> None:
    """Optional: propose topics and seeds from a description. Any failure falls through."""
    description = _ask("Describe your research in a few sentences (Enter to skip)")
    if not description:
        return
    try:
        _suggest_flow(vault, config.model_copy(update={"vault_path": vault}), description, False)
    except ValueError as exc:
        typer.echo(f"Skipped: {exc}")


def _wizard(vault: Path, fresh_profile: bool, fresh_config: bool) -> None:
    """Interactive setup. Every answer is saved as soon as it is confirmed."""
    try:
        config = load_config(vault / "config.yaml")
        _wizard_describe(vault, config)
        _wizard_topics(vault, fresh_profile)
        _wizard_categories(vault, config, fresh_config)
        _wizard_seeds(vault, load_config(vault / "config.yaml"))
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    typer.echo("")
    profile_show(vault)
    typer.echo("\nNext step: rpipe run")


# --- feedback: votes and saves -------------------------------------------------


def _resolve_paper(conn: sqlite3.Connection, text: str) -> str:
    """Turn an id or a title fragment into a paper id from the vault, or fail."""
    text = text.strip()
    if get_paper(conn, text) is not None:
        return text
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        "SELECT id, title FROM papers WHERE title LIKE ? ESCAPE '\\' ORDER BY id",
        (f"%{escaped}%",),
    ).fetchall()
    if not text or not rows:
        raise _fail(f"No paper in your vault matches '{text}'. Use the id shown in the digest. Nothing was changed.")
    if len(rows) == 1:
        return rows[0]["id"]
    try:
        index = _pick_index([f"{r['id']}  {r['title']}" for r in rows])
    except _SeedProblem as exc:
        raise _fail(str(exc)) from exc
    if index is None:
        typer.echo("Cancelled. Nothing was changed.")
        raise typer.Exit(code=0)
    return rows[index]["id"]


_VOTES = {"up": 1, "down": -1, "clear": 0}


@app.command("vote")
def vote(
    paper: Annotated[str, typer.Argument(help="Paper id from the digest, or part of its title.")],
    direction: Annotated[str, typer.Argument(help="up, down or clear.")],
    vault: VaultOption = None,
) -> None:
    """Record a thumbs up/down on a paper (or clear it)."""
    if direction not in _VOTES:
        raise _fail(f"Direction must be up, down or clear, not '{direction}'.")
    ctx = _open(vault)
    try:
        pid = _resolve_paper(ctx.conn, paper)
        record_vote(ctx.conn, pid, _VOTES[direction])
    except UnknownPaperError as exc:
        raise _fail(str(exc)) from exc
    finally:
        ctx.conn.close()
    typer.echo(f"Recorded vote {direction} for {pid}." if direction != "clear" else f"Cleared vote for {pid}.")


def _save_command(saved: bool) -> None:
    word = "save" if saved else "unsave"

    def command(
        paper: Annotated[str, typer.Argument(help="Paper id from the digest, or part of its title.")],
        vault: VaultOption = None,
    ) -> None:
        ctx = _open(vault)
        try:
            pid = _resolve_paper(ctx.conn, paper)
            set_saved(ctx.conn, pid, saved)
        except UnknownPaperError as exc:
            raise _fail(str(exc)) from exc
        finally:
            ctx.conn.close()
        typer.echo(f"{'Saved' if saved else 'Unsaved'} {pid}.")

    command.__doc__ = f"{word.capitalize()} a paper."
    app.command(word)(command)


_save_command(True)
_save_command(False)


@app.command("feedback")
def feedback(vault: VaultOption = None) -> None:
    """List your current votes and saved papers."""
    ctx = _open(vault)
    try:
        titles = {r["id"]: r["title"] for r in ctx.conn.execute("SELECT id, title FROM papers")}
        votes = current_votes(ctx.conn)
        saves = list_saved(ctx.conn)
    finally:
        ctx.conn.close()
    typer.echo("Votes")
    if not votes:
        typer.echo("  No votes yet. Try `rpipe vote ID up`.")
    for r in votes:
        typer.echo(f"  {'up  ' if r['vote'] > 0 else 'down'}  {r['paper_id']}  {titles.get(r['paper_id'], '(unknown paper)')}")
    typer.echo("Saved")
    if not saves:
        typer.echo("  No saved papers yet. Try `rpipe save ID`.")
    for r in saves:
        typer.echo(f"  {r['paper_id']}  {titles.get(r['paper_id'], '(unknown paper)')}")


schedule_app = typer.Typer(help="Run the pipeline every night with a systemd user timer.", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")

_LINGER_HINT = (
    "To run while you are logged out, enable lingering once (this command does not run it for you):\n"
    "  loginctl enable-linger $USER"
)


_EMBED_REINSTALL = "uv tool install --editable '.[embed]' --force"


def _embed_available() -> bool:
    """Is sentence-transformers importable here? (Replaced in tests.)"""
    return importlib.util.find_spec("sentence_transformers") is not None


def _rpipe_path() -> str:
    """The program the nightly timer will run: the one running now, else the first on PATH.

    Refuses when this environment lacks the embed extra: the 03:00 run would fail silently.
    """
    argv0 = sys.argv[0] if sys.argv else ""
    if os.path.basename(argv0) in ("rpipe", "research-pipeline") and os.path.isfile(argv0):
        found: str | None = argv0
    else:
        found = shutil.which("rpipe")
    if found is None:
        raise _fail("rpipe is not on PATH. Install it first (`uv tool install --editable '.[embed]'` in the repo), then retry.")
    if not _embed_available():
        raise _fail(
            "This rpipe was installed without the embedding extra (sentence-transformers is missing), "
            "so the nightly run would fail at 03:00 before recording anything. Nothing was written. "
            f"Reinstall it with `{_EMBED_REINSTALL}` (run in the repo), then retry."
        )
    return os.path.abspath(found)


def _need_systemctl(writes: bool = False) -> None:
    if shutil.which("systemctl") is None:
        raise _fail("systemctl not found (is systemd available?)." + ("" if not writes else " Nothing was written."))


def _schedule_vault(vault: Path | None) -> Path:
    path = resolve_vault(vault).expanduser().resolve()
    if not (path / "config.yaml").exists():
        raise _fail(f"No vault found at {path} (no config.yaml). Run `rpipe init` first.")
    return path


@schedule_app.command("show")
def schedule_show(
    time: Annotated[str, typer.Option(help="Time of day, 24-hour HH:MM.")] = "03:00",
    vault: VaultOption = None,
) -> None:
    """Print the unit files that `install` would write, without writing anything."""
    path = _schedule_vault(vault)
    try:
        timer = schedule.render_timer(time)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    try:
        service = schedule.render_service(path, _rpipe_path())
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"# {schedule.SERVICE_NAME}\n{service}")
    typer.echo(f"# {schedule.TIMER_NAME}\n{timer}")


@schedule_app.command("install")
def schedule_install(
    time: Annotated[str, typer.Option(help="Time of day, 24-hour HH:MM.")] = "03:00",
    vault: VaultOption = None,
) -> None:
    """Write the systemd user units and enable the nightly timer."""
    path = _schedule_vault(vault)
    try:
        schedule.render_timer(time)
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    rpipe_cmd = _rpipe_path()
    _need_systemctl(writes=True)
    try:
        written = schedule.install(path, time, schedule.default_unit_dir(), schedule.system_runner, rpipe_cmd)
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    for f in written:
        typer.echo(f"wrote {f}")
    typer.echo(f"Scheduled: `rpipe run` every day at {time} for vault {path}.")
    typer.echo(_LINGER_HINT)


@schedule_app.command("status")
def schedule_status() -> None:
    """Show when the timer last ran and will run next."""
    _need_systemctl()
    try:
        enabled = schedule.status(schedule.system_runner)
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    if not enabled:
        typer.echo("The nightly timer is not installed or not enabled. Run `rpipe schedule install` to set it up.")


@schedule_app.command("remove")
def schedule_remove() -> None:
    """Disable the timer and delete the unit files."""
    try:
        removed, warnings = schedule.remove(schedule.default_unit_dir(), schedule.system_runner)
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    for w in warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo("\n".join(f"removed {f}" for f in removed) or "nothing was installed")


@app.command("serve")
def serve_cmd(
    host: Annotated[str | None, typer.Option(help="Address to listen on (default: this machine's Tailscale IPv4).")] = None,
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8001,
    vault: VaultOption = None,
) -> None:
    """Browse the vault and vote/save from a phone with Datasette (private network only; no login)."""
    ctx = _open(vault)  # applies WAL mode and schema upgrades before Datasette sees the file
    ctx.conn.close()
    try:
        bind = serve_mod.resolve_host(host, serve_mod.system_runner)
    except serve_mod.ServeError as exc:
        raise _fail(str(exc)) from exc
    if not serve_mod.datasette_available():
        raise _fail("Datasette is not installed. Install it with: uv sync --extra embed --extra serve (in the repo), or for plain `rpipe`: uv tool install --editable '.[embed,serve]' --force")
    old_handler = signal.getsignal(signal.SIGTERM)
    meta: Path | None = None
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so the finally below still runs
        fd, name = tempfile.mkstemp(prefix="rpipe-datasette-", suffix=".json")
        meta = Path(name)
        with os.fdopen(fd, "w") as f:
            json.dump(serve_mod.build_metadata(), f)
        typer.echo(f"Serving {ctx.vault / 'vault.sqlite'} at http://{bind}:{port}/ (no login: reachable by anyone who can reach this address)")
        code = serve_mod.run_datasette(serve_mod.command(ctx.vault / "vault.sqlite", bind, port, meta))
    finally:
        if meta is not None:
            meta.unlink(missing_ok=True)
        signal.signal(signal.SIGTERM, old_handler)
    if code:
        raise typer.Exit(code=code)
