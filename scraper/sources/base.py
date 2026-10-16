from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator

from ..http import Http
from ..models import Listing


class BaseSource(ABC):
    name: str = "base"
    categories: tuple[str, ...] = ()

    def __init__(self, http: Http, config: dict | None = None, cache: dict | None = None):
        self.http = http
        self.config = config or {}
        # Per-source cache persisted between runs (data/cache/<name>.json) for
        # sites where details need one extra request per listing.
        self.cache: dict = cache if cache is not None else {}
        self.log = logging.getLogger(f"source.{self.name}")

    @abstractmethod
    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        """Yield every current listing from this source."""
