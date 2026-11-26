"""Command line interface."""

import gc
import importlib.util
import json
import os
import platform
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal, cast
from urllib.parse import urlparse

import httpx
import typer

from research_pipeline import bench as bench_mod
from research_pipeline import catalog as catalog_mod
from research_pipeline import (
    categories,
    judge,
    library_api,
    netguard,
    ollama_fit,
    ollama_tune,
    onboarding,
    profile_edit,
    recommender,
    registry,
    schedule,
    share_directory,
    share_fetch,
    share_github,
    share_publish,
)
from research_pipeline import serve as serve_mod
from research_pipeline.backends.claude_code import LOGIN_HINT, find_claude, logged_in, resolve_model
from research_pipeline.collect import run_collect
from research_pipeline.db import (
    SchemaTooNewError,
    UnknownPaperError,
    count_missing_summary,
    finish_run,
    get_paper,
    list_runs,
    open_db,
    papers_by_ids,
    papers_given_up_on,
    papers_to_summarize,
    previous_run_unfinished,
    requests_by_status,
    start_run,
)
from research_pipeline.digest import render_summary, write_digest
from research_pipeline.embed import Embedder, SentenceTransformerEmbedder
from research_pipeline.evaluate import (
    Grade,
    eval_exists,
    has_judged_run,
    latest_day,
    load_eval,
    run_eval,
    save_grade,
    ungraded,
)
from research_pipeline.evaluate import report as eval_report
from research_pipeline.feed_format import clean_text
from research_pipeline.fulltext import FullTextUnavailable, ensure_usable, fetch_fulltext
from research_pipeline.gold import (
    GOLD_TARGET,
    GoldPaper,
    add_fact,
    add_gold,
    edit_fact,
    has_gold,
    load_gold,
    remove_fact,
    remove_gold,
    suggest_papers,
)
from research_pipeline.ids import normalise_paper_id
from research_pipeline.llm import (
    CALL_TIMEOUT,
    LLM,
    NO_MODEL,
    LlmError,
    LlmLimitReached,
    OllamaClient,
    _with_tag,
    call_timeout,
    pull_model,
)
from research_pipeline.machine import OLLAMA_INSTALL, _generation_models, detect_machine, recommend
from research_pipeline.models import Config, PaperMeta, Profile, Section, check_share_name
from research_pipeline.parts import SPLIT_BELOW_CTX, resolve_diagrams, resolve_mode
from research_pipeline.progress import ListReporter, ProgressEvent, Reporter, StageHandle, stage
from research_pipeline.progress_render import LineRenderer, LiveRenderer, make_reporter
from research_pipeline.registry import make_arxiv  # tests replace cli.make_arxiv with a stub
from research_pipeline.requests_queue import drain_requests, runnable_requests
from research_pipeline.run_lock import RunLocked, VaultLock
from research_pipeline.scan import (
    DEFAULT_OLLAMA_PORT,
    detect_ollama_server,
    flash_on,
    setting_source,
    system_cmd,
)
from research_pipeline.seed_input import find_seed_candidates, parse_seed_input
from research_pipeline.seeds import SeedFetchError, cache_seed, resolve_seed_texts
from research_pipeline.sources.arxiv import ArxivSource
from research_pipeline.sources.base import Source, SourceError
from research_pipeline.spill import model_cap, run_reruns
from research_pipeline.summarize import LIMIT_STOPPED, TIMEOUT_STOPPED, run_summarize
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


@contextmanager
def _reporter() -> Iterator[LineRenderer | LiveRenderer]:
    """A progress reporter on stderr (live on a terminal, plain lines otherwise), always closed."""
    reporter = make_reporter()
    try:
        yield reporter
    finally:
        reporter.close()


def _pull_progress(handle: StageHandle, tag: str) -> Callable[[str], None]:
    """A `pull_model` progress callback: a step event when the whole percent changes, a note otherwise."""
    last: list[str] = []

    def emit(line: str) -> None:
        match = re.search(r"(\d+)%$", line)
        if match is None:
            handle.note(line)
        elif not last or last[0] != match.group(1):
            last[:] = [match.group(1)]
            handle.step(tag, current=int(match.group(1)), detail=f"{match.group(1)}%")

    return emit


class _Tee:
    """Sends each progress event to several reporters (the display, plus one the command reads)."""

    def __init__(self, *reporters: Reporter) -> None:
        self._reporters = reporters

    def emit(self, event: ProgressEvent) -> None:
        for r in self._reporters:
            r.emit(event)


def _release_gpu_memory() -> None:
    """Free memory the embedding model held before the summarizer loads.

    Embeddings run on the CPU (embed.py), so normally there is no GPU memory to hand back; this is
    a harmless safety net (gc + torch.cuda.empty_cache if torch is already loaded and sees a GPU).
    It never imports torch just for this.
    """
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _no_judge() -> LLM:
    raise judge.JudgeUnavailable("no judge configured")


@dataclass
class Pipeline:
    """The pieces that touch the outside world; tests swap them for stubs."""

    sources: list[Source]
    make_embedder: Callable[[], Embedder]  # called only by run and triage (may raise RuntimeError)
    seed_fetcher: Callable[[list[str]], list[PaperMeta]]
    make_llm: Callable[[], LLM | None]  # called only by run and summarize (may raise LlmError); None = no model
    fulltext_fetcher: Callable[[str], list[Section]]
    startup_errors: list[str] = field(default_factory=list)  # sources/plug-ins that failed to start
    make_judge: Callable[[], LLM] = field(default=lambda: _no_judge())  # used by gold draft and eval run --judge (may raise JudgeUnavailable)


def build_pipeline(config: Config) -> Pipeline:
    """The enabled sources, the real embedding model, and arXiv lookup for seed papers
    (always arXiv, whichever sources are enabled)."""
    sources, startup_errors = registry.build_sources(config)
    arxiv = next((s for s in sources if s.name == "arxiv" and hasattr(s, "fetch_by_ids")), None)
    private: list[ArxivSource] = []  # arXiv not enabled: one private client, made on first use

    def fetch_seeds(ids: list[str]) -> list[PaperMeta]:
        if arxiv is not None:  # same instance as collection, so its 3-second spacing is shared
            return arxiv.fetch_by_ids(ids)
        if not private:
            private.append(make_arxiv(config))
        return private[0].fetch_by_ids(ids)

    return Pipeline(
        sources=sources,
        make_embedder=lambda: SentenceTransformerEmbedder(config.embedding_model),
        seed_fetcher=fetch_seeds,
        make_llm=lambda: build_llm(config, config.vault_path, log=_progress),
        fulltext_fetcher=fetch_fulltext,
        startup_errors=startup_errors,
        make_judge=lambda: judge.make_judge(config),
    )


def build_llm(
    config: Config, vault: Path, log: Callable[[str], None] = lambda s: None
) -> LLM | None:
    """The configured summary backend (None when it is `none`). Raises LlmError if it cannot
    start; tests replace it."""
    return registry.build_backend(config, vault, log)


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
        typer.echo("  rpipe model setup       choose how summaries are written (run in a terminal)")
        typer.echo("  rpipe profile add-seed <arXiv link, id or title>   add papers you like")
        typer.echo("  rpipe profile show      check your profile (rpipe profile --help changes it)")
        typer.echo(NEXT_COMMANDS)
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


def _load_embedder(
    pipeline: Pipeline, model_name: str, *, reporter: Reporter | None = None
) -> Embedder:
    """Build the embedder; any failure becomes a RuntimeError whose message says what to do.

    Shown as a progress stage because the first run downloads the model (hundreds of MB).
    """
    try:
        with stage(reporter, "embedding model", label=model_name):
            return pipeline.make_embedder()
    except RuntimeError:  # e.g. the `embed` extra is not installed (message has the hint)
        raise
    except Exception as exc:  # noqa: BLE001 - failed download, broken torch install, ...
        raise RuntimeError(f"could not load embedding model {model_name}: {exc}") from exc


def _embedder(pipeline: Pipeline, model_name: str, *, reporter: Reporter | None = None) -> Embedder:
    try:
        return _load_embedder(pipeline, model_name, reporter=reporter)
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
    ctx: _Context,
    profile: Profile,
    pipeline: Pipeline,
    errors: list[str],
    *,
    reporter: Reporter | None = None,
) -> dict[str, str]:
    """Seed texts for triage; a failed lookup is appended to `errors`, never fatal."""
    cache = ctx.vault / "cache" / "seeds.json"
    try:
        texts = resolve_seed_texts(ctx.conn, cache, profile.seeds, pipeline.seed_fetcher, reporter=reporter)
    except SeedFetchError as exc:
        errors.append(str(exc))
        return exc.texts
    unresolved = [s for s in profile.seeds if s not in texts]
    if unresolved:
        errors.append(f"seeds: no paper found for {', '.join(unresolved)}")
    return texts


def health_line(
    day: date,
    counts: dict[str, int],
    errors: list[str],
    previous_unfinished: bool = False,
    unknown_categories: list[str] | None = None,
) -> str:
    detail = "; ".join(" ".join(clean_text(e).split()) for e in errors) or "none"
    extras = ""
    if counts.get("abstract_only"):
        extras += f" · abstract-only {counts['abstract_only']}"
    if counts.get("body_cut"):
        extras += f" · body cut {counts['body_cut']}"
    if counts.get("in_parts"):
        extras += f" · in parts {counts['in_parts']}"
    if counts.get("summary_failed"):
        extras += f" · summary failures {counts['summary_failed']}"
    if counts.get("rerun"):
        extras += f" · rerun {counts['rerun']}"
    if counts.get("rerun_waiting"):
        extras += f" · rerun waiting {counts['rerun_waiting']}"
    for key, label in (("requests_done", "requests done"), ("requests_failed", "requests failed"),
                       ("requests_waiting", "requests waiting")):
        if counts.get(key):
            extras += f" · {label} {counts[key]}"
    if counts.get("followed_feeds"):
        feeds, new = counts["followed_feeds"], counts.get("followed_new", 0)
        extras += f" · followed: {feeds} feed{'' if feeds == 1 else 's'}, {new} new post{'' if new == 1 else 's'}"
    if counts.get("summaries_off"):
        extras += " · summaries off (abstracts-only mode)"
    if unknown_categories:
        extras += f" · unknown categories: {', '.join(unknown_categories)}"
    return (
        f"Run {day.isoformat()}: collected {counts.get('inserted', 0)} new, "
        f"{counts.get('updated', 0)} updated · kept {counts.get('triaged', 0)} · "
        f"skipped {counts.get('skipped', 0)} · summarized {counts.get('summarized', 0)}"
        f"{extras} · errors: {detail}"
        f"{' · previous run did not finish' if previous_unfinished else ''}"
    )


def _count_waiting_requests(conn: sqlite3.Connection, counts: dict[str, int]) -> None:
    """On a night with no model to use, still show the requests that wait (`requests waiting N`)."""
    waiting = len(runnable_requests(conn))
    if waiting:
        counts["requests_waiting"] = waiting


def _ready_llm(pipeline: Pipeline) -> LLM | None:
    """The model client, checked: raises LlmError (with a fix hint) if it cannot be used.
    None means the backend is `none` (nothing to check)."""
    llm = pipeline.make_llm()
    if llm is None:
        return None
    check = getattr(llm, "check", None)
    if check is not None:
        check()
    return llm


def _read_mode(
    llm: LLM, setting: str, reporter: Reporter | None, set_with: str | None = None,
    diagrams: str = "auto",
) -> tuple[Literal["whole", "split"], float | None]:
    """`_read_plan` without the diagrams decision: the mode and the GPU share it read."""
    mode, share, _ = _read_plan(llm, setting, reporter, set_with, diagrams)
    return mode, share


def _read_plan(
    llm: LLM, setting: str, reporter: Reporter | None, set_with: str | None = None,
    diagrams: str = "auto",
) -> tuple[Literal["whole", "split"], float | None, bool]:
    """Resolve a summarize_mode setting (auto/whole/split) to whole or split, in a "reading mode" stage.

    For `auto` on Ollama the model is loaded (a note says so first) and its GPU share read
    (parts.resolve_mode). Any failure there reads whole, as before split mode existed. Shared
    by `rpipe run`, `rpipe summarize` and `rpipe eval run` so all three decide the same way.
    ``set_with`` names where a fixed mode came from (eval's `--mode`); default the setting.
    Returns the mode, the GPU share it read (None when not read) and whether to draw diagrams. The diagrams setting
    (parts.resolve_diagrams) is noted in the same stage and uses the same share; a fixed
    summarize mode does not load the model, so the share is unknown and `auto` draws.
    """
    backend = getattr(llm, "name", "")
    with stage(reporter, "reading mode") as handle:
        share: float | None = None
        problem = ""
        if setting == "auto" and backend == "ollama":
            try:
                ollama = cast(OllamaClient, llm)  # backend "ollama" is an OllamaClient
                handle.note(f"loading {llm.model_name} to see where it sits (GPU or RAM)")
                ollama.load()
                share = ollama.gpu_share()
            except Exception as exc:  # noqa: BLE001 - an unknown share reads whole
                problem = " ".join(str(exc).split()) or type(exc).__name__
        mode, reason = resolve_mode(setting, backend, share, llm.num_ctx, set_with=set_with)
        handle.note(f"{reason} ({problem})" if problem else reason)
        draw, draw_reason = resolve_diagrams(diagrams, backend, share)
        handle.note(draw_reason)
    return mode, share, draw


def _summarize_plan(
    llm: LLM, config: Config, reruns_possible: bool, reporter: Reporter | None, *, has_work: bool = True
) -> tuple[Literal["whole", "split"], int, bool]:
    """How this command reads papers: the mode, `fallback_above` and whether to draw diagrams.

    Mode: the summarize_mode setting, resolved by `_read_mode`; with nothing queued
    (``has_work=False``) there is no "reading mode" stage and no model load, just whole.
    fallback_above (Decision 2):
    with Ollama and reruns possible, only papers no rerun could read whole (needing more than
    the model's cap) go to parts; otherwise every cut paper does (0). See "Choosing the mode"
    in docs/guide/summaries.md.
    """
    backend = getattr(llm, "name", "")
    mode: Literal["whole", "split"] = "whole"
    diagrams, _ = resolve_diagrams(config.diagrams, backend, None)  # nothing queued: no stage, no note
    if has_work:
        mode, _share, diagrams = _read_plan(llm, config.summarize_mode, reporter, diagrams=config.diagrams)
    fallback_above = 0
    if backend == "ollama" and reruns_possible:
        fallback_above = model_cap(getattr(llm, "url", ""), llm.model_name)
    return mode, fallback_above, diagrams


def _require_model(config: Config) -> None:
    """Exit 1 with NO_MODEL when the backend is `none` (commands that need a model)."""
    if config.llm_backend == "none":
        raise _fail(NO_MODEL)


def _report(counts: dict[str, int], errors: list[str]) -> None:
    typer.echo("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    for e in errors:
        typer.echo(f"  error: {e}", err=True)


RUN_LOCKED_EXIT = 75  # EX_TEMPFAIL: "try again later"; the next night proceeds normally


_held: VaultLock | None = None  # the lock the running command holds, so a prompt can let go of it


def _take(lock: VaultLock, again: bool = False) -> None:
    """Take `lock`, or say who is working and exit 75."""
    try:
        lock.acquire()
    except RunLocked as exc:
        started = "nothing further was started" if again else "nothing was started"
        typer.echo(f"{exc}; {started}. Wait for it to finish (see `rpipe runs`).", err=True)
        raise typer.Exit(code=RUN_LOCKED_EXIT) from exc


@contextmanager
def _pipeline_lock(ctx: _Context, holder: str) -> Iterator[None]:
    """Hold the vault's run lock as `holder` (e.g. "rpipe summarize") for the block.

    If another pipeline command holds it, say which one and exit 75 before anything starts.
    Every locked command enters this right after `_open`, before any network, model or
    Ollama call. While the command waits for an answer (`_prompt`, `_confirm`) the lock is
    let go of, so a nightly run is never refused because a question sits unanswered.
    The list of locked commands is in docs/guide/running.md.
    """
    global _held
    lock = VaultLock(ctx.vault, holder)
    _take(lock)
    outer, _held = _held, lock
    try:
        yield
    finally:
        _held = outer
        lock.release()


@contextmanager
def _waiting_for_you() -> Iterator[None]:
    """Let go of the command's lock while it waits for the user; take it back after the answer.

    If another job took the lock meanwhile, the command stops with exit 75 instead of
    working beside it. Outside a locked command this does nothing.
    """
    lock = _held
    if lock is None or not lock.held:
        yield
        return
    lock.release()
    yield  # Ctrl-C or end of input leaves the lock free; the command is ending anyway
    _take(lock, again=True)


def _prompt(*args, **kwargs):
    """`typer.prompt` that lets go of the pipeline lock while waiting (see `_waiting_for_you`)."""
    with _waiting_for_you():
        return typer.prompt(*args, **kwargs)


def _confirm(*args, **kwargs) -> bool:
    """`typer.confirm` that lets go of the pipeline lock while waiting (see `_waiting_for_you`)."""
    with _waiting_for_you():
        return typer.confirm(*args, **kwargs)


@app.command()
def run(vault: VaultOption = None) -> None:
    """Collect, triage, summarize and write today's digest (the nightly command)."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe run"):
        _run_locked(ctx)


def _run_locked(ctx: _Context) -> None:
    profile = _load_profile(ctx)
    bad_categories = categories.unknown(ctx.config.arxiv_categories)
    if bad_categories:
        typer.echo(
            f"warning: unknown categories: {', '.join(bad_categories)} "
            "(fix with `rpipe profile set-categories`)",
            err=True,
        )
    pipeline = build_pipeline(ctx.config)
    run_id = start_run(ctx.conn)  # before anything can fail, so every failure leaves a runs row
    counts: dict[str, int] = {}
    errors: list[str] = []
    crashed = False
    out = None
    with _sigterm_as_interrupt():
        try:  # finish_run runs exactly once however this block ends (crash, Ctrl-C, SIGTERM, success)
            stage = "embed"
            errors += pipeline.startup_errors  # before anything can crash, so they are never dropped
            try:
                with _reporter() as reporter:
                    embedder = _load_embedder(
                        pipeline, ctx.config.embedding_model, reporter=reporter
                    )
                stage = "collect"
                with _reporter() as reporter:
                    counts, errors2 = run_collect(
                        ctx.conn, pipeline.sources, run_id, reporter=reporter
                    )
                errors += errors2
                stage = "triage"
                with _reporter() as reporter:
                    seeds = _seed_texts(ctx, profile, pipeline, errors, reporter=reporter)
                    # Your my_work/ files -> shelf rows, every night (even with no new papers).
                    library_api.shelf_sync(ctx.conn, ctx.vault / "my_work", embedder, reporter=reporter)
                    counts |= run_triage(
                        ctx.conn, embedder, profile, seeds, use_shelf=True, reporter=reporter
                    )
                # Triage is done with the embedder; free its GPU memory before the model probe
                # measures what is free (the nightly summaries run without it).
                del embedder
                _release_gpu_memory()
                stage = "summarize"
                try:
                    llm = _ready_llm(pipeline)
                except LlmLimitReached:
                    errors.append(LIMIT_STOPPED)
                    _count_waiting_requests(ctx.conn, counts)
                except Exception as exc:  # noqa: BLE001 - no model must never stop the digest
                    errors.append(f"summaries skipped, model unavailable: {exc}")
                    _count_waiting_requests(ctx.conn, counts)
                else:
                    if llm is None:
                        counts["summaries_off"] = 1  # chosen, not a failure: abstracts-only digest
                        _count_waiting_requests(ctx.conn, counts)
                    else:
                        # The morning deadline for the drain and the reruns.
                        # None when it is "off" or over 12 hours away.
                        deadline = schedule.deadline_at(datetime.now(), ctx.config.night_deadline)
                        with _reporter() as reporter:
                            # The setting, not tonight's clock: a paper cut in a daytime run is
                            # still rerun whole on a later night (rerun_candidates looks at every day).
                            # Nothing queued: no reason to load the model just to pick a mode.
                            has_work = bool(papers_to_summarize(ctx.conn, date.today())) or bool(
                                runnable_requests(ctx.conn))
                            mode, fallback_above, diagrams = _summarize_plan(
                                llm, ctx.config, ctx.config.night_deadline != "off", reporter,
                                has_work=has_work)
                            sum_counts, sum_errors = run_summarize(
                                ctx.conn, llm, pipeline.fulltext_fetcher, llm.num_ctx,
                                reporter=reporter, mode=mode, fallback_above=fallback_above,
                                diagrams=diagrams,
                            )
                        counts |= sum_counts
                        errors += sum_errors
                        timed_out = TIMEOUT_STOPPED in sum_errors
                        stage = "requests"
                        if LIMIT_STOPPED in sum_errors or timed_out:
                            # The model just stopped: don't spend a call (or a timeout) on requests.
                            _count_waiting_requests(ctx.conn, counts)
                        else:
                            try:
                                with _reporter() as reporter:
                                    req_counts, req_errors = drain_requests(
                                        ctx.conn, llm, pipeline.fulltext_fetcher, llm.num_ctx,
                                        reporter=reporter, mode=mode, fallback_above=fallback_above,
                                        diagrams=diagrams, deadline=deadline,
                                    )
                                counts |= req_counts
                                errors += req_errors
                                timed_out = TIMEOUT_STOPPED in req_errors
                            except Exception as exc:  # noqa: BLE001 - a request problem never stops reruns or the digest
                                errors.append(f"requests: {' '.join(str(exc).split()) or type(exc).__name__}")
                        if getattr(llm, "name", "") == "ollama":
                            stage = "rerun"
                            try:
                                with _reporter() as reporter:
                                    rerun_counts, rerun_errors = run_reruns(
                                        ctx.conn, llm, ctx.vault, deadline,
                                        reporter=reporter,
                                        model_timed_out=timed_out,
                                    )
                                counts |= rerun_counts
                                errors += rerun_errors
                            except Exception as exc:  # noqa: BLE001 - a rerun problem never stops the digest
                                errors.append(f"rerun: {' '.join(str(exc).split()) or type(exc).__name__}")
            except KeyboardInterrupt:
                errors.append(f"{stage}: interrupted")
                raise  # the user wants out: no digest, keep the conventional interrupt exit
            except Exception as exc:  # noqa: BLE001 - record it, then still try to write the digest
                crashed = True
                errors.append(f"{stage}: {' '.join(str(exc).split()) or type(exc).__name__}")
            _share_fetch_stage(ctx, counts, errors)  # before the digest, so tonight's health line has its counts
            today = date.today()
            unfinished = previous_run_unfinished(ctx.conn, run_id)
            try:
                out = write_digest(
                    ctx.conn, ctx.vault, today, health_line(
                        today, counts, errors, unfinished,
                        unknown_categories=bad_categories,
                    )
                )
            except KeyboardInterrupt:
                errors.append("digest: interrupted")
                raise
            except Exception as exc:  # noqa: BLE001
                crashed = True
                errors.append(f"digest: {' '.join(str(exc).split()) or type(exc).__name__}")
            # Publishing needs tonight's digest papers, so it runs after the digest; its error
            # goes to the run record (finish_run), not tonight's digest.
            _share_publish_stage(ctx, profile, errors)
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


def _share_publish_stage(ctx: _Context, profile: Profile, errors: list[str]) -> None:
    """Nightly stage `publish` (only when sharing is on), after the digest; never fatal."""
    if not ctx.config.share_enabled:
        return
    try:
        with _reporter() as reporter:
            _publish_feed(ctx, profile, _share_runner(), reporter,
                          handle=ctx.config.share_handle, name=ctx.config.share_display_name)
    except Exception as exc:  # noqa: BLE001 - a push problem never touches the digest
        reason = " ".join(str(exc).split()) or type(exc).__name__
        errors.append(f"sharing: push failed, will retry tomorrow: {reason}")


def _share_fetch_stage(ctx: _Context, counts: dict[str, int], errors: list[str]) -> None:
    """Nightly stage `fetch` + directory refresh (when sharing or following), before the digest; never fatal."""
    if not (ctx.config.share_enabled or library_api.follows(ctx.conn)):
        return
    try:
        with _share_http() as http, _reporter() as reporter:
            fetch_counts, fetch_errors = share_fetch.fetch_all(ctx.conn, http, reporter)
            counts |= fetch_counts
            errors += fetch_errors
            _found, dir_errors = share_directory.refresh(ctx.conn, http, reporter)
            errors += dir_errors
    except Exception as exc:  # noqa: BLE001 - a fetch problem never touches the digest
        errors.append(f"fetch: {' '.join(str(exc).split()) or type(exc).__name__}")


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
    with _pipeline_lock(ctx, "rpipe collect"):
        pipeline = build_pipeline(ctx.config)
        with _reporter() as reporter:
            counts, errors = run_collect(ctx.conn, pipeline.sources, reporter=reporter)
        typer.echo("Collected:")
        _report(counts, [*pipeline.startup_errors, *errors])


@app.command()
def triage(vault: VaultOption = None) -> None:
    """Rank `new` papers against your profile; keep the top N."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe triage"):
        profile = _load_profile(ctx)
        pipeline = build_pipeline(ctx.config)
        errors: list[str] = []
        with _reporter() as reporter:
            embedder = _embedder(pipeline, ctx.config.embedding_model, reporter=reporter)
            seeds = _seed_texts(ctx, profile, pipeline, errors, reporter=reporter)
            library_api.shelf_sync(ctx.conn, ctx.vault / "my_work", embedder, reporter=reporter)
            counts = run_triage(
                ctx.conn, embedder, profile, seeds, use_shelf=True, reporter=reporter
            )
        typer.echo("Triaged:")
        _report(counts, errors)


@app.command()
def summarize(
    paper_ids: Annotated[
        list[str] | None,
        typer.Argument(help="Summarize exactly these paper ids (any status; replaces an existing summary)."),
    ] = None,
    backlog: Annotated[
        bool, typer.Option("--backlog", help="Also summarize papers from earlier digests that have no summary (newest first).")
    ] = False,
    limit: Annotated[
        int | None, typer.Option("--limit", help="With --backlog: most backlog papers to summarize this run (default 10).")
    ] = None,
    refetch: Annotated[
        bool, typer.Option("--refetch", help="With paper ids: re-download and re-parse each paper's full text first (replaces the stored text).")
    ] = False,
    vault: VaultOption = None,
) -> None:
    """Summarize and critique kept papers (and today's digest papers lacking a summary) with the configured summary model."""
    if paper_ids and backlog:
        raise _fail("Give either paper ids or --backlog, not both.")
    if refetch and (backlog or not paper_ids):
        raise _fail("--refetch needs explicit paper ids (rpipe summarize ID... --refetch) and cannot be used with --backlog.")
    if limit is not None and not backlog:
        raise _fail("--limit only works with --backlog.")
    if limit is not None and limit < 1:
        raise _fail(f"--limit must be a positive whole number, got {limit}.")
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe summarize"):
        _require_model(ctx.config)
        pipeline = build_pipeline(ctx.config)
        today = date.today()
        if paper_ids:
            try:
                papers_by_ids(ctx.conn, paper_ids)
            except UnknownPaperError as exc:
                raise _fail(str(exc)) from exc
        else:
            normal = papers_to_summarize(ctx.conn, today) or papers_given_up_on(ctx.conn, today)
            total = count_missing_summary(ctx.conn, today) if backlog else 0
            if not normal and not total:
                if backlog:
                    typer.echo("Nothing to summarize: no papers in past digests are missing a summary, and no triaged papers or today's digest papers are waiting either.")
                else:
                    typer.echo("Nothing to summarize: no triaged papers, and today's digest has none missing a summary. Run `rpipe run` or `rpipe triage` first.")
                return
        try:
            llm = _ready_llm(pipeline)
        except Exception as exc:  # noqa: BLE001
            raise _fail(f"model unavailable: {exc}") from exc
        if llm is None:
            raise _fail(NO_MODEL)
        cap = (limit or 10) if backlog else None
        if backlog:
            if total:
                typer.echo(f"Backlog: {min(cap, total)} of {total} papers missing a summary (newest first; raise --limit for more)")
            else:
                typer.echo("No older digest papers are missing a summary.")
        done = ListReporter()
        with _reporter() as live:
            # Reruns follow tonight unless the deadline is off, so the same fallback rule as `run`.
            mode, fallback_above, diagrams = _summarize_plan(
                llm, ctx.config, ctx.config.night_deadline != "off", live)
            counts, errors = run_summarize(
                ctx.conn, llm, pipeline.fulltext_fetcher, llm.num_ctx,
                today=today, backlog_limit=cap, paper_ids=paper_ids or None,
                reporter=_Tee(live, done), refetch=refetch,
                mode=mode, fallback_above=fallback_above, diagrams=diagrams,
            )
        typer.echo("Summarized:")
        _report(counts, errors)
        _refresh_hint(
            ctx.conn, [e.label for e in done.events if e.kind == "step" and e.outcome == "done"], today
        )


def _refresh_hint(conn: sqlite3.Connection, paper_ids: list[str], today: date) -> None:
    """Name the older digests that gained summaries; their files are only rewritten by `rpipe digest`."""
    if not paper_ids:
        return
    marks = ",".join("?" * len(paper_ids))
    dates = [
        r[0] for r in conn.execute(
            f"SELECT DISTINCT digest_date FROM papers WHERE id IN ({marks})"
            " AND digest_date IS NOT NULL AND digest_date != ? ORDER BY digest_date DESC",
            (*paper_ids, today.isoformat()),
        )
    ]
    if dates:
        typer.echo("Older digests do not show new summaries until you refresh them:")
        for d in dates:
            typer.echo(f"  rpipe digest --date {d}")


@app.command()
def digest(
    vault: VaultOption = None,
    date_: Annotated[
        str | None, typer.Option("--date", help="Digest date, YYYY-MM-DD (default: today).")
    ] = None,
) -> None:
    """Write the markdown digest from papers already triaged."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe digest"):
        try:
            day = date.fromisoformat(date_) if date_ else date.today()
        except ValueError as exc:
            raise _fail(f"--date must be YYYY-MM-DD, got {date_!r}") from exc
        out = write_digest(ctx.conn, ctx.vault, day, f"Digest {day.isoformat()}: written from stored papers (no collection run)")
        typer.echo(f"Digest written to {out}")


# ---------------------------------------------------------------- request list

requests_app = typer.Typer(
    help="Work the app asked the pipeline to do (e.g. summarize one paper): see it, or do it now.",
    no_args_is_help=True,
)
app.add_typer(requests_app, name="requests")


@requests_app.command("add")
def requests_add(
    paper: Annotated[str, typer.Argument(help="Paper id (any spelling, e.g. 2611.25005v2 or an arXiv URL), or part of its title.")],
    vault: VaultOption = None,
) -> None:
    """Ask for a paper to be summarized: tonight in `rpipe run`, or now with `rpipe requests run`."""
    ctx = _open(vault)
    try:
        pid = _resolve_paper(ctx.conn, paper)
        before = library_api.open_request_ids(ctx.conn)
        rid = library_api.request(ctx.conn, "summarize", {"paper_id": pid})
    except UnknownPaperError as exc:
        raise _fail(str(exc)) from exc
    finally:
        ctx.conn.close()
    if rid in before:
        typer.echo(f"Request {rid} to summarize {pid} is already waiting; nothing new was added.")
    else:
        typer.echo(f"Request {rid}: summarize {pid}.")
    typer.echo("It runs during tonight's `rpipe run`, or now with `rpipe requests run`. "
               "`rpipe requests list` shows how it went.")


@requests_app.command("run")
def requests_run(vault: VaultOption = None) -> None:
    """Do the waiting requests now instead of at the nightly run."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe requests run"):
        _require_model(ctx.config)
        pending = runnable_requests(ctx.conn)
        if not pending:
            typer.echo("No requests waiting. `rpipe requests list` shows earlier ones.")
            return
        pipeline = build_pipeline(ctx.config)
        try:
            llm = _ready_llm(pipeline)
        except Exception as exc:  # noqa: BLE001
            raise _fail(f"model unavailable: {exc}") from exc
        if llm is None:
            raise _fail(NO_MODEL)
        with _reporter() as reporter:
            mode, fallback_above, diagrams = _summarize_plan(
                llm, ctx.config, ctx.config.night_deadline != "off", reporter, has_work=bool(pending))
            counts, errors = drain_requests(
                ctx.conn, llm, pipeline.fulltext_fetcher, llm.num_ctx,
                reporter=reporter, mode=mode, fallback_above=fallback_above, diagrams=diagrams,
            )
        typer.echo("Requests:")
        _report(counts, errors)


@requests_app.command("list")
def requests_list(vault: VaultOption = None) -> None:
    """List every request, newest first: id, status, kind, paper, tries, when asked, last error."""
    ctx = _open(vault)
    rows = requests_by_status(ctx.conn, "pending", "running", "done", "failed")
    if not rows:
        typer.echo("No requests.")
        return
    for row in reversed(rows):
        paper = json.loads(row["payload"]).get("paper_id", "-")
        line = f"{row['id']}  {row['status']}  {row['kind']}  {paper}  {row['tries']}  {row['created_at']}"
        if row["error"]:
            line += f"  {' '.join(row['error'].split())}"
        typer.echo(line)


# ---------------------------------------------------------------- profile commands

profile_app = typer.Typer(help="View and change your profile without editing files.", no_args_is_help=True)
app.add_typer(profile_app, name="profile")


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
    bad = categories.unknown(ctx.config.arxiv_categories)
    if bad:
        typer.echo(f"unknown categories: {', '.join(bad)}")


def _categories_text(cats: list[str]) -> str:
    return "all computer science (cs.*)" if cats == ["all"] else ", ".join(cats)


CATEGORY_HELP = (
    "arXiv (arxiv.org) is the free site where most computer-science and AI research appears\n"
    "first. Every paper is filed under subject categories, and rpipe only collects papers\n"
    "from the categories you pick here; your topics then rank what was collected. Pick the\n"
    "areas your topics fall in. More categories means more papers to sort (slower, a bit\n"
    "noisier); too few means good papers are never seen."
)
CATEGORY_PROMPT_HELP = (
    "Type the numbers you want, separated by commas (e.g. 1,2,5), or arXiv codes, or all\n"
    "for every computer-science category. Press Enter to keep the ticked ones.\n"
    "Change it any time with rpipe profile set-categories. Full list: https://arxiv.org/category_taxonomy"
)
OPENING = (
    "Setup has 5 short steps. Each answer is saved as you go: Ctrl-C stops, and running\n"
    "rpipe init again carries on. You can change anything later with rpipe profile."
)
SUMMARIES_HELP = (
    "rpipe reads the papers it picks for you and writes a summary of each. That needs a\n"
    "language model: one on this computer (Ollama: private; best with a graphics card),\n"
    "Claude through your Claude Code login (no graphics card needed; paper text is sent\n"
    "to Anthropic), or none (you still get ranked papers, with their abstracts).\n"
    "I'll check this computer and recommend one."
)
DESCRIBE_HELP = (
    "A shortcut: describe what you research or want to follow, in a few sentences, and the\n"
    "model proposes topics and seed papers for you to accept or reject one by one.\n"
    'Example: "I study security of LLM agents, especially prompt injection, and I teach an\n'
    'intro AI course." Press Enter to skip and add them yourself.'
)
TOPICS_HELP = (
    "Topics are short phrases for what you want to read about. Every new paper is compared\n"
    "with each topic, and the closest matches rank highest. Specific phrases work better\n"
    'than broad fields: "prompt injection attacks on LLM agents" beats "security".\n'
    "5 to 15 topics is plenty; near-duplicates add nothing, so use one per idea."
)
SEEDS_HELP = (
    "A seed paper is an example of exactly the kind of paper you want more of. You don't\n"
    "need to have read it; it just has to be on target. New papers are compared with each\n"
    "seed's title and abstract, which says far more than a short topic, so seeds are the\n"
    "strongest way to sharpen your feed. Paste an arXiv link, its id (e.g. 2307.15043) or\n"
    "its title; I look it up and you confirm. 3 to 10 is a good start."
)
SEEDS_TIP = (
    'Tip: add more any time with rpipe profile add-seed. "More like this" in the app also '
    "nudges ranking, but fades over weeks; seeds don't."
)
NEXT_COMMANDS = (
    "  rpipe run               collect today's papers, rank them and summarize the top ones\n"
    "                          (the first run can take an hour or more with a local model)\n"
    "  rpipe schedule install  do that every night by itself\n"
    "  rpipe app install       read your feed on your phone and desktop\n"
    "  rpipe share setup       optional: publish your feed so others can follow it"
)


def _step(n: int, name: str) -> None:
    typer.echo(f"\nStep {n} of 5: {name}")


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
    pick = _prompt(f"Pick 1-{n} or 0 to cancel", type=int)
    if not 0 <= pick <= n:
        raise _SeedProblem(f"{pick} is not between 0 and {n}. Nothing was changed.")
    return pick - 1 if pick else None


def _choose(candidates: list[PaperMeta], yes: bool) -> PaperMeta | None:
    if yes:
        return candidates[0]
    if len(candidates) == 1:
        typer.echo(_describe(candidates[0]))
        return candidates[0] if _confirm("Add this paper?", default=True) else None
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


LLM_DOWN = "Summary model unavailable; add topics by hand with `rpipe profile add-topic`"


def _onboarding_llm(config: Config) -> LLM | None:
    """The checked summary model for onboarding (same resolution as `run`); tests replace it."""
    return _ready_llm(build_pipeline(config))


def _suggest_flow(vault: Path, config: Config, description: str, yes: bool) -> bool:
    """Propose topics and seeds from `description`, adding each one the user approves.

    Returns False (changing nothing) if the summary model is unavailable. Raises ValueError for
    a too-short description, before any model call."""
    description = onboarding.check_description(description)
    try:
        llm = _onboarding_llm(config)
        if llm is None:
            typer.echo(NO_MODEL)
            return False
        if getattr(llm, "local", True) is not True:
            typer.echo(
                f"Your description will be sent to {llm.name} (model {llm.model_name}) to suggest topics."
            )
        with _reporter() as reporter, stage(reporter, "suggest", label="asking the model for topics and papers"):
            proposal = onboarding.propose(llm, description)  # no prompt while this reporter is open
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
        if yes or _confirm(f"Add topic '{t}'?", default=True):
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
            with _reporter() as reporter:
                resolved = onboarding.resolve_seeds(proposal.seed_queries, arxiv, reporter=reporter)
        except SourceError as exc:
            typer.echo(f"Could not reach arXiv: {exc}. No seeds were added.")
            return True
        found = [m for _, m in resolved if m is not None]
        typer.echo(f"Found {len(found)} of {len(resolved)} proposed papers on arXiv (the rest were dropped).")
        for meta in found:
            if yes or _confirm(f"Add seed {_describe(meta)}?", default=True):
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
    """Let the configured model propose topics and seed papers from a description; you approve each."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe profile suggest"):
        _require_model(ctx.config)
        if description is None:
            description = _prompt("Describe your research in a few sentences")
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


# ---------------------------------------------------------------- shelf commands

shelf_app = typer.Typer(help="Your own notes and writing (the shelf): papers close to them rank higher.", no_args_is_help=True)
app.add_typer(shelf_app, name="shelf")


def _looks_like_path(source: str) -> bool:
    """A single token ending .md/.txt, or naming a directory: the user meant a path, not a note."""
    s = source.strip()
    if not s:
        return False
    if len(s.split()) == 1 and s.lower().endswith((".md", ".txt")):
        return True
    try:
        return Path(s).expanduser().is_dir()
    except (OSError, ValueError):
        return False


def _item_count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@shelf_app.command("add")
def shelf_add(
    source: Annotated[str, typer.Argument(help="A .md or .txt file to copy in, or the text of a note.")],
    vault: VaultOption = None,
) -> None:
    """Add a file or a short note to the shelf and index it."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe shelf add"):
        path = Path(source.strip()).expanduser() if _looks_like_path(source) else None
        if path is not None and not path.is_file():
            raise _fail(
                f"File not found: {source.strip()}. Check the path, or put the note in quotes "
                'with spaces, e.g. rpipe shelf add "my idea about fuzzing".'
            )
        with _reporter() as reporter:
            embedder = _embedder(build_pipeline(ctx.config), ctx.config.embedding_model, reporter=reporter)
        with _clean_errors():
            item = library_api.shelf_add(ctx.conn, ctx.vault / "my_work", embedder, source)
        typer.echo(f"Added '{item.name}' to your shelf. Papers close to it will now rank higher.")


@shelf_app.command("list")
def shelf_list(vault: VaultOption = None) -> None:
    """Show what is on the shelf."""
    ctx = _open(vault)
    items = library_api.shelf_items(ctx.conn)
    if not items:
        typer.echo('Your shelf is empty. Add a note or file with: rpipe shelf add "your idea" (or a .md/.txt path).')
        return
    typer.echo(f"Shelf: {_item_count(len(items), 'item')}")
    for item in items:
        typer.echo(f"  {item.name}  {_item_count(item.chunks, 'chunk')}  added {item.added_at[:10]}")


@shelf_app.command("remove")
def shelf_remove(
    name: Annotated[str, typer.Argument(help="The item name shown by `rpipe shelf list`.")],
    vault: VaultOption = None,
) -> None:
    """Take an item off the shelf (its file is moved to my_work/.removed/, not deleted)."""
    ctx = _open(vault)
    try:
        moved = library_api.shelf_remove(ctx.conn, ctx.vault / "my_work", name)
    except KeyError as exc:
        raise _fail(str(exc.args[0])) from exc
    except ValueError as exc:
        raise _fail(str(exc)) from exc
    if len(moved) > 1:
        typer.echo(f"Removed '{name}'. Moved {len(moved)} files to my_work/.removed/: {', '.join(moved)}.")
    else:
        typer.echo(f"Removed '{name}'. The file is in my_work/.removed/ if you want it back.")


@shelf_app.command("sync")
def shelf_sync(vault: VaultOption = None) -> None:
    """Re-index my_work/ after you added, edited or deleted files by hand."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe shelf sync"):
        with _reporter() as reporter:
            embedder = _embedder(build_pipeline(ctx.config), ctx.config.embedding_model, reporter=reporter)
            res = library_api.shelf_sync(ctx.conn, ctx.vault / "my_work", embedder, reporter=reporter)
        typer.echo(f"Shelf synced: {len(res.added)} added, {len(res.updated)} updated, {len(res.removed)} removed.")
        for label, names in (("Added", res.added), ("Updated", res.updated), ("Removed", res.removed)):
            if names:
                typer.echo(f"  {label}: {', '.join(names)}")
        if res.ignored:
            typer.echo(
                f"Ignored {len(res.ignored)} file(s): {', '.join(res.ignored)}. "
                "Only .md and .txt files directly in my_work/ are indexed "
                "(if a .md and a .txt share a name, the .md is used)."
            )


# ---------------------------------------------------------------- gold commands

gold_app = typer.Typer(help="Manage the gold set: papers with the facts a correct summary must contain.", no_args_is_help=True)
app.add_typer(gold_app, name="gold")


def _ask_facts() -> list[str]:
    """Read key facts one per line until a blank line. Ctrl-C/EOF aborts before anything is saved."""
    typer.echo("Key facts a correct summary must get right (specific and checkable).")
    typer.echo("Type one per line; press Enter on an empty line to finish. Aim for 3 or more.")
    facts: list[str] = []
    while True:
        line = profile_edit.normalise_text(_prompt(f"Fact {len(facts) + 1}", default="", show_default=False))
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
    with _pipeline_lock(ctx, "rpipe gold add"):
        if text is None:
            text = _prompt("arXiv link, id or title")
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
        mark = "  (unreviewed: drafted by Claude)" if p.unreviewed else ""
        typer.echo(f"\n{profile_edit.normalise_text(p.paper_id)}  {profile_edit.normalise_text(p.title)}{mark}")
        for n, fact in enumerate(p.key_facts, 1):
            typer.echo(f"  {n}. {profile_edit.normalise_text(fact)}")


@gold_app.command("suggest")
def gold_suggest(
    count: Annotated[int, typer.Option("--count", min=1, help="How many papers to suggest.")] = GOLD_TARGET,
    vault: VaultOption = None,
) -> None:
    """Suggest papers from your vault for the gold set, spread across short to long."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe gold suggest"):
        with _clean_errors():
            picks = suggest_papers(ctx.conn, load_gold(ctx.vault), count)
        if not picks:
            typer.echo(
                "No summarized papers with full text yet — run `rpipe run` first, "
                "or add papers by hand with `rpipe gold add`."
            )
            return
        typer.echo(f"{len(picks)} suggested paper(s), shortest to longest:")
        for s in picks:
            typer.echo(f"  {s.paper_id}  ~{s.tokens} tokens  {profile_edit.normalise_text(s.title)}")
        typer.echo("Pick the ones you know well, then: rpipe gold draft ID ...")


@gold_app.command("auto")
def gold_auto(
    count: Annotated[int | None, typer.Option("--count", min=1, help="How many papers to add (default: fill the set up to 10).")] = None,
    vault: VaultOption = None,
) -> None:
    """Fill the gold set with no questions: pick papers, let Claude draft the facts, save them unreviewed."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe gold auto"):
        pipeline = build_pipeline(ctx.config)
        try:
            llm = pipeline.make_judge()
        except judge.JudgeUnavailable as exc:
            raise _fail(f"The judge is not available: {exc}") from exc
        with _clean_errors():
            existing = load_gold(ctx.vault)
            wanted = count if count is not None else GOLD_TARGET - len(existing)
            if wanted <= 0:
                typer.echo(f"The gold set already has {len(existing)} papers; nothing to add. Use --count N to add more.")
                return
            picks = suggest_papers(ctx.conn, existing, wanted)
        if not picks:
            typer.echo("No summarized papers with full text yet: run `rpipe run` first, or add papers by hand with `rpipe gold add`.")
            return
        typer.echo(
            "The paper text is sent to Claude to draft the facts. Nothing is reviewed: every paper is saved as unreviewed, "
            "so the scores only compare runs with each other."
        )
        added = 0
        with _reporter() as reporter, stage(reporter, "gold auto", total=len(picks)) as handle:
            for number, pick in enumerate(picks, 1):
                try:
                    # The title is already in the vault, so only the HTML page is fetched (no arXiv API call).
                    sections = ensure_usable(pipeline.fulltext_fetcher(pick.paper_id))
                    facts = [profile_edit.normalise_text(f) for f in judge.draft_facts(llm, pick.title, sections)]
                    facts = [f for f in facts if f]
                    if not facts:
                        handle.step(pick.paper_id, current=number, outcome="no facts")
                        continue
                    add_gold(ctx.vault, GoldPaper(paper_id=pick.paper_id, title=pick.title, key_facts=facts, unreviewed=True))
                    added += 1
                    handle.step(pick.paper_id, current=number)
                except FullTextUnavailable:
                    handle.step(pick.paper_id, current=number, outcome="no full text")
                except LlmLimitReached:
                    handle.step(pick.paper_id, current=number, outcome="usage limit reached")
                    break
                except (LlmError, SourceError) as exc:
                    handle.step(pick.paper_id, current=number, outcome="failed", detail=str(exc))
        typer.echo(f"Added {added} paper(s), all unreviewed. See them with `rpipe gold list`; replace any later with `rpipe gold remove ID` then `rpipe gold draft ID`.")


@gold_app.command("draft")
def gold_draft(
    ids: Annotated[list[str], typer.Argument(help="One or more arXiv ids to draft key facts for.")],
    vault: VaultOption = None,
) -> None:
    """Draft key facts for papers with the cloud judge, then review them one by one."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe gold draft"):
        pipeline = build_pipeline(ctx.config)
        try:
            llm = pipeline.make_judge()
        except judge.JudgeUnavailable as exc:
            raise _fail(f"The judge is not available: {exc}") from exc
        with _clean_errors():
            existing = load_gold(ctx.vault)
        typer.echo("The paper text is sent to Claude to draft the facts. You review every one before it is saved.")
        for number, paper_id in enumerate(ids, 1):
            paper_id = paper_id.strip()
            if has_gold(existing, paper_id):
                typer.echo(f"{paper_id} is already in your gold set. Add facts with: rpipe gold add-fact {paper_id} \"...\"")
                continue
            try:
                # The stage covers only the waiting work; review prompts happen after it closes.
                with _reporter() as reporter, stage(reporter, "gold draft", total=len(ids), label=paper_id) as handle:
                    found = pipeline.seed_fetcher([paper_id])
                    if not found:
                        handle.step(paper_id, current=number, outcome="not found")
                        drafted = None
                    else:
                        try:
                            sections = ensure_usable(pipeline.fulltext_fetcher(found[0].id))
                        except FullTextUnavailable:
                            handle.step(paper_id, current=number, outcome="no full text")
                            drafted = []
                        else:
                            drafted = judge.draft_facts(llm, found[0].title, sections)
                            handle.step(paper_id, current=number)
            except judge.JudgeUnavailable as exc:
                raise _fail(f"The judge is not available: {exc}") from exc
            except (LlmError, SourceError) as exc:
                typer.echo(f"Could not draft facts for {paper_id}: {exc}. Add it by hand with `rpipe gold add {paper_id}`.")
                continue
            if drafted is None:
                typer.echo(f"No paper found for {paper_id} on arXiv.")
                continue
            if not drafted:
                typer.echo(f"There is no HTML full text for {paper_id}; add it by hand with `rpipe gold add {paper_id}`.")
                continue
            title = found[0].title
            try:
                facts = _review_drafted(drafted, profile_edit.normalise_text(title))
            except (typer.Abort, KeyboardInterrupt, EOFError):
                typer.echo(f"\nStopped. Nothing was saved for {paper_id}.")
                return
            if not facts:
                typer.echo(f"No facts kept, so nothing was saved for {paper_id}.")
                continue
            add_gold(ctx.vault, GoldPaper(paper_id=found[0].id, title=title, key_facts=facts))
            existing = load_gold(ctx.vault)
            typer.echo(f"Added {paper_id} with {len(facts)} fact(s).")


def _review_drafted(drafted: list[str], title: str) -> list[str]:
    """Walk the drafted facts (keep, edit or drop each), then ask for extra ones."""
    typer.echo(f"\n{title}")
    kept: list[str] = []
    for n, fact in enumerate(drafted, 1):
        typer.echo(f"  {n}. {fact}")
        choice = ""
        while choice not in ("k", "e", "d"):
            choice = _prompt("Keep, edit or drop? [k/e/d]", default="k", show_default=False).strip().lower()
        if choice == "k":
            kept.append(fact)
        elif choice == "e":
            new = profile_edit.normalise_text(_prompt("New text (empty keeps the draft)", default="", show_default=False))
            kept.append(new or fact)
    return kept + _ask_facts()


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
        paper_id = _prompt("Paper id")
    matches = [p for p in papers if has_gold([p], paper_id)]
    if not matches:
        raise _fail(f"{paper_id} is not in your gold set. Add it first: rpipe gold add {paper_id}")
    paper = matches[0]
    if number is None:
        typer.echo(f"{paper.paper_id}  {paper.title}")
        for n, fact in enumerate(paper.key_facts, 1):
            typer.echo(f"  {n}. {fact}")
        number = _prompt(f"Number of the fact to {verb}", type=int)
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
            text = _prompt("New text for the fact")
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
        if not yes and not _confirm(f'Remove fact {number} of {paper_id} ("{fact}")?', default=False):
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
def eval_run(
    vault: VaultOption = None,
    use_judge: Annotated[bool, typer.Option("--judge", help="Also grade every key fact with the Claude judge.")] = False,
    mode: Annotated[str | None, typer.Option(
        "--mode", help="How to read papers for this eval only: auto, whole or split (default: your summarize mode).",
    )] = None,
) -> None:
    """Summarize every gold-set paper with the local model and write a report to grade."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe eval run"):
        _require_model(ctx.config)
        setting = ctx.config.summarize_mode if mode is None else mode.strip().lower()
        if setting not in profile_edit.SUMMARIZE_MODES:
            raise _fail(f"unknown mode {mode!r}: choose one of {', '.join(profile_edit.SUMMARIZE_MODES)}")
        with _clean_errors():
            papers = load_gold(ctx.vault)
        if not papers:
            raise _fail("Your gold set is empty. Add papers you know well with `rpipe gold add` first.")
        if len(papers) < GOLD_TARGET:
            typer.echo(f"Warning: the gold set has {len(papers)} papers; {GOLD_TARGET} make a fair test (rpipe gold add).")
        pipeline = build_pipeline(ctx.config)
        eval_judge: LLM | None = None
        if use_judge:
            try:
                eval_judge = pipeline.make_judge()
            except judge.JudgeUnavailable as exc:
                raise _fail(f"The judge is not available: {exc}") from exc
            typer.echo("The paper text is sent to Claude to grade the summaries.")
        try:
            llm = _ready_llm(pipeline)
        except Exception as exc:  # noqa: BLE001
            raise _fail(f"model unavailable: {exc}") from exc
        if llm is None:
            raise _fail(NO_MODEL)

        def abstract_for(paper_id: str) -> str:
            found = pipeline.seed_fetcher([paper_id])
            return found[0].abstract if found else ""

        today = date.today()
        if eval_exists(ctx.vault, today):
            typer.echo(f"Replacing today's earlier eval ({today.isoformat()}); its grades are cleared.")
            if has_judged_run(ctx.vault, today):
                typer.echo("Kept today's earlier judged run as the baseline for the comparison.")
        typer.echo(f"Summarizing {len(papers)} paper(s); this can take a while...")
        with _clean_errors():
            with _reporter() as reporter:
                read_mode, _share, diagrams = _read_plan(
                    llm, setting, reporter, set_with=None if mode is None else "--mode for this eval",
                    diagrams=ctx.config.diagrams)
                md = run_eval(ctx.vault, llm, pipeline.fulltext_fetcher, llm.num_ctx, today, abstract_for,
                              reporter=reporter, judge=eval_judge, mode=read_mode, diagrams=diagrams)
        typer.echo(f"Report written: {md}")
        if eval_judge:
            typer.echo("Next: rpipe eval report")
        else:
            typer.echo("Next: rpipe eval grade, then rpipe eval report (or re-run with --judge to have Claude grade)")


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
                answer = _prompt("Is this summary accurate? [y/n]").strip().lower()
                if answer in ("y", "n"):
                    break
                typer.echo("Please answer y or n.")
            note = _prompt("Note (optional)", default="", show_default=False).strip()
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


model_app = typer.Typer(help="Show, change and check the summary model and its backend.", no_args_is_help=True)
app.add_typer(model_app, name="model")


def _ollama_only(cfg: Config, command: str) -> None:
    if cfg.llm_backend != "ollama":
        raise _fail(
            f"`rpipe model {command}` only applies to the ollama backend "
            f"(current: {cfg.llm_backend}). Nothing was changed."
        )


@model_app.command("show")
def model_show(vault: VaultOption = None) -> None:
    """Show the backend and which model (and server) are configured."""
    ctx = _open(vault)
    cfg = ctx.config
    typer.echo(f"backend: {cfg.llm_backend}")
    if cfg.llm_backend == "none":
        typer.echo("No summary model: digests list abstracts only. Choose one with `rpipe model setup`.")
        return
    typer.echo(f"model: {cfg.llm_model}")
    typer.echo(f"summarize mode: {cfg.summarize_mode} (change with `rpipe model set-mode`)")
    typer.echo(f"diagrams: {cfg.diagrams} (change with `rpipe model set-diagrams`)")
    if cfg.summarize_mode == "auto":
        typer.echo(f"  auto: split when the model is not fully in GPU memory or the context is "
                   f"below {SPLIT_BELOW_CTX} tokens (Ollama); whole otherwise")
    if cfg.llm_backend == "claude-code":
        typer.echo(f"effort: {cfg.llm_effort}")
    else:
        typer.echo(f"effort: {cfg.llm_effort} (only applies to the claude-code backend)")
    if cfg.llm_backend != "ollama":
        if cfg.llm_backend == "claude-code":
            typer.echo(f"claude: {cfg.llm_cli_path or 'found on PATH when used'}")
        return
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
    name: Annotated[str, typer.Argument(help="Model name (ollama: e.g. qwen3:14b; claude-code: sonnet, haiku, opus), or auto.")],
    vault: VaultOption = None,
) -> None:
    """Choose which model writes summaries (ollama: install it first with ollama pull NAME; claude-code: sonnet, haiku, opus)."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_llm_model(ctx.vault, name)
    typer.echo(f"model is now {name.strip()}. Check it works with `rpipe model check`.")


@model_app.command("set-effort")
def model_set_effort(
    level: Annotated[str, typer.Argument(help="low (fast, default), medium or high (slower, more thinking).")],
    vault: VaultOption = None,
) -> None:
    """Choose how hard Claude Code thinks per call: low, medium or high (only the claude-code backend uses it)."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_llm_effort(ctx.vault, level)
    note = "" if ctx.config.llm_backend == "claude-code" else " (saved, but only the claude-code backend uses it)"
    typer.echo(f"effort is now {level.strip().lower()}{note}.")


@model_app.command("set-embedding")
def model_set_embedding(
    name: Annotated[str, typer.Argument(help="Sentence-embedding model name, e.g. BAAI/bge-small-en-v1.5.")],
    vault: VaultOption = None,
) -> None:
    """Choose the model that measures how similar papers are to your profile."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_embedding_model(ctx.vault, name)
    typer.echo(f"embedding model is now {name.strip()}.")
    typer.echo(
        "The next run downloads the model (the first download can take a while; progress is shown) "
        "and re-embeds your shelf, seed papers and the papers still waiting to be ranked, because "
        "vectors from different models cannot be compared. Papers ranked earlier are not re-embedded, "
        "so your past votes and saves stop boosting rankings until you switch back."
    )
    typer.echo("To go back: rpipe model set-embedding BAAI/bge-small-en-v1.5")


@model_app.command("set-backend")
def model_set_backend(
    name: Annotated[str, typer.Argument(help="Backend: ollama, claude-code, none, or an installed plug-in.")],
    vault: VaultOption = None,
    force: Annotated[bool, typer.Option("--force", help="Switch without checking the backend is ready.")] = False,
) -> None:
    """Choose where summaries come from. Checks the backend first unless --force. Changing backend resets the model to auto; re-choosing the current one keeps it."""
    ctx = _open(vault)
    cfg = ctx.config
    name = name.strip()
    known = {n for n, p in registry.plugins(registry.BACKEND_GROUP).items() if p.factory}
    cli_path: str | None = None
    changed = cfg.llm_backend != name
    if name == "claude-code":
        cli_path = find_claude(cfg.llm_cli_path)
        if not force:
            if cli_path is None:
                raise _fail(
                    "Claude Code not found (looked at the recorded path, PATH and ~/.local/bin). "
                    "Install it and run `rpipe model setup`, or use --force to switch anyway."
                )
            if not logged_in(cli_path):
                raise _fail(f"Claude Code is not logged in: {LOGIN_HINT} (or use --force to switch anyway).")
    elif name == "ollama" and not force:
        try:
            OllamaClient(cfg.llm_url, "", 8192).ping()
        except LlmError as exc:
            raise _fail(f"{exc} (or use --force to switch anyway).") from exc
    with _clean_errors():
        profile_edit.set_llm_backend(ctx.vault, name, known, cli_path)
    note = "; model reset to auto" if changed else "; model unchanged"
    typer.echo(f"backend is now {name}{note}. Check it with `rpipe model check`.")


@model_app.command("set-url")
def model_set_url(
    url: Annotated[str, typer.Argument(help="Ollama server address, e.g. http://localhost:11434.")],
    vault: VaultOption = None,
) -> None:
    """Point the pipeline at a different Ollama server (must start with http:// or https://)."""
    ctx = _open(vault)
    _ollama_only(ctx.config, "set-url")
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
    _ollama_only(ctx.config, "set-context")
    with _clean_errors():
        profile_edit.set_llm_num_ctx(ctx.vault, n)
    typer.echo("context window is now auto." if n.strip().lower() == "auto"
               else f"context window is now {int(n)} tokens.")


@model_app.command("set-mode")
def model_set_mode(
    mode: Annotated[str, typer.Argument(help="auto (chosen per run), whole (one call per paper) or split (read in parts).")],
    vault: VaultOption = None,
) -> None:
    """Choose how papers are read: whole, split (front matter and body notes, then combined) or auto."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_summarize_mode(ctx.vault, mode)
    stored = load_config(ctx.vault / "config.yaml").summarize_mode
    if stored == "auto":
        typer.echo("summarize mode is now auto: each run picks split or whole (see `rpipe model show`).")
    else:
        typer.echo(f"summarize mode is now {stored}.")


@model_app.command("set-diagrams")
def model_set_diagrams(
    setting: Annotated[str, typer.Argument(help="auto (off on a CPU-only model), on or off.")],
    vault: VaultOption = None,
) -> None:
    """Choose whether each paper also gets a diagram (one more model call): auto, on or off."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_diagrams(ctx.vault, setting)
    stored = load_config(ctx.vault / "config.yaml").diagrams
    if stored == "auto":
        typer.echo("diagrams is now auto: drawn unless the model runs on the CPU only (see `rpipe model show`).")
    else:
        typer.echo(f"diagrams is now {stored}.")


@model_app.command("fit")
def model_fit(vault: VaultOption = None) -> None:
    """Probe Ollama now: choose the best installed model and the largest context that fits in GPU memory."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe model fit"):
        cfg = ctx.config
        _ollama_only(cfg, "fit")
        try:
            if cfg.llm_model != "auto" and cfg.llm_num_ctx != "auto":
                typer.echo(f"pinned: {cfg.llm_model} at {cfg.llm_num_ctx} tokens (nothing to probe)")
                typer.echo(build_llm(cfg, ctx.vault).check())
                return
            typer.echo("Probing Ollama: this loads and unloads models and can take a few minutes.")
            with _reporter() as reporter, stage(reporter, "model fit") as handle:
                fit = ollama_fit.resolve(
                    cfg.llm_url, ctx.vault, cfg.llm_model, cfg.llm_num_ctx, force=True, log=handle.note,
                )
        except (LlmError, OSError, ValueError) as exc:
            raise _fail(str(exc)) from exc
        typer.echo(f"Using model {fit.model} with a context of {fit.num_ctx} tokens")


@model_app.command("bench")
def model_bench(vault: VaultOption = None) -> None:
    """Measure how fast the chosen model reads and writes, and estimate the time per paper."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe model bench"):
        if ctx.config.llm_backend != "ollama":
            raise _fail("the benchmark measures local models; the cloud backend's speed is the "
                        "provider's. Nothing was measured.")
        try:
            _measure_speed(ctx.vault, ctx.conn, ctx.config)
        except (LlmError, OSError, ValueError) as exc:
            raise _fail(str(exc)) from exc


def _fit_cached(cfg: Config, vault: Path) -> bool:
    """Whether the context for the configured model is already measured (False if unknown)."""
    try:
        return ollama_fit.cached_fit(cfg.llm_url, vault, cfg.llm_model, cfg.llm_num_ctx) is not None
    except (LlmError, OSError, ValueError):
        return False


def _measure_speed(vault: Path, conn: sqlite3.Connection, cfg: Config) -> None:
    """Time the configured Ollama model, save the result and print it (raises LlmError on failure)."""
    if cfg.llm_num_ctx == "auto" and not _fit_cached(cfg, vault):
        typer.echo("The context that fits is not measured yet, so this first measures the context that fits "
                   "(it loads and unloads models and can take up to 15 minutes), then the speed.")
    typer.echo("Measuring speed: this loads the model and takes about 1-3 minutes on a GPU, "
               "up to 5 on a CPU.")
    with _reporter() as reporter, stage(reporter, "model bench") as handle:
        fit = ollama_fit.resolve(cfg.llm_url, vault, cfg.llm_model, cfg.llm_num_ctx, log=handle.note)
        tokens = bench_mod.median_paper_tokens(conn)
        handle.note(f"timing {fit.model} at {fit.num_ctx} tokens")
        result = bench_mod.run_bench(cfg.llm_url, fit.model, fit.num_ctx, tokens)
    bench_mod.save_bench(vault, result)
    typer.echo(
        f"{fit.model}: reads {result.read_tps:.0f} tokens/s, writes {result.write_tps:.0f} tokens/s; "
        f"about {result.seconds_per_paper:.0f} s per paper ({result.paper_tokens:,}-token paper), "
        f"about {result.seconds_per_paper * 15 / 60:.0f} min for 15 papers"
    )


def _scan_server(url: str):
    return detect_ollama_server(url, system_cmd)


def _run_plan(commands: list[list[str]], undo_hint: bool = False) -> bool:
    """Run each command in the user's terminal; stop at the first failure and say so.

    With `undo_hint`, a failure after the first command (which wrote the file) also prints the
    undo command, since the settings file may now be in place."""
    for i, argv in enumerate(commands):
        typer.echo(f"$ {' '.join(argv)}")
        if ollama_tune.run_tty(argv) != 0:
            typer.echo(f"Failed: {' '.join(argv)}. The commands after it did not run.")
            if undo_hint and i > 0:
                typer.echo(f"To undo: {ollama_tune.UNDO_COMMAND}")
            return False
    return True


def _wait_for_ollama(url: str) -> bool:
    with _reporter() as reporter, stage(reporter, "ollama restart", label="waiting for Ollama") as handle:
        handle.note("waiting up to 30 s for the server to answer")
        return ollama_tune.wait_ready(url)


def _tune_undo(url: str) -> None:
    if not ollama_tune.DROPIN.exists():
        typer.echo(f"Nothing to undo: {ollama_tune.DROPIN} does not exist.")
        return
    server = _scan_server(url)
    note = ollama_tune.host_note(server, url)
    if note:
        typer.echo(note)
    wait_url = ollama_tune.service_url(server, url)
    commands = ollama_tune.undo_commands()
    typer.echo("This removes only rpipe's own settings file; other files are untouched. Commands:")
    for argv in commands:
        typer.echo("  " + " ".join(argv))
    if not _confirm("Undo now?", default=False):
        typer.echo("Nothing was changed.")
        return
    if not _run_plan(commands):
        raise typer.Exit(code=1)
    if not _wait_for_ollama(wait_url):
        raise _fail("Ollama did not answer after the restart; check `systemctl status ollama`")
    typer.echo("Done: rpipe's settings are removed and Ollama is running again.")


@model_app.command("tune")
def model_tune(
    undo: Annotated[bool, typer.Option("--undo", help="Remove the settings rpipe added.")] = False,
    vault: VaultOption = None,
) -> None:
    """Offer Ollama settings that fit longer contexts in GPU memory; asks first, verifies, can be undone."""
    if not _is_tty():
        raise _fail("rpipe model tune is interactive (it asks before using sudo); run it in a terminal")
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe model tune"):
        cfg = ctx.config
        url = cfg.llm_url
        if undo:
            _tune_undo(url)
            return
        with _reporter() as reporter, stage(reporter, "scan", label="the Ollama server"):
            server = _scan_server(url)
        note = ollama_tune.host_note(server, url)
        if note:
            typer.echo(note)
        settings = ollama_tune.advise(server)
        if not settings:
            typer.echo("Ollama already has the recommended settings; nothing to change.")
            for key in (ollama_tune.FLASH, ollama_tune.KV_TYPE, ollama_tune.PARALLEL):
                if key in server.env:
                    source = setting_source(server, key)
                    typer.echo(f"  {key}={server.env[key]}" + (f" (set in {source})" if source else ""))
            return
        if server.how != "systemd":
            typer.echo(ollama_tune.manual_instructions(server, settings))
            return
        if server.host_matches is False:
            typer.echo("Warning: changing the service would not change the server rpipe uses.")
            if not _confirm("Continue anyway?", default=False):
                typer.echo("Nothing was changed.")
                return
        for s in settings:
            typer.echo(f"{s.name}: {s.current or 'not set'} -> {s.value}\n  what: {s.what}\n  why: {s.why}")
        written = ollama_tune.owned(server)
        content = ollama_tune.render_dropin(written)
        tmp_dir = Path(tempfile.mkdtemp())
        tmp = tmp_dir / "rpipe.conf"
        try:
            tmp.write_text(content, encoding="utf-8")
            commands = ollama_tune.apply_commands(tmp)
            typer.echo(f"\nThis writes {ollama_tune.DROPIN} (it always lists all three of rpipe's settings, so a "
                       f"later re-tune never drops one set earlier):\n{content}\nCommands (sudo will ask for your password):")
            for argv in commands:
                typer.echo("  " + " ".join(argv))
            typer.echo(f"To undo later: {ollama_tune.UNDO_COMMAND}")
            if not _confirm("Apply?", default=False):
                typer.echo("Nothing was changed.")
                return
            before = None
            if cfg.llm_backend == "ollama" and server.host_matches is not False:
                try:
                    before = ollama_fit.cached_fit(url, ctx.vault, cfg.llm_model, cfg.llm_num_ctx)
                except (LlmError, OSError, ValueError):
                    before = None
            if not _run_plan(commands, undo_hint=True):
                raise typer.Exit(code=1)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if not _wait_for_ollama(ollama_tune.service_url(server, url)):
            raise _fail("Ollama did not answer after the restart; check `systemctl status ollama`. "
                        f"To undo: {ollama_tune.UNDO_COMMAND}")
        after = _scan_server(url)
        missing = [s for s in written if after.env.get(s.name) != s.value]
        if missing:
            raise _fail("restarted, but these are not live: " + ", ".join(s.name for s in missing)
                        + f". To undo: {ollama_tune.UNDO_COMMAND}")
        typer.echo("All settings are live: " + ", ".join(f"{s.name}={s.value}" for s in written))
        if server.host_matches is False:
            typer.echo("Skipping the context re-measure: it would measure the server rpipe uses, "
                       "which is not the service that changed.")
            return
        if cfg.llm_backend == "ollama":
            typer.echo("Re-measuring the context that fits (this probes the GPU and can take a few minutes).")
            try:
                with _reporter() as reporter, stage(reporter, "model fit") as handle:
                    fit = ollama_fit.resolve(url, ctx.vault, cfg.llm_model, cfg.llm_num_ctx, force=True,
                                             log=handle.note)
            except (LlmError, OSError, ValueError) as exc:
                raise _fail(str(exc)) from exc
            old = f"{before.num_ctx:,}" if before else "not measured before"
            typer.echo(f"context: {old} -> {fit.num_ctx:,} tokens ({fit.model})")


@model_app.command("check")
def model_check(vault: VaultOption = None) -> None:
    """Check that the backend works (ollama: running, model installed; claude-code: logged in). With ollama `auto` settings this may probe the GPU (slow the first time)."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe model check"):
        _require_model(ctx.config)
        try:
            with _reporter() as reporter, stage(reporter, "model check") as handle:
                llm = build_llm(ctx.config, ctx.vault, log=handle.note)
                line = llm.check() if hasattr(llm, "check") else f"{llm.name} backend is configured."
            typer.echo(line)
        except (LlmError, OSError, ValueError) as exc:
            raise _fail(str(exc)) from exc


# ---------------------------------------------------------------- init wizard


def _ask(prompt: str) -> str:
    """One line of input; blank returns "". Ctrl-C or end of input raises typer.Abort."""
    return _prompt(prompt, default="", show_default=False).strip()


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
        if not _confirm("Add more topics?", default=False):
            return
    else:
        typer.echo(TOPICS_HELP)
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
    ticked = set(config.arxiv_categories)
    if not fresh_config:
        typer.echo(f"arXiv categories: {_categories_text(config.arxiv_categories)}")
        if not _confirm("Change categories?", default=False):
            return
    typer.echo(CATEGORY_HELP)
    typer.echo("")
    for i, (code, text) in enumerate(categories.COMMON, 1):
        typer.echo(f"  [{'x' if code in ticked else ' '}] {i:>2}. {code:<8} {text}")
    extra = [c for c in config.arxiv_categories if c not in dict(categories.COMMON)]
    if extra:
        typer.echo(f"Also selected: {_categories_text(extra)}")
    typer.echo("")
    typer.echo(CATEGORY_PROMPT_HELP)
    entered = _prompt("Categories", default=current)
    try:
        cats = categories.parse_choice(entered, config.arxiv_categories)
        if cats != config.arxiv_categories:
            profile_edit.set_categories(vault, cats)
    except (ValueError, ConfigError, OSError) as exc:
        typer.echo(f"Skipped: {exc}")


def _wizard_seeds(vault: Path, config: Config) -> None:
    typer.echo(SEEDS_HELP)
    arxiv = make_arxiv(config)  # one client for the whole run, so lookups stay 3 s apart
    try:
        while text := _ask("Paper link, arXiv id or title (blank to finish)"):
            try:
                typer.echo(_add_seed(vault, arxiv, text))
            except _SeedProblem as exc:
                typer.echo(f"Skipped: {exc}")
    finally:
        _close(arxiv)
    if len(load_profile(vault / "profile.yaml").seeds) < 3:
        typer.echo(SEEDS_TIP)


def _describe_machine(info) -> None:
    typer.echo("What I found on this computer:")
    typer.echo(f"  system: {info.os}" + (f", {info.ram_gb:.0f} GB of memory" if info.ram_gb else ""))
    if info.gpu is None:
        typer.echo("  graphics card: no GPU found")
    else:
        typer.echo(f"  graphics card: {info.gpu.name}, {info.gpu.vram_gb:.1f} GB of GPU memory")
    if not info.ollama_installed:
        typer.echo("  Ollama: not installed")
    else:
        models = ", ".join(info.ollama_models) or "no models pulled yet"
        typer.echo(f"  Ollama: installed, {'running' if info.ollama_running else 'not running'}; {models}")
    if info.ram_free_gb is not None:
        typer.echo(f"  memory free right now: {info.ram_free_gb:.0f} GB")
    if info.cpu is not None:
        typer.echo(f"  CPU: {info.cpu.threads} threads, vector instructions: {', '.join(info.cpu.vector) or 'none found'}")
    if info.disk_free_gb is not None:
        typer.echo(f"  free disk where Ollama keeps models: {info.disk_free_gb:.0f} GB")
    if info.claude_path is None:
        typer.echo("  Claude Code: not found")
    else:
        typer.echo(f"  Claude Code: found at {info.claude_path}, "
                   f"{'logged in' if info.claude_logged_in else 'not logged in'}")


def _describe_ollama_server(server, llm_url: str) -> None:
    """Plain-words lines about how the Ollama server runs and its performance settings."""
    how = {"systemd": "a system service (systemd)", "systemd-user": "a user service (systemd --user)",
           "manual": "started by hand (ollama serve)", "docker": "in Docker", "app": "the Ollama app",
           "unknown": "not found running"}[server.how]
    typer.echo(f"  Ollama server: version {server.version or 'unknown'}, {how}")
    if server.how not in ("systemd", "systemd-user"):
        typer.echo("  (rpipe can only read the server settings of a systemd service)")
        return

    def source(key: str) -> str:
        found = setting_source(server, key)
        return f" (set in {found})" if found else ""

    flash = server.env.get("OLLAMA_FLASH_ATTENTION")
    typer.echo("  flash attention: " + (f"on{source('OLLAMA_FLASH_ATTENTION')}" if flash_on(flash) else "off"))
    typer.echo(f"  context memory type (KV cache): {server.env.get('OLLAMA_KV_CACHE_TYPE', 'f16 (default)')}"
               + (source("OLLAMA_KV_CACHE_TYPE") if "OLLAMA_KV_CACHE_TYPE" in server.env else ""))
    typer.echo(f"  parallel slots: {server.env.get('OLLAMA_NUM_PARALLEL', 'automatic (default)')}")
    if server.proxied:
        try:
            proxy_port = urlparse(llm_url).port or DEFAULT_OLLAMA_PORT
        except ValueError:
            proxy_port = DEFAULT_OLLAMA_PORT
        typer.echo(f"  rpipe reaches this Ollama through a pass-through proxy on port {proxy_port} "
                   "(same models): its settings apply.")
    if server.host_matches is False:
        port = server.env.get("OLLAMA_HOST", "").rpartition(":")[2]
        typer.echo(f"  note: the Ollama service listens on port {port} but rpipe talks to "
                   f"{llm_url.split('://')[-1]}; settings rpipe changes would go to the service, not the server rpipe uses")


@model_app.command("scan")
def model_scan(vault: VaultOption = None) -> None:
    """Read this computer (GPUs, memory, CPU, disk) and the Ollama server's settings; change nothing."""
    ctx = _open(vault)
    with _reporter() as reporter, stage(reporter, "scan", label="this computer"):
        info = detect_machine(ctx.config)
    _describe_machine(info)
    if len(info.gpus) > 1:
        typer.echo("  all GPUs: " + "; ".join(f"{g.name} ({g.vram_gb:.1f} GB)" for g in info.gpus))
    if info.ollama is not None:
        _describe_ollama_server(info.ollama, ctx.config.llm_url)


def _model_kind(e: catalog_mod.CatalogEntry) -> str:
    if e.arch == "moe":
        return f"MoE ({e.active_b:g} active of {e.params_b:g} B)" if e.active_b else "MoE"
    return "dense"


def _machine_summary(info) -> None:
    """Three lines: memory and CPU, the GPU(s) and size class, Ollama and free disk."""
    mem = f"{info.ram_gb:.0f} GB of memory" if info.ram_gb else "memory unknown"
    if info.ram_free_gb is not None:
        mem += f" ({info.ram_free_gb:.0f} GB free now)"
    cpu = f", {info.cpu.threads} CPU threads" if info.cpu is not None else ""
    typer.echo(f"this computer: {info.os}, {mem}{cpu}")
    vram = recommender.total_vram(info)
    if vram == 0:
        gpu = "no GPU found"
    elif len(info.gpus) > 1:
        gpu = " + ".join(f"{g.name} ({g.vram_gb:.1f} GB)" for g in info.gpus) + f" = {vram:.1f} GB"
    else:
        gpu = f"{(info.gpu or info.gpus[0]).name}, {vram:.1f} GB"
    typer.echo(f"GPU: {gpu} (size class: {recommender.band_for(info)})")
    if not info.ollama_installed:
        ollama = "Ollama: not installed"
    else:
        count = len(_generation_models(info.ollama_models))
        ollama = (f"Ollama: {'running' if info.ollama_running else 'not running'}, "
                  f"{count} text model{'s' if count != 1 else ''} installed")
    disk = f"; free disk for models: {info.disk_free_gb:.0f} GB" if info.disk_free_gb is not None else ""
    typer.echo(ollama + disk)


def _pin_off_gpu(vault: Path, name: str) -> None:
    """Pin model and context for a model that runs (partly) outside the GPU."""
    profile_edit.set_llm_model(vault, name)
    profile_edit.set_llm_num_ctx(vault, recommender.CPU_CTX)
    typer.echo(f"Model {name} and a context of {recommender.CPU_CTX:,} tokens are both pinned: the automatic "
               "choice (auto) only accepts models that sit fully in GPU memory, and this one does not.")


def _pick_candidate(count: int) -> int | None:
    while True:
        text = _prompt("Use one? number, or Enter to keep your current model",
                            default="", show_default=False).strip()
        if not text:
            return None
        if text.isdigit() and 1 <= int(text) <= count:
            return int(text) - 1
        typer.echo(f"Enter a number from 1 to {count}, or just press Enter.")


def _go_back_hint(backend: str, model: str, num_ctx: int | str) -> None:
    if backend == "ollama":
        steps = [f"rpipe model set {model}", f"rpipe model set-context {num_ctx}"]
    else:
        steps = [f"rpipe model set-backend {backend}"]
        if backend != "none" and model != "auto":
            steps.append(f"rpipe model set {model}")
    typer.echo("To go back: " + ", then ".join(steps))


def _download(cfg: Config, info, entry: catalog_mod.CatalogEntry) -> bool:
    """Show size against free disk, refuse when it does not fit, else ask (default no) and pull.

    True only when the model was downloaded; every other outcome is explained on screen.
    Used by `rpipe model recommend` and the setup walkthrough."""
    free = info.disk_free_gb
    typer.echo(f"download {entry.download_gb:g} GB; free disk where Ollama keeps models: "
               + (f"{free:.0f} GB" if free is not None else "unknown"))
    if free is not None and entry.download_gb + 2 > free:
        typer.echo(f"not enough free disk: {entry.pull} needs {entry.download_gb:g} GB plus 2 GB to spare. "
                   "Free some space or pick another model.")
        return False
    if not info.ollama_running:
        typer.echo("Ollama is not running, so nothing can be downloaded. Start it (or install it: "
                   f"{OLLAMA_INSTALL.get(info.os, OLLAMA_INSTALL['Windows'])}) and try again.")
        return False
    if not _confirm(f"Download {entry.pull} now ({entry.download_gb:g} GB)?", default=False):
        return False
    try:
        with _reporter() as reporter, stage(reporter, "model pull", total=100, label=entry.pull) as handle:
            pull_model(cfg.llm_url, entry.pull, _pull_progress(handle, entry.pull))
    except LlmError as exc:
        typer.echo(f"Download failed: {exc}")
        return False
    return True


def _note_call_limit(bench: bench_mod.Bench | None) -> None:
    """When reading a typical paper takes longer than CALL_TIMEOUT, say the measured speed lifts the limit."""
    if bench is None or bench.read_tps <= 0:
        return
    seconds = bench.paper_tokens / bench.read_tps
    if seconds > CALL_TIMEOUT:
        limit = call_timeout(bench.num_ctx, bench)
        typer.echo(
            f"Note: at the measured {bench.read_tps:.0f} tokens/s, reading a {bench.paper_tokens:,}-token "
            f"paper takes about {seconds:,.0f} s, longer than the usual {CALL_TIMEOUT:.0f}-second limit for "
            f"one model call. Because this speed was measured, calls to this model may take up to about "
            f"{limit:,.0f} s, so long papers still finish; a night of them just takes longer. A smaller or "
            "faster model makes the night shorter.")


def _use_candidate(ctx: _Context, info, cand: recommender.Candidate) -> None:
    """Download (with consent), set the model, measure its context or pin it, offer the speed test."""
    cfg = ctx.config
    entry = cand.entry
    if not cand.installed and not _download(cfg, info, entry):
        typer.echo("Nothing was changed.")
        return
    previous = (cfg.llm_backend, cfg.llm_model, cfg.llm_num_ctx)
    try:
        if cfg.llm_backend != "ollama":
            profile_edit.set_llm_backend(ctx.vault, "ollama", set(registry.plugins(registry.BACKEND_GROUP)))
            typer.echo(f"backend: {cfg.llm_backend} -> ollama")
        if cand.placement == "gpu":
            profile_edit.set_llm_model(ctx.vault, entry.pull)
            profile_edit.set_llm_num_ctx(ctx.vault, "auto")
            typer.echo(f"model is now {entry.pull}; context: auto (the largest that fits, measured now)")
        else:
            _pin_off_gpu(ctx.vault, entry.pull)
    except (ValueError, ConfigError, OSError) as exc:
        raise _fail(str(exc)) from exc
    ok = True
    if cand.placement == "gpu":
        typer.echo("Measuring the context that fits: this loads the model and can take a few minutes.")
        try:
            with _reporter() as reporter, stage(reporter, "model fit") as handle:
                fit = ollama_fit.resolve(cfg.llm_url, ctx.vault, entry.pull, "auto", force=True, log=handle.note)
            typer.echo(f"measured: {fit.model} with a context of {fit.num_ctx:,} tokens")
        except (LlmError, OSError, ValueError) as exc:
            typer.echo(f"The fit failed: {exc}")
            ok = False
    if ok and _confirm("Measure its speed now (about 1-5 minutes)?", default=False):
        new_cfg = load_config(ctx.vault / "config.yaml").model_copy(update={"vault_path": ctx.vault})
        try:
            _measure_speed(ctx.vault, ctx.conn, new_cfg)
        except (LlmError, OSError, ValueError) as exc:
            typer.echo(f"Speed not measured: {exc}")
    _note_call_limit({_with_tag(k): b for k, b in bench_mod.load_benches(ctx.vault).items()}
                     .get(_with_tag(entry.pull)))
    _go_back_hint(*previous)
    if not ok:
        raise typer.Exit(code=1)


@model_app.command("recommend")
def model_recommend(vault: VaultOption = None) -> None:
    """Rank catalog models for this computer, installed or not; on a terminal, pick one to download, set and measure."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe model recommend"):
        cfg = ctx.config
        with _reporter() as reporter, stage(reporter, "scan", label="this computer"):
            info = detect_machine(cfg)
        catalog = catalog_mod.load_catalog(ctx.vault)
        candidates = recommender.rank(info, catalog, bench_mod.load_benches(ctx.vault))
        _machine_summary(info)
        if info.ollama is not None and info.ollama.how != "unknown":
            note = ollama_tune.host_note(info.ollama, cfg.llm_url)
            if note:
                typer.echo(note + " The context estimates below assume its default settings.")
            settings = ollama_tune.advise(info.ollama)
            readable = info.ollama.how in ("systemd", "systemd-user") or bool(info.ollama.env)
            if settings and not readable:
                typer.echo("\nrpipe cannot read this server's settings. If these are not set yet, they would fit "
                           "a longer context in the same memory:")
                for st in settings:
                    typer.echo(f"  {st.name}={st.value}: {st.what}")
                typer.echo("Run rpipe model tune for how to set them")
            elif settings:
                typer.echo("\nOllama settings that would fit a longer context in the same memory:")
                for st in settings:
                    typer.echo(f"  {st.name}={st.value} (now: {st.current or 'not set'}): {st.what}")
                typer.echo("Run rpipe model tune to apply them")
        typer.echo("")
        if candidates:
            typer.echo("Models for this computer, best first:")
            for i, cand in enumerate(candidates, 1):
                typer.echo(f"  {i}. {cand.entry.pull} ({cand.entry.family}, {_model_kind(cand.entry)})")
                for reason in cand.reasons:
                    typer.echo(f"     - {reason}")
        else:
            typer.echo("No model in the catalog fits this computer's GPU memory or its memory (RAM).")
        for name in recommender.uncatalogued(info, catalog):
            typer.echo(f"installed, not in the catalog: {name} (rpipe model fit measures any installed model)")
        if info.claude_logged_in:
            typer.echo("cloud: Claude Code is logged in; rpipe model set-backend claude-code uses it")
        if not candidates or not _is_tty():
            return
        try:
            pick = _pick_candidate(len(candidates))
            if pick is None:
                typer.echo("Nothing was changed.")
                return
            _use_candidate(ctx, info, candidates[pick])
        except (typer.Abort, KeyboardInterrupt, EOFError):
            typer.echo("\nStopped. Nothing further was changed.")
            raise typer.Exit(code=130) from None


def _recheck(config: Config, info, problem: str, ok: Callable, question: str):
    """Show `problem` and re-detect the machine each time the user says they fixed it."""
    while not ok(info):
        typer.echo(problem)
        if not _confirm(question, default=False):
            break
        info = detect_machine(config)
    return info


def _setup_ollama(vault: Path, config: Config, info, rec, known: set[str]) -> None:
    info = _recheck(config, info, f"Ollama is not installed. Install it yourself with:\n  {OLLAMA_INSTALL.get(info.os, OLLAMA_INSTALL['Windows'])}",
                    lambda i: i.ollama_installed, "Installed it? Check again")
    pulled = None
    if rec.pull is not None and rec.backend == "ollama" and info.ollama_installed:
        if _download(config, info, rec.pull):
            pulled = rec.pull
    profile_edit.set_llm_backend(vault, "ollama", known)
    # The automatic choice (auto) only accepts models that sit fully in GPU memory, so when the
    # best option here runs (partly) outside the GPU, a model must be pinned or nothing will run.
    ranked = recommender.rank(info, catalog_mod.load_catalog(vault), {}, top=None)
    if not ranked or ranked[0].placement == "gpu":
        return
    if pulled is not None:
        # Reaching here means nothing fits on the GPU, so rank allowed CPU choices: do the same here.
        where = recommender.place(pulled, info, allow_cpu=True)
        if where is not None and where[0] != "gpu":
            _pin_off_gpu(vault, pulled.pull)
            return
    usable = next((c for c in ranked if c.installed and c.placement != "gpu"), None)
    if usable is not None:
        _pin_off_gpu(vault, usable.entry.pull)
    else:
        typer.echo("No model that runs on this computer is installed yet: "
                   "run `rpipe model recommend` to pick and pin a model that runs on this computer.")


def _setup_claude(vault: Path, config: Config, info, known: set[str]) -> None:
    hint = ("Claude Code was not found. Install it, run `claude` once in a terminal and log in, then come back."
            if info.claude_path is None else
            "Claude Code is not logged in. Run `claude` once in a terminal and log in, then come back.")
    info = _recheck(config, info, hint, lambda i: i.claude_logged_in, "Logged in? Check again")
    typer.echo(
        "With Claude Code, the text of each paper is sent to Anthropic to be summarised. A night uses "
        "about 30 calls from your plan's usage quota. Your shelf (my_work/) never leaves this machine. "
        "`haiku` uses less quota than `sonnet`; `opus` uses the most."
    )
    while True:
        model = _prompt("Model (sonnet, haiku or opus)", default="sonnet").strip()
        try:
            resolve_model(model)
            break
        except ValueError as exc:
            typer.echo(str(exc))
    profile_edit.set_llm_backend(vault, "claude-code", known, info.claude_path)
    profile_edit.set_llm_model(vault, model)


def _wizard_backend(vault: Path, offer_keep: bool = False) -> bool:
    """Look at this computer, recommend a summary backend, save the user's choice and check it.

    With `offer_keep` (the vault already has a config) the user is first asked whether to keep
    the current backend; yes skips straight to the check, so a pinned model survives a re-run.
    Returns True when the final check passed (`none` counts as ready)."""
    config = load_config(vault / "config.yaml")
    if offer_keep and _confirm(f"Keep current backend ({config.llm_backend})?", default=True):
        return _check_backend(vault)
    info = detect_machine(config)
    _describe_machine(info)
    rec = recommend(info, catalog_mod.load_catalog(vault))
    typer.echo(f"\nRecommended: {rec.backend}")
    for reason in rec.reasons:
        typer.echo(f"  - {reason}")
    options = [rec.backend, *rec.alternatives]
    labels = {"ollama": "a model on this computer (Ollama)", "claude-code": "Claude Code (your login)",
              "none": "no summaries (abstracts only)"}  # all three are always offered
    typer.echo("\nChoose how summaries are written:")
    for i, name in enumerate(options, 1):
        typer.echo(f"  {i}. {labels.get(name, name)}" + ("  [recommended]" if i == 1 else ""))
    while True:
        pick = _prompt("Choice", default=1, type=int)
        if 1 <= pick <= len(options):
            break
        typer.echo(f"Enter a number from 1 to {len(options)}.")
    choice = options[pick - 1]
    known = set(registry.plugins(registry.BACKEND_GROUP))
    try:
        if choice == "ollama":
            _setup_ollama(vault, config, info, rec, known)
        elif choice == "claude-code":
            _setup_claude(vault, config, info, known)
        else:
            profile_edit.set_llm_backend(vault, "none", known)
            typer.echo("No summary model: digests list each paper's abstract instead of a summary. "
                       "Change this any time with `rpipe model setup`.")
    except (ValueError, ConfigError, OSError) as exc:
        raise _fail(str(exc)) from exc
    return _check_backend(vault)


def _check_backend(vault: Path) -> bool:
    """Test the saved backend and say whether it is ready."""
    config = load_config(vault / "config.yaml")
    if config.llm_backend == "none":
        typer.echo("Ready: abstracts-only mode needs nothing else.")
        return True
    try:
        with _reporter() as reporter, stage(reporter, "model check") as handle:
            llm = build_llm(config, vault, log=handle.note)
            line = llm.check() if hasattr(llm, "check") else f"{llm.name} backend is configured."
    except (LlmError, OSError, ValueError) as exc:
        typer.echo(f"Not ready yet: {exc}\nFix it, then run rpipe model setup")
        return False
    typer.echo(f"Ready: {line}")
    return True


catalog_app = typer.Typer(help="The list of models rpipe can recommend: show it or fetch a newer one.", no_args_is_help=True)
model_app.add_typer(catalog_app, name="catalog")


@catalog_app.command("show")
def catalog_show(vault: VaultOption = None) -> None:
    """List the candidate models, grouped by the size of computer they suit."""
    ctx = _open(vault)
    cat = catalog_mod.load_catalog(ctx.vault)
    typer.echo(f"Model catalog version {cat.version} (updated {cat.updated}), {len(cat.entries)} models")
    for band in catalog_mod.BANDS:
        rows = [e for e in cat.entries if band in e.bands]
        if not rows:
            continue
        typer.echo(f"\n{band}")
        for e in rows:
            typer.echo(f"  {e.pull} — {e.family}, {_model_kind(e)}, {e.quant}, {e.download_gb:g} GB, "
                       f"trained context {e.trained_ctx:,}")


@catalog_app.command("update")
def catalog_update(vault: VaultOption = None) -> None:
    """Fetch the newest model catalog, show what changed and, after a yes, keep it in your vault."""
    ctx = _open(vault)
    current = catalog_mod.load_catalog(ctx.vault)
    try:
        with _reporter() as reporter, stage(reporter, "catalog update", label=catalog_mod.CATALOG_URL):
            text = catalog_mod.fetch_catalog_text()
            fetched = catalog_mod.parse_catalog(text)
    except ValueError as exc:
        raise _fail(f"Catalog not updated: {exc}") from exc
    if fetched.version <= current.version:
        typer.echo(f"The catalog is already up to date (version {current.version}).")
        return
    typer.echo(f"Catalog version {current.version} -> {fetched.version}:")
    for line in catalog_mod.diff_catalogs(current, fetched) or ["(no model changes)"]:
        typer.echo(f"  {line}")
    if not _is_tty():
        raise _fail("Nothing saved: run in a terminal to confirm.")
    if not _confirm("Save this catalog?", default=False):
        typer.echo("Not saved.")
        return
    catalog_mod.save_fetched(ctx.vault, text)
    typer.echo(f"Saved to {ctx.vault / 'cache' / catalog_mod.FETCHED_NAME}")


@model_app.command("setup")
def model_setup(vault: VaultOption = None) -> None:
    """Walk through choosing a summary backend: checks this computer, recommends one, saves it and tests it."""
    if not _is_tty():
        raise _fail("rpipe model setup is interactive; use `rpipe model set-backend NAME` in scripts")
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe model setup"):
        try:
            ready = _wizard_backend(ctx.vault, offer_keep=True)
        except (typer.Abort, KeyboardInterrupt, EOFError):
            typer.echo("\nStopped. Nothing further was changed; run rpipe model setup to continue.")
            raise typer.Exit(code=130) from None
        if not ready:
            raise typer.Exit(code=1)


def _wizard_describe(vault: Path, config: Config) -> None:
    """Optional: propose topics and seeds from a description. Any failure falls through."""
    _step(2, "Describe your research (optional)")
    if config.llm_backend == "none":
        typer.echo(NO_MODEL)
        return
    typer.echo(DESCRIBE_HELP)
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
        typer.echo(OPENING)
        _step(1, "Summaries")
        typer.echo(SUMMARIES_HELP)
        # The backend comes first: the describe step below already calls the model.
        _wizard_backend(vault, offer_keep=not fresh_config)
        config = load_config(vault / "config.yaml")
        _wizard_describe(vault, config)
        _step(3, "Topics")
        _wizard_topics(vault, fresh_profile)
        _step(4, "arXiv categories")
        _wizard_categories(vault, config, fresh_config)
        _step(5, "Seed papers")
        _wizard_seeds(vault, load_config(vault / "config.yaml"))
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    typer.echo("")
    profile_show(vault)
    typer.echo("\nAll set. Next:")
    typer.echo(NEXT_COMMANDS)


# --- feedback: votes and saves -------------------------------------------------


def _resolve_paper(conn: sqlite3.Connection, text: str) -> str:
    """Turn an id or a title fragment into a paper id from the vault, or fail."""
    text = text.strip()
    try:  # an id in any accepted spelling: 2611.25005v2, arXiv:2611.25005, an arxiv.org URL
        pid = normalise_paper_id(text)
    except ValueError:
        pid = None
    if pid is not None and get_paper(conn, pid) is not None:
        return pid
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


_VOTES = ("up", "down", "clear")


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
        library_api.vote(ctx.conn, pid, direction)
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
            library_api.save(ctx.conn, pid, saved)
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
        votes = library_api.list_votes(ctx.conn)
        saves = library_api.saved_papers(ctx.conn)
        waiting = len(library_api.later_queue(ctx.conn))
    finally:
        ctx.conn.close()
    typer.echo("Votes")
    if not votes:
        typer.echo("  No votes yet. Try `rpipe vote ID up`.")
    for v in votes:
        typer.echo(f"  {'up  ' if v.vote > 0 else 'down'}  {v.paper_id}  {v.title or '(unknown paper)'}")
    typer.echo("Saved")
    if not saves:
        typer.echo("  No saved papers yet. Try `rpipe save ID`.")
    for p in saves:
        typer.echo(f"  {p.id}  {p.title or '(unknown paper)'}")
    typer.echo("Review later")
    if waiting:
        typer.echo(f"  {waiting} {'paper' if waiting == 1 else 'papers'} waiting. Run `rpipe review --later`.")
    else:
        typer.echo("  Nothing waiting.")


_REVIEW_KEYS = {"u": "up", "d": "down", "s": "skip", "q": "quit"}


def _show_paper(paper: library_api.PaperView, header: str) -> None:
    typer.echo(f"\n{header}\n")
    typer.echo(paper.title)
    typer.echo(f"id: {paper.id}")
    if paper.triage_reasons:
        typer.echo("why: " + "; ".join(paper.triage_reasons))
    typer.echo("")
    ps = paper.summary
    if ps is not None:
        typer.echo("\n".join(render_summary(ps)).rstrip())
        if ps.read_mode != "whole":
            how = "map-reduce" if ps.read_mode == "map_reduce" else ps.read_mode
            typer.echo(f"\nread in parts: {how}, {ps.parts} notes call{'s' if ps.parts != 1 else ''}")
    else:
        typer.echo(paper.abstract.strip() or "No abstract available.")
    typer.echo(f"\n{paper.url}")


@app.command("review")
def review(
    later: Annotated[bool, typer.Option("--later", help="Go through the papers you skipped earlier.")] = False,
    vault: VaultOption = None,
) -> None:
    """Read each unreviewed digest paper in the terminal and vote up, down, skip or quit."""
    if not _is_tty():
        raise _fail("rpipe review needs a terminal. Use `rpipe vote ID up|down` instead.")
    ctx = _open(vault)
    try:
        queue = library_api.later_queue(ctx.conn) if later else library_api.review_queue(ctx.conn)
        if not queue:
            typer.echo("Nothing waiting in review later." if later else "Nothing to review.")
            return
        voted = skipped = 0
        aborted = False
        try:
            for i, paper in enumerate(queue, start=1):
                if later:
                    header = f"Review later · {i} of {len(queue)}"
                else:
                    progress = library_api.queue_progress(ctx.conn, paper.digest_date or "")
                    header = f"Digest {paper.digest_date} · paper {progress.handled + 1} of {progress.total}"
                _show_paper(paper, f"{header} · {voted} voted, {skipped} skipped")
                while True:
                    answer = _prompt("[u]p  [d]own  [s]kip  [q]uit").strip()[:1].lower()
                    if answer in _REVIEW_KEYS:
                        break
                    typer.echo("Please answer u, d, s or q.")
                if answer == "q":
                    break
                if answer == "s":
                    library_api.skip(ctx.conn, paper.id)
                    skipped += 1
                else:
                    library_api.vote(ctx.conn, paper.id, "up" if answer == "u" else "down")
                    library_api.unskip(ctx.conn, paper.id)
                    voted += 1
        except (typer.Abort, KeyboardInterrupt, EOFError):
            aborted = True
        left = len(library_api.later_queue(ctx.conn) if later else library_api.review_queue(ctx.conn))
        typer.echo(f"Voted {voted}, skipped {skipped} this session. {left} left in the queue.")
        if aborted:
            raise typer.Abort()
    finally:
        ctx.conn.close()


@app.command("show")
def show(
    papers: Annotated[list[str] | None, typer.Argument(help="Paper ids or parts of titles.")] = None,
    recent: Annotated[int | None, typer.Option("--recent", help="Show every paper of the latest N digests (N is 1 or more).")] = None,
    vault: VaultOption = None,
) -> None:
    """Print a paper's summary and critique. Read-only: no voting."""
    if not papers and recent is None:
        raise _fail("Say which paper: `rpipe show ID_OR_TITLE`, or `rpipe show --recent 1` for the latest digest.")
    if recent is not None and recent < 1:
        raise _fail("--recent must be 1 or more.")
    ctx = _open(vault)
    try:
        pids = [_resolve_paper(ctx.conn, p) for p in papers or []]
        shown = [library_api.get_paper(ctx.conn, pid) for pid in pids]
        if recent is not None:
            shown += library_api.recent_papers(ctx.conn, recent)
        for paper in shown:
            _show_paper(paper, f"Paper {paper.id}")
    finally:
        ctx.conn.close()


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


def _platform() -> str:
    """The operating system name (Linux, Darwin, Windows); tests replace it."""
    return platform.system()


def _need_linux(command: str = "schedule") -> None:
    if (system := _platform()) != "Linux":
        hint = (f"On {system}, run `rpipe run` once a day from your own scheduler (see the README)."
                if command == "schedule" else f"On {system}, start it by hand with `rpipe app run`.")
        raise _fail(f"rpipe {command} uses systemd, which only exists on Linux. {hint}")


def _need_systemctl(writes: bool = False) -> None:
    if shutil.which("systemctl") is None:
        raise _fail("systemctl not found (is systemd available?)." + ("" if not writes else " Nothing was written."))


def _schedule_vault(vault: Path | None) -> Path:
    path = resolve_vault(vault).expanduser().resolve()
    if not (path / "config.yaml").exists():
        raise _fail(f"No vault found at {path} (no config.yaml). Run `rpipe init` first.")
    return path


def _warn_backend(vault: Path) -> None:
    """After scheduling, say so if the nightly run would not be able to summarise."""
    try:
        config = load_config(vault / "config.yaml")
        if config.llm_backend == "none":
            return
        llm = build_llm(config, vault, log=_progress)
        if hasattr(llm, "check"):
            llm.check()
    except (LlmError, OSError, ValueError, ConfigError) as exc:
        typer.echo(f"Warning: the summary backend is not ready ({exc}). Until it is, the nightly run "
                   "writes abstracts only. Fix it with `rpipe model setup`.")


@schedule_app.command("show")
def schedule_show(
    time: Annotated[str, typer.Option(help="Time of day, 24-hour HH:MM.")] = "03:00",
    vault: VaultOption = None,
) -> None:
    """Print the night deadline and the unit files that `install` would write, without writing anything."""
    path = _schedule_vault(vault)
    # The deadline comes first, so it is shown even when the unit files cannot be rendered.
    try:
        deadline = load_config(path / "config.yaml").night_deadline
    except ConfigError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"# Reruns of cut papers and the request list: deadline {deadline} "
               "(change with `rpipe schedule set-deadline`)")
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


@schedule_app.command("set-deadline")
def schedule_set_deadline(
    value: Annotated[str, typer.Argument(help="24-hour time such as 07:00, or off for no reruns and no time limit on requests.")],
    vault: VaultOption = None,
) -> None:
    """Set the morning time by which reruns must finish and after which no requested summary starts.

    Normal papers always run.
    """
    path = _schedule_vault(vault)
    with _clean_errors():
        profile_edit.set_night_deadline(path, value)
    stored = load_config(path / "config.yaml").night_deadline
    if stored == "off":
        typer.echo("Reruns of cut papers are off; requested summaries run with no time limit.")
    else:
        typer.echo(f"Reruns of cut papers must finish by {stored}; no requested summary starts after it.")


@schedule_app.command("install")
def schedule_install(
    time: Annotated[str, typer.Option(help="Time of day, 24-hour HH:MM.")] = "03:00",
    vault: VaultOption = None,
) -> None:
    """Write the systemd user units and enable the nightly timer."""
    _need_linux()
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
    _warn_backend(path)


@schedule_app.command("status")
def schedule_status() -> None:
    """Show when the timer last ran and will run next."""
    _need_linux()
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
    _need_linux()
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


app_app = typer.Typer(help="Your private feed in a browser: run it, or keep it always on.", no_args_is_help=True)
app.add_typer(app_app, name="app")

_APP_MISSING = (
    "The app is not installed. Install it with: uv sync --extra embed --extra serve --extra app (in the repo), "
    "or for plain rpipe: uv tool install --editable '.[embed,serve,app]' --force"
)


def _app_available() -> bool:
    """Are the app's web libraries importable here? (Replaced in tests.)"""
    return all(importlib.util.find_spec(name) is not None for name in ("fastapi", "uvicorn", "jinja2"))


def _app_host(host: str | None) -> str:
    try:
        return netguard.resolve_host(host, netguard.system_runner)
    except netguard.HostError as exc:
        raise _fail(str(exc)) from exc


def _app_settings(ctx: _Context) -> tuple[list[str], dict | None]:
    """What the People page needs from the vault: your topic names and your sharing state.

    A missing or broken profile only means no topics; it never stops the app from starting.
    """
    try:
        topics = list(load_profile(ctx.vault / "profile.yaml").topics)
    except Exception:  # noqa: BLE001 - the app needs topic names only; any profile problem means none
        topics = []
    share = None
    if ctx.config.share_enabled and ctx.config.share_handle:
        handle = ctx.config.share_handle
        share = {"handle": handle, "display_name": ctx.config.share_display_name or handle,
                 "address": share_publish.feed_url(handle)}
    return topics, share


def _restart_app_if_installed() -> None:
    """After a sharing change, restart the background app (if installed and running) so People shows it."""
    from research_pipeline.webapp import service as app_service

    if _platform() != "Linux" or shutil.which("systemctl") is None:
        return
    if app_service.address(schedule.default_unit_dir()) is None:
        return
    try:
        if app_service.is_active(schedule.system_runner) and app_service.restart(schedule.system_runner):
            typer.echo("Restarted the app so it shows the change.")
    except Exception:  # noqa: BLE001 - a restart problem must never fail a sharing command that worked
        return


@app_app.command("run")
def app_run(
    host: Annotated[str | None, typer.Option(help="Address to listen on (default: this machine's Tailscale IPv4).")] = None,
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = 8002,
    vault: VaultOption = None,
) -> None:
    """Start the app in this terminal (Ctrl+C stops it). For always-on, use `rpipe app install`."""
    if not _app_available():
        raise _fail(_APP_MISSING)
    ctx = _open(vault)  # applies WAL mode and schema upgrades before the app opens the file
    ctx.conn.close()
    bind = _app_host(host)
    import uvicorn

    from research_pipeline.webapp import service as app_service
    from research_pipeline.webapp.server import create_app

    shown = f"[{bind}]" if ":" in bind else bind
    my_topics, share = _app_settings(ctx)
    typer.echo(f"Your feed is at http://{shown}:{port}/ (only your own devices can reach it)")
    typer.echo("Open exactly this address: a machine name such as name.tailnet.ts.net will not work.")
    uvicorn.run(create_app(ctx.vault / "vault.sqlite", app_service.allowed_hosts(bind), f"http://{shown}:{port}/",
                                my_topics=my_topics, share=share),
                host=bind, port=port, log_level="warning")


@app_app.command("install")
def app_install(
    host: Annotated[str | None, typer.Option(help="Address to listen on (default: this machine's Tailscale IPv4).")] = None,
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = 8002,
    vault: VaultOption = None,
) -> None:
    """Keep the app running in the background, started at login (a systemd user service)."""
    from research_pipeline.webapp import service as app_service

    _need_linux("app")
    path = _schedule_vault(vault)
    if not _app_available():
        raise _fail(_APP_MISSING + " Nothing was written.")
    bind = _app_host(host)
    rpipe_cmd = _rpipe_path()
    _need_systemctl(writes=True)
    try:
        written = app_service.install(path, rpipe_cmd, bind, port, schedule.default_unit_dir(), schedule.system_runner)
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"wrote {written}")
    typer.echo(f"The app is running. Open {app_service.address(written.parent)} on any device on your tailnet "
               "(use the address with the number; a machine name will not work).")
    typer.echo("On a phone, use the browser's Add to Home Screen to get an app icon.")
    typer.echo(_LINGER_HINT)


@app_app.command("uninstall")
def app_uninstall() -> None:
    """Stop the background app and delete its service file."""
    from research_pipeline.webapp import service as app_service

    _need_linux("app")
    try:
        removed, warnings = app_service.remove(schedule.default_unit_dir(), schedule.system_runner)
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    for w in warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo("\n".join(f"removed {f}" for f in removed) or "nothing was installed")


@app_app.command("status")
def app_status() -> None:
    """Say whether the background app is installed and running, and where to open it."""
    from research_pipeline.webapp import service as app_service

    _need_linux("app")
    addr = app_service.address(schedule.default_unit_dir())
    if addr is None:
        typer.echo("Not installed. Set it up with: rpipe app install")
        return
    _need_systemctl()
    try:
        active = app_service.is_active(schedule.system_runner)
    except schedule.ScheduleError as exc:
        raise _fail(str(exc)) from exc
    if active:
        typer.echo(f"Running at {addr}")
    else:
        typer.echo("Installed but not running. Check: journalctl --user -u research-pipeline-app")


sources_app = typer.Typer(help="List, turn on and turn off paper sources.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@app.command()
def plugins() -> None:
    """List installed plug-ins (paper sources and model backends) and whether each loads."""
    for group in (registry.SOURCE_GROUP, registry.BACKEND_GROUP):
        typer.echo(f"{group}:")
        found = registry.plugins(group)
        if not found:
            typer.echo("  (none)")
        for key, p in found.items():
            typer.echo(f"  {key}  {p.origin}  {p.error or 'ok'}")


@sources_app.command("list")
def sources_list(vault: VaultOption = None) -> None:
    """Show every available source, whether it is on, where it comes from and any load error."""
    ctx = _open(vault)
    for key, p in registry.plugins(registry.SOURCE_GROUP).items():
        state = "on" if key in ctx.config.sources else "off"
        typer.echo(f"{key}  {state}  {p.origin}  {p.error or 'ok'}")
    for name in ctx.config.sources:
        if name not in registry.plugins(registry.SOURCE_GROUP):
            typer.echo(f"{name}  on  not installed")


@sources_app.command("set-max-results")
def sources_set_max_results(
    n: Annotated[int, typer.Argument(help="Most papers to fetch from arXiv per run (50 to 5000).")],
    vault: VaultOption = None,
) -> None:
    """Set the safety cap on arXiv papers fetched per run (50 to 5000)."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_arxiv_max_results(ctx.vault, n)
    typer.echo(f"arXiv fetches at most {n} papers per run.")


@sources_app.command("enable")
def sources_enable(
    name: Annotated[str, typer.Argument(help="Source name from `rpipe sources list`.")],
    vault: VaultOption = None,
) -> None:
    """Turn a source on (it is collected from on the next run)."""
    ctx = _open(vault)
    with _clean_errors():
        known = {k for k, p in registry.plugins(registry.SOURCE_GROUP).items() if p.factory}
        changed = profile_edit.enable_source(ctx.vault, name, known)
    typer.echo(f"source {name.strip()} is now on." if changed else f"source {name.strip()} was already on.")


@sources_app.command("disable")
def sources_disable(
    name: Annotated[str, typer.Argument(help="Source name from `rpipe sources list`.")],
    vault: VaultOption = None,
) -> None:
    """Turn a source off (at least one must stay on)."""
    ctx = _open(vault)
    with _clean_errors():
        changed = profile_edit.disable_source(ctx.vault, name)
    typer.echo(f"source {name.strip()} is now off." if changed else f"source {name.strip()} was already off.")


# ---------------------------------------------------------------- share commands

share_app = typer.Typer(help="Publish your feed, follow other people's, and browse the public directory.", no_args_is_help=True)
app.add_typer(share_app, name="share")
mix_app = typer.Typer(help="Include or leave out a followed feed in Mix.", no_args_is_help=True)
share_app.add_typer(mix_app, name="mix")

PUBLIC_WARNING = (
    "This makes your feed public. Anyone can read it. "
    "Copies people have already downloaded can't be pulled back if you stop."
)


def _share_runner() -> share_github.Runner:
    """Runs `git` and `gh`. Tests replace this with a fake."""
    return share_github.SubprocessRunner()


def _share_http() -> httpx.Client:
    """The client for feeds and the directory. GitHub's API rejects requests without a User-Agent.

    Redirects are off here because `share_fetch.get_limited` follows them itself (https only).
    """
    return httpx.Client(headers={"User-Agent": "research-pipeline/0.1"}, timeout=20, follow_redirects=False)


def _gh_login_interactive() -> int:
    """Run `gh auth login` on the terminal (it asks its own questions). Tests replace this."""
    return subprocess.run(["gh", "auth", "login"], check=False).returncode


def _gh_refresh_interactive() -> int:
    """Run `gh auth refresh` to add the delete permission, on the terminal (it prints a code and opens a browser)."""
    return subprocess.run(["gh", "auth", "refresh", "-h", "github.com", "-s", "delete_repo"],
                          check=False).returncode


def _clone_dir(vault: Path) -> Path:
    return vault / "share" / "rpipe-feed"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_clone(runner: share_github.Runner, handle: str, clone: Path) -> None:
    """Clone the feed repository if this machine has no copy yet (a re-run, or a new computer)."""
    if (clone / ".git").exists():
        return
    clone.parent.mkdir(parents=True, exist_ok=True)
    r = runner.run(["git", "clone", f"https://github.com/{handle}/rpipe-feed.git", str(clone)])
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        raise share_github.GithubError(f"Cloning your rpipe-feed repository failed: {detail}")


def _publish_feed(
    ctx: _Context, profile: Profile, runner: share_github.Runner, reporter, *,
    handle: str | None, name: str | None, active: bool = True,
) -> str:
    """Build and push the feed ("pushed" or "unchanged"). GithubError if sharing isn't set up."""
    if not handle or not name:
        raise share_github.GithubError("Sharing isn't set up yet. Run `rpipe share setup`.")
    clone = _clone_dir(ctx.vault)
    _ensure_clone(runner, handle, clone)
    # commits carry GitHub's private noreply address, never the person's own email (also set for old clones)
    share_github.set_identity(runner, clone, handle, name)
    return share_publish.publish(
        ctx.conn, clone, runner, display_name=name, topics=profile.topics, active=active,
        now=_now(), handle=handle, reporter=reporter,
    )


def _name_for_prompt(text: str) -> str:
    try:
        return check_share_name(text)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _require_sharing(ctx: _Context) -> None:
    if not (ctx.config.share_enabled and ctx.config.share_handle):
        raise _fail("Publishing is off. Run `rpipe share setup` first.")


def _follow_by_handle(ctx: _Context, handle: str) -> library_api.FollowView:
    wanted = handle.strip().removeprefix("@").lower()
    for f in library_api.follows(ctx.conn):
        if f.handle.lower() == wanted:
            return f
    raise _fail(f"You don't follow '{handle}'. See who you follow with `rpipe share following`.")


@share_app.command("setup")
def share_setup(vault: VaultOption = None) -> None:
    """Guided first-time publishing (run it again to repair things)."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    with _pipeline_lock(ctx, "rpipe share setup"):
        runner = _share_runner()
        try:
            share_github.check_tools(runner)
            try:
                handle = share_github.login(runner)
            except share_github.GithubError:
                typer.echo("You need to sign in to GitHub first. Follow the steps below.")
                with _waiting_for_you():
                    code = _gh_login_interactive()
                if code != 0:
                    raise _fail("GitHub sign-in didn't finish. Run `rpipe share setup` again.") from None
                handle = share_github.login(runner)
            status = share_github.repo_status(runner, handle)
            if status == "other":
                raise _fail(
                    f"github.com/{handle}/rpipe-feed already exists and isn't an rpipe feed. "
                    "Rename or delete it on GitHub, then run `rpipe share setup` again."
                )
            default = ctx.config.share_display_name
            if not default:
                r = runner.run(["gh", "api", "user", "--jq", ".name"])
                default = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else handle
            name = _prompt("Display name shown to people who follow you", default=default[:60],
                           value_proc=_name_for_prompt)
            typer.echo(PUBLIC_WARNING)
            if not _confirm("Publish your feed?"):
                typer.echo("Nothing was created or changed.")
                return
            clone = _clone_dir(ctx.vault)
            if status == "missing":
                shutil.rmtree(clone, ignore_errors=True)  # a leftover from an aborted setup
                clone.parent.mkdir(parents=True, exist_ok=True)
                share_github.create_repo(runner, handle, clone)
            else:
                _ensure_clone(runner, handle, clone)
            share_github.setup_git_credentials(runner)
            with _reporter() as reporter:
                _publish_feed(ctx, profile, runner, reporter, handle=handle, name=name)
                share_github.set_topic(runner, handle, True)
                share_github.enable_pages(runner, handle)
                address = share_publish.feed_url(handle)
                with _share_http() as http:
                    live = share_publish.wait_live(http, address, reporter)
            if not live:
                raise _fail(
                    "GitHub Pages isn't serving your feed yet (it can take a few minutes). "
                    "Run `rpipe share setup` again in a little while; nothing is lost."
                )
            test = runner.run(["git", "push", "--dry-run"], cwd=clone)
            if test.returncode != 0:
                detail = (test.stderr or test.stdout or "").strip()
                raise _fail(
                    f"A test push failed, so tonight's automatic push would fail too: {detail}\n"
                    "Fix the sign-in with `gh auth login`, then run `rpipe share setup` again."
                )
            profile_edit.set_share(ctx.vault, enabled=True, display_name=name, handle=handle)
        except share_github.GithubError as exc:
            raise _fail(str(exc)) from exc
        _restart_app_if_installed()
        typer.echo(f"Your feed is live: {address}")
        typer.echo("It updates every night. Others can follow it with `rpipe share follow " + handle + "`.")


@share_app.command("status")
def share_status(vault: VaultOption = None) -> None:
    """Whether you are publishing, your address, the last push, and each feed you follow."""
    ctx = _open(vault)
    cfg = ctx.config
    if cfg.share_enabled and cfg.share_handle:
        typer.echo(f"Publishing: on as '{cfg.share_display_name}'")
        typer.echo(f"  Address: {share_publish.feed_url(cfg.share_handle)}")
        clone = _clone_dir(ctx.vault)
        if (clone / ".git").exists():
            r = _share_runner().run(["git", "log", "-1", "--format=%cI"], cwd=clone)
            if r.returncode == 0 and r.stdout.strip():
                typer.echo(f"  Last local commit: {r.stdout.strip()[:19].replace('T', ' ')}")
        last = list_runs(ctx.conn, 1)
        if last and last[0]["finished_at"]:
            bad = [e for e in json.loads(last[0]["errors"] or "[]") if e.startswith("sharing:")]
            typer.echo(f"  Last night's push: {bad[0] if bad else 'ok'}")
    else:
        typer.echo("Publishing: off (start with `rpipe share setup`)")
    feeds = library_api.follows(ctx.conn)
    if not feeds:
        typer.echo("Following: nobody yet (find feeds with `rpipe share browse`)")
        return
    typer.echo(f"Following {len(feeds)}:")
    for f in feeds:
        typer.echo("  " + _follow_line(f))


def _follow_line(f: library_api.FollowView) -> str:
    bits = [f"{clean_text(f.display_name)} (@{clean_text(f.handle)})"]
    if f.waiting:
        bits.append("waiting for the first fetch")
    else:
        bits.append(f"{f.posts} posts, fetched {(f.last_fetched_at or '')[:10]}")
    bits.append("in Mix" if f.in_mix else "not in Mix")
    if f.stopped:
        bits.append("stopped publishing")
    if f.last_error:
        bits.append(f"last error: {clean_text(f.last_error)}")
    return " · ".join(bits)


@share_app.command("publish")
def share_publish_now(vault: VaultOption = None) -> None:
    """Rebuild and push your feed now (the nightly run does this on its own)."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    with _pipeline_lock(ctx, "rpipe share publish"):
        _require_sharing(ctx)
        try:
            with _reporter() as reporter:
                result = _publish_feed(ctx, profile, _share_runner(), reporter,
                                       handle=ctx.config.share_handle, name=ctx.config.share_display_name)
        except share_github.GithubError as exc:
            raise _fail(str(exc)) from exc
        typer.echo("Pushed your feed." if result == "pushed" else "Nothing changed since the last push.")


@share_app.command("set-name")
def share_set_name(
    name: Annotated[str, typer.Argument(help="The name people see (1 to 60 characters).")],
    vault: VaultOption = None,
) -> None:
    """Change your display name (republishes if you are publishing)."""
    ctx = _open(vault)
    with _clean_errors():
        profile_edit.set_share(ctx.vault, display_name=name)
    name = check_share_name(name)
    if not (ctx.config.share_enabled and ctx.config.share_handle):
        typer.echo(f"Display name set to '{name}'. It is used when you run `rpipe share setup`.")
        return
    profile = _load_profile(ctx)
    with _pipeline_lock(ctx, "rpipe share set-name"):
        try:
            with _reporter() as reporter:
                _publish_feed(ctx, profile, _share_runner(), reporter,
                              handle=ctx.config.share_handle, name=name)
        except share_github.GithubError as exc:
            raise _fail(f"Name saved, but publishing failed (tonight's run will retry): {exc}") from exc
    typer.echo(f"Display name changed to '{name}' and published.")
    _restart_app_if_installed()


@share_app.command("stop")
def share_stop(
    delete: Annotated[bool, typer.Option("--delete", help="Also delete the rpipe-feed repository on GitHub.")] = False,
    vault: VaultOption = None,
) -> None:
    """Stop publishing and leave the directory (optionally delete the repository)."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    with _pipeline_lock(ctx, "rpipe share stop"):
        handle = ctx.config.share_handle
        if not handle:
            raise _fail("You are not publishing. Nothing to stop.")
        runner = _share_runner()
        try:
            if delete:
                if not _confirm(f"Delete github.com/{handle}/rpipe-feed? Followers stop getting updates. "
                                "Copies already downloaded stay with them."):
                    typer.echo("Nothing was deleted or changed.")
                    return
                if not share_github.has_delete_scope(runner):
                    # Interactive (a one-time code, a browser sign-in): run on the terminal, not
                    # captured, with the vault lock let go of while it waits for you.
                    typer.echo("GitHub needs your OK to delete repositories. A code is shown below and a "
                               "browser sign-in may open; follow the steps there.")
                    with _waiting_for_you():
                        code = _gh_refresh_interactive()
                    if code != 0:
                        raise _fail("Getting permission to delete the repository didn't finish. "
                                    "Run `rpipe share stop --delete` again.")
                share_github.delete_repo(runner, handle)
                shutil.rmtree(_clone_dir(ctx.vault), ignore_errors=True)
            else:
                with _reporter() as reporter:
                    _publish_feed(ctx, profile, runner, reporter, handle=handle,
                                  name=ctx.config.share_display_name, active=False)
                share_github.set_topic(runner, handle, False)
        except share_github.GithubError as exc:
            raise _fail(str(exc)) from exc
        profile_edit.set_share(ctx.vault, enabled=False)
    _restart_app_if_installed()
    if delete:
        typer.echo("Deleted your rpipe-feed repository and stopped publishing.")
    else:
        typer.echo("Stopped publishing and left the directory. Your repository is kept; "
                   "the old address still works and says you stopped. Run `rpipe share setup` to start again.")


@share_app.command("browse")
def share_browse(
    search: Annotated[str | None, typer.Argument(help="Only feeds whose name, handle or topics contain this.")] = None,
    vault: VaultOption = None,
) -> None:
    """Refresh the directory of published feeds and list it."""
    ctx = _open(vault)
    profile = _load_profile(ctx)
    with _pipeline_lock(ctx, "rpipe share browse"):
        with _share_http() as http, _reporter() as reporter:
            _count, errors = share_directory.refresh(ctx.conn, http, reporter)
    for e in errors:
        typer.echo(f"  warning: {e}", err=True)
    entries = library_api.directory(ctx.conn, profile.topics, ctx.config.share_handle)
    if search:
        q = search.strip().lower()
        entries = [e for e in entries if q in e.display_name.lower() or q in e.handle.lower()
                   or any(q in t.lower() for t in e.topics)]
    if not entries:
        typer.echo("No feeds found." if search else "No published feeds found yet.")
        return
    mine = {" ".join(t.lower().split()) for t in profile.topics}
    for e in entries:
        typer.echo(_browse_entry(e, mine))
        typer.echo("")
    typer.echo("Follow one with `rpipe share follow HANDLE`.")


BROWSE_TOPICS_SHOWN = 3


def _browse_entry(e: "library_api.DirectoryEntryView", mine: set[str]) -> str:
    """One directory entry as a short block: name line, a stats line, at most 3 topics.

    Topics you share come first; the rest are counted ("+16 more") so a long interest
    list never turns the listing into a wall of text.
    """
    tag = "  (you)" if e.is_you else "  (following)" if e.following else ""
    head = typer.style(clean_text(e.display_name), bold=True) + f"  @{clean_text(e.handle)}{tag}"
    stats = f"about {e.posts_per_day:g} papers a day"
    if not e.is_you:
        stats += f" · {e.shared_topics} topic{'' if e.shared_topics == 1 else 's'} in common"
    topics = [clean_text(t) for t in e.topics]
    topics.sort(key=lambda t: " ".join(t.lower().split()) not in mine)  # shared first, order otherwise kept
    shown = ", ".join(topics[:BROWSE_TOPICS_SHOWN]) or "no topics"
    if len(topics) > BROWSE_TOPICS_SHOWN:
        shown += f", +{len(topics) - BROWSE_TOPICS_SHOWN} more"
    return f"{head}\n  {stats}\n  {shown}"


@share_app.command("follow")
def share_follow(
    target: Annotated[str, typer.Argument(help="A GitHub handle, or the https:// address of a feed.")],
    vault: VaultOption = None,
) -> None:
    """Follow a feed and fetch everything it has published."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe share follow"):
        try:
            with _share_http() as http, _reporter() as reporter:
                result = share_fetch.follow(ctx.conn, target, http, own_handle=ctx.config.share_handle,
                                            reporter=reporter)
        except ValueError as exc:
            raise _fail(str(exc)) from exc
    if result.error:
        typer.echo(f"Following {target.strip()}, but the first fetch failed: {result.error}. "
                   "`rpipe share fetch` tries again.", err=True)
        return
    typer.echo(f"Following {target.strip()}: {result.new_posts} posts downloaded.")


@share_app.command("unfollow")
def share_unfollow(handle: Annotated[str, typer.Argument(help="Their handle, as in `rpipe share following`.")],
                   vault: VaultOption = None) -> None:
    """Stop following a feed (posts you saved or voted on are kept)."""
    ctx = _open(vault)
    feed = _follow_by_handle(ctx, handle)
    library_api.unfollow(ctx.conn, feed.feed_id)
    typer.echo(f"Stopped following {clean_text(feed.display_name)} (@{clean_text(feed.handle)}).")


@share_app.command("following")
def share_following(vault: VaultOption = None) -> None:
    """List the feeds you follow."""
    ctx = _open(vault)
    feeds = library_api.follows(ctx.conn)
    if not feeds:
        typer.echo("You follow nobody yet. Find feeds with `rpipe share browse`.")
        return
    for f in feeds:
        typer.echo(_follow_line(f))


@share_app.command("fetch")
def share_fetch_now(vault: VaultOption = None) -> None:
    """Fetch every feed you follow now (the nightly run does this on its own)."""
    ctx = _open(vault)
    with _pipeline_lock(ctx, "rpipe share fetch"):
        if not library_api.follows(ctx.conn):
            typer.echo("You follow nobody yet. Find feeds with `rpipe share browse`.")
            return
        with _share_http() as http, _reporter() as reporter:
            counts, errors = share_fetch.fetch_all(ctx.conn, http, reporter)
    typer.echo(f"Fetched {counts['followed_feeds']} feeds, {counts['followed_new']} new posts.")
    for e in errors:
        typer.echo(f"  error: {e}", err=True)


@mix_app.command("add")
def share_mix_add(handle: Annotated[str, typer.Argument(help="Their handle.")], vault: VaultOption = None) -> None:
    """Include a followed feed in Mix."""
    ctx = _open(vault)
    feed = _follow_by_handle(ctx, handle)
    library_api.set_in_mix(ctx.conn, feed.feed_id, True)
    typer.echo(f"{clean_text(feed.display_name)} is now in Mix.")


@mix_app.command("remove")
def share_mix_remove(handle: Annotated[str, typer.Argument(help="Their handle.")], vault: VaultOption = None) -> None:
    """Leave a followed feed out of Mix (you still follow it)."""
    ctx = _open(vault)
    feed = _follow_by_handle(ctx, handle)
    library_api.set_in_mix(ctx.conn, feed.feed_id, False)
    typer.echo(f"{clean_text(feed.display_name)} is no longer in Mix.")
