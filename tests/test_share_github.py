import json
import subprocess

import pytest
from fakes import FakeRunner, done

from research_pipeline import share_github as sg


def test_check_tools_missing_gh_message():
    runner = FakeRunner({("gh", "--version"): done(127, "", "not found")})
    with pytest.raises(sg.GithubError) as e:
        sg.check_tools(runner)
    assert "`gh` is missing" in str(e.value) and "cli.github.com" in str(e.value)
    sg.check_tools(FakeRunner())


def test_login_returns_handle_or_asks_to_sign_in():
    assert sg.login(FakeRunner({("gh", "api", "user"): done(0, "Alex\n")})) == "Alex"
    with pytest.raises(sg.GithubError, match="gh auth login"):
        sg.login(FakeRunner({("gh", "api", "user"): done(1, "", "not logged in")}))


def test_repo_status_missing_rpipe_other():
    assert sg.repo_status(FakeRunner({("gh", "api", "repos/a/rpipe-feed"): done(1)}), "a") == "missing"
    # the repo exists but has no feed.json
    other = FakeRunner({("gh", "api", "-H"): done(1, "", "Not Found")})
    assert sg.repo_status(other, "a") == "other"
    mine = FakeRunner({("gh", "api", "-H"): done(0, json.dumps({"_rpipe": {"format": 1}}))})
    assert sg.repo_status(mine, "a") == "rpipe"
    notjson = FakeRunner({("gh", "api", "-H"): done(0, "hello")})
    assert sg.repo_status(notjson, "a") == "other"


def test_set_topic_on_and_off():
    on = FakeRunner()
    sg.set_topic(on, "a", True)
    assert "names[]=rpipe-feed" in on.calls[0]
    off = FakeRunner()
    sg.set_topic(off, "a", False)
    assert json.loads(off.inputs[0]) == {"names": []}


def test_enable_pages_tolerates_already_enabled_but_not_other_errors():
    ok = FakeRunner({("gh", "api", "-X", "POST"): done(1, "", "Pages is already enabled (HTTP 409)")})
    sg.enable_pages(ok, "a")
    bad = FakeRunner({("gh", "api", "-X", "POST"): done(1, "", "HTTP 403: forbidden")})
    with pytest.raises(sg.GithubError, match="forbidden"):
        sg.enable_pages(bad, "a")


def test_create_repo_creates_then_clones(tmp_path):
    runner = FakeRunner()
    sg.create_repo(runner, "a", tmp_path / "clone")
    assert runner.calls[0][:3] == ["gh", "repo", "create"] and "--public" in runner.calls[0]
    assert runner.calls[1][:2] == ["git", "clone"]


def test_credentials_and_delete():
    runner = FakeRunner()
    sg.setup_git_credentials(runner)
    sg.delete_repo(runner, "a")
    assert runner.calls[0] == ["gh", "auth", "setup-git"]
    assert runner.calls[1][:3] == ["gh", "auth", "refresh"] and "delete_repo" in runner.calls[1]
    assert runner.calls[2] == ["gh", "repo", "delete", "a/rpipe-feed", "--yes"]
    with pytest.raises(sg.GithubError):
        sg.delete_repo(FakeRunner({("gh", "repo", "delete"): done(1, "", "nope")}), "a")


def test_subprocess_runner_reports_missing_command():
    r = sg.SubprocessRunner().run(["definitely-not-a-command-xyz"])
    assert isinstance(r, subprocess.CompletedProcess) and r.returncode == 127
