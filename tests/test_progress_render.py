import io
from dataclasses import replace

from research_pipeline.progress import ProgressEvent
from research_pipeline.progress_render import LineRenderer, LiveRenderer, format_line, make_reporter


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_line_renderer_plain_lines_no_ansi():
    buf = io.StringIO()
    r = LineRenderer(buf, start_thread=False)
    r.emit(ProgressEvent("collect", "start", label="arXiv"))
    r.emit(ProgressEvent("collect", "done", elapsed=3.2))
    r.close()
    out = buf.getvalue()
    assert "\x1b[" not in out and "\r" not in out
    assert out.count("\n") == 2


def test_summarize_paper_line_unchanged():
    e = ProgressEvent("summarize", "step", label="2611.25005", current=2, total=3, elapsed=41.2, outcome="done")
    assert format_line(e) == "[2/3] 2611.25005 done (summary + critique, 41s)"
    assert format_line(replace(e, outcome="stopped")) == "[2/3] 2611.25005 stopped: usage limit reached"
    assert format_line(replace(e, outcome="failed", detail="timeout")) == "[2/3] 2611.25005 failed: timeout"


def test_format_line_wording():
    assert format_line(ProgressEvent("collect", "start", label="arXiv")) == "collect: arXiv…"
    assert format_line(ProgressEvent("collect", "note", label="page 2")) == "  page 2"
    assert format_line(ProgressEvent("triage", "step", label="x", current=1, total=4)) == "  [1/4] x"
    assert (
        format_line(ProgressEvent("triage", "step", label="x", current=1, total=4, outcome="failed", detail="boom"))
        == "  [1/4] x failed: boom"
    )
    assert format_line(ProgressEvent("collect", "done", elapsed=90)) == "collect done (1m30s)"
    assert format_line(ProgressEvent("collect", "done", elapsed=3.2)) == "collect done (3s)"
    assert format_line(ProgressEvent("collect", "done", outcome="failed", detail="down")) == "collect failed: down"


def test_heartbeat_after_30s_of_silence():
    now = [0.0]
    buf = io.StringIO()
    r = LineRenderer(buf, heartbeat=30.0, clock=lambda: now[0], start_thread=False)
    r.emit(ProgressEvent("summarize", "note", label="writing the critique for 2611.25005"))
    now[0] = 29.0
    r.tick()
    assert "still waiting" not in buf.getvalue()
    now[0] = 90.0
    r.tick()
    assert "still waiting: writing the critique for 2611.25005 (1m30s)" in buf.getvalue()


def test_no_heartbeat_after_stage_done():
    now = [0.0]
    buf = io.StringIO()
    r = LineRenderer(buf, heartbeat=30.0, clock=lambda: now[0], start_thread=False)
    r.emit(ProgressEvent("collect", "start", label="arXiv"))
    r.emit(ProgressEvent("collect", "done"))
    now[0] = 500.0
    r.tick()
    assert "still waiting" not in buf.getvalue()


def test_close_stops_thread_even_after_error_and_is_idempotent():
    r = LineRenderer(io.StringIO(), heartbeat=0.01)
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        r.close()
    assert not r._thread.is_alive()
    r.close()


def test_make_reporter_picks_by_tty():
    assert isinstance(make_reporter(io.StringIO()), LineRenderer)
    live = make_reporter(Tty())
    assert isinstance(live, LiveRenderer)
    live.close()


def test_live_renderer_runs_and_closes_cleanly():
    buf = Tty()
    r = LiveRenderer(buf)
    r.emit(ProgressEvent("collect", "start", label="arXiv"))  # no total: no bar
    r.emit(ProgressEvent("summarize", "start", total=0))
    r.emit(ProgressEvent("summarize", "start", total=3))
    r.emit(ProgressEvent("summarize", "step", label="a", current=1, total=3, outcome="done", elapsed=2))
    r.emit(ProgressEvent("summarize", "note", label="writing"))
    r.emit(ProgressEvent("summarize", "done", total=3))
    r.close()
    assert r._live.is_started is False
    r.close()


def test_live_renderer_close_after_exception():
    r = LiveRenderer(Tty())
    r.emit(ProgressEvent("collect", "start", label="x"))
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        r.close()
    assert r._live.is_started is False


def test_cli_reporter_always_closes():
    from research_pipeline import cli

    try:
        with cli._reporter() as rep:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert rep._thread is None or not rep._thread.is_alive() or rep._stop.is_set()


def test_live_renderer_does_not_hijack_stdout(capsys):
    buf = Tty()
    r = LiveRenderer(buf)
    r.emit(ProgressEvent("collect", "start", label="x"))
    print("RESULT-LINE")
    r.close()
    assert "RESULT-LINE" in capsys.readouterr().out
    assert "RESULT-LINE" not in buf.getvalue()


def test_heartbeat_names_latest_step_label():
    now = [0.0]
    buf = io.StringIO()
    r = LineRenderer(buf, heartbeat=30.0, clock=lambda: now[0], start_thread=False)
    r.emit(ProgressEvent("summarize", "step", label="2611.25005", current=1, total=3))
    now[0] = 40.0
    r.tick()
    assert "still waiting: 2611.25005 (40s)" in buf.getvalue()


def test_live_renderer_keeps_notes_as_permanent_lines():
    buf = Tty()
    r = LiveRenderer(buf)
    r.emit(ProgressEvent("model fit", "start"))
    r.emit(ProgressEvent("model fit", "note", label="trying m at 8192 tokens"))
    r.close()
    assert "  trying m at 8192 tokens" in buf.getvalue()


def _pull_through(renderer, percents):
    from research_pipeline import cli
    from research_pipeline.progress import stage

    with stage(renderer, "model pull", total=100, label="m:1") as h:
        cb = cli._pull_progress(h, "m:1")
        for p in percents:
            cb(f"downloading {p}%")
            yield


def test_pull_percent_moves_the_live_bar():
    r = LiveRenderer(Tty())
    seen = []
    for _ in _pull_through(r, [10, 43]):
        seen.append(r._progress.tasks[0].completed)
    r.close()
    assert seen == [10, 43]


def test_pull_percent_reaches_plain_output_about_every_ten_percent():
    buf = io.StringIO()
    r = LineRenderer(buf, start_thread=False)
    for _ in _pull_through(r, [1, 2, 9, 10, 11, 43, 99, 100]):
        pass
    r.close()
    lines = buf.getvalue().splitlines()
    assert "  m:1 1%" in lines and "  m:1 43%" in lines and "  m:1 100%" in lines
    assert "  m:1 2%" not in lines and "  m:1 11%" not in lines
