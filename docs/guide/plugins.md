# Plug-ins

## What it does

A *plug-in* is a Python package, installed next to `rpipe`, that adds something the pipeline can use without changing the pipeline's code. There are two kinds, called *groups*:

- **Sources** (`research_pipeline.sources`): where papers come from. A source is a small object with a `name` and a `fetch()` method that returns papers.
- **Backends** (`research_pipeline.backends`): what writes the summaries (see `backends.md`).

The pipeline ships with built-ins: sources `arxiv` and `hf_daily`. Anyone can add more by installing a package. See what is installed with `rpipe plugins`; turn sources on and off with `rpipe sources list|enable|disable` (see `sources.md`).

## How it works

1. A package announces a plug-in with an *entry point*: a line in its `pyproject.toml` saying "under the group `research_pipeline.sources`, the name `mysource` means the function `make_source` in package `mypkg`". Python records it in the package's installed metadata, so any program can ask "who registered something in this group?" without importing everything.
2. `registry.plugins(group)` returns a dict of name to `Plugin(name, factory, origin, error)`. The built-ins come first, then every entry point of that group. `origin` is `built-in` or the package's name.
3. Loading an entry point imports the package. If that fails (the package was uninstalled half-way, or has a bug) the plug-in is still listed, with `factory=None` and `error` set to for example `ImportError: ...`. Nothing crashes.
4. A plug-in whose name equals a built-in is ignored and listed as `arxiv (other-package)` with the error `name taken by a built-in`.
5. `registry.build_sources(config)` goes through `config.sources` in order, calls each factory with the `Config` and returns `(sources, errors)`. A name that is not installed, whose package fails to import, or whose factory raises becomes an error string such as ``source gone: not installed; see `rpipe sources list` `` and is skipped.
6. `rpipe run` adds those errors to its error list before collecting, so they appear in the digest's health line and the `runs` table, and the digest is still written from the sources that did start.

Worked example: you enabled `mysource`, then uninstalled its package. Tonight's health line says ``errors: source mysource: not installed; see `rpipe sources list` `` and the digest contains papers from arXiv and Hugging Face as usual.

### A third-party source in 15 lines

`pyproject.toml` of the package:

```toml
[project]
name = "mypkg"
version = "0.1"
[project.entry-points."research_pipeline.sources"]
mysource = "mypkg:make_source"
```

`mypkg/__init__.py`:

```python
from research_pipeline.models import PaperMeta

class MySource:
    name = "mysource"

    def fetch(self) -> list[PaperMeta]:
        return [PaperMeta(id="my-1", title="A paper", source="mysource", url="https://example.org/1")]

def make_source(config):  # config: research_pipeline.models.Config
    return MySource()
```

Install it into the same environment as `rpipe`, then `rpipe sources enable mysource`.

## Why it's built this way

- **Entry points, not a plug-in folder:** installing and removing are ordinary `pip`/`uv` operations, and nobody edits files.
- **Built-ins are hard-coded in `registry.py`, not entry points:** an editable install made before an entry point was added keeps stale metadata and would silently lose the source. The built-ins must always work.
- **A broken plug-in is reported, not fatal:** the pipeline runs unattended at night; a missing paper source must degrade the digest, not cancel it.
- Rejected: crashing at start-up on any unknown name (a nightly run would produce nothing).

## How to run / check it

```
rpipe plugins
rpipe sources list
uv run pytest tests/test_registry.py tests/test_cli_sources.py -q
```

Tests fake the entry points (`registry._entry_points`), so they run offline.

## Where the code is

- `src/research_pipeline/registry.py`: `Plugin`, `plugins`, `build_sources`, `SOURCE_GROUP`, `BACKEND_GROUP`
- `src/research_pipeline/cli.py`: `build_pipeline`, `plugins`, `sources` commands
- `tests/test_registry.py`, `tests/test_cli_sources.py`, `tests/test_e2e.py`
