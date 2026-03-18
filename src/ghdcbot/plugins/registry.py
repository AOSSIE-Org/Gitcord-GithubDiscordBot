from __future__ import annotations

import importlib
from typing import Any, TypeVar

from ghdcbot.core.errors import AdapterError

T = TypeVar("T")


def load_adapter(dotted_path: str) -> type[T]:
    try:
        module_path, class_name = dotted_path.split(":", 1)
        module = importlib.import_module(module_path)
        adapter_cls = getattr(module, class_name)
    except (ValueError, ImportError, AttributeError) as exc:
        raise AdapterError(f"Unable to load adapter: {dotted_path}") from exc
    return adapter_cls


def build_adapter(dotted_path: str, **kwargs: Any) -> Any:
    adapter_cls = load_adapter(dotted_path)
    return adapter_cls(**kwargs)
