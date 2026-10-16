"""Lejebolig.dk — rental site (~10k listings), server-rendered HTML.

Listing: GET /lejeboliger (40 cards) then GET /Hunter/MoreResultsV2?isCitySearch=False
repeatedly on the same session (server-side offset). Ends with '<div class="last"></div>'.
Default search already excludes summer houses (type 7) and swaps (type 6), newest first.
Cards lack zip/address, so new ids get one detail-page fetch + one /mapHandler/{id} call;
results are kept in the per-source cache so each listing is fetched once.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..models import Listing
from . import register
from .base import BaseSource

BASE = "https://www.lejebolig.dk"
XHR = {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/lejeboliger"}
DA_MONTHS = {m: i for i, m in enumerate(
    ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober", "november", "december"], 1)}
TYPE_MAP = {"lejlighed": "apartment", "hus/villa": "house", "hus": "house", "villa": "house", "rækkehus": "townhouse",
            "værelse": "room", "delebolig": "room", "sommerhus": None, "byttebolig": None}
BAD_WORDS = ("sommerhus", "kolonihave", "fritidshus")


def _int(s: str | None) -> int | None:
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for item in soup.select(".lease-item"):
        a = item.select_one("a.lease-info[href]")
        if a is None:  # .lease-item.dummy: hidden for anonymous users
            continue
        m = re.match(r"/lejebolig/(\d+)/", a["href"])
        if not m:
            continue
        sub = item.select_one(".lease-sub-header")
        sub = sub.get_text(" ", strip=True) if sub else ""
        msub = re.match(r"(.+?)\s+(?:på|i)\s+(.+)$", sub)
        specs = [s.get_text(strip=True) for s in item.select(".lease-spec span")]
        imgs_tag = item.select_one(".lease-image script[type='application/json']")
        try:
            imgs = json.loads(imgs_tag.string or "[]") if imgs_tag else []
        except ValueError:
            imgs = []
        img_div = item.select_one(".lease-image")
        if not imgs and img_div and img_div.get("data-lazy-bg") and "map-image" not in (img_div.get("class") or []):
            imgs = [img_div["data-lazy-bg"]]
        h2 = item.select_one(".lease-description h2")
        out.append({
            "id": m.group(1),
            "url": BASE + a["href"],
            "title": h2.get_text(strip=True) if h2 else None,
            "type": msub.group(1) if msub else sub,
            "area": msub.group(2) if msub else None,
            "rent": _int(item.select_one(".rent").get_text()) if item.select_one(".rent") else None,
            "size_m2": _int(specs[0]) if len(specs) > 0 else None,
            "rooms": _int(specs[1]) if len(specs) > 1 else None,
            "period": specs[2] if len(specs) > 2 else None,
            "images": imgs,
        })
    return out


def parse_da_date(s: str) -> str | None:
    m = re.match(r"(\d{1,2})\.\s*(\w+)\s+(\d{4})", (s or "").strip())
    if not m or m.group(2).lower() not in DA_MONTHS:
        return None
    return dt.date(int(m.group(3)), DA_MONTHS[m.group(2).lower()], int(m.group(1))).isoformat()


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    ld: dict = {}
    for sc in soup.select("script[type='application/ld+json']"):
        try:
            d = json.loads(sc.string or "")
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "RealEstateListing":
            ld = d
    addr = (ld.get("itemOffered") or {}).get("address") or {}
    dl = {}
    for div in soup.select("#property-details > div"):
        dd = div.select_one("dd")
        if dd and div.get("class"):
            dl[div["class"][0]] = dd.get_text(strip=True)
    econ = {}
    for li in soup.select("#lease-economics li, #lease-time li"):
        k, _, v = li.get_text(" ", strip=True).partition(":")
        econ[k.strip()] = v.strip()
    time_tag = soup.select_one("#lease-time time[datetime]")
    pets = soup.select_one("#lease-pets")
    pets_txt = pets.get_text(" ", strip=True) if pets else ""
    flags = [li.get_text(strip=True) for li in soup.select("#lease-flags li")]
    h2 = soup.select_one("header h2.unstyled")
    desc_div = soup.select_one(".lease-text > div")
    if desc_div:
        for ul in desc_div.select("ul#digitalshowcase"):
            ul.decompose()
    ledig = econ.get("Ledig")
    floor = None
    if h2:
        mf = re.search(r"-\s*(\d+)\.", h2.get_text(" ", strip=True))
        floor = int(mf.group(1)) if mf else None
    return {
        "street": addr.get("streetAddress"),
        "zip": _int(addr.get("postalCode")),
        "city": addr.get("addressLocality"),
        "type": dl.get("type"),
        "area": dl.get("location"),
        "rent": _int(dl.get("rent")),
        "period": dl.get("rental-period"),
        "size_m2": _int(dl.get("sqm")),
        "rooms": _int(dl.get("rooms")),
        "floor": floor,
        "prepaid_rent": _int(econ.get("Forudbetalt leje")),
        "deposit": _int(econ.get("Depositum")),
        "available_from": "now" if ledig == "Straks" else parse_da_date(ledig or ""),
        "listed_date": (time_tag["datetime"][:10] if time_tag else (ld.get("datePosted") or "")[:10]) or None,
        "pets_allowed": True if pets_txt.startswith("Husdyr tilladt") else (False if "ikke tilladt" in pets_txt else None),
        "furnished": "Lejemålet udlejes møbleret" in flags,
        "balcony": any("altan" in f.lower() for f in flags),
        "elevator": any("elevator" in f.lower() for f in flags),
        "images": [a["href"] for a in soup.select("#lease-image-carousel a.item[href]")],
        "description": desc_div.get_text(" ", strip=True) if desc_div else None,
    }


@register
class Lejebolig(BaseSource):
    name = "lejebolig"
    categories = ("rent",)

    def fetch(self, max_pages: int | None = None) -> Iterator[Listing]:
        budget = int(self.config.get("detail_budget", 300))
        r = self.http.get(BASE + "/lejeboliger")
        m = re.search(r'id="TotalCount"[^>]*value="(\d+)"', r.text)
        self.log.info("total listings: %s", m.group(1) if m else "?")
        cards = parse_cards(r.text)
        page, seen = 1, set()
        while True:
            self.log.info("page %d: %d cards", page, len(cards))
            for card in cards:
                if card["id"] in seen:
                    continue
                seen.add(card["id"])
                detail = self.cache.get(card["id"])
                if detail is None and budget > 0:
                    budget -= 1
                    detail = self._fetch_detail(card)
                    if detail is not None:
                        self.cache[card["id"]] = detail
                listing = self._convert(card, detail or {})
                if listing:
                    yield listing
            if max_pages and page >= max_pages:
                break
            r = self.http.get(BASE + "/Hunter/MoreResultsV2?isCitySearch=False", headers=XHR)
            if 'class="last"' in r.text:
                break
            cards = parse_cards(r.text)
            if not cards:
                break
            page += 1
        # drop cache entries for listings that are gone
        for lid in [k for k in self.cache if k not in seen]:
            del self.cache[lid]

    def _fetch_detail(self, card: dict) -> dict | None:
        try:
            html = self.http.get(card["url"]).text
            detail = parse_detail(html)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("detail %s failed: %s", card["id"], exc)
            return None
        try:
            geo = self.http.get(
                f"{BASE}/mapHandler/{card['id']}",
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": card["url"], "Accept": "application/json, text/javascript, */*; q=0.01"},
            ).json()
            if geo:
                detail["lat"], detail["lon"] = geo[0].get("lat"), geo[0].get("lng")
                detail.setdefault("zip", geo[0].get("ZipCode"))
                detail.setdefault("city", geo[0].get("City"))
        except Exception as exc:  # noqa: BLE001
            self.log.debug("mapHandler %s failed: %s", card["id"], exc)
        return detail

    def _convert(self, card: dict, d: dict) -> Listing | None:
        type_name = (d.get("type") or card.get("type") or "").lower()
        ptype = TYPE_MAP.get(type_name, "other")
        if ptype is None:
            return None
        text = f"{card.get('title') or ''} {d.get('description') or ''}".lower()
        if any(w in text for w in BAD_WORDS) and ptype != "apartment":
            return None
        street = d.get("street") or ""
        return Listing(
            source=self.name,
            source_id=card["id"],
            category="rent",
            url=card["url"],
            title=card.get("title"),
            address=street,  # street name only; lejebolig.dk hides house numbers
            zip_code=d.get("zip"),
            city=d.get("city") or card.get("area"),
            lat=d.get("lat"),
            lon=d.get("lon"),
            price=d.get("rent") or card.get("rent"),
            deposit=d.get("deposit"),
            prepaid_rent=d.get("prepaid_rent"),
            property_type=ptype,
            size_m2=d.get("size_m2") or card.get("size_m2"),
            rooms=d.get("rooms") or card.get("rooms"),
            floor=d.get("floor"),
            balcony=d.get("balcony"),
            elevator=d.get("elevator"),
            pets_allowed=d.get("pets_allowed"),
            furnished=d.get("furnished"),
            available_from=d.get("available_from"),
            rental_period=d.get("period") or card.get("period"),
            listed_date=d.get("listed_date"),
            images=d.get("images") or card.get("images") or [],
            description=d.get("description"),
            extra={k: v for k, v in {"area": card.get("area"), "details_fetched": bool(d) or None}.items() if v},
        )
