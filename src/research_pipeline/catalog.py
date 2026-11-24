"""The model catalog: a versioned list of candidate Ollama models the recommender ranks.

A copy ships with the package; `rpipe model catalog update` can fetch a newer one into the vault.
The higher `version` wins; an invalid or older fetched copy is ignored."""
import os
import tempfile
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

Band = Literal["cpu", "small", "mid", "high", "very_large"]
BANDS: tuple[str, ...] = ("cpu", "small", "mid", "high", "very_large")

CATALOG_URL = "https://raw.githubusercontent.com/jrandall54/research-pipeline/main/src/research_pipeline/defaults/model_catalog.yaml"
FETCHED_NAME = "model_catalog.yaml"


class CatalogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    family: str
    pull: str
    arch: Literal["dense", "moe"]
    params_b: float
    active_b: float | None = None
    quant: str
    download_gb: float
    trained_ctx: int
    kv_mib_per_1k: float
    bands: list[Band]
    notes: str = ""

    @field_validator("bands")
    @classmethod
    def _bands_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("bands must list at least one band")
        return v


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    updated: date
    entries: list[CatalogEntry]

    @model_validator(mode="after")
    def _ids_unique(self) -> "Catalog":
        seen: set[str] = set()
        for e in self.entries:
            if e.id in seen:
                raise ValueError(f"duplicate model id: {e.id}")
            seen.add(e.id)
        return self


def parse_catalog(text: str) -> Catalog:
    """Parse and validate catalog YAML; raises ValueError with a plain message."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"the catalog is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("the catalog must be a mapping with version, updated and entries")
    try:
        return Catalog.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"the catalog is invalid: {problems}") from exc


def shipped_catalog() -> Catalog:
    text = resources.files("research_pipeline.defaults").joinpath("model_catalog.yaml").read_text("utf-8")
    return parse_catalog(text)


def load_catalog(vault: Path) -> Catalog:
    """The fetched copy when it is valid and newer than the shipped one, else the shipped one."""
    shipped = shipped_catalog()
    path = vault / "cache" / FETCHED_NAME
    try:
        fetched = parse_catalog(path.read_text("utf-8"))
    except (OSError, ValueError):
        return shipped
    return fetched if fetched.version > shipped.version else shipped


def fetch_catalog(url: str = CATALOG_URL, get=httpx.get) -> Catalog:
    """Download and validate a catalog; raises ValueError on any network, HTTP or schema problem."""
    return parse_catalog(fetch_catalog_text(url, get))


def fetch_catalog_text(url: str = CATALOG_URL, get=httpx.get) -> str:
    try:
        resp = get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"could not fetch {url}: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"could not fetch {url}: {exc}") from exc
    return resp.text


def _gb(x: float) -> str:
    return f"{x:g}"


def diff_catalogs(old: Catalog, new: Catalog) -> list[str]:
    """Plain lines: `+ id (pull, X GB)`, `- id`, `~ id: field old -> new`."""
    before = {e.id: e for e in old.entries}
    after = {e.id: e for e in new.entries}
    lines = [f"+ {i} ({e.pull}, {_gb(e.download_gb)} GB)" for i, e in after.items() if i not in before]
    lines += [f"- {i}" for i in before if i not in after]
    for i, e in after.items():
        if i in before:
            for field in CatalogEntry.model_fields:
                a, b = getattr(before[i], field), getattr(e, field)
                if a != b:
                    lines.append(f"~ {i}: {field} {a} -> {b}")
    return lines


def save_fetched(vault: Path, text: str) -> None:
    """Write the fetched copy atomically (temp file in the same folder, then rename over)."""
    path = vault / "cache" / FETCHED_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
