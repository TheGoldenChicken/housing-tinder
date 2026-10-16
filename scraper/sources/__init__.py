"""Source registry. Each module exposes a class deriving from BaseSource."""
from __future__ import annotations

from .base import BaseSource

REGISTRY: dict[str, type[BaseSource]] = {}


def register(cls: type[BaseSource]) -> type[BaseSource]:
    REGISTRY[cls.name] = cls
    return cls


def load_all() -> dict[str, type[BaseSource]]:
    # Import side effects populate REGISTRY.
    from . import boliga, boligsiden  # noqa: F401

    for mod in ("boligportal", "lejebolig", "boligzonen", "edc", "home", "findbolig"):
        try:
            __import__(f"{__name__}.{mod}")
        except ImportError:
            pass
    return REGISTRY
