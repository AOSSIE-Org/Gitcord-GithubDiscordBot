"""Shared configuration access helpers."""

from __future__ import annotations

from typing import Any


def cfg_get(target: Any, key: str, default: Any = None) -> Any:
    """Retrieve an attribute or dict key from target, returning default if absent or None."""
    if target is None:
        return default
    if isinstance(target, dict):
        val = target.get(key)
    else:
        val = getattr(target, key, None)
    return default if val is None else val
