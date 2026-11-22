import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from research_pipeline.cli import app
from research_pipeline.vault import (
    ConfigError,
    init_vault,
    load_config,
    load_profile,
    resolve_vault,
)

FOLDERS = ["raw", "markdown", "summaries", "my_work", "gold", "digests", "cache"]


def test_init_creates_expected_layout(tmp_path):
    vault = tmp_path / "v"
    init_vault(vault)
    for name in FOLDERS:
        assert (vault / name).is_dir()
    assert (vault / "vault.sqlite").is_file()
    conn = sqlite3.connect(vault / "vault.sqlite")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert "papers" in tables
    cfg = load_config(vault / "config.yaml")
    assert cfg.vault_path == vault
    profile = load_profile(vault / "profile.yaml")
    assert profile.seeds == []
    assert profile.topics


def test_init_with_topics(tmp_path):
    init_vault(tmp_path, topics=["agent security", "kernels"])
    assert load_profile(tmp_path / "profile.yaml").topics == ["agent security", "kernels"]


def test_init_twice_preserves_edited_profile(tmp_path):
    init_vault(tmp_path)
    (tmp_path / "profile.yaml").write_text("topics: [mine]\nseeds: []\n")
    (tmp_path / "config.yaml").write_text(f"vault_path: {tmp_path}\nrss_feeds: [x]\n")
    init_vault(tmp_path)
    assert load_profile(tmp_path / "profile.yaml").topics == ["mine"]
    assert load_config(tmp_path / "config.yaml").rss_feeds == ["x"]


def test_load_profile_zero_seeds_ok(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("topics: [a]\nseeds: []\n")
    assert load_profile(p).seeds == []


def test_load_profile_invalid_yaml_raises_clear_error(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("topics: [unclosed\n")
    with pytest.raises(ConfigError, match="profile.yaml"):
        load_profile(p)


def test_load_profile_schema_violation_names_file(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("topics: [a]\nseeds: []\ntop_n: 0\n")
    with pytest.raises(ConfigError, match="profile.yaml"):
        load_profile(p)


def test_load_config_missing_file_names_file(tmp_path):
    with pytest.raises(ConfigError, match="config.yaml"):
        load_config(tmp_path / "config.yaml")


def test_resolve_vault(monkeypatch, tmp_path):
    assert resolve_vault(tmp_path) == tmp_path
    monkeypatch.setenv("RESEARCH_VAULT", str(tmp_path / "env"))
    assert resolve_vault(None) == tmp_path / "env"
    monkeypatch.delenv("RESEARCH_VAULT")
    assert resolve_vault(None) == Path.home() / "research-vault"


def test_cli_init_via_typer_runner(tmp_path):
    result = CliRunner().invoke(app, ["init", "--vault", str(tmp_path / "v")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "v" / "profile.yaml").is_file()
    assert "profile.yaml" in result.output
    data = yaml.safe_load((tmp_path / "v" / "config.yaml").read_text())
    assert data["vault_path"] == str(tmp_path / "v")


def test_cli_init_tty_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr("research_pipeline.cli._is_tty", lambda: True)
    monkeypatch.setattr("research_pipeline.cli.make_arxiv", lambda config: object())
    result = CliRunner().invoke(
        app,
        ["init", "--vault", str(tmp_path / "v")],
        input="\nagent security\nkernels\n\ncs.CR, cs.OS\n\n",
    )
    assert result.exit_code == 0, result.output
    assert load_profile(tmp_path / "v" / "profile.yaml").topics == ["agent security", "kernels"]
    assert load_config(tmp_path / "v" / "config.yaml").arxiv_categories == ["cs.CR", "cs.OS"]


def test_load_profile_unquoted_arxiv_id_keeps_trailing_zeros(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("topics: [a]\nseeds: [2301.10000, 2401.00001]\ntop_n: 5\n")
    profile = load_profile(p)
    assert profile.seeds == ["2301.10000", "2401.00001"]
    assert profile.top_n == 5


def test_load_profile_unquoted_short_id(tmp_path):
    p = tmp_path / "profile.yaml"
    p.write_text("topics: [a]\nseeds: [2301.1234]\n")
    assert load_profile(p).seeds == ["2301.1234"]


def test_relative_vault_gives_absolute_vault_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["init", "--vault", "foo"])
    assert result.exit_code == 0, result.output
    cfg = load_config(tmp_path / "foo" / "config.yaml")
    assert cfg.vault_path == (tmp_path / "foo").resolve()
    assert cfg.vault_path.is_absolute()


def test_tilde_env_vault_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("RESEARCH_VAULT", "~/v")
    assert resolve_vault(None) == (tmp_path / "v").resolve()
    result = CliRunner().invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert load_config(tmp_path / "v" / "config.yaml").vault_path == (tmp_path / "v").resolve()


def test_init_vault_direct_relative_path_normalized(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_vault(Path("rel"))
    assert load_config(tmp_path / "rel" / "config.yaml").vault_path == (tmp_path / "rel").resolve()


def test_unknown_keys_are_rejected_naming_the_file(tmp_path):
    from research_pipeline.vault import ConfigError, load_config, load_profile

    p = tmp_path / "profile.yaml"
    p.write_text('topics: ["a"]\nseeds: []\nkeyword: ["x"]\n')
    with pytest.raises(ConfigError, match="profile.yaml") as e:
        load_profile(p)
    assert "keyword" in str(e.value)
    c = tmp_path / "config.yaml"
    c.write_text("vault_path: /v\narxiv-categories: [cs.LG]\n")
    with pytest.raises(ConfigError, match="config.yaml"):
        load_config(c)


def test_default_templates_still_load(tmp_path):
    from research_pipeline.vault import init_vault, load_config, load_profile

    init_vault(tmp_path / "v")
    assert load_profile(tmp_path / "v" / "profile.yaml").topics
    assert load_config(tmp_path / "v" / "config.yaml").arxiv_max_results == 1000
