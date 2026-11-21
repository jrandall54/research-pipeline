import json

import pytest
from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.models import PaperMeta
from research_pipeline.sources.base import SourceError
from research_pipeline.vault import init_vault, load_config, load_profile

runner = CliRunner()


def paper(pid="2307.15043", title="Universal and Transferable Adversarial Attacks"):
    return PaperMeta(
        id=pid, title=title, abstract="An abstract.", authors=["Andy Zou", "Zifan Wang"],
        source="arxiv", url=f"https://arxiv.org/abs/{pid}", published="2023-07-27",
    )


class StubArxiv:
    def __init__(self, by_id=None, by_title=None, error=None):
        self.by_id, self.by_title, self.error = by_id or [], by_title or [], error
        self.calls = []

    def fetch_by_ids(self, ids):
        self.calls.append(("id", ids))
        if self.error:
            raise self.error
        return self.by_id

    def search_title(self, query, max_results=5):
        self.calls.append(("title", query))
        if self.error:
            raise self.error
        return self.by_title


@pytest.fixture
def vault(tmp_path):
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security", "agent safety"])
    return path


def run(vault, *args, input=None):
    return runner.invoke(cli.app, ["profile", *args, "--vault", str(vault)], input=input)


def use(monkeypatch, stub):
    monkeypatch.setattr(cli, "make_arxiv", lambda config: stub)
    return stub


def test_profile_show_lists_everything_numbered(vault, monkeypatch):
    run(vault, "add-author", "Andy Zou")
    run(vault, "add-keyword", "jailbreak")
    stub = use(monkeypatch, StubArxiv(by_id=[paper()]))
    run(vault, "add-seed", "2307.15043", "--yes")
    (vault / "cache" / "seeds.json").unlink()
    stub.by_id = [paper("2301.00001", "Cached one")]
    run(vault, "add-seed", "2301.00001", "--yes")
    res = run(vault, "show")
    assert res.exit_code == 0
    out = res.output
    assert "1. LLM security" in out and "2. agent safety" in out
    assert "Andy Zou" in out and "jailbreak" in out
    assert "1. 2307.15043  (title not cached)" in out
    assert "2. 2301.00001  Cached one" in out
    assert "Top N: 15" in out
    assert "cs.CR" in out


def test_profile_show_uses_cached_title(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    run(vault, "add-seed", "2307.15043", "--yes")
    res = run(vault, "show")
    assert "1. 2307.15043  Universal and Transferable Adversarial Attacks" in res.output


def test_add_topic_then_duplicate_message(vault):
    res = run(vault, "add-topic", "  prompt   injection ")
    assert res.exit_code == 0 and "Added topic: prompt injection" in res.output
    assert "prompt injection" in load_profile(vault / "profile.yaml").topics
    res = run(vault, "add-topic", "PROMPT injection")
    assert res.exit_code == 0 and "already in your profile" in res.output
    res = run(vault, "add-topic", "   ")
    assert res.exit_code == 1 and "must not be empty" in res.output
    assert "Traceback" not in res.output


def test_remove_topic_by_number_and_missing(vault):
    res = run(vault, "remove-topic", "1")
    assert res.exit_code == 0 and "Removed topic" in res.output
    assert load_profile(vault / "profile.yaml").topics == ["agent safety"]
    res = run(vault, "remove-topic", "nonsense")
    assert res.exit_code == 0 and "not in your profile" in res.output
    res = run(vault, "remove-topic", "9")
    assert res.exit_code == 1 and "numbered 9" in res.output


def test_add_seed_single_candidate_confirm_yes(vault, monkeypatch):
    stub = use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = run(vault, "add-seed", "https://arxiv.org/abs/2307.15043v2", input="y\n")
    assert res.exit_code == 0, res.output
    assert "Add this paper? [Y/n]" in res.output
    assert "Added seed 2307.15043: Universal and Transferable Adversarial Attacks" in res.output
    assert "Andy Zou" in res.output and "2023" in res.output
    assert load_profile(vault / "profile.yaml").seeds == ["2307.15043"]
    cache = json.loads((vault / "cache" / "seeds.json").read_text())
    assert "2307.15043" in cache
    assert stub.calls == [("id", ["2307.15043"])]


def test_add_seed_declined_changes_nothing(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    before = (vault / "profile.yaml").read_text()
    res = run(vault, "add-seed", "2307.15043", input="n\n")
    assert res.exit_code == 0 and "Nothing was changed" in res.output
    assert (vault / "profile.yaml").read_text() == before
    assert not (vault / "cache" / "seeds.json").exists()


def test_add_seed_title_pick_second(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_title=[paper("2301.00001", "First"), paper("2302.00002", "Second")]))
    res = run(vault, "add-seed", "some title words", input="2\n")
    assert res.exit_code == 0, res.output
    assert "1. First" in res.output and "2. Second" in res.output
    assert "Pick 1-2 or 0 to cancel" in res.output
    assert load_profile(vault / "profile.yaml").seeds == ["2302.00002"]
    assert "Added seed 2302.00002: Second" in res.output


def test_add_seed_title_pick_zero_cancels(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_title=[paper("2301.00001", "First"), paper("2302.00002", "Second")]))
    res = run(vault, "add-seed", "some title words", input="0\n")
    assert res.exit_code == 0 and "Nothing was changed" in res.output
    assert load_profile(vault / "profile.yaml").seeds == []


def test_add_seed_yes_takes_first_candidate(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_title=[paper("2301.00001", "First"), paper("2302.00002", "Second")]))
    res = run(vault, "add-seed", "some title words", "--yes")
    assert res.exit_code == 0 and "Added seed 2301.00001: First" in res.output


def test_add_seed_not_found_exit_1(vault, monkeypatch):
    use(monkeypatch, StubArxiv())
    res = run(vault, "add-seed", "no such paper at all")
    assert res.exit_code == 1
    assert 'No paper found for "no such paper at all"' in res.output


def test_add_seed_arxiv_down_exit_1_file_unchanged(vault, monkeypatch):
    use(monkeypatch, StubArxiv(error=SourceError("HTTP 503")))
    before = (vault / "profile.yaml").read_text()
    res = run(vault, "add-seed", "2307.15043", "--yes")
    assert res.exit_code == 1
    assert "Could not reach arXiv: HTTP 503. Nothing was changed." in res.output
    assert (vault / "profile.yaml").read_text() == before


@pytest.mark.parametrize("text", ["", "   "])
def test_add_seed_empty_input_rejected_before_arxiv(vault, monkeypatch, text):
    stub = use(monkeypatch, StubArxiv(by_title=[paper()]))
    res = run(vault, "add-seed", text, "--yes")
    assert res.exit_code == 1 and "empty" in res.output
    assert stub.calls == []


def test_add_seed_already_present_skips_arxiv(vault, monkeypatch):
    stub = use(monkeypatch, StubArxiv(by_id=[paper()]))
    run(vault, "add-seed", "2307.15043", "--yes")
    stub.calls.clear()
    res = run(vault, "add-seed", "arxiv.org/pdf/2307.15043v3.pdf")
    assert res.exit_code == 0 and "already in your profile" in res.output
    assert stub.calls == []


def test_add_seed_title_pick_already_present(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()], by_title=[paper()]))
    run(vault, "add-seed", "2307.15043", "--yes")
    res = run(vault, "add-seed", "universal attacks", "--yes")
    assert res.exit_code == 0 and "already in your profile" in res.output


def test_remove_seed_by_number(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    run(vault, "add-seed", "2307.15043", "--yes")
    res = run(vault, "remove-seed", "1")
    assert res.exit_code == 0 and "Removed seed" in res.output
    assert load_profile(vault / "profile.yaml").seeds == []
    res = run(vault, "remove-seed", "2307.15043")
    assert res.exit_code == 0 and "not in your profile" in res.output


def test_set_top_n(vault):
    res = run(vault, "set-top-n", "7")
    assert res.exit_code == 0 and "top_n is now 7" in res.output
    assert load_profile(vault / "profile.yaml").top_n == 7
    res = run(vault, "set-top-n", "0")
    assert res.exit_code == 1 and "Traceback" not in res.output


def test_set_categories_and_invalid_exit_1(vault):
    res = run(vault, "set-categories", "cs.CR", "stat.ML")
    assert res.exit_code == 0 and "cs.CR, stat.ML" in res.output
    assert load_config(vault / "config.yaml").arxiv_categories == ["cs.CR", "stat.ML"]
    res = run(vault, "set-categories", "CS-cr")
    assert res.exit_code == 1 and "invalid arXiv categories" in res.output
    assert load_config(vault / "config.yaml").arxiv_categories == ["cs.CR", "stat.ML"]


def test_invalid_profile_file_is_a_clean_error(vault):
    (vault / "profile.yaml").write_text("topics: [a]\nseeds: []\nbogus: 1\n")
    res = run(vault, "add-topic", "x")
    assert res.exit_code == 1 and "profile.yaml" in res.output


@pytest.mark.parametrize(
    "args",
    [["show"], ["add-topic", "x"], ["add-seed", "2307.15043"], ["set-top-n", "3"], ["remove-author", "1"]],
)
def test_commands_on_missing_vault_say_run_rpipe_init(tmp_path, args):
    res = run(tmp_path / "nope", *args)
    assert res.exit_code == 1 and "rpipe init" in res.output


def test_add_seed_pick_out_of_range_exit_1(vault, monkeypatch):
    use(monkeypatch, StubArxiv(by_title=[paper("2301.00001", "First"), paper("2302.00002", "Second")]))
    res = run(vault, "add-seed", "some title words", input="7\n")
    assert res.exit_code == 1 and "Nothing was changed" in res.output
    assert load_profile(vault / "profile.yaml").seeds == []


@pytest.mark.parametrize("form", ["https://arxiv.org/abs/2307.15043", "arXiv:2307.15043", "2307.15043v3"])
def test_add_seed_other_spellings_are_already_present(vault, monkeypatch, form):
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    run(vault, "add-seed", "2307.15043", "--yes")
    res = run(vault, "add-seed", form, "--yes")
    assert res.exit_code == 0 and "already in your profile" in res.output
    assert load_profile(vault / "profile.yaml").seeds == ["2307.15043"]


def test_hand_written_versioned_seed_dedupes(vault, monkeypatch):
    p = vault / "profile.yaml"
    p.write_text(p.read_text().replace("seeds: []", 'seeds: ["2307.15043v2"]'))
    use(monkeypatch, StubArxiv(by_id=[paper()]))
    res = run(vault, "add-seed", "2307.15043", "--yes")
    assert "already in your profile" in res.output
    assert load_profile(p).seeds == ["2307.15043v2"]


def _with_bogus_seed(vault):
    p = vault / "profile.yaml"
    p.write_text(p.read_text().replace("seeds: []", 'seeds: ["bogus-entry", "2307.15043v2"]'))


def test_remove_seed_by_id_with_non_id_item_earlier(vault):
    _with_bogus_seed(vault)
    res = run(vault, "remove-seed", "2307.15043")
    assert res.exit_code == 0, res.output
    assert load_profile(vault / "profile.yaml").seeds == ["bogus-entry"]


def test_remove_seed_by_non_id_value(vault):
    _with_bogus_seed(vault)
    res = run(vault, "remove-seed", "bogus-entry")
    assert res.exit_code == 0, res.output
    assert load_profile(vault / "profile.yaml").seeds == ["2307.15043v2"]


def test_one_arxiv_client_per_command_is_closed(vault, monkeypatch):
    stub = use(monkeypatch, StubArxiv(by_id=[paper()]))
    closed = []
    stub.close = lambda: closed.append(1)
    made = []
    monkeypatch.setattr(cli, "make_arxiv", lambda config: made.append(1) or stub)
    run(vault, "add-seed", "2307.15043", "--yes")
    assert len(made) == 1 and closed == [1]


def test_set_categories_all_and_show(vault):
    res = run(vault, "set-categories", "all")
    assert res.exit_code == 0 and "all computer science (cs.*)" in res.output
    assert load_config(vault / "config.yaml").arxiv_categories == ["all"]
    res = run(vault, "show")
    assert "arXiv categories: all computer science (cs.*)" in res.output
    res = run(vault, "set-categories", "all", "cs.CR")
    assert res.exit_code == 1 and "all" in res.output and "Traceback" not in res.output
    assert load_config(vault / "config.yaml").arxiv_categories == ["all"]


def test_set_categories_help_explains_categories():
    res = runner.invoke(cli.app, ["profile", "set-categories", "--help"])
    assert res.exit_code == 0
    for text in ("cs.CR", "category_taxonomy", "all"):
        assert text in res.output


def test_invalid_category_error_points_at_taxonomy(vault):
    res = run(vault, "set-categories", "CS-cr")
    assert res.exit_code == 1 and "https://arxiv.org/category_taxonomy" in res.output


def test_lowercase_subcategory_config_loads_and_set_categories_repairs_it(vault):
    cfg = vault / "config.yaml"
    cfg.write_text(cfg.read_text().replace("arxiv_categories:", "arxiv_categories: [physics.comp-ph]\n#", 1))
    assert load_config(cfg).arxiv_categories == ["physics.comp-ph"]
    assert run(vault, "show").exit_code == 0
    res = run(vault, "set-categories", "cs.CR")
    assert res.exit_code == 0
    assert load_config(cfg).arxiv_categories == ["cs.CR"]
    assert run(vault, "set-categories", "cond-mat.stat-mech").exit_code == 0
