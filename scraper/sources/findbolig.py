"""Findbolig.nu — non-profit / housing-association rentals (individually advertised units).

POST https://findbolig.nu/api/search  (page 0-based, pageSize, orderBy Created DESC)
The server sends an incomplete TLS chain, so certificate verification is disabled for this host.
Waiting-list "Property" entries are skipped; only advertised residences are listed.
"""
from __future__ import annotations

import html as html_mod
import json
import re
from collections.abc import Iterator

from ..models import Listing
from . import register
from .base import BaseSource

BASE = "https://findbolig.nu"
HEADERS = {"Accept": "application/json, text/plain, */*", "Referer": f"{BASE}/da-dk/find", "Origin": BASE}
TYPE_MAP = {"lejlighed": "apartment", "rækkehus": "townhouse", "villa": "house", "værelse": "room", "kollegiebolig": "room",
            "p plads": None, "p-plads": None}
# fallback GUIDs observed 2026-10 (search configuration is fetched live when possible)
FALLBACK_TYPES = {"0b119adc-83b4-4d51-981c-4b3c26d58d50": "Lejlighed", "3e84aa07-3527-48f0-a39d-edf1af269434": "Rækkehus",
                  "98f1dc7f-6767-4134-8d60-52f608604c3d": "Villa", "49db7502-2e94-42da-addc-b5f56a9bcf07": "Værelse",
                  "4c9afa6d-ccbf-4184-88ba-0a4846116fe2": "Kollegiebolig"}


@register
class Findbolig(BaseSource):
    name = "findbolig"
    categories = ("rent",)

    def _config(self) -> tuple[dict, dict]:
        types, facilities = dict(FALLBACK_TYPES), {}
        try:
            html = self.http.get(f"{BASE}/da-dk/find", verify=False).text
            m = re.search(r'<script[^>]*id="search-configuration"[^>]*>(.*?)</script>', html, re.S)
            cfg = json.loads(html_mod.unescape(m.group(1)).strip()) if m else {}
            types.update({t["id"]: t["name"] for t in cfg.get("propertyTypes") or []})
            facilities = {f["id"]: f["name"] for f in cfg.get("facilities") or []}
        except Exception as exc:  # noqa: BLE001
            self.log.warning("could not load search configuration: %s", exc)
        return types, facilities

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        types, facilities = self._config()
        page, seen = 0, set()
        while True:
            body = {"pageSize": 50, "page": page, "orderBy": "Created", "orderDirection": "DESC", "mixedResults": False,
                    "filters": {"Type": "Residence", "TypeIndividualResidence": "true"}, "facets": []}
            data = self.http.post(f"{BASE}/api/search", json=body, headers=HEADERS, verify=False).json()
            results = [r for r in data.get("results") or [] if r.get("$type") == "Residence"]
            total = int(data.get("totalResults") or 0)
            self.log.info("page %d: %d residences (total %d)", page, len(results), total)
            for r in results:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                listing = self._convert(r, types, facilities)
                if listing:
                    yield listing
            page += 1
            if not results or page * 50 >= total or (max_pages and page >= max_pages):
                break

    def _convert(self, r: dict, types: dict, facilities: dict) -> Listing | None:
        type_name = types.get(r.get("propertyType"), "")
        ptype = TYPE_MAP.get(type_name.lower(), "other")
        if ptype is None or r.get("residenceAdvertStatus") not in (None, "Published"):
            return None
        facs = {facilities.get(f, "") for f in (r.get("facilities") or []) + (r.get("propertyFacilities") or [])}
        facs_l = {f.lower() for f in facs if f}
        pets = True if any("husdyr tilladt" in f for f in facs_l) else (False if any("husdyr ikke" in f for f in facs_l) else None)
        street = " ".join(str(x) for x in (r.get("street"), r.get("number"), r.get("letter")) if x)
        if r.get("floor") is not None:
            street += f", {'st' if r['floor'] == 0 else str(r['floor']) + '.'}"
            if r.get("door"):
                street += f" {r['door']}"
        rent = r.get("rent")
        months = r.get("monthsOfDeposit")
        return Listing(
            source=self.name,
            source_id=str(r.get("shortId") or r["id"]),
            category="rent",
            url=f"{BASE}/da-dk/residence/{r.get('shortId')}",
            address=street,
            zip_code=r.get("postalCode"),
            city=r.get("city"),
            municipality=r.get("commune"),
            lat=r.get("latitude"),
            lon=r.get("longitude"),
            price=int(rent) if rent else None,
            monthly_cost=int(r["aconto"]) if r.get("aconto") else None,
            deposit=int(rent * months) if rent and months else None,
            prepaid_rent=int(rent * r["monthsOfPrepaid"]) if rent and r.get("monthsOfPrepaid") else None,
            property_type=ptype,
            size_m2=int(r["area"]) if r.get("area") else None,
            rooms=r.get("rooms"),
            floor=r.get("floor"),
            has_bathroom=True if any("bad" in f for f in facs_l) else None,
            year_built=r.get("propertyYear") or None,
            balcony=True if "altan" in facs_l else None,
            elevator=True if "elevator" in facs_l else None,
            pets_allowed=pets,
            available_from=(r.get("availableFrom") or "")[:10] or None,
            rental_period=r.get("rentalPeriod"),
            listed_date=(r.get("created") or "")[:10] or None,
            images=[BASE + u for u in ((r.get("media") or {}).get("images") or [])][:6],
            realtor=r.get("propertyCompanyName") or r.get("propertyOrganization"),
            extra={k: v for k, v in {"rent_model": r.get("rentModel"), "members_only": r.get("membersOnly") or None,
                                      "property": r.get("propertyName")}.items() if v},
        )
