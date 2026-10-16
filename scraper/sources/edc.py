"""EDC — realtor chain rentals (own cases only; the API also proxies BoligPortal ads, which we skip).

GET https://www.edc.dk/api/v1/cases/quick-search?c-gruppe=Rent&pageNr=N&pageSize=100
Default sort returns EDC-own cases first; results are capped at 1000 per query.
"""
from __future__ import annotations

from collections.abc import Iterator

from ..models import Listing
from . import register
from .base import BaseSource

API = "https://www.edc.dk/api/v1/cases/quick-search"
TYPE_MAP = {"lejlighed": "apartment", "rækkehus": "townhouse", "villa": "house", "værelse": "room", "landejendom": "farm",
            "villalejlighed": "villa_apartment", "fritidshus": None, "kolonihave": None}


def _val(d: dict | None):
    return (d or {}).get("value")


@register
class Edc(BaseSource):
    name = "edc"
    categories = ("rent",)

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        page, seen = 1, set()
        while True:
            data = self.http.get_json(API, params={"c-gruppe": "Rent", "pageNr": page, "pageSize": 100},
                                      headers={"Accept": "application/json", "Referer": "https://www.edc.dk/lejebolig/"})
            items = data.get("items") or []
            own = [i for i in items if i.get("isEdcCase", i.get("source") == "EDC")]
            self.log.info("page %d/%s: %d items, %d EDC-own", page, data.get("totalPages"), len(items), len(own))
            for it in own:
                if it["id"] in seen:
                    continue
                seen.add(it["id"])
                listing = self._convert(it)
                if listing:
                    yield listing
            if not items or len(own) < len(items) or page >= int(data.get("totalPages") or 0) or (max_pages and page >= max_pages):
                break
            page += 1

    def _convert(self, it: dict) -> Listing | None:
        ptype = TYPE_MAP.get((it.get("estateTypeName") or "").lower(), "other")
        if ptype is None or it.get("isRented") or not it.get("urlPath"):
            return None
        geo = it.get("geoCoordinates") or {}
        imgs = []
        for img in it.get("images") or []:
            src = img.get("src") or ""
            if "Size" in src and 500 <= int(img.get("width") or 0) <= 900:
                imgs.append(src)
        if not imgs:
            imgs = [img["src"] for img in (it.get("images") or [])[-1:] if img.get("src")]
        url = it["urlPath"] if it["urlPath"].startswith("http") else "https://www.edc.dk" + it["urlPath"]
        return Listing(
            source=self.name,
            source_id=str(it["id"]),
            category="rent",
            url=url,
            address=it.get("address") or "",
            zip_code=int(it["zipCode"]) if str(it.get("zipCode") or "").isdigit() else None,
            city=it.get("city"),
            lat=geo.get("latitude"),
            lon=geo.get("longitude"),
            price=int(_val(it.get("rent")) or 0) or None,
            property_type=ptype,
            size_m2=int(_val(it.get("livingArea")) or 0) or None,
            lot_m2=int(_val(it.get("areaLand")) or 0) or None,
            rooms=_val(it.get("rooms")),
            year_built=it.get("yearBuild") or None,
            energy_label=(it.get("energyRating") or it.get("energyClassification") or None),
            listed_date=(it.get("statusChangeDate") or "")[:10] or None,
            images=imgs[:6],
            realtor="EDC",
        )
