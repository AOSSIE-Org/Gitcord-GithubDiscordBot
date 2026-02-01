"""
Test that following the README setup flow does not cause problems.

Simulates: clone repo, install -e ., set GITHUB_TOKEN/DISCORD_TOKEN,
copy config/example.yaml, run run-once. Asserts config loads, run_once
completes without error, and audit reports are written.
"""

from pathlib import Path

import pytest

from ghdcbot.cli import build_orchestrator
from ghdcbot.config.loader import load_config
from ghdcbot.core.errors import ConfigError


# Path to repo root (parent of tests/)
REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "example.yaml"


def test_example_config_file_exists() -> None:
    """README says to use config/example.yaml; it must exist."""
    assert EXAMPLE_CONFIG_PATH.exists(), "config/example.yaml missing (README setup)"
    assert EXAMPLE_CONFIG_PATH.is_file()


def test_load_config_with_env_vars_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """README says to set GITHUB_TOKEN and DISCORD_TOKEN; config must load."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-github")
    monkeypatch.setenv("DISCORD_TOKEN", "test-token-discord")
    config = load_config(str(EXAMPLE_CONFIG_PATH))
    assert config.runtime.mode.value == "dry-run"
    assert config.github.org == "example-org"
    assert config.discord.guild_id == "000000000000000000"
    assert config.runtime.data_dir == "/tmp/ghdcbot-state"


def test_load_config_fails_without_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """If env vars are missing, config load should fail with a clear error."""
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("DISCORD_TOKEN", "")
    # Prevent .env from repopulating env so we truly test missing vars
    import ghdcbot.config.loader as loader_module
    monkeypatch.setattr(loader_module, "load_dotenv", lambda: None)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ConfigError) as excinfo:
        load_config(str(EXAMPLE_CONFIG_PATH))
    assert "GITHUB_TOKEN" in str(excinfo.value) or "DISCORD_TOKEN" in str(excinfo.value)


def test_readme_setup_run_once_completes_and_writes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Simulate README flow: env vars set, config like example.yaml with isolated
    data_dir, run run-once. Asserts no crash and audit reports exist.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "test-token-github")
    monkeypatch.setenv("DISCORD_TOKEN", "test-token-discord")

    # Use isolated data_dir (README uses /tmp/ghdcbot-state; we use tmp_path)
    config_content = EXAMPLE_CONFIG_PATH.read_text()
    config_content = config_content.replace(
        'data_dir: "/tmp/ghdcbot-state"',
        f'data_dir: "{tmp_path}"',
    )
    config_path = tmp_path / "ghdcbot-config.yaml"
    config_path.write_text(config_content)

    config = load_config(str(config_path))
    orchestrator = build_orchestrator(str(config_path))

    try:
        orchestrator.run_once()
    finally:
        orchestrator.close()

    reports_dir = tmp_path / "reports"
    audit_json = reports_dir / "audit.json"
    audit_md = reports_dir / "audit.md"

    assert reports_dir.exists(), "README: data_dir/reports should exist after run-once"
    assert audit_json.exists(), "README: audit.json should be written"
    assert audit_md.exists(), "README: audit.md should be written"
    assert audit_json.read_text()
    assert "dry-run" in audit_md.read_text() or "Summary" in audit_md.read_text()
