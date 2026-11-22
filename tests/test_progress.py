import pytest

from research_pipeline.progress import ListReporter, ProgressEvent, emit, stage


def test_stage_emits_start_steps_and_done():
    rep = ListReporter()
    t = iter([0.0, 1.0, 2.5, 4.0])
    with stage(rep, "collect", total=2, clock=lambda: next(t)) as s:
        s.step("page 1", current=1)
        s.step("page 2", current=2)
    kinds = [e.kind for e in rep.events]
    assert kinds == ["start", "step", "step", "done"]
    assert all(e.stage == "collect" for e in rep.events)
    assert rep.events[1].total == 2 and rep.events[1].current == 1
    assert rep.events[1].elapsed == 1.0
    assert rep.events[-1].elapsed == 4.0 and rep.events[-1].outcome == ""


def test_stage_failure_emits_failed_done_and_reraises():
    rep = ListReporter()
    with pytest.raises(ValueError):
        with stage(rep, "summarize"):
            raise ValueError("boom")
    assert rep.events[-1].kind == "done" and rep.events[-1].outcome == "failed"
    assert rep.events[-1].detail == "boom"


def test_none_reporter_is_silent():
    with stage(None, "x") as s:
        s.step("a")
        s.note("b")


def test_note_and_step_outcome_and_detail_are_carried():
    rep = ListReporter()
    with stage(rep, "summarize", total=3, clock=lambda: 0.0) as s:
        s.note("waiting for model")
        s.step("2611.25005", current=2, outcome="done", detail="41s")
    note, step = rep.events[1], rep.events[2]
    assert note.kind == "note" and note.label == "waiting for model"
    assert step.outcome == "done" and step.detail == "41s"
    assert step.total == 3 and step.stage == "summarize"


def test_stage_start_carries_label_and_total():
    rep = ListReporter()
    with stage(rep, "triage", total=5, label="ranking papers", clock=lambda: 0.0):
        pass
    start = rep.events[0]
    assert start.kind == "start" and start.label == "ranking papers"
    assert start.total == 5 and start.elapsed == 0.0


def test_emit_none_is_noop_and_list_reporter_collects():
    emit(None, ProgressEvent(stage="x", kind="note", label="ignored"))
    rep = ListReporter()
    emit(rep, ProgressEvent(stage="x", kind="note", label="kept"))
    assert [e.label for e in rep.events] == ["kept"]


def test_progress_event_is_frozen():
    ev = ProgressEvent(stage="x", kind="start")
    with pytest.raises(AttributeError):
        ev.label = "changed"  # type: ignore[misc]


def test_stage_failure_with_empty_message_uses_exception_name():
    rep = ListReporter()
    with pytest.raises(KeyboardInterrupt):
        with stage(rep, "collect"):
            raise KeyboardInterrupt
    assert rep.events[-1].detail == "KeyboardInterrupt"
