"""home.dk — realtor chain rentals (~500 listings).

POST https://api.home.dk/search/homedk/case-catalog?page=N&pageSize=100&sortBy=default
Needs X-Shop-Host / X-Forwarded-Host: home.dk headers.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from ..models import Listing
from . import register
from .base import BaseSource

API = "https://api.home.dk/search/homedk/case-catalog"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json", "Origin": "https://home.dk",
           "Referer": "https://home.dk/", "X-Shop-Host": "home.dk", "X-Forwarded-Host": "home.dk"}
BODY = {"filters": {"singleFilters": {}, "multipleFilters": {"listingType": {"selectedValues": [{"value": "1"}]}},
                    "bitFilters": {"isBusinessCase": {"selectedValue": {"value": False}}},
                    "rangeFilters": {}, "isOnlyOpenHouse": {"selectedValue": {"value": False}}},
        "facets": ["propertyCategorySlug.values"]}
TYPE_MAP = {"condo": "apartment", "terracedhouse": "townhouse", "villa": "house", "house": "house", "villaapartment": "villa_apartment",
            "farm": "farm", "room": "room", "holidayhouse": None, "holidayplot": None, "plot": None}


@register
class Home(BaseSource):
    name = "home"
    categories = ("rent",)

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        page, seen = 1, set()
        while True:
            resp = self.http.post(f"{API}?page={page}&loadPrevious=false&pageSize=100&sortBy=default", json=BODY, headers=HEADERS)
            data = resp.json()
            results = data.get("results") or []
            self.log.info("page %d: %d results (total %s)", page, len(results), data.get("total"))
            for r in results:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                listing = self._convert(r)
                if listing:
                    yield listing
            if not results or not data.get("hasNextPage") or (max_pages and page >= max_pages):
                break
            page += 1

    def _convert(self, r: dict) -> Listing | None:
        ptype = TYPE_MAP.get((r.get("type") or "").lower(), "other")
        if ptype is None or "underRent" in (r.get("tags") or []):
            return None
        addr = r.get("address") or {}
        full = addr.get("full") or ""
        zip_code = int(addr["postalCode"]) if str(addr.get("postalCode") or "").isdigit() else None
        street = re.sub(rf",\s*{zip_code}\s+.*$", "", full) if zip_code else full
        mf = re.search(r",\s*(\d+)\.\s*(?:th|tv|mf|\d+)?\.?$", street)
        floor = int(mf.group(1)) if mf else (0 if re.search(r",\s*st\.?", street) else None)
        return Listing(
            source=self.name,
            source_id=str(r["id"]),
            category="rent",
            url="https://home.dk/" + (r.get("url") or "").lstrip("/"),
            title=r.get("headline") or None,
            address=street,
            zip_code=zip_code,
            city=addr.get("city"),
            lat=addr.get("latitude"),
            lon=addr.get("longitude"),
            price=((r.get("offer") or {}).get("rentPerMonth") or {}).get("amount") or None,
            property_type=ptype,
            size_m2=(r.get("stats") or {}).get("floorArea") or None,
            lot_m2=(r.get("stats") or {}).get("plotArea") or None,
            floor=floor,
            images=[m["url"] for m in (r.get("presentationMedia") or []) if m.get("url")][:6],
            realtor="home",
            extra={"coming_soon": True} if r.get("isComingSoon") else {},
        )
