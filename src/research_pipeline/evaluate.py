"""Run the summarizer on the gold set, record your grades, and report the result.

Results live in `<vault>/eval/YYYY-MM-DD.json` (entries and grades) with a readable
`YYYY-MM-DD.md` beside it. See docs/guide/evaluation.md.
"""

import os
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from research_pipeline.fulltext import FullTextUnavailable, ensure_usable
from research_pipeline.gold import GoldPaper, load_gold
from research_pipeline.judge import FactVerdict, is_self_graded, judge_summary
from research_pipeline.llm import LLM, LlmLimitReached
from research_pipeline.models import Section
from research_pipeline.profile_edit import normalise_text
from research_pipeline.progress import Reporter, stage
from research_pipeline.prompts import PROMPT_VERSION
from research_pipeline.summarize import Claim, PaperSummary, summarize_paper
from research_pipeline.vault import ConfigError

PASS_MARK = 0.9
SNIPPET_CHARS = 1500  # cited text longer than this is cut in the report


class EvalEntry(BaseModel):
    paper_id: str
    title: str
    summary: PaperSummary | None = None
    error: str | None = None
    citations_ok: bool = False  # every claim cites a section that exists
    verdicts: list[FactVerdict | None] | None = None  # one slot per key fact; None = not judged
    judge_error: str | None = None
    key_facts: list[str] = []  # the key facts used for this run (old files have none)


class EvalSetup(BaseModel):
    backend: str
    model: str
    num_ctx: int
    prompt_version: str
    judge_model: str | None = None
    self_graded: bool = False
    unreviewed_papers: int = 0  # gold papers whose facts were auto-drafted and never checked by a person
    read_mode: str = "whole"  # the resolved reading mode, whole or split (old files were all whole)


class Grade(BaseModel):
    paper_id: str
    accurate: bool
    note: str = ""


class EvalFile(BaseModel):
    day: date
    entries: list[EvalEntry]
    grades: list[Grade] = []
    setup: EvalSetup | None = None


def _dir(vault: Path) -> Path:
    return vault / "eval"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def eval_exists(vault: Path, day: date) -> bool:
    return (_dir(vault) / f"{day.isoformat()}.json").exists()


def eval_days(vault: Path) -> list[date]:
    """The days that have an eval file, oldest first."""
    days = []
    for p in _dir(vault).glob("*.json"):
        try:
            days.append(date.fromisoformat(p.stem))
        except ValueError:
            continue
    return sorted(days)


def latest_day(vault: Path) -> date | None:
    """The day of the newest file in `<vault>/eval/`, or None if there is none."""
    return max(eval_days(vault), default=None)


def _previous_path(vault: Path, day: date) -> Path:
    return _dir(vault) / f"{day.isoformat()}.previous.json"


def _load_path(path: Path) -> EvalFile:
    try:
        return EvalFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as e:
        raise ConfigError(f"{path}: not a valid eval file: {e}") from e


def load_eval(vault: Path, day: date) -> EvalFile:
    return _load_path(_dir(vault) / f"{day.isoformat()}.json")


def has_judged_run(vault: Path, day: date) -> bool:
    """True when that day's eval file exists and has at least one verdict."""
    try:
        return any(e.verdicts is not None for e in load_eval(vault, day).entries)
    except ConfigError:
        return False


def _write_json(vault: Path, data: EvalFile) -> None:
    _atomic_write(_dir(vault) / f"{data.day.isoformat()}.json", data.model_dump_json(indent=2))


def _cited_text(claim: Claim, sections: list[Section]) -> list[str]:
    by_title = {s.title.strip().casefold(): s for s in sections}
    out = []
    for cited in claim.sections:
        sec = by_title.get(cited.strip().casefold())
        if sec is None:
            out.append(f"  > **{cited}**: NOT FOUND in the paper (unverified citation)")
            continue
        text = " ".join(sec.text.split())
        if len(text) > SNIPPET_CHARS:
            text = text[:SNIPPET_CHARS] + " [...]"
        out.append(f"  > **{sec.title}**: {text}")
    return out


def _claim_md(label: str, claim: Claim, sections: list[Section]) -> list[str]:
    cites = ", ".join(claim.sections)
    return [f"- **{label}:** {claim.text} _[{cites}]_", *_cited_text(claim, sections)]


def _entry_md(entry: EvalEntry, gold: GoldPaper | None, sections: list[Section]) -> str:
    lines = [f"## {entry.paper_id}: {entry.title}", "", "Key facts (tick each one the summary gets right):"]
    for fact in gold.key_facts if gold else []:
        lines.append(f"- [ ] {fact}")
    lines.append("")
    if entry.summary is None:
        lines += [f"**No summary: {entry.error}**", ""]
        return "\n".join(lines)
    s = entry.summary
    basis = "full text" if s.basis == "full_text" else "ABSTRACT ONLY (no full text was available)"
    lines += [f"Basis: {basis}" + (" (body cut to fit the context)" if s.body_cut else ""), ""]
    if s.dropped:
        left_out = ", ".join(f"{normalise_text(d.title)} ({d.chars:,} chars)" for d in s.dropped)
        lines += [f"Left out: {left_out}", ""]
    lines += [f"**{s.summary.one_line}**", ""]
    for label, claim in (
        ("Problem", s.summary.problem),
        ("Approach", s.summary.approach),
        ("Results", s.summary.results),
        ("Limitations", s.summary.limitations),
    ):
        lines += _claim_md(label, claim, sections)
    lines += ["", "Critique:"]
    for c in s.critique.strengths:
        lines += _claim_md("Strength", c, sections)
    for c in s.critique.weaknesses:
        lines += _claim_md("Weakness", c, sections)
    for q in s.critique.questions:
        lines.append(f"- **Question:** {q}")
    if s.unverified:
        lines += ["", "Unverified claims (cited a section that does not exist):"]
        lines += [f"- {t}" for t in s.unverified]
    lines.append("")
    return "\n".join(lines)


def run_eval(
    vault: Path,
    llm: LLM,
    fetch: Callable[[str], list[Section]],
    num_ctx: int,
    today: date,
    abstract_for: Callable[[str], str] | None = None,
    *,
    reporter: Reporter | None = None,
    judge: LLM | None = None,
    mode: Literal["whole", "split"] = "whole",
) -> Path:
    """Summarize every gold paper and write `eval/<today>.json` and `.md`; return the .md path.

    A paper with no full text is summarized from its abstract (looked up with
    ``abstract_for``). A paper that fails gets an error entry and the rest carry on.
    Re-running on the same day overwrites that day's file, grades included; if the file being
    replaced has verdicts it is first kept as `YYYY-MM-DD.previous.json`, the baseline for `compare`.
    With ``judge``, each summary is also graded fact by fact; the usage limit stops judging
    (summaries carry on), any other judge error skips that one paper.
    ``mode`` is the resolved reading mode (whole or split). Eval never uses the rerun-aware
    fallback of nightly runs: a paper cut in whole mode goes straight to parts (fallback_above=0),
    so one eval measures one reading of every paper.
    """
    entries: list[EvalEntry] = []
    sections_by_id: dict[str, list[Section]] = {}
    gold = load_gold(vault)
    judging = judge is not None
    limit_hit = False
    with stage(reporter, "eval", total=len(gold)) as handle:
        for n, g in enumerate(gold, 1):
            try:
                try:
                    sections: list[Section] | None = ensure_usable(fetch(g.paper_id))
                except FullTextUnavailable:
                    sections = None
                abstract = ""
                if sections is None:
                    abstract = abstract_for(g.paper_id) if abstract_for else ""
                    if not abstract.strip():
                        raise ValueError("no full text and no abstract could be found")
                    sections_by_id[g.paper_id] = [Section(title="Abstract", text=abstract)]
                else:
                    sections_by_id[g.paper_id] = sections
                result = summarize_paper(llm, g.title, abstract, sections, num_ctx, mode=mode, fallback_above=0)
                entry = EvalEntry(
                    paper_id=g.paper_id,
                    title=g.title,
                    summary=result,
                    citations_ok=not result.unverified,
                    key_facts=list(g.key_facts),
                )
                entries.append(entry)
                if judge is not None:
                    if limit_hit:
                        entry.judge_error = "usage limit reached"
                    else:
                        handle.note(f"{g.paper_id}: judging")
                        try:
                            entry.verdicts = judge_summary(
                                judge, g.title, sections_by_id[g.paper_id], g.key_facts, result
                            )
                        except LlmLimitReached:
                            limit_hit = True
                            entry.judge_error = "usage limit reached"
                        except Exception as exc:  # noqa: BLE001 - a judge failure must not duplicate the entry
                            entry.judge_error = " ".join(str(exc).split()) or type(exc).__name__
            except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the rest
                msg = " ".join(str(exc).split()) or type(exc).__name__
                entries.append(EvalEntry(paper_id=g.paper_id, title=g.title, error=msg, key_facts=list(g.key_facts)))
            handle.step(
                g.paper_id, current=n, outcome="failed" if entries[-1].error else "ok", detail=entries[-1].error or ""
            )

    self_graded = False
    if judge is not None:
        try:
            self_graded = is_self_graded(llm, judge)
        except AttributeError:  # a stand-in without a backend name cannot be matched
            self_graded = False
    setup = EvalSetup(
        backend=getattr(llm, "name", "?"),
        model=getattr(llm, "model_name", "?"),
        num_ctx=num_ctx,
        prompt_version=PROMPT_VERSION,
        judge_model=getattr(judge, "model_name", "?") if judging else None,
        self_graded=self_graded,
        unreviewed_papers=sum(g.unreviewed for g in gold),
        read_mode=mode,
    )
    if has_judged_run(vault, today):
        _atomic_write(_previous_path(vault, today), (_dir(vault) / f"{today.isoformat()}.json").read_text(encoding="utf-8"))
    _write_json(vault, EvalFile(day=today, entries=entries, setup=setup))
    ok = sum(e.citations_ok for e in entries)
    parts = [
        f"# Eval {today.isoformat()}",
        "",
        f"Model: {getattr(llm, 'model_name', '?')}. Grade with `rpipe eval grade`; read the result with `rpipe eval report`.",
        "",
    ]
    gold_by_id = {g.paper_id: g for g in gold}
    for e in entries:
        parts.append(_entry_md(e, gold_by_id.get(e.paper_id), sections_by_id.get(e.paper_id, [])))
    parts.append(f"## Automatic result\n\nCitations all verified: {ok} of {len(entries)} papers.\n")
    md = _dir(vault) / f"{today.isoformat()}.md"
    _atomic_write(md, "\n".join(parts))
    return md


def save_grade(vault: Path, day: date, grade: Grade) -> None:
    """Record one grade at once (atomic), replacing an earlier grade for the same paper."""
    data = load_eval(vault, day)
    data.grades = [g for g in data.grades if g.paper_id != grade.paper_id] + [grade]
    _write_json(vault, data)


def ungraded(data: EvalFile) -> list[EvalEntry]:
    """Entries that have a summary and no grade yet (failed papers cannot be graded)."""
    graded = {g.paper_id for g in data.grades}
    return [e for e in data.entries if e.summary is not None and e.paper_id not in graded]


def judged_counts(data: EvalFile) -> dict[str, int]:
    """Fact verdict totals over the papers the judge graded."""
    out = {"papers_judged": 0, "covered": 0, "missed": 0, "contradicted": 0, "not_judged": 0, "unverified": 0}
    for e in data.entries:
        if e.verdicts is None:
            continue
        out["papers_judged"] += 1
        out["unverified"] += len(e.summary.unverified) if e.summary else 0
        for v in e.verdicts:
            out["not_judged" if v is None else v.verdict] += 1
    return out


def _norm_fact(fact: str) -> str:
    return " ".join(fact.split()).casefold()


def _verdicts_by_key(entry: EvalEntry, by_text: bool) -> dict[str | int, FactVerdict]:
    """The judged facts of one paper, keyed by normalised fact text (or by position for old files)."""
    out: dict[str | int, FactVerdict] = {}
    for i, v in enumerate(entry.verdicts or []):
        if v is None:
            continue
        key: str | int = _norm_fact(entry.key_facts[i]) if by_text and i < len(entry.key_facts) else i
        out.setdefault(key, v)
    return out


def _setup_line(label: str, setup: EvalSetup | None) -> str:
    if setup is None:
        return f"  {label}: setup not recorded"
    judge = f", judge {setup.judge_model}" if setup.judge_model else ""
    return (f"  {label}: {setup.backend}/{setup.model}, context {setup.num_ctx}, prompts {setup.prompt_version}, "
            f"mode {setup.read_mode}{judge}")


def _mode_change(previous: EvalSetup | None, current: EvalSetup | None) -> list[str]:
    """One line when the reading mode differs between the two runs (a missing setup reads as whole)."""
    before = previous.read_mode if previous else "whole"
    now = current.read_mode if current else "whole"
    return [f"  Reading mode changed: mode {before} → {now}"] if before != now else []


def compare(previous: EvalFile, current: EvalFile) -> str:
    """Compare two judged runs over the papers judged in both; ends with `Change: PASS|FAIL`.

    Passes when facts covered drop by at most 1, contradictions do not rise and unverified
    citations do not rise. Facts are matched by their text (by position for old files without a
    key-fact snapshot); a fact not judged in both runs is left out for that paper.
    """
    prev = {e.paper_id: e for e in previous.entries if e.verdicts is not None}
    cur = {e.paper_id: e for e in current.entries if e.verdicts is not None}
    common = [pid for pid in cur if pid in prev]
    if not common:
        return "No earlier judged run to compare."
    totals = {"p": {"covered": 0, "contradicted": 0, "unverified": 0}, "c": {"covered": 0, "contradicted": 0, "unverified": 0}}
    changed: list[str] = []
    for pid in common:
        a, b = prev[pid], cur[pid]
        by_text = bool(a.key_facts) and bool(b.key_facts)
        if by_text and [_norm_fact(f) for f in a.key_facts] != [_norm_fact(f) for f in b.key_facts]:
            changed.append(pid)
        va, vb = _verdicts_by_key(a, by_text), _verdicts_by_key(b, by_text)
        both = va.keys() & vb.keys()
        for key, e, verdicts in (("p", a, va), ("c", b, vb)):
            for k in both:
                if verdicts[k].verdict in ("covered", "contradicted"):
                    totals[key][verdicts[k].verdict] += 1
            totals[key]["unverified"] += len(e.summary.unverified) if e.summary else 0
    p, c = totals["p"], totals["c"]
    earlier = (
        f"the earlier run on {previous.day.isoformat()}"
        if previous.day != current.day
        else f"the earlier run today ({previous.day.isoformat()})"
    )
    lines = [
        f"Compared with {earlier} over {len(common)} paper(s) judged in both:",
        _setup_line("Earlier", previous.setup),
        _setup_line("Now", current.setup),
        *_mode_change(previous.setup, current.setup),
        *[f"  facts changed for {pid} since the earlier run; compared only the unchanged ones" for pid in changed],
        f"  Facts covered: {p['covered']} -> {c['covered']}",
        f"  Contradicted: {p['contradicted']} -> {c['contradicted']}",
        f"  Unverified citations: {p['unverified']} -> {c['unverified']}",
    ]
    broken = []
    if p["covered"] - c["covered"] > 1:
        broken.append("facts covered")
    if c["contradicted"] > p["contradicted"]:
        broken.append("contradicted")
    if c["unverified"] > p["unverified"]:
        broken.append("unverified citations")
    lines.append("Change: " + (f"FAIL ({', '.join(broken)})" if broken else "PASS"))
    return "\n".join(lines)


def _earlier_judged(vault: Path, day: date) -> EvalFile | None:
    """The comparison baseline: the day's own `.previous` file, else the latest earlier judged day."""
    kept = _previous_path(vault, day)
    if kept.exists():
        try:
            data = _load_path(kept)
            if any(e.verdicts is not None for e in data.entries):
                return data
        except ConfigError:
            pass
    for d in reversed([d for d in eval_days(vault) if d < day]):
        try:
            data = load_eval(vault, d)
        except ConfigError:
            continue
        if any(e.verdicts is not None for e in data.entries):
            return data
    return None


def report(vault: Path, day: date) -> str:
    data = load_eval(vault, day)
    grades = {g.paper_id: g for g in data.grades}
    total = len(data.entries)
    accurate = sum(1 for e in data.entries if e.paper_id in grades and grades[e.paper_id].accurate)
    clean = sum(e.citations_ok for e in data.entries)
    waiting = [e for e in data.entries if e.summary is not None and e.paper_id not in grades]
    rate = accurate / total if total else 0.0
    verdict = "PASS" if rate >= PASS_MARK else "FAIL"
    if grades:
        lines = [
            f"Eval {day.isoformat()}",
            f"Graded: {len(grades)} of {total}",
            f"Accurate: {accurate} of {total} ({rate:.0%}); pass mark {PASS_MARK:.0%}",
            f"Citation-clean: {clean} of {total}",
            f"Result: {verdict}"
            + (" (not everything is graded yet; ungraded papers count as not accurate)" if waiting else ""),
        ]
    else:
        lines = [
            f"Eval {day.isoformat()}",
            "Human grades: none yet (optional: rpipe eval grade)",
            f"Citation-clean: {clean} of {total}",
        ]
    failed = []
    for e in data.entries:
        if e.summary is None:
            failed.append(f"  {e.paper_id} {e.title}: no summary ({e.error})")
        elif e.paper_id in grades and not grades[e.paper_id].accurate:
            note = grades[e.paper_id].note or "no note"
            failed.append(f"  {e.paper_id} {e.title}: {note}")
    if failed:
        lines += ["", "Failed:", *failed]
    if waiting and grades:
        lines += ["", "Not graded yet:", *[f"  {e.paper_id} {e.title}" for e in waiting]]
    if (data.setup and data.setup.judge_model is not None) or any(e.verdicts is not None for e in data.entries):
        n = judged_counts(data)
        facts = n["covered"] + n["missed"] + n["contradicted"] + n["not_judged"]
        lines += ["", f"Judged: {n['papers_judged']} of {total} papers"]
        lines.append(
            f"Facts covered: {n['covered']} of {facts} (missed {n['missed']}, contradicted {n['contradicted']}, "
            f"not judged {n['not_judged']})"
        )
        if data.setup and data.setup.self_graded:
            lines.append("Self-graded: the judge is the same model that wrote the summaries")
        if data.setup and data.setup.unreviewed_papers:
            lines.append(
                f"Unreviewed answer key: {data.setup.unreviewed_papers} of {total} papers use facts Claude drafted and "
                "no person checked. Read the scores as a comparison between runs, not as proof of accuracy."
            )
        errors = [e for e in data.entries if e.judge_error]
        lines += [f"  not judged {e.paper_id} {e.title}: {e.judge_error}" for e in errors]
        previous = _earlier_judged(vault, day)
        lines += ["", compare(previous, data) if previous else "No earlier judged run to compare."]
    if data.setup:
        s = data.setup
        lines += ["", f"Setup: {s.backend}/{s.model}, context {s.num_ctx}, prompts {s.prompt_version}, mode {s.read_mode}"]
    return "\n".join(lines)
