"""Category codes are checked against the packaged copy of arXiv's list."""

import pytest
from test_e2e import install, run_cli, vault  # noqa: F401  (offline run setup)

from research_pipeline import cli
from research_pipeline import profile_edit as pe
from research_pipeline.categories import closest, known_categories, unknown
from research_pipeline.vault import load_config


def test_known_includes_cs_and_old_style():
    k = known_categories()
    assert {"cs.LG", "cs.CR", "stat.ML", "math.PR", "hep-th", "astro-ph", "cond-mat",
            "cond-mat.stat-mech", "physics.comp-ph"} <= k


def test_cs_ML_unknown_with_suggestion():
    assert unknown(["cs.ML", "cs.LG", "all"]) == ["cs.ML"]
    assert closest("cs.ML") == "cs.LG"


def test_closest_is_none_when_nothing_is_near():
    assert closest("zzzzzz") is None


def test_set_categories_rejects_unknown(vault):  # noqa: F811
    with pytest.raises(ValueError, match=r"cs\.ML is not an arXiv category \(did you mean cs\.LG\?\)"):
        pe.set_categories(vault, ["cs.ML"])
    assert load_config(vault / "config.yaml").arxiv_categories != ["cs.ML"]


def test_cli_set_categories_rejects_unknown(vault):  # noqa: F811
    res = run_cli("profile", "set-categories", "cs.CR", "cs.ML", "--vault", str(vault))
    assert res.exit_code == 1
    assert "cs.ML is not an arXiv category (did you mean cs.LG?)" in res.output


def _store_cs_ml(vault):  # noqa: F811
    path = vault / "config.yaml"
    text = path.read_text()
    import re
    path.write_text(re.sub(r"(?m)^arxiv_categories:.*$", 'arxiv_categories: ["cs.ML"]', text))


def test_stored_unknown_category_still_runs(vault, monkeypatch):  # noqa: F811
    _store_cs_ml(vault)
    install(monkeypatch)
    res = run_cli("run", "--vault", str(vault))
    assert res.exit_code == 0, res.output
    assert "unknown categories: cs.ML" in res.output
    assert "unknown categories: cs.ML" in next((vault / "digests").glob("*.md")).read_text().splitlines()[0]


def test_profile_show_lists_unknown_categories(vault):  # noqa: F811
    _store_cs_ml(vault)
    res = run_cli("profile", "show", "--vault", str(vault))
    assert "unknown categories: cs.ML" in res.output


def test_no_unknown_line_when_all_known(vault):  # noqa: F811
    res = run_cli("profile", "show", "--vault", str(vault))
    assert "unknown categories" not in res.output
    assert "unknown categories" not in cli.health_line(__import__("datetime").date(2026, 1, 1), {}, [])
