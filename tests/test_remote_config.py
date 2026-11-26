"""Tests for remote gitcord.yaml loading, merge, and cache fallback."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml

from ghdcbot.config.loader import load_config
from ghdcbot.config.remote import (
    apply_bootstrap_overlays,
    deep_merge,
    fetch_remote_config_yaml,
)
from ghdcbot.core.errors import ConfigError


def _contents_response(text: str, *, sha: str = "abc123") -> httpx.Response:
    payload = {
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "type": "file",
        "name": "gitcord.yaml",
        "path": "gitcord.yaml",
    }
    return httpx.Response(200, json=payload)


def test_deep_merge_nested_overlay_wins() -> None:
    base = {"runtime": {"data_dir": "/data", "mode": "dry-run"}, "github": {"token": "local"}}
    overlay = {"runtime": {"mode": "active"}, "github": {"org": "AOSSIE-Org"}}
    merged = deep_merge(base, overlay)
    assert merged["runtime"]["data_dir"] == "/data"
    assert merged["runtime"]["mode"] == "active"
    assert merged["github"]["token"] == "local"
    assert merged["github"]["org"] == "AOSSIE-Org"


def test_apply_bootstrap_overlays_keeps_secrets_and_data_dir() -> None:
    remote = {
        "runtime": {
            "mode": "active",
            "log_level": "INFO",
            "data_dir": "/should-not-win",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {"org": "AOSSIE-Org", "permissions": {"read": True, "write": True}},
        "discord": {"guild_id": "1", "permissions": {"read": True, "write": True}},
    }
    bootstrap = {
        "runtime": {"data_dir": "/data"},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    merged = apply_bootstrap_overlays(remote, bootstrap)
    assert merged["runtime"]["data_dir"] == "/data"
    assert merged["runtime"]["mode"] == "active"
    assert merged["github"]["token"] == "${GITHUB_TOKEN}"
    assert merged["github"]["org"] == "AOSSIE-Org"
    assert merged["discord"]["token"] == "${DISCORD_TOKEN}"
    assert merged["remote_config"]["enabled"] is True


def test_fetch_remote_config_yaml_decodes_contents(monkeypatch: pytest.MonkeyPatch) -> None:
    remote_yaml = "runtime:\n  mode: active\ngithub:\n  org: AOSSIE-Org\n"
    client = MagicMock()
    client.get.return_value = _contents_response(remote_yaml, sha="deadbeef")
    client.close = MagicMock()
    monkeypatch.setattr(
        "ghdcbot.config.remote.build_github_httpx_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "ghdcbot.config.remote.resolve_github_token",
        lambda **kwargs: "tok",
    )

    result = fetch_remote_config_yaml(
        owner="AOSSIE-Org",
        repo=".github",
        path="gitcord.yaml",
        ref="main",
        pat="tok",
    )
    assert result.sha == "deadbeef"
    assert result.data["github"]["org"] == "AOSSIE-Org"
    assert "AOSSIE-Org/.github/gitcord.yaml" in result.source


def test_fetch_remote_config_yaml_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.get.return_value = httpx.Response(404, text="Not Found")
    client.close = MagicMock()
    monkeypatch.setattr(
        "ghdcbot.config.remote.build_github_httpx_client",
        lambda *args, **kwargs: client,
    )
    monkeypatch.setattr(
        "ghdcbot.config.remote.resolve_github_token",
        lambda **kwargs: "tok",
    )
    with pytest.raises(ConfigError, match="not found"):
        fetch_remote_config_yaml(owner="AOSSIE-Org", repo=".github", path="missing.yaml")


def _minimal_remote_dict() -> dict[str, Any]:
    return {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": "/ignored",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
            "activity_period_days": 7,
        },
        "github": {
            "org": "AOSSIE-Org",
            "api_base": "https://api.github.com",
            "permissions": {"read": True, "write": False},
        },
        "discord": {
            "guild_id": "123456789012345678",
            "permissions": {"read": True, "write": False},
        },
        "assignments": {},
    }


def test_load_config_remote_merge_and_env_expand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
            "ref": "main",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")
    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="cafef00d",
            source="AOSSIE-Org/.github/gitcord.yaml@main",
        ),
    )

    config = load_config(str(bootstrap_path))
    assert config.github.org == "AOSSIE-Org"
    assert config.github.token == "ghp_test"
    assert config.discord.token == "discord_test"
    assert config.runtime.data_dir == str(data_dir)
    assert config.remote_config is not None
    assert config.remote_config.enabled is True
    assert (data_dir / "remote_config_cache.yaml").is_file()


def test_load_config_uses_cache_when_fetch_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cache = _minimal_remote_dict()
    (data_dir / "remote_config_cache.yaml").write_text(
        yaml.safe_dump(cache), encoding="utf-8"
    )
    (data_dir / "remote_config_cache.meta.yaml").write_text(
        yaml.safe_dump({"source": "cached", "sha": "old"}),
        encoding="utf-8",
    )

    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    def _boom(**kwargs: object) -> None:
        raise ConfigError("network down")

    monkeypatch.setattr("ghdcbot.config.loader.fetch_remote_config_yaml", _boom)

    config = load_config(str(bootstrap_path))
    assert config.github.org == "AOSSIE-Org"
    assert config.github.token == "ghp_test"


def test_load_config_fetch_failure_without_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    def _boom(**kwargs: object) -> None:
        raise ConfigError("network down")

    monkeypatch.setattr("ghdcbot.config.loader.fetch_remote_config_yaml", _boom)

    with pytest.raises(ConfigError, match="No local remote_config_cache"):
        load_config(str(bootstrap_path))


def test_load_config_local_only_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")
    cfg = {
        "runtime": {
            "mode": "dry-run",
            "log_level": "INFO",
            "data_dir": str(tmp_path / "data"),
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {
            "org": "example-org",
            "token": "${GITHUB_TOKEN}",
            "api_base": "https://api.github.com",
            "permissions": {"read": True, "write": False},
        },
        "discord": {
            "guild_id": "123456789012345678",
            "token": "${DISCORD_TOKEN}",
            "permissions": {"read": True, "write": False},
        },
    }
    path = tmp_path / "local.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    config = load_config(str(path))
    assert config.github.org == "example-org"
    assert config.remote_config is None


def test_bootstrap_data_dir_env_expanded_before_cache_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrap runtime.data_dir with env var is expanded before creating cache dir."""
    monkeypatch.chdir(tmp_path)
    target_data_dir = tmp_path / "actual_expanded_data"
    env_var_name = "TEST_BOOTSTRAP_DATA_DIR_1"
    monkeypatch.setenv(env_var_name, str(target_data_dir))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    bootstrap = {
        "runtime": {"data_dir": f"${{{env_var_name}}}"},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="test_sha",
            source="AOSSIE-Org/.github/gitcord.yaml",
        ),
    )

    literal_dir = Path(f"${{{env_var_name}}}")
    assert not literal_dir.exists()

    config = load_config(str(bootstrap_path))

    # Assert literal ${VAR} directory was NOT created
    assert not literal_dir.exists()
    # Assert cache was written into the expanded directory
    assert (target_data_dir / "remote_config_cache.yaml").is_file()
    assert (target_data_dir / "remote_config_cache.meta.yaml").is_file()
    assert config.runtime.data_dir == str(target_data_dir)


def test_bootstrap_remote_config_owner_and_repo_expanded_before_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrap remote_config.owner and repo env vars are expanded before fetching."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CONFIG_OWNER_VAR", "ExpandedOrg")
    monkeypatch.setenv("CONFIG_REPO_VAR", "expanded-repo")
    monkeypatch.setenv("CONFIG_PATH_VAR", "custom-gitcord.yaml")
    monkeypatch.setenv("CONFIG_REF_VAR", "develop")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "${CONFIG_OWNER_VAR}",
            "repo": "${CONFIG_REPO_VAR}",
            "path": "${CONFIG_PATH_VAR}",
            "ref": "${CONFIG_REF_VAR}",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    captured_args: dict[str, Any] = {}

    def _mock_fetch(**kwargs: Any) -> MagicMock:
        captured_args.update(kwargs)
        return MagicMock(
            data=_minimal_remote_dict(),
            sha="owner_repo_sha",
            source="ExpandedOrg/expanded-repo/custom-gitcord.yaml@develop",
        )

    monkeypatch.setattr("ghdcbot.config.loader.fetch_remote_config_yaml", _mock_fetch)

    config = load_config(str(bootstrap_path))

    # Verify fetch_remote_config_yaml received the expanded values
    assert captured_args["owner"] == "ExpandedOrg"
    assert captured_args["repo"] == "expanded-repo"
    assert captured_args["path"] == "custom-gitcord.yaml"
    assert captured_args["ref"] == "develop"
    assert config.remote_config is not None
    assert config.remote_config.owner == "ExpandedOrg"
    assert config.remote_config.repo == "expanded-repo"
    assert config.remote_config.path == "custom-gitcord.yaml"
    assert config.remote_config.ref == "develop"


def test_remote_config_env_vars_expanded_after_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env vars in remote config are still expanded after merging into bootstrap."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("REMOTE_CHANNEL_ID", "987654321098765432")
    monkeypatch.setenv("REMOTE_ORG", "ExpandedRemoteOrg")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    remote_dict = _minimal_remote_dict()
    remote_dict["github"]["org"] = "${REMOTE_ORG}"
    remote_dict["discord"]["activity_channel_id"] = "${REMOTE_CHANNEL_ID}"

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=remote_dict,
            sha="remote_env_sha",
            source="AOSSIE-Org/.github/gitcord.yaml",
        ),
    )

    config = load_config(str(bootstrap_path))
    assert config.github.org == "ExpandedRemoteOrg"
    assert config.discord.activity_channel_id == "987654321098765432"


def test_load_config_without_env_vars_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configurations without environment variables continue working as expected."""
    data_dir = tmp_path / "plain_data"
    data_dir.mkdir()

    bootstrap = {
        "runtime": {"data_dir": str(data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "PlainOwner",
            "repo": "plain-repo",
            "path": "gitcord.yaml",
        },
        "github": {"token": "literal_github_token"},
        "discord": {"token": "literal_discord_token"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="plain_sha",
            source="PlainOwner/plain-repo/gitcord.yaml",
        ),
    )

    config = load_config(str(bootstrap_path))
    assert config.github.token == "literal_github_token"
    assert config.discord.token == "literal_discord_token"
    assert config.runtime.data_dir == str(data_dir)
    assert config.remote_config is not None
    assert config.remote_config.owner == "PlainOwner"
    assert (data_dir / "remote_config_cache.yaml").is_file()


def test_load_config_cache_fallback_with_expanded_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache fallback succeeds when data_dir is provided via environment variable."""
    monkeypatch.chdir(tmp_path)
    target_data_dir = tmp_path / "cached_data"
    target_data_dir.mkdir()
    monkeypatch.setenv("ENV_CACHE_DATA_DIR", str(target_data_dir))
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    cache = _minimal_remote_dict()
    (target_data_dir / "remote_config_cache.yaml").write_text(
        yaml.safe_dump(cache), encoding="utf-8"
    )
    (target_data_dir / "remote_config_cache.meta.yaml").write_text(
        yaml.safe_dump({"source": "cached_remote", "sha": "cached_sha"}),
        encoding="utf-8",
    )

    bootstrap = {
        "runtime": {"data_dir": "${ENV_CACHE_DATA_DIR}"},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    def _network_failure(**kwargs: Any) -> None:
        raise ConfigError("network timeout")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml", _network_failure
    )

    literal_dir = Path("${ENV_CACHE_DATA_DIR}")
    assert not literal_dir.exists()

    config = load_config(str(bootstrap_path))
    assert not literal_dir.exists()
    assert config.github.org == "AOSSIE-Org"
    assert config.runtime.data_dir == str(target_data_dir)
    assert config.github.token == "ghp_test"


def test_no_double_expansion_nested_placeholder_consistent_cache_and_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure DATA_DIR containing a nested placeholder does not expand twice and diverge from cache."""
    monkeypatch.chdir(tmp_path)
    data_dir_literal = str(tmp_path / "${TENANT}")
    monkeypatch.setenv("RESOLVED_DATA_DIR", data_dir_literal)
    monkeypatch.setenv("TENANT", "team")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    bootstrap = {
        "runtime": {"data_dir": "${RESOLVED_DATA_DIR}"},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="test_sha",
            source="AOSSIE-Org/.github/gitcord.yaml",
        ),
    )

    config = load_config(str(bootstrap_path))

    # runtime.data_dir must match the cache path exactly without expanding ${TENANT} to 'team'
    assert config.runtime.data_dir == data_dir_literal
    assert (Path(data_dir_literal) / "remote_config_cache.yaml").is_file()
    assert not (tmp_path / "team").exists()


def test_no_double_expansion_unset_inner_placeholder_does_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inner placeholder in an env var value does not fail even if that inner variable is unset."""
    monkeypatch.chdir(tmp_path)
    data_dir_literal = str(tmp_path / "${UNSET_INNER_VAR}")
    monkeypatch.setenv("RESOLVED_DATA_DIR", data_dir_literal)
    monkeypatch.delenv("UNSET_INNER_VAR", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    bootstrap = {
        "runtime": {"data_dir": "${RESOLVED_DATA_DIR}"},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="test_sha",
            source="AOSSIE-Org/.github/gitcord.yaml",
        ),
    )

    config = load_config(str(bootstrap_path))
    assert config.runtime.data_dir == data_dir_literal


def test_no_double_expansion_bootstrap_token_preserves_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure a token value containing a placeholder is not modified by a second expansion."""
    token_value = "ghp_secret_${NAME}_123"
    monkeypatch.setenv("GITHUB_TOKEN", token_value)
    monkeypatch.setenv("NAME", "substituted_value")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    bootstrap = {
        "runtime": {"data_dir": str(tmp_path / "data")},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml",
        lambda **kwargs: MagicMock(
            data=_minimal_remote_dict(),
            sha="test_sha",
            source="AOSSIE-Org/.github/gitcord.yaml",
        ),
    )

    config = load_config(str(bootstrap_path))
    assert config.github.token == token_value


def test_cache_fallback_expands_cached_remote_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache fallback path expands environment variables contained in cached remote config."""
    monkeypatch.chdir(tmp_path)
    target_data_dir = tmp_path / "cached_data"
    target_data_dir.mkdir()
    monkeypatch.setenv("CACHED_CHANNEL_ID", "999888777666")
    monkeypatch.setenv("CACHED_ORG", "CachedOrg")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("DISCORD_TOKEN", "discord_test")

    cache = _minimal_remote_dict()
    cache["github"]["org"] = "${CACHED_ORG}"
    cache["discord"]["activity_channel_id"] = "${CACHED_CHANNEL_ID}"
    (target_data_dir / "remote_config_cache.yaml").write_text(
        yaml.safe_dump(cache), encoding="utf-8"
    )
    (target_data_dir / "remote_config_cache.meta.yaml").write_text(
        yaml.safe_dump({"source": "cached_remote", "sha": "cached_sha"}),
        encoding="utf-8",
    )

    bootstrap = {
        "runtime": {"data_dir": str(target_data_dir)},
        "remote_config": {
            "enabled": True,
            "owner": "AOSSIE-Org",
            "repo": ".github",
            "path": "gitcord.yaml",
        },
        "github": {"token": "${GITHUB_TOKEN}"},
        "discord": {"token": "${DISCORD_TOKEN}"},
    }
    bootstrap_path = tmp_path / "bootstrap.yaml"
    bootstrap_path.write_text(yaml.safe_dump(bootstrap), encoding="utf-8")

    def _network_failure(**kwargs: Any) -> None:
        raise ConfigError("network timeout")

    monkeypatch.setattr(
        "ghdcbot.config.loader.fetch_remote_config_yaml", _network_failure
    )

    config = load_config(str(bootstrap_path))
    assert config.github.org == "CachedOrg"
    assert config.discord.activity_channel_id == "999888777666"
