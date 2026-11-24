"""Machine scan: CPU, memory, disk, every GPU, and the Ollama server's own settings. Fully offline."""
from pathlib import Path

from typer.testing import CliRunner

from research_pipeline import cli
from research_pipeline.machine import Gpu, MachineInfo
from research_pipeline.scan import (
    CpuInfo,
    OllamaServer,
    all_gpus,
    detect_cpu,
    detect_ollama_server,
    ollama_models_dir,
    parse_systemd_env,
    setting_source,
)

ENV_LINE = ("Environment=PATH=/home/x/.local/bin:/usr/bin OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 "
            'OLLAMA_HOST=127.0.0.1:11436 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 '
            'LLAMA_ARG_CACHE_RAM=0 "OLLAMA_ORIGINS=*"')
DROP = "DropInPaths=/etc/systemd/system/ollama.service.d/kv-cache.conf"


def fake_run(answers):
    """Answer by the joined command line; anything unlisted is a missing tool."""
    def run(argv, timeout):
        return answers.get(" ".join(argv), (127, ""))
    return run


def systemd_run(env=ENV_LINE):
    return fake_run({
        "systemctl is-active ollama": (0, "active\n"),
        "systemctl show ollama --property=Environment": (0, env + "\n"),
        "systemctl show ollama --property=DropInPaths": (0, DROP + "\n"),
    })


def test_parse_systemd_env_handles_quotes():
    line = ('Environment=PATH=/bin OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 '
            '"OLLAMA_ORIGINS=*" "OLLAMA_X=a b"')
    assert parse_systemd_env(line) == {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
                                       "OLLAMA_ORIGINS": "*", "OLLAMA_X": "a b"}


def test_parse_systemd_env_survives_unbalanced_quote():
    assert parse_systemd_env('Environment="OLLAMA_X=a') == {}


def test_detect_cpu_flags():
    cpu = detect_cpu(fake_run({}), "Linux", read_text=lambda: "model name : x\nflags\t\t: fpu avx2 avx512f\n")
    assert cpu.vector == ["avx2", "avx512"] and cpu.threads >= 1


def test_detect_cpu_without_flags_or_file():
    assert detect_cpu(fake_run({}), "Linux", read_text=lambda: "").vector == []


def test_systemd_server_detected():
    server = detect_ollama_server("http://localhost:11436", systemd_run(), version=lambda url: "0.34.0")
    assert server.how == "systemd" and server.version == "0.34.0"
    assert server.env["OLLAMA_KV_CACHE_TYPE"] == "q8_0" and "PATH" not in server.env
    assert server.drop_ins == ["/etc/systemd/system/ollama.service.d/kv-cache.conf"]
    assert server.host_matches is True


def test_host_mismatch_flagged():
    server = detect_ollama_server("http://localhost:11434", systemd_run(), version=lambda url: "0.34.0")
    assert server.host_matches is False
    no_host = detect_ollama_server("http://localhost:11434", systemd_run("Environment=OLLAMA_NUM_PARALLEL=1"),
                                   version=lambda url: None)
    assert no_host.host_matches is True


def test_missing_tools_mean_unknown():
    server = detect_ollama_server("http://localhost:11434", fake_run({}), version=lambda url: None)
    assert server.how == "unknown" and server.env == {} and server.drop_ins == []
    assert server.host_matches is None


def test_manual_server_when_only_http_answers():
    server = detect_ollama_server("http://localhost:11434", fake_run({}), version=lambda url: "0.34.0")
    assert server.how in ("manual", "app") and server.host_matches is None


def test_models_dir_prefers_env():
    assert ollama_models_dir(OllamaServer("1", "systemd", {"OLLAMA_MODELS": "/data/m"}, [], None)) == Path("/data/m")
    assert ollama_models_dir(OllamaServer("1", "systemd", {}, [], None)) == Path("/usr/share/ollama/.ollama/models")
    assert ollama_models_dir(OllamaServer("1", "manual", {}, [], None)) == Path.home() / ".ollama" / "models"


def test_all_gpus_lists_every_card():
    run = fake_run({"nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits":
                    (0, "RTX 3060, 12288\nRTX 5070 Ti, 16303\n")})
    gpus = all_gpus(run, "Linux", "x86_64")
    assert [g.name for g in gpus] == ["RTX 3060", "RTX 5070 Ti"]


def test_setting_source_names_the_drop_in_file(tmp_path):
    conf = tmp_path / "kv-cache.conf"
    conf.write_text("[Service]\nEnvironment=OLLAMA_KV_CACHE_TYPE=q8_0\n")
    server = OllamaServer("1", "systemd", {}, [str(tmp_path / "missing.conf"), str(conf)], None)
    assert setting_source(server, "OLLAMA_KV_CACHE_TYPE") == "kv-cache.conf"
    assert setting_source(server, "OLLAMA_FLASH_ATTENTION") is None


def test_model_scan_command_prints_sections(tmp_path, monkeypatch):
    from research_pipeline.vault import init_vault
    vault = tmp_path / "vault"
    init_vault(vault, topics=["x"])
    conf = tmp_path / "kv-cache.conf"
    conf.write_text("Environment=OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0\n")
    server = OllamaServer("0.34.0", "systemd", {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
                                                 "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_HOST": "127.0.0.1:11436"},
                         [str(conf)], False)
    gpu = Gpu("RTX 5070 Ti", 15.9, "nvidia")
    machine = MachineInfo(os="Linux", ram_gb=32.0, gpu=gpu, ollama_installed=True, ollama_running=True,
                          ollama_models=["qwen3:14b"], claude_path=None, claude_logged_in=False, gpus=[gpu],
                          cpu=CpuInfo(16, ["avx2"]), ram_free_gb=20.0, disk_free_gb=300.0, ollama=server)
    monkeypatch.setattr(cli, "detect_machine", lambda config: machine)
    out = CliRunner().invoke(cli.app, ["model", "scan", "--vault", str(vault)]).output
    for text in ("GPU", "memory", "CPU", "Ollama", "flash attention: on (set in kv-cache.conf)",
                 "free disk", "q8_0", "parallel slots: 1", "port 11436 but rpipe talks to localhost:11434"):
        assert text in out


def test_machine_key_collects_version_settings_and_gpus(monkeypatch):
    from research_pipeline import scan
    monkeypatch.setattr(scan, "ollama_version", lambda url: "0.6.0")
    run = fake_run({
        "systemctl is-active ollama": (0, "active\n"),
        "systemctl show ollama --property=Environment": (0, ENV_LINE + "\n"),
        "systemctl show ollama --property=DropInPaths": (0, DROP + "\n"),
        "nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits": (0, "RTX 4090, 24564\n"),
    })
    key = scan.machine_key("http://x", run)
    assert key["ollama"] == "0.6.0"
    assert key["env"] == {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0",
                          "OLLAMA_NUM_PARALLEL": "1"}
    assert key["gpus"] == ["RTX 4090:24.0"]


def test_machine_key_never_raises(monkeypatch):
    from research_pipeline import scan

    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(scan, "ollama_version", boom)
    assert scan.machine_key("http://x", fake_run({})) == {"ollama": None, "env": {}, "gpus": []}


def test_all_gpus_reads_free_memory_when_given():
    run = fake_run({"nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits":
                    (0, "RTX 5070 Ti, 16303, 15364\nOld, Card, 8192, 100\n")})
    first, second = all_gpus(run, "Linux", "x86_64")
    assert (first.name, round(first.vram_gb, 2), round(first.free_gb, 2)) == ("RTX 5070 Ti", 15.92, 15.0)
    assert second.name == "Old, Card" and second.free_gb == 100 / 1024
    two_fields = fake_run({"nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits":
                           (0, "RTX 3060, 12288\n")})
    (only,) = all_gpus(two_fields, "Linux", "x86_64")
    assert only.vram_gb == 12.0 and only.free_gb is None


def test_scan_display_treats_true_as_flash_on(tmp_path, monkeypatch):
    from research_pipeline.vault import init_vault
    vault = tmp_path / "vault"
    init_vault(vault, topics=["x"])
    server = OllamaServer("0.34.0", "systemd", {"OLLAMA_FLASH_ATTENTION": "true"}, [], True)
    machine = MachineInfo(os="Linux", ram_gb=32.0, gpu=None, ollama_installed=True, ollama_running=True,
                          ollama_models=[], claude_path=None, claude_logged_in=False, ollama=server)
    monkeypatch.setattr(cli, "detect_machine", lambda config: machine)
    out = CliRunner().invoke(cli.app, ["model", "scan", "--vault", str(vault)]).output
    assert "flash attention: on" in out
