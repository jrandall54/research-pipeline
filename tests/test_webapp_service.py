import pytest

from research_pipeline import schedule
from research_pipeline.webapp import service


def test_render_service_exact_lines(tmp_path):
    text = service.render_service(tmp_path / "my vault", "/usr/bin/rpipe", "100.64.0.5", 8002)
    lines = text.splitlines()
    assert "Description=Research pipeline app" in lines
    assert "After=network-online.target tailscaled.service" in lines
    assert "Wants=network-online.target" in lines
    assert f'Environment="RESEARCH_VAULT={tmp_path}/my vault"' in lines
    assert 'ExecStart="/usr/bin/rpipe" app run --host 100.64.0.5 --port 8002' in lines
    assert "Restart=on-failure" in lines and "RestartSec=10" in lines
    assert "WantedBy=default.target" in lines
    assert service.SERVICE_NAME == "research-pipeline-app.service"


def test_render_service_quotes_vault_and_rpipe(tmp_path):
    text = service.render_service(tmp_path / "a b%c", "/opt/r pipe/$x/rpipe", "127.0.0.1", 1)
    assert "a b%%c" in text
    assert 'ExecStart="/opt/r pipe/$$x/rpipe" app run' in text


def test_render_service_rejects_control_chars(tmp_path):
    with pytest.raises(schedule.ScheduleError):
        service.render_service(tmp_path / "a\nb", "/bin/rpipe", "127.0.0.1", 8002)


def test_install_probes_before_writing_then_enables(tmp_path):
    calls = []
    path = service.install(tmp_path / "v", "/bin/rpipe", "127.0.0.1", 8002, tmp_path / "u",
                           lambda c: calls.append(c) or 0)
    assert path == tmp_path / "u" / service.SERVICE_NAME and path.exists()
    assert calls == [["systemctl", "--user", "show-environment"],
                     ["systemctl", "--user", "daemon-reload"],
                     ["systemctl", "--user", "enable", "--now", service.SERVICE_NAME]]


def test_install_failed_probe_writes_nothing(tmp_path):
    with pytest.raises(schedule.ScheduleError, match="nothing written"):
        service.install(tmp_path / "v", "/bin/rpipe", "127.0.0.1", 8002, tmp_path / "u", lambda c: 1)
    assert not (tmp_path / "u").exists()


def test_install_enable_failure_says_files_left(tmp_path):
    with pytest.raises(schedule.ScheduleError, match="left in place"):
        service.install(tmp_path / "v", "/bin/rpipe", "127.0.0.1", 8002, tmp_path / "u",
                        lambda c: 1 if "enable" in c else 0)


def test_remove_nothing_installed_runs_nothing(tmp_path):
    calls = []
    assert service.remove(tmp_path, lambda c: calls.append(c) or 0) == ([], [])
    assert calls == []


def test_remove_deletes_and_warns(tmp_path):
    (tmp_path / service.SERVICE_NAME).write_text("x")
    removed, warnings = service.remove(tmp_path, lambda c: 1)
    assert removed == [tmp_path / service.SERVICE_NAME] and len(warnings) == 2
    assert not (tmp_path / service.SERVICE_NAME).exists()


def test_address_parsed_from_unit(tmp_path):
    assert service.address(tmp_path) is None
    (tmp_path / service.SERVICE_NAME).write_text(service.render_service(tmp_path, "/bin/rpipe", "100.64.0.5", 8123))
    assert service.address(tmp_path) == "http://100.64.0.5:8123/"


def test_address_ipv6_is_bracketed(tmp_path):
    (tmp_path / service.SERVICE_NAME).write_text(service.render_service(tmp_path, "/bin/rpipe", "::1", 8002))
    assert service.address(tmp_path) == "http://[::1]:8002/"


def test_is_active_uses_exit_code():
    seen = []
    assert service.is_active(lambda c: seen.append(c) or 0) is True
    assert seen == [["systemctl", "--user", "is-active", "--quiet", service.SERVICE_NAME]]
    assert service.is_active(lambda c: 3) is False


@pytest.mark.parametrize("bind,expected", [
    ("100.64.0.5", {"100.64.0.5"}),
    ("127.0.0.1", {"127.0.0.1", "localhost", "::1"}),
    ("localhost", {"localhost", "127.0.0.1", "::1"}),
    ("::1", {"::1", "localhost", "127.0.0.1"}),
    ("127.0.0.2", {"127.0.0.2", "localhost", "127.0.0.1", "::1"}),
    ("LOCALHOST", {"localhost", "127.0.0.1", "::1"}),
])
def test_allowed_hosts(bind, expected):
    assert service.allowed_hosts(bind) == expected
