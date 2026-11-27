"""
Regression test for issue #175.

The installation docs tell users to copy-paste `ghdcbot --config .../<cmd>`
(and the Docker equivalent). If any referenced `<cmd>` is not a registered
subparser in `src/ghdcbot/cli.py`, the user hits
`argparse: error: argument command: invalid choice: '<cmd>'` before anything
useful happens. Issue #175 was exactly this — docs referenced a `validate`
subcommand that was never merged to main.

Keep the scan limited to install/setup docs that users follow verbatim.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SOURCE = REPO_ROOT / "src" / "ghdcbot" / "cli.py"

# Doc surfaces users copy-paste during install/setup.
DOC_PATHS: tuple[str, ...] = (
    "INSTALLATION.md",
    "README.md",
    "environment_variables.md",
    "docs/DOCKER.md",
    "AGENTS.md",
)

# `ghdcbot --config <path> <subcommand>` — captures the first token after
# the config path. argparse rejects flags before the subcommand, so the
# token right after the config path is always the subcommand name.
_LOCAL_CMD_RE = re.compile(r"ghdcbot\s+--config\s+\S+\s+([a-z][a-z\-]*)")
_DOCKER_CMD_RE = re.compile(
    r"docker\s+compose\s+run\s+--rm\s+bot\s+--config\s+\S+\s+([a-z][a-z\-]*)"
)

# Capture the first positional string literal of every `.add_parser("name", ...)`.
_ADD_PARSER_RE = re.compile(r"\.add_parser\(\s*\"([a-z][a-z\-]*)\"")


def _discover_subcommands() -> set[str]:
    source = CLI_SOURCE.read_text(encoding="utf-8")
    return set(_ADD_PARSER_RE.findall(source))


def test_cli_source_registers_expected_subcommands() -> None:
    """Sanity: the parser carries the subcommands the install flow relies on."""
    subs = _discover_subcommands()
    for required in ("run-once", "preflight-sync", "bot"):
        assert required in subs, f"{required!r} missing from cli.py add_parser calls"


def test_install_docs_reference_only_real_subcommands() -> None:
    """
    Every `ghdcbot --config ... <cmd>` and `docker compose run --rm bot --config ... <cmd>`
    in the install docs must map to a real subparser. Prevents regressions of #175.
    """
    known = _discover_subcommands()
    problems: list[str] = []
    for rel in DOC_PATHS:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for pattern, label in ((_LOCAL_CMD_RE, "local"), (_DOCKER_CMD_RE, "docker")):
            for match in pattern.finditer(text):
                cmd = match.group(1)
                if cmd not in known:
                    problems.append(f"{rel}: {label} reference to unknown subcommand {cmd!r}")
    assert not problems, (
        "Install docs reference non-existent ghdcbot subcommands:\n  "
        + "\n  ".join(problems)
    )
