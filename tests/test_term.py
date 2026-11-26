"""term.py: plain text off a terminal, colour on one, marks always."""

import io

import pytest

from research_pipeline import term


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _all_helpers() -> None:
    term.header("Title")
    term.step(1, 5, "Name")
    term.hint("a\nb")
    term.ok("saved")
    term.warn("careful")
    term.bullet("item")
    term.fields([("a", "1"), ("longer", "2")])


def test_plain_when_not_tty(capsys):
    _all_helpers()
    term.error("bad")
    out = capsys.readouterr()
    assert "\x1b" not in out.out + out.err
    assert "✓ saved" in out.out and "! careful" in out.out and "  • item" in out.out
    assert "✗ bad" in out.err
    assert "\x1b" not in term.strong("x") + term.cmd("rpipe run") + term.dim("x")


def test_colour_when_tty(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    fake = FakeTty()
    monkeypatch.setattr("sys.stdout", fake)
    term.ok("saved")
    assert "\x1b[" in fake.getvalue()
    monkeypatch.setattr(term, "use_colour", lambda err=False: True)
    assert "\x1b[" in term.strong("x")


def test_no_color_env(monkeypatch):
    monkeypatch.setattr("sys.stdout", FakeTty())
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert term.use_colour() is True
    monkeypatch.setenv("NO_COLOR", "1")
    assert term.use_colour() is False


def test_header_width(monkeypatch, capsys):
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): type("S", (), {"columns": 40})())
    term.header("Title")
    assert len(capsys.readouterr().out.strip("\n")) == 40
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): type("S", (), {"columns": 200})())
    term.header("Title")
    assert len(capsys.readouterr().out.strip("\n")) == 80


def test_header_never_cuts_a_long_title(monkeypatch, capsys):
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): type("S", (), {"columns": 40})())
    title = "Grading eval 2026-11-26: 12 paper(s) to go, a very long title indeed"
    term.header(title)
    out = capsys.readouterr().out
    assert title in out and "─" * 3 not in out.split(title)[1]


def test_fields_align(capsys):
    term.fields([("a", "one"), ("longer key", "two")])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].index("one") == lines[1].index("two")


def test_error_to_stderr(capsys):
    term.error("boom")
    out = capsys.readouterr()
    assert out.out == "" and out.err == "✗ boom\n"


def test_hint_indents_every_line(capsys):
    term.hint("one\ntwo")
    assert capsys.readouterr().out == "  one\n  two\n"


@pytest.mark.parametrize("fn", [term.prompt, term.confirm])
def test_prompt_prefix(fn, monkeypatch):
    seen = []
    monkeypatch.setattr("typer.prompt", lambda t, **k: seen.append(t) or "x")
    monkeypatch.setattr("typer.confirm", lambda t, **k: seen.append(t) or True)
    fn("Go?")
    assert seen == ["› Go?"]


def test_commands_print_no_escape_codes_off_a_terminal(tmp_path):
    """CliRunner (like a pipe or the systemd journal) is not a terminal: no colour codes."""
    from typer.testing import CliRunner

    from research_pipeline import cli
    from research_pipeline.vault import init_vault

    init_vault(tmp_path / "v")
    runner = CliRunner()
    shown = runner.invoke(cli.app, ["profile", "show", "--vault", str(tmp_path / "v")])
    added = runner.invoke(cli.app, ["profile", "add-topic", "fuzzing", "--vault", str(tmp_path / "v")])
    assert shown.exit_code == 0 and added.exit_code == 0
    assert "\x1b" not in shown.output + added.output
    assert "── Topics " in shown.output and "✓ Added topic: fuzzing" in added.output


def test_fail_goes_to_stderr_with_exit_1(tmp_path):
    from typer.testing import CliRunner

    from research_pipeline import cli

    result = CliRunner().invoke(cli.app, ["profile", "show", "--vault", str(tmp_path / "none")])
    assert result.exit_code == 1
    assert "✗ No vault found" in result.stderr and "\x1b" not in result.stderr


def test_continuation_lines_align_under_the_text(capsys):
    term.ok("first\nsecond")
    term.warn("a\nb\n\nc")
    term.error("x\ny")
    term.warn("to stderr", err=True)
    cap = capsys.readouterr()
    assert cap.out == "✓ first\n  second\n! a\n  b\n\n  c\n"
    assert cap.err == "✗ x\n  y\n! to stderr\n"


def test_leading_newline_becomes_blank_line_before_the_mark(capsys):
    term.warn("\nX")
    term.ok("\n\nY")
    assert capsys.readouterr().out == "\n! X\n\n\n✓ Y\n"


def _cols(monkeypatch, n):
    monkeypatch.setattr("shutil.get_terminal_size", lambda fallback=(80, 24): type("S", (), {"columns": n})())


def test_hint_rewraps_on_a_narrow_terminal(monkeypatch, capsys):
    _cols(monkeypatch, 44)
    text = ("rpipe reads the papers it picks for you and writes a summary of each. That needs a\n"
            "language model: one on this computer (Ollama: private; best with a graphics card).\n"
            "\n"
            "  rpipe run   collect today's papers")
    term.hint(text)
    lines = capsys.readouterr().out.split("\n")
    assert all(len(line) <= 44 for line in lines)
    assert all(line.startswith("  ") or not line for line in lines)
    joined = " ".join(line.strip() for line in lines if line.strip())
    assert "writes a summary of each. That needs a language model:" in joined
    assert "" in lines  # the blank line between paragraphs is kept
    assert "    rpipe run   collect today's papers" in lines  # indented lines keep their break


def test_hint_keeps_line_breaks_on_a_wide_terminal(monkeypatch, capsys):
    _cols(monkeypatch, 120)
    term.hint("one line\nsecond line")
    assert capsys.readouterr().out == "  one line\n  second line\n"


def test_fields_wrap_values_under_themselves_when_narrow(monkeypatch, capsys):
    _cols(monkeypatch, 44)
    term.fields([("arXiv categories", "cs.CR, cs.LG, cs.AI, cs.CL, cs.OS, cs.SE, cs.DC, cs.NI")])
    lines = capsys.readouterr().out.rstrip("\n").split("\n")
    assert len(lines) > 1 and all(len(line) <= 44 for line in lines)
    assert lines[1].startswith(" " * 20) and lines[1].strip().startswith("cs.")


def test_marked_message_rewraps_under_the_text_when_narrow(monkeypatch, capsys):
    _cols(monkeypatch, 44)
    term.ok("Vault ready at /home/someone/research-vault and everything else is ready too")
    lines = capsys.readouterr().out.rstrip("\n").split("\n")
    assert lines[0].startswith("✓ Vault ready") and all(len(line) <= 44 for line in lines)
    assert all(line.startswith("  ") for line in lines[1:])
