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
