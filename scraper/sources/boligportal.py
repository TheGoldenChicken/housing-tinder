"""BoligPortal.dk — Denmark's largest rental site (~12k listings).

Endpoint: POST https://www.boligportal.dk/api/search/list?offset=N  (18 per page, fixed)
Needs: a prior GET of /lejeboliger/ (sets Cloudflare __cf_bm + Django csrftoken cookies),
then X-CSRFToken + X-Request-Source headers. Body must contain {"categories": {"values": null}}.
Results are ordered by advertised_date desc. Descriptions are empty in search results.
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator

from ..models import Listing
from . import register
from .base import BaseSource

BASE = "https://www.boligportal.dk"
TYPE_MAP = {
    "rental_apartment": "apartment",
    "rental_condominium": "apartment",
    "rental_housing_cooperative": "apartment",
    "rental_youth_housing": "apartment",
    "rental_room": "room",
    "rental_club_room": "room",
    "rental_house": "house",
    "rental_villa": "house",
    "rental_parcel_house": "house",
    "rental_detached_single_family_house": "house",
    "rental_small_house": "house",
    "rental_multi_family_house": "house",
    "rental_townhouse": "townhouse",
    "rental_city_house": "townhouse",
    "rental_double_house": "townhouse",
    "rental_half_double_house": "townhouse",
    # rental_summer_house intentionally absent → skipped
}
SESSION_TTL = 20 * 60  # __cf_bm cookie lives ~30 min


@register
class BoligPortal(BaseSource):
    name = "boligportal"
    categories = ("rent",)

    def _warm(self) -> None:
        self.http.get(f"{BASE}/lejeboliger/", headers={"Accept-Language": "da-DK,da;q=0.9"})
        self._warmed = time.monotonic()

    def _headers(self) -> dict:
        return {
            "X-CSRFToken": self.http.session.cookies.get("csrftoken") or "",
            "X-Request-Source": "WEB_FRONTEND",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Language": "da",
            "Referer": f"{BASE}/lejeboliger/",
        }

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        self._warm()
        offset, page, seen = 0, 0, set()
        body = json.dumps({"categories": {"values": None}})
        while True:
            if time.monotonic() - self._warmed > SESSION_TTL:
                self._warm()
            resp = self.http.post(f"{BASE}/api/search/list?offset={offset}", headers=self._headers(), data=body)
            data = resp.json()
            results = data.get("results") or []
            page += 1
            self.log.info("offset %d: %d results (total %s)", offset, len(results), data.get("result_count"))
            for r in results:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                listing = self._convert(r)
                if listing:
                    yield listing
            if not results or not data.get("next_page_url") or (max_pages and page >= max_pages):
                break
            offset += int(data.get("limit") or len(results))

    def _convert(self, r: dict) -> Listing | None:
        ptype = TYPE_MAP.get(r.get("category"))
        if ptype is None or r.get("deleted"):
            return None
        title = r.get("title") or ""
        if any(w in title.lower() for w in ("sommerhus", "kolonihave", "fritidshus")):
            return None
        feats = r.get("features") or {}
        loc = r.get("location") or {}
        period = r.get("rental_period")
        rental_period = None
        if period is not None:
            rental_period = "unlimited" if int(period) == 0 else f"{int(period)} months"
        street = f"{r.get('street_name') or ''} {r.get('street_number') or ''}".strip()
        try:
            zip_code = int(r.get("postal_code") or 0) or None
        except ValueError:
            zip_code = None

        def _int(v):
            return int(v) if isinstance(v, (int, float)) else None

        return Listing(
            source=self.name,
            source_id=str(r["id"]),
            category="rent",
            url=BASE + r["url"],
            title=title or None,
            address=street,
            zip_code=zip_code,
            city=r.get("city"),
            lat=loc.get("lat"),
            lon=loc.get("lng"),
            price=_int(r.get("monthly_rent")),
            monthly_cost=_int(r.get("monthly_rent_extra_costs")),
            deposit=_int(r.get("deposit")),
            prepaid_rent=_int(r.get("prepaid_rent")),
            property_type=ptype,
            size_m2=_int(r.get("size_m2")),
            rooms=r.get("rooms"),
            floor=r.get("floor"),
            energy_label=r.get("energy_rating") or None,
            balcony=feats.get("balcony"),
            elevator=feats.get("elevator"),
            pets_allowed=feats.get("pet_friendly"),
            furnished=feats.get("furnished"),
            available_from=r.get("available_from"),
            rental_period=rental_period,
            listed_date=(r.get("advertised_date") or r.get("created") or "")[:10] or None,
            images=[i["url"] for i in (r.get("images") or []) if i.get("url") and not i.get("is_floor_plan")],
            extra={k: v for k, v in {
                "newbuild": r.get("is_newbuild") or None,
                "social_housing": r.get("social_housing") or None,
                "parking": feats.get("parking") or None,
                "other_details": r.get("other_details") or None,
            }.items() if v},
        )
