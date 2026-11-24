"""Model catalog: schema, fetched-vs-shipped choice, diff, and `rpipe model catalog show|update`."""
import re
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from research_pipeline import cli, registry
from research_pipeline.catalog import (
    CATALOG_URL,
    FETCHED_NAME,
    diff_catalogs,
    fetch_catalog,
    load_catalog,
    parse_catalog,
    save_fetched,
    shipped_catalog,
)
from research_pipeline.vault import init_vault

FIXTURE = Path(__file__).parent / "fixtures" / "catalog_small.yaml"
TEXT = FIXTURE.read_text()
runner = CliRunner()


def bump(text: str, version: int) -> str:
    return text.replace("version: 1", f"version: {version}", 1)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_entry_points", lambda group: [])
    path = tmp_path / "vault"
    init_vault(path, topics=["LLM security"])
    return path


def run(vault, *args, input=None):
    return runner.invoke(cli.app, [*args, "--vault", str(vault)], input=input)


def test_parse_valid_fixture():
    cat = parse_catalog(TEXT)
    assert len(cat.entries) == 3
    assert cat.entries[0].arch == "dense"


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="small-dense-q4"):
        parse_catalog(TEXT.replace("mid-moe-q4", "small-dense-q4"))


def test_unknown_field_rejected():
    with pytest.raises(ValueError):
        parse_catalog(TEXT.replace("family: Alpha", "family: Alpha\n  colour: red"))


def test_empty_bands_rejected():
    with pytest.raises(ValueError):
        parse_catalog(TEXT.replace("bands: [very_large]", "bands: []"))


def test_garbage_rejected():
    with pytest.raises(ValueError):
        parse_catalog("{{{ not yaml")


def test_load_prefers_newer_fetched(vault):
    save_fetched(vault, bump(TEXT, 999))
    assert load_catalog(vault).version == 999


@pytest.mark.parametrize("text", [None, "garbage: [", "version: 0\nupdated: 2026-01-01\nentries: []"])
def test_load_ignores_older_or_invalid_fetched(vault, text):
    if text is not None:
        save_fetched(vault, text)
    assert load_catalog(vault).version == shipped_catalog().version


def test_load_ignores_fetched_with_version_zero(vault):
    save_fetched(vault, bump(TEXT, 0))
    assert load_catalog(vault).version == shipped_catalog().version


def test_diff_lists_added_removed_changed():
    old = parse_catalog(TEXT)
    new_text = TEXT.replace("download_gb: 2.5", "download_gb: 2.6").replace("big-dense-q3", "new-one")
    lines = diff_catalogs(old, parse_catalog(new_text))
    assert "+ new-one (hf.co/example/gamma-70b-GGUF:UD-IQ3_S, 30 GB)" in lines
    assert "- big-dense-q3" in lines
    assert "~ small-dense-q4: download_gb 2.5 -> 2.6" in lines


def test_fetch_error_is_value_error():
    def get(url, **kw):
        return httpx.Response(404, request=httpx.Request("GET", url))
    with pytest.raises(ValueError, match="404"):
        fetch_catalog(get=get)


@respx.mock
def test_catalog_update_shows_diff_and_saves_on_yes(vault, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, text=bump(TEXT, 999)))
    out = run(vault, "model", "catalog", "update", input="y\n")
    assert out.exit_code == 0, out.output
    assert "Save this catalog?" in out.output
    assert (vault / "cache" / FETCHED_NAME).exists()


@respx.mock
def test_catalog_update_no_saves_nothing(vault, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, text=bump(TEXT, 999)))
    out = run(vault, "model", "catalog", "update", input="n\n")
    assert out.exit_code == 0, out.output
    assert not (vault / "cache" / FETCHED_NAME).exists()


@respx.mock
def test_catalog_update_without_terminal_prints_diff_and_exits_1(vault, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: False)
    respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, text=bump(TEXT, 999)))
    out = run(vault, "model", "catalog", "update")
    assert out.exit_code == 1
    assert "run in a terminal to confirm" in out.output
    assert not (vault / "cache" / FETCHED_NAME).exists()


@respx.mock
def test_catalog_update_unreachable_saves_nothing(vault, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    respx.get(CATALOG_URL).mock(return_value=httpx.Response(404))
    out = run(vault, "model", "catalog", "update")
    assert out.exit_code == 1
    assert CATALOG_URL in out.output
    assert not (vault / "cache" / FETCHED_NAME).exists()


@respx.mock
def test_catalog_update_older_says_up_to_date(vault, monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    respx.get(CATALOG_URL).mock(return_value=httpx.Response(200, text=bump(TEXT, 0)))
    out = run(vault, "model", "catalog", "update")
    assert out.exit_code == 0
    assert "already up to date" in out.output
    assert "Save this catalog?" not in out.output


def test_catalog_show_groups_by_band(vault):
    out = run(vault, "model", "catalog", "show")
    assert out.exit_code == 0, out.output
    assert "cpu" in out.output and "very_large" in out.output
    assert "GB" in out.output


def test_shipped_catalog_valid():
    cat = shipped_catalog()
    bands = {b for e in cat.entries for b in e.bands}
    assert bands == {"cpu", "small", "mid", "high", "very_large"}
    assert not any(re.search(r"^q\d", e.pull) for e in cat.entries)
