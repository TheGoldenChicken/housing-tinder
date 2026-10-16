"""Boligzonen.dk — rental site (~9k listings). Rails app behind Cloudflare.

List: GET https://boligzonen.dk/lejebolig.json?page=N&sort=updated_at&direction=desc
      → {"result_html": <18 cards>, "count": "8849", "pagination": <nav html>}
Cards have street (no number), city, rent, rooms, m², type, images but no zip/coords;
the detail page (ld+json RealEstateListing) adds zip, lat/lon, deposit, dates, description.
Detail pages are fetched once per listing (per-source cache, per-run budget).
Property types on the site: Lejlighed, Værelse, Hus / Rækkehus, Byttebolig (no summer houses).
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..models import Listing
from . import register
from .base import BaseSource

BASE = "https://boligzonen.dk"
TYPE_MAP = {"lejlighed": "apartment", "værelse": "room", "hus / rækkehus": "house", "hus": "house", "rækkehus": "townhouse", "byttebolig": None}


def _imgproxy_original(url: str) -> str:
    """Last path segment of boligzonen's imgproxy URL is urlsafe-base64 of the original S3 URL."""
    try:
        seg = url.rsplit("/", 1)[1]
        return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)).decode()
    except Exception:  # noqa: BLE001
        return url


def _dkk(v: str | None) -> int | None:
    v = re.sub(r"[^\d]", "", v or "")
    return int(v) if v else None


def parse_cards(result_html: str) -> list[dict]:
    soup = BeautifulSoup(result_html, "lxml")
    out = []
    for a in soup.select("a.property-component-card"):
        if not a.get("data-id") or not a.get("href"):
            continue
        title = a.select_one(".title > div")
        loc_title = a.select_one(".location-title")
        street = a.select_one(".location > span")
        city = a.select_one(".zip-code-name")
        price = a.select_one(".price-amount")
        m = re.match(r"(\d+)\s+værelse\w*\s+på\s+(\d+)", loc_title.get_text(" ", strip=True)) if loc_title else None
        imgs: list[str] = []
        for src in a.select("picture source[srcset]"):
            first = src["srcset"].split()[0]
            orig = _imgproxy_original(first)
            if orig not in imgs:
                imgs.append(orig)
        out.append({
            "id": str(a["data-id"]),
            "url": BASE + a["href"],
            "type": title.get_text(strip=True) if title else None,
            "rooms": int(m.group(1)) if m else None,
            "size_m2": int(m.group(2)) if m else None,
            "street": street.get_text(strip=True).rstrip(",") if street else None,
            "city": city.get_text(strip=True) if city else None,
            "rent": _dkk(price.get_text(strip=True)) if price else None,
            "images": imgs,
        })
    return out


def last_page(pagination_html: str) -> int | None:
    nav = BeautifulSoup(pagination_html or "", "lxml")
    last_a = nav.select_one("nav.pagination span.last a")
    if last_a:
        m = re.search(r"[?&]page=(\d+)", last_a["href"])
        if m:
            return int(m.group(1))
    nums = [int(x.get_text(strip=True)) for x in nav.select("nav.pagination .page a") if x.get_text(strip=True).isdigit()]
    return max(nums) if nums else None


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    ld: dict = {}
    for sc in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(sc.string or "")
        except ValueError:
            continue
        for o in data if isinstance(data, list) else [data]:
            if isinstance(o, dict) and o.get("@type") == "RealEstateListing":
                ld = o
    me, off = ld.get("mainEntity") or {}, ld.get("offers") or {}
    addr, geo = me.get("address") or {}, me.get("geo") or {}
    bars: dict[str, str] = {}
    for bar in soup.select(".section-bar"):
        lab, val = bar.select_one(".section-bar-label"), bar.select_one(".section-bar-value")
        if lab and val:
            text = val.get_text(" ", strip=True)
            if text and text not in ("—", "-", "–"):
                bars.setdefault(lab.get_text(strip=True), text)
    desc = soup.select_one("p.description.show-more-content") or soup.select_one("p.description")
    yes = lambda v: True if (v or "").strip().lower().startswith("ja") else (False if (v or "").strip().lower().startswith(("nej", "ikke")) else None)  # noqa: E731
    return {
        "title": ld.get("name"),
        "description": desc.get_text(" ", strip=True) if desc else (ld.get("description") or None),
        "listed_date": (ld.get("datePosted") or "")[:10] or None,
        "street": addr.get("streetAddress"),
        "zip": _dkk(addr.get("postalCode")),
        "city": addr.get("addressLocality"),
        "lat": geo.get("latitude"),
        "lon": geo.get("longitude"),
        "rooms": me.get("numberOfRooms"),
        "size_m2": (me.get("floorSize") or {}).get("value"),
        "rent": off.get("price"),
        "available_from": (off.get("availabilityStarts") or "")[:10] or None,
        "type": bars.get("Boligtype"),
        "deposit": _dkk(bars.get("Depositum")),
        "aconto": _dkk(bars.get("Aconto")),
        "prepaid_rent": _dkk(bars.get("Forudbetalt leje")),
        "rental_period": bars.get("Lejeperiode"),
        "pets_allowed": me.get("petsAllowed") if isinstance(me.get("petsAllowed"), bool) else yes(bars.get("Husdyr")),
        "furnished": yes(bars.get("Møbleret")),
        "elevator": yes(bars.get("Elevator")),
        "balcony": yes(bars.get("Altan")),
        "energy_label": (bars.get("Energimærke") or "").strip() or None,
        "images": [_imgproxy_original(u) for u in (ld.get("image") or [])],
    }


@register
class Boligzonen(BaseSource):
    name = "boligzonen"
    categories = ("rent",)

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        budget = int(self.config.get("detail_budget", 300))
        page, seen = 1, set()
        while True:
            data = self.http.get_json(f"{BASE}/lejebolig.json", params={"page": page, "sort": "updated_at", "direction": "desc"})
            cards = parse_cards(data.get("result_html") or "")
            last = last_page(data.get("pagination") or "")
            self.log.info("page %d/%s: %d cards (count %s)", page, last, len(cards), data.get("count"))
            for card in cards:
                if card["id"] in seen:
                    continue
                seen.add(card["id"])
                detail = self.cache.get(card["id"])
                if detail is None and budget > 0:
                    budget -= 1
                    try:
                        detail = parse_detail(self.http.get(card["url"]).text)
                        self.cache[card["id"]] = detail
                    except Exception as exc:  # noqa: BLE001
                        self.log.warning("detail %s failed: %s", card["id"], exc)
                        detail = None
                listing = self._convert(card, detail or {})
                if listing:
                    yield listing
            if not cards or (last and page >= last) or (max_pages and page >= max_pages):
                break
            page += 1
        for lid in [k for k in self.cache if k not in seen]:
            del self.cache[lid]

    def _convert(self, card: dict, d: dict) -> Listing | None:
        type_name = (d.get("type") or card.get("type") or "").lower().strip()
        ptype = TYPE_MAP.get(type_name, "other")
        if ptype is None:
            return None
        text = f"{d.get('title') or ''} {d.get('description') or ''}".lower()
        if any(w in text for w in ("sommerhus", "kolonihave", "fritidshus")) and ptype != "apartment":
            return None
        if ptype == "house" and "rækkehus" in text:
            ptype = "townhouse"
        return Listing(
            source=self.name,
            source_id=card["id"],
            category="rent",
            url=card["url"],
            title=d.get("title"),
            address=d.get("street") or card.get("street") or "",  # street name only on this site
            zip_code=d.get("zip"),
            city=d.get("city") or card.get("city"),
            lat=d.get("lat"),
            lon=d.get("lon"),
            price=d.get("rent") or card.get("rent"),
            monthly_cost=d.get("aconto"),
            deposit=d.get("deposit"),
            prepaid_rent=d.get("prepaid_rent"),
            property_type=ptype,
            size_m2=d.get("size_m2") or card.get("size_m2"),
            rooms=d.get("rooms") or card.get("rooms"),
            energy_label=d.get("energy_label"),
            balcony=d.get("balcony"),
            elevator=d.get("elevator"),
            pets_allowed=d.get("pets_allowed"),
            furnished=d.get("furnished"),
            available_from=d.get("available_from"),
            rental_period=d.get("rental_period"),
            listed_date=d.get("listed_date"),
            images=d.get("images") or card.get("images") or [],
            description=d.get("description"),
            extra={"details_fetched": True} if d else {},
        )
