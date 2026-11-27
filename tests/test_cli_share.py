import json
from datetime import date

import httpx
import pytest
from fakes import FakeRunner, done
from typer.testing import CliRunner

from research_pipeline import cli, profile_edit, schedule, share_publish
from research_pipeline import feed_format as ff
from research_pipeline.vault import init_vault, load_config

runner = CliRunner()
NOW = "2026-11-26T03:00:00Z"
BASE = "https://alex.github.io/rpipe-feed/"
SYSTEMCTL: list = []


def feed_files(name="Alex M."):
    post = ff.Post(
        paper=ff.PaperBlock(id="p1", title="Title", abstract="Abs", authors=["A"],
                            url="https://arxiv.org/abs/p1", published="2026-11-25"),
        digest_date="2026-11-25", rank=1, summary=None, reasons=[], provenance=None,
    )
    return ff.build_files([post], display_name=name, topics=["agents"], active=True,
                          now=NOW, base_url=BASE)


def serving(files, base=BASE):
    """An httpx client answering from a dict of files (no network)."""
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for path, data in files.items():
            if url == base + path:
                return httpx.Response(200, content=data)
        return httpx.Response(404)
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def signed_in(extra=None):
    script = {
        ("gh", "api", "user", "--jq", ".login"): done(0, "alex\n"),
        ("gh", "api", "user", "--jq", ".name"): done(0, "Alex Morgan\n"),
        ("gh", "api", "repos/alex/rpipe-feed"): done(1, "", "gh: Not Found (HTTP 404)"),
        ("git", "diff", "--cached"): done(1),  # something is staged, so a push happens
    }
    script.update(extra or {})
    return FakeRunner(script)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "vault"
    init_vault(path, topics=["agents"])
    monkeypatch.setattr(share_publish, "wait_live", lambda *a, **k: True)
    # No test may touch the real app service: the unit directory is empty and systemctl is recorded.
    monkeypatch.setattr(schedule, "default_unit_dir", lambda: tmp_path / "units")
    monkeypatch.setattr(schedule, "system_runner", lambda c: SYSTEMCTL.append(c) or 0)
    SYSTEMCTL.clear()
    # No test may run the real interactive gh commands (they would wait for a person).
    monkeypatch.setattr(cli, "_gh_login_interactive", lambda: 0)
    monkeypatch.setattr(cli, "_gh_refresh_interactive", lambda: 0)
    return path


def use(monkeypatch, fake, http=None):
    monkeypatch.setattr(cli, "_share_runner", lambda: fake)
    if http is not None:
        monkeypatch.setattr(cli, "_share_http", http)


def sh(vault, *args, input=None):
    return runner.invoke(cli.app, ["share", *args, "--vault", str(vault)], input=input)


def enable(vault):
    profile_edit.set_share(vault, enabled=True, display_name="Alex M.", handle="alex")
    (vault / "share" / "rpipe-feed" / ".git").mkdir(parents=True)


def test_setup_happy_path(vault, monkeypatch):
    fake = signed_in()
    use(monkeypatch, fake)
    res = sh(vault, "setup", input="Alex M.\ny\n")
    assert res.exit_code == 0, res.output
    assert cli.PUBLIC_WARNING in res.output
    assert "https://alex.github.io/rpipe-feed/feed.json" in res.output
    cfg = load_config(vault / "config.yaml")
    assert (cfg.share_enabled, cfg.share_handle, cfg.share_display_name) == (True, "alex", "Alex M.")
    assert any(c[:4] == ["gh", "api", "-X", "PUT"] and c[4].endswith("/topics") for c in fake.calls)
    assert any(c[:4] == ["gh", "api", "-X", "POST"] and c[4].endswith("/pages") for c in fake.calls)
    assert ["git", "push"] in fake.calls and ["git", "push", "--dry-run"] in fake.calls
    assert ["gh", "auth", "setup-git"] in fake.calls


def test_setup_shows_public_warning_and_aborts_on_no(vault, monkeypatch):
    fake = signed_in()
    use(monkeypatch, fake)
    res = sh(vault, "setup", input="Alex M.\nn\n")
    assert res.exit_code == 0, res.output
    assert cli.PUBLIC_WARNING in res.output
    assert not load_config(vault / "config.yaml").share_enabled
    assert not any(c[:3] == ["gh", "repo", "create"] for c in fake.calls)


def test_setup_refuses_other_repo(vault, monkeypatch):
    fake = signed_in({
        ("gh", "api", "repos/alex/rpipe-feed"): done(0, json.dumps({"description": "mine"})),
        ("gh", "api", "-H"): done(1, "", "404"),
    })
    use(monkeypatch, fake)
    res = sh(vault, "setup", input="Alex M.\ny\n")
    assert res.exit_code == 1
    assert "github.com/alex/rpipe-feed already exists and isn't an rpipe feed" in res.output
    assert "Rename or delete it on GitHub, then run `rpipe share setup` again." in res.output
    assert not any(c[:3] == ["gh", "repo", "create"] for c in fake.calls)


def test_setup_missing_gh_says_how_to_install(vault, monkeypatch):
    use(monkeypatch, FakeRunner({("gh", "--version"): done(127, "", "gh: command not found")}))
    res = sh(vault, "setup")
    assert res.exit_code == 1
    assert "`gh` is missing" in res.output and "https://cli.github.com" in res.output


def test_setup_clones_when_repo_exists_but_clone_is_missing(vault, monkeypatch):
    feed = json.dumps({"_rpipe": {}})
    fake = signed_in({
        ("gh", "api", "repos/alex/rpipe-feed"): done(0, json.dumps({"description": "x"})),
        ("gh", "api", "-H"): done(0, feed),
    })
    use(monkeypatch, fake)
    res = sh(vault, "setup", input="Alex M.\ny\n")
    assert res.exit_code == 0, res.output
    assert any(c[:2] == ["git", "clone"] for c in fake.calls)
    assert not any(c[:3] == ["gh", "repo", "create"] for c in fake.calls)


def test_follow_then_following_lists_feed(vault, monkeypatch):
    use(monkeypatch, FakeRunner(), serving(feed_files()))
    res = sh(vault, "follow", "alex")
    assert res.exit_code == 0, res.output
    out = sh(vault, "following").output
    assert "Alex M." in out and "@alex" in out and "1 posts" in out


def test_follow_unknown_handle_says_so(vault, monkeypatch):
    use(monkeypatch, FakeRunner(), serving({}))
    res = sh(vault, "follow", "nobody")
    assert res.exit_code == 1
    assert "No rpipe feed" in res.output
    assert "You follow nobody" in sh(vault, "following").output


def test_unfollow_unknown_handle_message(vault):
    res = sh(vault, "unfollow", "ghost")
    assert res.exit_code == 1
    assert "You don't follow 'ghost'" in res.output


def test_unfollow_is_case_insensitive(vault, monkeypatch):
    use(monkeypatch, FakeRunner(), serving(feed_files()))
    sh(vault, "follow", "alex")
    res = sh(vault, "unfollow", "@ALEX")
    assert res.exit_code == 0, res.output
    assert "You follow nobody" in sh(vault, "following").output


def test_mix_remove_sets_flag(vault, monkeypatch):
    use(monkeypatch, FakeRunner(), serving(feed_files()))
    sh(vault, "follow", "alex")
    assert sh(vault, "mix", "remove", "Alex").exit_code == 0
    assert "not in Mix" in sh(vault, "following").output
    assert sh(vault, "mix", "add", "alex").exit_code == 0
    assert "not in Mix" not in sh(vault, "following").output


def test_stop_removes_topic_and_disables(vault, monkeypatch):
    enable(vault)
    fake = signed_in()
    use(monkeypatch, fake)
    res = sh(vault, "stop")
    assert res.exit_code == 0, res.output
    assert not load_config(vault / "config.yaml").share_enabled
    topic = [c for c in fake.calls if c[:4] == ["gh", "api", "-X", "PUT"] and c[4].endswith("/topics")]
    assert topic and fake.inputs[fake.calls.index(topic[0])] == json.dumps({"names": []})
    feed = json.loads((vault / "share" / "rpipe-feed" / "feed.json").read_bytes())
    assert feed["_rpipe"]["active"] is False


def test_stop_delete_asks_first(vault, monkeypatch):
    enable(vault)
    fake = signed_in()
    use(monkeypatch, fake)
    res = sh(vault, "stop", "--delete", input="n\n")
    assert res.exit_code == 0, res.output
    assert not any(c[:3] == ["gh", "repo", "delete"] for c in fake.calls)
    assert load_config(vault / "config.yaml").share_enabled
    res = sh(vault, "stop", "--delete", input="y\n")
    assert res.exit_code == 0, res.output
    assert any(c[:3] == ["gh", "repo", "delete"] for c in fake.calls)
    assert not (vault / "share" / "rpipe-feed").exists()
    assert not load_config(vault / "config.yaml").share_enabled


def test_status_shows_publish_and_follows(vault, monkeypatch):
    enable(vault)
    use(monkeypatch, signed_in({("git", "log"): done(0, "2026-11-26T03:00:05+00:00\n")}),
        serving(feed_files("Bo"), base="https://bo.github.io/rpipe-feed/"))
    assert sh(vault, "follow", "bo").exit_code == 0
    out = sh(vault, "status").output
    assert "Publishing: on as 'Alex M.'" in out
    assert "https://alex.github.io/rpipe-feed/feed.json" in out
    assert "2026-11-26 03:00:05" in out
    assert "Following 1" in out and "Bo (@bo)" in out


def test_set_name_republishes(vault, monkeypatch):
    enable(vault)
    fake = signed_in()
    use(monkeypatch, fake)
    res = sh(vault, "set-name", "Alex Morgan")
    assert res.exit_code == 0, res.output
    assert load_config(vault / "config.yaml").share_display_name == "Alex Morgan"
    assert ["git", "push"] in fake.calls
    assert sh(vault, "set-name", "").exit_code == 1


def test_browse_filters_and_marks_followed(vault, monkeypatch):
    search = json.dumps({"items": [{"name": "rpipe-feed", "owner": {"login": "alex"}}]}).encode()
    files = {**feed_files()}
    files_all = {"feed.json": files["feed.json"]}

    def handler(request):
        url = str(request.url)
        if url.startswith("https://api.github.com/search"):
            return httpx.Response(200, content=search)
        if url == BASE + "feed.json":
            return httpx.Response(200, content=files_all["feed.json"])
        return httpx.Response(404)

    use(monkeypatch, FakeRunner(), lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    out = sh(vault, "browse").output
    assert "Alex M.  @alex\n" in out and "1 topic in common" in out
    assert "papers a day" in out and "(following)" not in out
    assert "No feeds found." in sh(vault, "browse", "zzz").output
    assert "@alex" in sh(vault, "browse", "AGENTS").output


def install_app_unit(tmp_path):
    from research_pipeline.webapp import service

    unit = tmp_path / "units"
    unit.mkdir()
    (unit / service.SERVICE_NAME).write_text("ExecStart=/x/rpipe app run --host 127.0.0.1 --port 8002\n")


def _linux(monkeypatch):
    monkeypatch.setattr(cli, "_platform", lambda: "Linux")
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.mark.parametrize("args", [("set-name", "Alex Morgan"), ("stop",)])
def test_sharing_change_restarts_installed_app(vault, monkeypatch, tmp_path, args):
    _linux(monkeypatch)
    install_app_unit(tmp_path)
    enable(vault)
    use(monkeypatch, signed_in())
    res = sh(vault, *args)
    assert res.exit_code == 0, res.output
    assert "Restarted the app so it shows the change." in res.output
    assert ["systemctl", "--user", "restart", "research-pipeline-app.service"] in SYSTEMCTL


def test_setup_restarts_installed_app(vault, monkeypatch, tmp_path):
    _linux(monkeypatch)
    install_app_unit(tmp_path)
    use(monkeypatch, signed_in())
    res = sh(vault, "setup", input="Alex M.\ny\n")
    assert res.exit_code == 0, res.output
    assert "Restarted the app so it shows the change." in res.output


def test_sharing_change_without_installed_app_does_nothing(vault, monkeypatch):
    _linux(monkeypatch)
    enable(vault)
    use(monkeypatch, signed_in())
    res = sh(vault, "set-name", "Alex Morgan")
    assert res.exit_code == 0 and "Restarted" not in res.output
    assert SYSTEMCTL == []


def test_commits_use_a_noreply_identity_set_before_the_first_commit(vault, monkeypatch):
    fake = signed_in()
    use(monkeypatch, fake)
    assert sh(vault, "setup", input="Alex M.\ny\n").exit_code == 0
    kinds = [c[1] for c in fake.calls if c[0] == "git"]
    assert fake.calls.index(["git", "config", "user.email", "alex@users.noreply.github.com"]) < \
        [i for i, c in enumerate(fake.calls) if c[:2] == ["git", "commit"]][0]
    assert ["git", "config", "user.name", "Alex M."] in fake.calls and "commit" in kinds


def test_setup_runs_gh_sign_in_when_signed_out(vault, monkeypatch):
    logins = [done(1, "", "not logged in"), done(0, "alex\n")]
    fake = signed_in({("gh", "api", "user", "--jq", ".login"): logins})
    use(monkeypatch, fake)
    ran = []
    monkeypatch.setattr(cli, "_gh_login_interactive", lambda: ran.append(1) or 0)
    res = sh(vault, "setup", input="Alex M.\ny\n")
    assert res.exit_code == 0, res.output
    assert ran == [1] and "sign in to GitHub first" in res.output
    ran.clear()
    monkeypatch.setattr(cli, "_gh_login_interactive", lambda: 1)
    fake2 = signed_in({("gh", "api", "user", "--jq", ".login"): done(1, "", "not logged in")})
    use(monkeypatch, fake2)
    res = sh(vault, "setup", input="Alex M.\ny\n")
    assert res.exit_code == 1 and "sign-in didn't finish" in res.output


def test_stop_delete_refreshes_scope_interactively_outside_the_lock(vault, monkeypatch):
    enable(vault)
    fake = signed_in({("gh", "auth", "status"): done(0, "Token scopes: 'repo'")})
    use(monkeypatch, fake)
    seen = {}

    def refresh():
        seen["lock_held"] = cli._held is not None and cli._held.held
        return 0

    monkeypatch.setattr(cli, "_gh_refresh_interactive", refresh)
    res = sh(vault, "stop", "--delete", input="y\n")
    assert res.exit_code == 0, res.output
    assert seen == {"lock_held": False} and "browser sign-in may open" in res.output
    assert not any(c[:3] == ["gh", "auth", "refresh"] for c in fake.calls)  # never captured
    assert any(c[:3] == ["gh", "repo", "delete"] for c in fake.calls)


def test_stop_delete_skips_refresh_when_token_already_allows_it(vault, monkeypatch):
    enable(vault)
    use(monkeypatch, signed_in({("gh", "auth", "status"): done(0, "Token scopes: 'delete_repo'")}))
    monkeypatch.setattr(cli, "_gh_refresh_interactive", lambda: pytest.fail("not needed"))
    assert sh(vault, "stop", "--delete", input="y\n").exit_code == 0


def test_stop_delete_failure_paths(vault, monkeypatch):
    enable(vault)
    fake = signed_in({("gh", "auth", "status"): done(0, "Token scopes: 'repo'")})
    use(monkeypatch, fake)
    monkeypatch.setattr(cli, "_gh_refresh_interactive", lambda: 1)
    res = sh(vault, "stop", "--delete", input="y\n")
    assert res.exit_code == 1 and "permission to delete" in res.output
    assert not any(c[:3] == ["gh", "repo", "delete"] for c in fake.calls)
    assert load_config(vault / "config.yaml").share_enabled
    fake = signed_in({("gh", "auth", "status"): done(0, "delete_repo"),
                      ("gh", "repo", "delete"): done(1, "", "HTTP 403")})
    use(monkeypatch, fake)
    res = sh(vault, "stop", "--delete", input="y\n")
    assert res.exit_code == 1 and "HTTP 403" in res.output
    assert load_config(vault / "config.yaml").share_enabled


def test_browse_marks_your_own_feed(vault, monkeypatch):
    profile_edit.set_share(vault, enabled=True, display_name="Alex M.", handle="alex")
    search = json.dumps({"items": [{"name": "rpipe-feed", "owner": {"login": "alex"}}]}).encode()
    feed = feed_files()["feed.json"]

    def handler(request):
        url = str(request.url)
        if url.startswith("https://api.github.com/search"):
            return httpx.Response(200, content=search)
        return httpx.Response(200, content=feed) if url == BASE + "feed.json" else httpx.Response(404)

    use(monkeypatch, FakeRunner(), lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    assert "(you)" in sh(vault, "browse").output


def test_terminal_output_strips_escape_sequences(vault):
    from research_pipeline import db
    conn = db.open_db(vault / "vault.sqlite")
    db.upsert_follow(conn, "github:bo/rpipe-feed", "https://bo/feed.json", "bo", "Bo\x1b[31m red", [])
    conn.commit()
    conn.close()
    out = sh(vault, "following").output
    assert "\x1b" not in out and "Bo[31m red" in out
    assert "\x1b" not in cli.health_line(date(2026, 11, 26), {}, ["fetch Bo\x1b[2J: boom"])


def test_browse_entry_shows_three_topics_shared_first():
    from research_pipeline import library_api

    e = library_api.DirectoryEntryView(
        feed_id="github:alex/rpipe-feed", handle="alex", display_name="Alex M.",
        topics=["robotics", "java", "LLM", "Agents", "compilers", "vision"], posts_per_day=8.0,
        preview=[], shared_topics=1, following=False, is_you=False)
    out = cli._browse_entry(e, {"agents"})
    assert out.splitlines()[2] == "  Agents, robotics, java, +3 more"
    assert "1 topic in common" in out and len(out.splitlines()) == 3


def prof(vault, *args):
    return runner.invoke(cli.app, ["profile", *args, "--vault", str(vault)])


def test_topic_change_republishes_when_sharing(vault, monkeypatch):
    enable(vault)
    fake = signed_in()
    use(monkeypatch, fake)
    res = prof(vault, "add-topic", "local models")
    assert res.exit_code == 0, res.output
    assert ["git", "push"] in fake.calls and "Published your feed with the new topics." in res.output
    fake.calls.clear()
    res = prof(vault, "remove-topic", "local models")
    assert res.exit_code == 0 and ["git", "push"] in fake.calls


def test_topic_change_does_not_publish_when_private(vault, monkeypatch):
    fake = signed_in()
    use(monkeypatch, fake)
    res = prof(vault, "add-topic", "local models")
    assert res.exit_code == 0 and fake.calls == []


def test_topic_change_keeps_the_edit_when_the_push_fails(vault, monkeypatch):
    enable(vault)
    fake = signed_in({("git", "push"): done(1, "", "network down")})
    use(monkeypatch, fake)
    res = prof(vault, "add-topic", "local models")
    assert res.exit_code == 0, res.output
    assert "Your topics are saved, but publishing failed" in res.output
    assert "local models" in prof(vault, "show").output
