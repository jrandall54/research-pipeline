"""The GitHub side of sharing: create the ``rpipe-feed`` repository, turn on Pages, set credentials.

Everything goes through a ``Runner`` (the real one runs ``git`` and ``gh``; tests pass a fake), so
no test touches GitHub. Failures raise ``GithubError`` with a plain message and what to do next.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Protocol

REPO = "rpipe-feed"
TOPIC = "rpipe-feed"
DESCRIPTION = "My rpipe research feed"


def feed_url(handle: str) -> str:
    """Where a published feed is served (GitHub Pages)."""
    return f"https://{handle.lower()}.github.io/{REPO}/feed.json"


class GithubError(Exception):
    """Something on the GitHub side failed; the message says what and how to fix it."""


class Runner(Protocol):
    def run(self, args: list[str], cwd: Path | None = None,
            input: str | None = None) -> subprocess.CompletedProcess[str]: ...


COMMAND_TIMEOUT_S = 120  # network commands (git clone/push, gh api ...) never wait longer than this


class SubprocessRunner:
    """Runs a real command. Never prompts for a password and never waits forever.

    A hung ``git push`` (a network stall, a locked keyring asking to be unlocked) would hold the
    pipeline lock all night, so every command has a time limit and a timeout is a ``GithubError``.
    """

    def __init__(self, timeout_s: float = COMMAND_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s

    def run(self, args: list[str], cwd: Path | None = None,
            input: str | None = None) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args, cwd=cwd, input=input, check=False, capture_output=True, text=True,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, timeout=self.timeout_s,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(args, 127, "", f"{args[0]}: command not found")
        except subprocess.TimeoutExpired:
            raise GithubError(
                f"`{' '.join(args[:2])}` took longer than {int(self.timeout_s)} seconds and was stopped. "
                "Check your internet connection (and that your keyring is unlocked), then try again."
            ) from None


_HINTS = {
    "git": "Install git (for example `sudo apt install git`), then try again.",
    "gh": "Install the GitHub command line tool from https://cli.github.com, then try again.",
}


def _fail(what: str, result: subprocess.CompletedProcess[str]) -> GithubError:
    detail = (result.stderr or result.stdout or "").strip()
    return GithubError(f"{what} failed: {detail}" if detail else f"{what} failed.")


def check_tools(runner: Runner) -> None:
    """Raise ``GithubError`` naming each missing tool (`git`, `gh`) with how to install it."""
    missing = [t for t in ("git", "gh") if runner.run([t, "--version"]).returncode != 0]
    if missing:
        raise GithubError(" ".join(f"`{t}` is missing. {_HINTS[t]}" for t in missing))


def login(runner: Runner) -> str:
    """The signed-in GitHub handle. The CLI runs `gh auth login` first if this raises."""
    r = runner.run(["gh", "api", "user", "--jq", ".login"])
    handle = r.stdout.strip()
    if r.returncode != 0 or not handle:
        raise GithubError("Sign in first: gh auth login")
    return handle


def repo_status(runner: Runner, handle: str) -> Literal["missing", "rpipe", "other"]:
    """Whether ``<handle>/rpipe-feed`` is absent, an rpipe feed (or our own half-made one), or other.

    A repo that only has ``README.md`` and the description ``create_repo`` sets counts as
    "rpipe": setup died after creating it and a retry must be able to carry on.
    Anything but a clean "not found" from GitHub raises (offline, signed out) rather than
    reading as "missing".
    """
    r = runner.run(["gh", "api", f"repos/{handle}/{REPO}"])
    if r.returncode != 0:
        if "404" in r.stderr or "not found" in r.stderr.lower():
            return "missing"
        raise _fail("Looking up your rpipe-feed repository", r)
    try:
        repo = json.loads(r.stdout)
    except ValueError:
        repo = None
    f = runner.run(["gh", "api", "-H", "Accept: application/vnd.github.raw+json",
                    f"repos/{handle}/{REPO}/contents/feed.json"])
    if f.returncode == 0:
        try:
            doc = json.loads(f.stdout)
        except ValueError:
            return "other"
        return "rpipe" if isinstance(doc, dict) and "_rpipe" in doc else "other"
    if not isinstance(repo, dict) or repo.get("description") != DESCRIPTION:
        return "other"
    root = runner.run(["gh", "api", f"repos/{handle}/{REPO}/contents"])
    try:
        names = {e.get("name") for e in json.loads(root.stdout)} if root.returncode == 0 else None
    except (ValueError, AttributeError):
        names = None
    return "rpipe" if names == {"README.md"} else "other"


def set_identity(runner: Runner, clone_dir: Path, handle: str, name: str | None) -> None:
    """Give the clone its own git identity so commits never carry the person's real email.

    The name is the display name (or the handle); the email is GitHub's private noreply address.
    Also means machines with no global git identity can still commit.
    """
    for key, value in (("user.name", name or handle),
                       ("user.email", f"{handle}@users.noreply.github.com")):
        r = runner.run(["git", "config", key, value], cwd=clone_dir)
        if r.returncode != 0:
            raise _fail("Setting the git identity of your feed clone", r)


def create_repo(runner: Runner, handle: str, clone_dir: Path) -> None:
    """Create the public repository (with a first commit so ``main`` exists) and clone it."""
    r = runner.run(["gh", "repo", "create", f"{handle}/{REPO}", "--public", "--add-readme",
                    "--description", DESCRIPTION])
    if r.returncode != 0:
        raise _fail("Creating the repository", r)
    r = runner.run(["git", "clone", f"https://github.com/{handle}/{REPO}.git", str(clone_dir)])
    if r.returncode != 0:
        raise _fail("Cloning the repository", r)


def set_topic(runner: Runner, handle: str, on: bool) -> None:
    """Add (or remove) the ``rpipe-feed`` topic, which is how the public directory finds feeds."""
    args = ["gh", "api", "-X", "PUT", f"repos/{handle}/{REPO}/topics"]
    for name in ([TOPIC] if on else []):
        args += ["-f", f"names[]={name}"]
    if not on:
        args += ["--input", "-"]
    r = runner.run(args, input=None if on else json.dumps({"names": []}))
    if r.returncode != 0:
        raise _fail("Updating the repository topic", r)


def enable_pages(runner: Runner, handle: str) -> None:
    """Serve the repository's ``main`` branch at the site root. Already enabled is fine."""
    r = runner.run(["gh", "api", "-X", "POST", f"repos/{handle}/{REPO}/pages",
                    "-f", "source[branch]=main", "-f", "source[path]=/"])
    if r.returncode != 0 and "already" not in (r.stderr + r.stdout).lower() and "409" not in r.stderr:
        raise _fail("Turning on GitHub Pages", r)


def setup_git_credentials(runner: Runner) -> None:
    """Let plain `git push` use the GitHub sign-in, so the nightly run never asks for a password."""
    r = runner.run(["gh", "auth", "setup-git"])
    if r.returncode != 0:
        raise _fail("Setting up git credentials", r)


def has_delete_scope(runner: Runner) -> bool:
    """Whether the signed-in token may delete repositories (``gh auth status`` lists its scopes)."""
    r = runner.run(["gh", "auth", "status"])
    return "delete_repo" in (r.stdout + r.stderr)


def delete_repo(runner: Runner, handle: str) -> None:
    """Delete ``<handle>/rpipe-feed``. The CLI gets the ``delete_repo`` permission first."""
    r = runner.run(["gh", "repo", "delete", f"{handle}/{REPO}", "--yes"])
    if r.returncode != 0:
        raise _fail("Deleting the repository", r)
