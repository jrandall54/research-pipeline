"""Vault layout, path resolution, and config/profile loading."""

import json
import os
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError

from research_pipeline.db import open_db
from research_pipeline.models import Config, Profile

FOLDERS = ["raw", "markdown", "summaries", "my_work", "gold", "digests", "cache"]
DEFAULT_TOPICS = [
    "security of LLM agents and prompt injection",
    "efficient inference for large language models",
    "operating system and kernel security",
]
DEFAULT_CATEGORIES = ["cs.LG", "cs.CL", "cs.CR", "cs.OS", "cs.SE"]


class ConfigError(Exception):
    """A config or profile file is missing, not valid YAML, or breaks the schema."""


def resolve_vault(path: Path | None) -> Path:
    """Explicit path, else $RESEARCH_VAULT, else ~/research-vault."""
    if path is not None:
        return path
    env = os.environ.get("RESEARCH_VAULT")
    return Path(env) if env else Path.home() / "research-vault"


def _default_text(name: str) -> str:
    return resources.files("research_pipeline.defaults").joinpath(name).read_text("utf-8")


def init_vault(
    path: Path,
    *,
    topics: list[str] | None = None,
    categories: list[str] | None = None,
) -> None:
    """Create the vault layout. Idempotent; never overwrites config.yaml or profile.yaml."""
    path.mkdir(parents=True, exist_ok=True)
    for name in FOLDERS:
        (path / name).mkdir(exist_ok=True)

    profile = path / "profile.yaml"
    if not profile.exists():
        profile.write_text(
            _default_text("profile.yaml").replace(
                "__TOPICS__", json.dumps(topics or DEFAULT_TOPICS)
            ),
            encoding="utf-8",
        )
    config = path / "config.yaml"
    if not config.exists():
        config.write_text(
            _default_text("config.yaml")
            .replace("__VAULT_PATH__", json.dumps(str(path)))
            .replace("__CATEGORIES__", json.dumps(categories or DEFAULT_CATEGORIES)),
            encoding="utf-8",
        )
    open_db(path / "vault.sqlite").close()


class _NoFloatLoader(yaml.SafeLoader):
    """SafeLoader that leaves float-looking scalars (e.g. arXiv id 2301.10000) as strings."""


_NoFloatLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:float"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _load[T: BaseModel](path: Path, model: type[T]) -> T:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_NoFloatLoader)  # noqa: S506
    except OSError as e:
        raise ConfigError(f"{path}: cannot read file ({e.strerror or e})") from e
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    try:
        return model.model_validate(data if data is not None else {})
    except ValidationError as e:
        raise ConfigError(f"{path}: invalid settings:\n{e}") from e


def load_profile(path: Path) -> Profile:
    return _load(path, Profile)


def load_config(path: Path) -> Config:
    return _load(path, Config)
