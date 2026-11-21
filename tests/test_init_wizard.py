import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.sources.base import SourceError
from research_pipeline.vault import init_vault, load_config, load_profile

from test_cli_profile import StubArxiv, paper

runner = CliRunner()
ABORT = "Saved what you entered so far. Re-run rpipe init to continue."


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: True)


def init(tmp_path, input):
    return runner.invoke(cli.app, ["init", "--vault", str(tmp_path / "v")], input=input)


def use(monkeypatch, stub):
    monkeypatch.setattr(cli, "make_arxiv", lambda config: stub)
    return stub


def test_wizard_saves_topics_and_seeds(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    # topics (2, blank), categories (accept default), seed link + confirm, blank
    answers = "prompt injection\nagent safety\n\n\nhttps://arxiv.org/abs/2307.15043\ny\n\n"
    result = init(tmp_path, answers)
    assert result.exit_code == 0, result.output
    profile = load_profile(tmp_path / "v" / "profile.yaml")
    assert profile.topics == ["prompt injection", "agent safety"]  # examples replaced
    assert profile.seeds == ["2307.15043"]
    assert "Tip: 10-20 seeds work best; add more later with rpipe profile add-seed" in result.output
    assert "rpipe run" in result.output
    assert "Topics:" in result.output


def test_wizard_changes_categories(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv())
    result = init(tmp_path, "\ncs.CR, stat.ML\n\n")
    assert result.exit_code == 0, result.output
    assert load_config(tmp_path / "v" / "config.yaml").arxiv_categories == ["cs.CR", "stat.ML"]


def test_wizard_rerun_keeps_existing_and_skips_prompts_when_declined(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv())
    init_vault(tmp_path / "v", topics=["mine"], categories=["cs.CR"])
    # decline more topics, decline category change, blank seed
    result = init(tmp_path, "n\nn\n\n")
    assert result.exit_code == 0, result.output
    assert "Add more topics? [y/N]" in result.output
    assert "mine" in result.output
    assert load_profile(tmp_path / "v" / "profile.yaml").topics == ["mine"]
    assert load_config(tmp_path / "v" / "config.yaml").arxiv_categories == ["cs.CR"]


def test_wizard_rerun_adds_topics_when_accepted(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv())
    init_vault(tmp_path / "v", topics=["mine"])
    result = init(tmp_path, "y\nextra topic\n\nn\n\n")
    assert result.exit_code == 0, result.output
    assert load_profile(tmp_path / "v" / "profile.yaml").topics == ["mine", "extra topic"]


def test_wizard_eof_midway_keeps_confirmed_items(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv())
    result = init(tmp_path, "kept topic\nsecond\n")  # EOF at the next topic prompt
    assert result.exit_code == 130
    assert ABORT in result.output
    assert load_profile(tmp_path / "v" / "profile.yaml").topics == ["kept topic", "second"]


def test_wizard_keyboard_interrupt_exits_130(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv())

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.typer, "prompt", boom)
    result = init(tmp_path, "")
    assert result.exit_code == 130
    assert ABORT in result.output
    assert (tmp_path / "v" / "profile.yaml").exists()


def test_non_tty_init_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    result = init(tmp_path, None)
    assert result.exit_code == 0
    assert "Vault ready at" in result.output
    assert "Next steps:" in result.output
    assert "Topic (blank to finish)" not in result.output
    assert len(load_profile(tmp_path / "v" / "profile.yaml").topics) == 3


def test_wizard_arxiv_down_skips_seed_and_continues(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv(error=SourceError("timeout")))
    result = init(tmp_path, "t1\n\n\nsome title\nanother\n\n")
    assert result.exit_code == 0, result.output
    assert result.output.count("Could not reach arXiv") == 2
    assert load_profile(tmp_path / "v" / "profile.yaml").seeds == []


def test_wizard_cache_failure_skips_seed(tmp_path, tty, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))

    def full(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "cache_seed", full)
    result = init(tmp_path, "t1\n\n\n2307.15043\ny\n\n")
    assert result.exit_code == 0, result.output
    assert "disk full" in result.output
    assert load_profile(tmp_path / "v" / "profile.yaml").seeds == []


def test_add_seed_cache_failure_is_clean_and_leaves_no_half_state(tmp_path, monkeypatch):
    path = tmp_path / "v"
    init_vault(path, topics=["t"])
    use(monkeypatch, StubArxiv(by_id=[paper()]))

    def full(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(cli, "cache_seed", full)
    result = runner.invoke(cli.app, ["profile", "add-seed", "2307.15043", "--vault", str(path)], input="y\n")
    assert result.exit_code == 1
    assert "disk full" in result.output
    assert "Traceback" not in result.output
    assert load_profile(path / "profile.yaml").seeds == []


def topics_after(tmp_path, tty_input, monkeypatch):
    use(monkeypatch, StubArxiv())
    result = init(tmp_path, tty_input)
    assert result.exit_code == 0, result.output
    return load_profile(tmp_path / "v" / "profile.yaml").topics


@pytest.mark.parametrize("typed", ["operating system and kernel security", "  Operating  SYSTEM and kernel security "])
def test_wizard_entered_example_topic_survives(tmp_path, tty, monkeypatch, typed):
    topics = topics_after(tmp_path, f"{typed}\n\n\n\n", monkeypatch)
    assert "operating system and kernel security" in [t.casefold() for t in topics]
    assert len(topics) == 3  # only a topic of your own replaces the examples


def test_wizard_non_example_topic_replaces_examples(tmp_path, tty, monkeypatch):
    assert topics_after(tmp_path, "my own topic\n\n\n\n", monkeypatch) == ["my own topic"]


def test_wizard_non_example_then_example_keeps_example(tmp_path, tty, monkeypatch):
    topics = topics_after(tmp_path, "my own topic\noperating system and kernel security\n\n\n\n", monkeypatch)
    assert topics == ["my own topic", "operating system and kernel security"]


def test_wizard_example_then_non_example_keeps_both(tmp_path, tty, monkeypatch):
    topics = topics_after(tmp_path, "operating system and kernel security\nmy own topic\n\n\n\n", monkeypatch)
    assert topics == ["operating system and kernel security", "my own topic"]


def test_wizard_example_removal_failure_is_skipped_not_a_traceback(tmp_path, tty, monkeypatch):
    def boom(vault, text):
        raise OSError("read-only")

    monkeypatch.setattr(cli.profile_edit, "remove_topic", boom)
    use(monkeypatch, StubArxiv())
    result = init(tmp_path, "my own topic\n\n\n\n")
    assert result.exit_code == 0, result.output
    assert "Skipped" in result.output and "read-only" in result.output
