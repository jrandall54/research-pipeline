"""Edit profile.yaml and config.yaml from code without losing the user's comments.

Every public function reads the file, checks it is valid, changes one thing, checks the
result is still valid, and writes it back atomically. A file that is already invalid, or
a change that would make it invalid, raises ConfigError and leaves the file untouched.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from research_pipeline.backends.claude_code import resolve_model
from research_pipeline.llm import NO_MODEL
from research_pipeline.models import ALL_CATEGORIES, CATEGORY_RE, Config, Profile
from research_pipeline.vault import ConfigError, load_config, load_profile

CATEGORY_ERROR_HINT = (
    "use names like cs.CR, stat.ML or hep-th (full list: https://arxiv.org/category_taxonomy), "
    "or the single word all"
)
_NEW_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")
_OLD_ID_RE = re.compile(r"^[a-z-]+(\.[A-Z]{2})?/\d{7}$")


# C0/C1 control characters (incl. ESC) become a space; invisible bidi controls (which can
# make text display in a different order than it is stored) are dropped. Letters of any
# script, including right-to-left ones, are untouched.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c]")


def normalise_text(s: str) -> str:
    """Remove control and bidi-control characters, strip, and collapse runs of whitespace
    to single spaces."""
    return " ".join(_CONTROL.sub(" ", _BIDI.sub("", s)).split())


def _key(s: str) -> str:
    return normalise_text(s).casefold()


def normalise_arxiv_id(s: str) -> str:
    """Bare arXiv id without a version suffix; ValueError if `s` is not an id."""
    t = s.strip()
    t = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", t)
    t = re.sub(r"^arxiv:", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\.pdf$", "", t)
    t = re.sub(r"v\d+$", "", t)
    if _NEW_ID_RE.match(t) or _OLD_ID_RE.match(t):
        return t
    raise ValueError(f"{s!r} is not an arXiv id (expected something like 2307.15043)")


def seed_key(s: str) -> str:
    """Comparison key for a seed: its bare arXiv id if it is one, else its plain text."""
    try:
        return normalise_arxiv_id(s)
    except ValueError:
        return _key(s)


def has_seed(seeds: list[str], text: str) -> bool:
    """True if `text` names a seed already in `seeds`, however either was spelled."""
    wanted = seed_key(text)
    return any(seed_key(str(s)) == wanted for s in seeds)


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)  # "  - item" under its key, like the template
    y.width = 4096  # never re-wrap the user's long lines
    # Keep float-looking scalars (arXiv id 2301.10000) as text, like vault.load_profile.
    resolver = type("NoFloatResolver", (y.Resolver,), {})
    resolver.yaml_implicit_resolvers = {
        ch: [(tag, rx) for tag, rx in rs if tag != "tag:yaml.org,2002:float"]
        for ch, rs in y.Resolver.yaml_implicit_resolvers.items()
    }
    y.Resolver = resolver
    return y


def _plain(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if isinstance(x, bool) or x is None:
        return x
    if isinstance(x, int):
        return int(x)
    return str(x)


def _validate(path: Path, data: Any, model: type[BaseModel]) -> None:
    try:
        model.model_validate(_plain(data) if data is not None else {})
    except ValidationError as e:
        raise ConfigError(f"{path}: invalid settings:\n{e}") from e


def _read(path: Path, model: type[BaseModel]) -> Any:
    """Load for editing, after the normal loader has confirmed the file is valid."""
    (load_profile if model is Profile else load_config)(path)  # raises ConfigError
    try:
        data = _yaml().load(path.read_text(encoding="utf-8"))
    except (OSError, YAMLError) as e:
        raise ConfigError(f"{path}: cannot read file ({e})") from e
    return data


def _atomic_write(path: Path, data: Any) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _yaml().dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _edit(path: Path, model: type[BaseModel], change) -> bool:
    data = _read(path, model)
    if not change(data):
        return False
    _validate(path, data, model)
    _atomic_write(path, data)
    return True


def _profile_path(vault: Path) -> Path:
    return vault / "profile.yaml"


def _config_path(vault: Path) -> Path:
    return vault / "config.yaml"


def _list(data: Any, field: str) -> list:
    if data.get(field) is None:
        data[field] = []
    return data[field]


def _add(vault: Path, field: str, value: str, wrap=lambda v: v, key=_key) -> bool:
    def change(data) -> bool:
        items = _list(data, field)
        if any(key(str(i)) == key(value) for i in items):
            return False
        items.append(wrap(value))
        return True

    return _edit(_profile_path(vault), Profile, change)


def _remove(vault: Path, field: str, text_or_index: str, norm=_key) -> bool:
    def change(data) -> bool:
        items = _list(data, field)
        ref = text_or_index.strip()
        wanted = norm(ref)
        for i, item in enumerate(items):
            if norm(str(item)) == wanted:
                del items[i]
                return True
        if ref.isdigit():
            n = int(ref)
            if not 1 <= n <= len(items):
                raise ValueError(f"no {field} entry numbered {n} (there are {len(items)})")
            del items[n - 1]
            return True
        return False

    return _edit(_profile_path(vault), Profile, change)


def add_topic(vault: Path, text: str) -> bool:
    text = normalise_text(text)
    if not text:
        raise ValueError("topic must not be empty")
    return _add(vault, "topics", text)


def remove_topic(vault: Path, text_or_index: str) -> bool:
    return _remove(vault, "topics", text_or_index)


def add_author(vault: Path, name: str) -> bool:
    name = normalise_text(name)
    if not name:
        raise ValueError("author name must not be empty")
    return _add(vault, "followed_authors", name)


def remove_author(vault: Path, text_or_index: str) -> bool:
    return _remove(vault, "followed_authors", text_or_index)


def add_keyword(vault: Path, word: str) -> bool:
    word = normalise_text(word)
    if not word:
        raise ValueError("keyword must not be empty")
    return _add(vault, "keywords", word)


def remove_keyword(vault: Path, text_or_index: str) -> bool:
    return _remove(vault, "keywords", text_or_index)


def add_seed_id(vault: Path, arxiv_id: str) -> bool:
    # Quoted on write so the id is always read back as text, never as a number.
    return _add(vault, "seeds", normalise_arxiv_id(arxiv_id), DoubleQuotedScalarString, key=seed_key)


def remove_seed(vault: Path, id_or_index: str) -> bool:
    return _remove(vault, "seeds", id_or_index, norm=seed_key)


def set_top_n(vault: Path, n: int) -> None:
    def change(data) -> bool:
        data["top_n"] = n
        return True

    _edit(_profile_path(vault), Profile, change)


def set_feedback_halflife(vault: Path, days: float | str) -> None:
    """Days after which a vote or save counts half as much (1 to 3650)."""
    try:
        value = float(days)
    except (TypeError, ValueError):
        raise ValueError(f"{days!r} is not a number of days (give 1 to 3650, for example 60)") from None
    if not 1 <= value <= 3650:  # also rejects nan
        raise ValueError(f"{days!r} is out of range: give a number of days from 1 to 3650")

    def change(data) -> bool:
        data["feedback_halflife_days"] = value
        return True

    _edit(_profile_path(vault), Profile, change)


def set_categories(vault: Path, cats: list[str]) -> None:
    path = _config_path(vault)
    cleaned = [c.strip() for c in cats]
    cleaned = [ALL_CATEGORIES if c.lower() == ALL_CATEGORIES else c for c in cleaned]
    if ALL_CATEGORIES in cleaned and cleaned != [ALL_CATEGORIES]:
        raise ConfigError(
            f"{path}: 'all' means every computer-science category (cs.*), so it cannot be combined with "
            "others: give either all, or a list of categories"
        )
    bad = [c for c in cleaned if not CATEGORY_RE.match(c)]
    if bad or not cleaned:
        raise ConfigError(
            f"{path}: invalid arXiv categories {bad or cats!r}: {CATEGORY_ERROR_HINT}; "
            "give at least one"
        )

    def change(data) -> bool:
        data["arxiv_categories"] = cleaned
        return True

    _edit(path, Config, change)


def set_llm_backend(vault: Path, name: str, known: set[str], cli_path: str | None = None) -> None:
    """Choose the summary backend. Switching backends resets the model to `auto` (names differ per
    backend); re-choosing the current backend keeps a pinned model."""
    name = name.strip()
    if name not in known:
        raise ValueError(f"unknown backend {name!r}; known backends: {', '.join(sorted(known))}")

    def change(data) -> bool:
        if data.get("llm_backend", "ollama") != name:
            data["llm_model"] = "auto"
        data["llm_backend"] = name
        if cli_path is not None:
            data["llm_cli_path"] = cli_path
        return True

    _edit(_config_path(vault), Config, change)


def set_llm_model(vault: Path, name: str) -> None:
    name = name.strip()
    backend = load_config(_config_path(vault)).llm_backend
    if backend == "none":
        raise ValueError(NO_MODEL)
    if not name:
        raise ValueError("model name must not be empty (for example qwen3:14b, or auto)")
    if name.lower() == "auto":
        name = "auto"
    elif backend == "claude-code":
        resolve_model(name)  # ValueError naming the allowed choices; the user's text is stored

    def change(data) -> bool:
        data["llm_model"] = name
        return True

    _edit(_config_path(vault), Config, change)


def set_llm_url(vault: Path, url: str) -> None:
    url = url.strip()
    if not re.match(r"^https?://\S+$", url):
        raise ValueError(f"{url!r} is not a server address: it must start with http:// or https://")

    def change(data) -> bool:
        data["llm_url"] = url
        return True

    _edit(_config_path(vault), Config, change)


def set_llm_num_ctx(vault: Path, n: int | str) -> None:
    """Pin the context window to a whole number of tokens (at least 8192), or give `auto`."""
    if isinstance(n, str):
        text = n.strip().lower()
        if text == "auto":
            n = "auto"
        elif text.isdigit():
            n = int(text)
        else:
            raise ValueError(
                f"{n!r} is not a context size: give a whole number of tokens (at least 8192) or auto"
            )

    def change(data) -> bool:
        data["llm_num_ctx"] = n
        return True

    _edit(_config_path(vault), Config, change)


def enable_source(vault: Path, name: str, known: set[str]) -> bool:
    """Turn a source on (appended to the end of the order). False if it was already on."""
    name = name.strip()
    if name not in known:
        raise ValueError(f"unknown source {name!r}; known sources: {', '.join(sorted(known))}")

    def change(data) -> bool:
        sources = _list(data, "sources") if "sources" in data else ["arxiv", "hf_daily"]
        if name in sources:
            return False
        data["sources"] = [*sources, name]
        return True

    return _edit(_config_path(vault), Config, change)


def disable_source(vault: Path, name: str) -> bool:
    """Turn a source off. False if it was already off; the last enabled one cannot be removed."""
    name = name.strip()

    def change(data) -> bool:
        sources = list(data["sources"]) if "sources" in data else ["arxiv", "hf_daily"]
        if name not in sources:
            return False
        if len(sources) == 1:
            raise ValueError("at least one source must stay enabled")
        data["sources"] = [s for s in sources if s != name]
        return True

    return _edit(_config_path(vault), Config, change)
