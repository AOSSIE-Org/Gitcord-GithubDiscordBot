"""Regression checks for OpenSSF criteria ID parsing used by checklist-score.yml."""

from __future__ import annotations

import re


def parse_upstream_ids(content: str) -> set[str]:
    """Mirror the sync-criteria regexes in .github/workflows/checklist-score.yml."""
    ids = set(re.findall(r"\[([a-z][a-z0-9_]+)\]\(#\1\)", content))
    ids |= set(re.findall(r'<a name="([a-z][a-z0-9_]+)"></a>', content))
    return ids


def test_parse_markdown_self_links() -> None:
    sample = (
        "* [description_good](#description_good)\n"
        "* [interact](#interact)\n"
        "* [not_a_match](#other)\n"
    )
    assert parse_upstream_ids(sample) == {"description_good", "interact"}


def test_parse_html_anchor_name_format() -> None:
    sample = (
        '<a name="description_good"></a>\n'
        '<a name="interact"></a>\n'
        '<a name="BadId"></a>\n'
    )
    assert parse_upstream_ids(sample) == {"description_good", "interact"}


def test_parse_combined_formats() -> None:
    sample = (
        "* [contribution](#contribution)\n"
        '<a name="report_process"></a>\n'
    )
    assert parse_upstream_ids(sample) == {"contribution", "report_process"}
