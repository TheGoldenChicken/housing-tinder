"""Boliga.dk — sale aggregator (realtors + self-sale). Covers sale + andel.

API: https://api.boliga.dk/api/v2/search/results?pageSize=500&page=N&propertyType=1,2,3,5,6,9
propertyType: 1 villa, 2 rækkehus, 3 ejerlejlighed, 4 fritidshus, 5 andelsbolig,
              6 landejendom, 7 helårsgrund, 8 fritidsgrund, 9 villalejlighed, 10 andet
"""
from __future__ import annotations

from collections.abc import Iterator

from ..models import Listing
from . import register
from .base import BaseSource

API = "https://api.boliga.dk/api/v2/search/results"
TYPE_MAP = {1: "house", 2: "townhouse", 3: "apartment", 5: "apartment", 6: "farm", 9: "villa_apartment"}


@register
class Boliga(BaseSource):
    name = "boliga"
    categories = ("sale", "andel")
    page_size = 500

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        page, seen = 1, set()
        while True:
            data = self.http.get_json(
                API,
                params={"pageSize": self.page_size, "page": page, "propertyType": ",".join(map(str, TYPE_MAP))},
            )
            results = data.get("results") or []
            meta = data.get("meta") or {}
            self.log.info("page %d/%s: %d results", page, meta.get("totalPages"), len(results))
            for r in results:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                listing = self._convert(r)
                if listing:
                    yield listing
            if not results or page >= int(meta.get("totalPages") or 0) or (max_pages and page >= max_pages):
                break
            page += 1

    def _convert(self, r: dict) -> Listing | None:
        ptype = TYPE_MAP.get(r.get("propertyType"))
        if ptype is None or not r.get("isActive", True):
            return None
        secondary = (r.get("secondaryPropertyType") or 0)
        if r["propertyType"] == 5 and secondary in TYPE_MAP:
            ptype = TYPE_MAP[secondary]
        energy = (r.get("energyClass") or "").strip("- ").upper() or None
        return Listing(
            source=self.name,
            source_id=str(r["id"]),
            category="andel" if r["propertyType"] == 5 else "sale",
            url=f"https://www.boliga.dk/bolig/{r['id']}",
            address=r.get("street") or "",
            zip_code=r.get("zipCode"),
            city=r.get("city"),
            municipality_code=r.get("municipality"),
            lat=r.get("latitude"),
            lon=r.get("longitude"),
            price=r.get("price"),
            monthly_cost=r.get("exp") or None,
            property_type=ptype,
            size_m2=r.get("size") or None,
            lot_m2=r.get("lotSize") or None,
            rooms=r.get("rooms") or None,
            floor=r.get("floor"),
            year_built=r.get("buildYear") or None,
            energy_label=energy,
            listed_date=(r.get("createdDate") or "")[:10] or None,
            images=[i["url"] for i in (r.get("images") or []) if i.get("url")],
            realtor=r.get("agentDisplayName") or None,
            extra={k: v for k, v in {
                "down_payment": r.get("downPayment"),
                "basement_m2": r.get("basementSize"),
                "price_change_pct": r.get("priceChangePercentTotal"),
                "self_sale": r.get("selfsale") or None,
                "foreclosure": r.get("isForeclosure") or None,
            }.items() if v not in (None, 0, False)},
        )
