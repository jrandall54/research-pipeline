import subprocess

import pytest

from research_pipeline import netguard, serve


def _fake(stdout="", returncode=0, raises=None):
    def run(argv):
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")
    return run


BAD = ["", " ", "0", "0.0.0.0", "0.0.0", "::", "::0", "[::]", "[::0]", "0:0:0:0:0:0:0:0",
       "::ffff:0.0.0.0", "192.168.1.5", "8.8.8.8", "example.com", "localhost.",
       "::ffff:127.0.0.1", "::ffff:100.64.0.5", "127.1", "fe80::1%eth0", "::1%lo"]
GOOD = ["localhost", "127.0.0.1", "::1", "100.64.0.5", "100.127.255.254", "fd7a:115c:a1e0::1"]


def test_localhost_case_insensitive():
    assert netguard.resolve_host("LOCALHOST", _fake(raises=AssertionError)) == "localhost"


@pytest.mark.parametrize("good", GOOD)
def test_allowlist_accepts(good):
    assert netguard.resolve_host(good, _fake(raises=AssertionError)) == good


def test_tailscale_result_is_checked():
    with pytest.raises(netguard.HostError):
        netguard.resolve_host(None, _fake("0.0.0.0\n"))


@pytest.mark.parametrize("bad", BAD)
def test_refuses_all_interfaces(bad):
    with pytest.raises(netguard.HostError):
        netguard.resolve_host(bad, _fake("100.64.0.5\n"))


def test_defaults_to_tailscale_ip():
    assert netguard.resolve_host(None, _fake("100.64.0.5\n")) == "100.64.0.5"
    assert netguard.resolve_host("127.0.0.1", _fake(raises=AssertionError)) == "127.0.0.1"


@pytest.mark.parametrize("runner_", [
    _fake(raises=FileNotFoundError()), _fake("", returncode=1), _fake("\n"),
])
def test_tailscale_failure_message(runner_):
    with pytest.raises(netguard.HostError, match="Tailscale not running; use --host 127.0.0.1"):
        netguard.tailscale_ip(runner_)


def test_serve_reexports_netguard():
    assert serve.check_host is netguard.check_host and serve.ServeError is netguard.HostError
