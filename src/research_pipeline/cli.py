"""Command line interface."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from research_pipeline.vault import DEFAULT_CATEGORIES, init_vault, resolve_vault

app = typer.Typer(help="Nightly research paper pipeline.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Nightly research paper pipeline."""


def _is_tty() -> bool:
    return sys.stdin.isatty()


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


@app.command()
def init(
    vault: Annotated[
        Path | None, typer.Option(help="Vault folder (default: $RESEARCH_VAULT or ~/research-vault).")
    ] = None,
) -> None:
    """Create the vault folders, config.yaml, profile.yaml and database (safe to re-run)."""
    path = resolve_vault(vault)
    topics = categories = None
    if _is_tty():
        entered = typer.prompt("Topics you care about (comma-separated, blank for examples)", default="")
        topics = _split(entered) or None
        entered = typer.prompt("arXiv categories (comma-separated)", default=", ".join(DEFAULT_CATEGORIES))
        categories = _split(entered) or None
    existed = {n for n in ("config.yaml", "profile.yaml") if (path / n).exists()}
    init_vault(path, topics=topics, categories=categories)
    typer.echo(f"Vault ready at {path}")
    for name in ("config.yaml", "profile.yaml"):
        typer.echo(f"  {name}: {'kept existing' if name in existed else 'created'}")
    typer.echo("  vault.sqlite, raw/, markdown/, summaries/, my_work/, gold/, digests/, cache/ ready")
    typer.echo("\nNext steps:")
    typer.echo(f"  1. Edit {path / 'profile.yaml'} (topics, followed authors, keywords).")
    typer.echo("  2. Replace the 3 placeholder seeds with 10-20 arXiv ids of papers you like.")
