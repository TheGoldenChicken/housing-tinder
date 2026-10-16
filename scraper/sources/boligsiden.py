"""Boligsiden.dk — Denmark's largest sale aggregator (all realtor chains).
Covers: sale (villa/condo/townhouse/...), andel (cooperative), rent (realtor rentals).

API: https://api.boligsiden.dk/search/cases   (sale + cooperative)
     https://api.boligsiden.dk/search/rentals (rentals)
Plain requests get Cloudflare-challenged on larger page sizes; curl_cffi works.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

from ..models import Listing
from . import register
from .base import BaseSource

API = "https://api.boligsiden.dk"

# Boligsiden addressType → our property type. Anything missing is skipped.
TYPE_MAP = {
    "villa": "house",
    "terraced house": "townhouse",
    "double house": "townhouse",
    "condo": "apartment",
    "villa apartment": "villa_apartment",
    "cooperative": "apartment",  # refined below from building data
    "farm": "farm",
    "hobby farm": "farm",
    "room": "room",
}
SALE_TYPES = ["villa", "terraced house", "double house", "condo", "villa apartment", "cooperative", "farm", "hobby farm"]


def _img(case: dict) -> list[str]:
    urls = []
    for img in (case.get("images") or [])[:6]:
        srcs = img.get("imageSources") or []
        # sizes are ordered small→large; take a medium one when present
        pick = srcs[min(3, len(srcs) - 1)] if srcs else None
        if pick:
            urls.append(pick["url"])
    if not urls and case.get("defaultImage"):
        srcs = case["defaultImage"].get("imageSources") or []
        if srcs:
            urls.append(srcs[min(3, len(srcs) - 1)]["url"])
    return urls


def _address(addr: dict) -> str:
    s = f"{addr.get('roadName', '')} {addr.get('houseNumber', '')}".strip()
    floor, door = addr.get("floor"), addr.get("door")
    if floor:
        s += f", {floor}."
        if door:
            s += f" {door}"
    return s


def _floor(addr: dict) -> int | None:
    f = addr.get("floor")
    if f in (None, ""):
        return None
    if str(f).lower() in ("st", "stuen", "0"):
        return 0
    try:
        return int(str(f).rstrip("."))
    except ValueError:
        return None


@register
class Boligsiden(BaseSource):
    name = "boligsiden"
    categories = ("sale", "andel", "rent")
    per_page = 200

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        yield from self._paginate("/search/cases", {"addressTypes": ",".join(SALE_TYPES)}, max_pages, rental=False)
        yield from self._paginate("/search/rentals", {}, max_pages, rental=True)

    def _paginate(self, path: str, params: dict, max_pages: int | None, rental: bool) -> Iterator[Listing]:
        page, seen_ids = 1, set()
        while True:
            q = {"per_page": self.per_page, "page": page, "sortBy": "address", **params}
            data = self.http.get_json(API + path, params=q)
            cases = data.get("cases") or []
            total = data.get("totalHits", 0)
            self.log.info("%s page %d: %d cases (total %d)", path, page, len(cases), total)
            for case in cases:
                if case["caseID"] in seen_ids:
                    continue
                seen_ids.add(case["caseID"])
                listing = self._convert(case, rental)
                if listing:
                    yield listing
            if not cases or page * self.per_page >= total or (max_pages and page >= max_pages):
                break
            page += 1

    def _convert(self, case: dict, rental: bool) -> Listing | None:
        addr = case.get("address") or {}
        atype = case.get("addressType") or addr.get("addressType")
        ptype = TYPE_MAP.get(atype)
        if ptype is None:
            return None
        if atype == "cooperative":
            bname = ((addr.get("buildings") or [{}])[0].get("buildingName") or "").lower()
            if "række" in bname or "kæde" in bname or "dobbelt" in bname:
                ptype = "townhouse"
            elif "enfamilie" in bname or "parcel" in bname or "stuehus" in bname:
                ptype = "house"
        if rental:
            category = "rent"
        elif case.get("distinction") == "cooperative" or atype == "cooperative":
            category = "andel"
        else:
            category = "sale"

        muni = addr.get("municipality") or {}
        coords = case.get("coordinates") or addr.get("coordinates") or {}
        days = (case.get("daysListed") or {}).get("days")
        listed = (date.today() - timedelta(days=int(days))).isoformat() if days is not None else None
        rent = case.get("rental") or {}
        building = (addr.get("buildings") or [{}])[0]
        bath = case.get("numberOfBathrooms")
        if bath is None:
            bath = building.get("numberOfBathrooms")

        if rental:
            price = case.get("monthlyExpense")
            deposit = case.get("priceCash")
            monthly_cost = None
        else:
            price = case.get("priceCash")
            deposit = None
            monthly_cost = case.get("monthlyExpense")

        return Listing(
            source=self.name,
            source_id=case["caseID"],
            category=category,
            url=f"https://www.boligsiden.dk/adresse/{addr.get('slugAddress') or case.get('slugAddress') or case['slug']}",
            title=case.get("descriptionTitle"),
            address=_address(addr),
            zip_code=addr.get("zipCode") or (addr.get("zip") or {}).get("zipCode"),
            city=addr.get("cityName") or (addr.get("city") or {}).get("name"),
            municipality=muni.get("name"),
            municipality_code=muni.get("municipalityCode"),
            lat=coords.get("lat"),
            lon=coords.get("lon"),
            price=price,
            monthly_cost=monthly_cost,
            deposit=deposit,
            prepaid_rent=rent.get("prepaidRent"),
            property_type=ptype,
            size_m2=case.get("housingArea") or addr.get("livingArea"),
            lot_m2=case.get("lotArea"),
            rooms=case.get("numberOfRooms"),
            floor=_floor(addr),
            bathrooms=bath,
            toilets=case.get("numberOfToilets") or building.get("numberOfToilets"),
            has_bathroom=(bath > 0) if isinstance(bath, int) else None,
            year_built=case.get("yearBuilt") or building.get("yearBuilt"),
            energy_label=(case.get("energyLabel") or "").upper() or None,
            balcony=case.get("hasBalcony"),
            elevator=case.get("hasElevator"),
            terrace=case.get("hasTerrace"),
            pets_allowed=rent.get("petsAllowed"),
            available_from=rent.get("from"),
            rental_period=("until " + rent["to"]) if rent.get("to") else None,
            listed_date=listed,
            images=_img(case),
            description=case.get("descriptionBody"),
            realtor=(case.get("realtor") or {}).get("name") or (rent.get("landlord") or {}).get("name"),
            extra={k: v for k, v in {
                "realtor_url": case.get("caseUrl"),
                "down_payment": (case.get("realTe") or case.get("realEstate") or {}).get("downPayment"),
                "price_change_pct": case.get("priceChangePercentage"),
                "label": case.get("label"),
            }.items() if v not in (None, "", 0)},
        )
