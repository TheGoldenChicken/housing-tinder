"""Normalised listing model shared by every source."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Literal

Category = Literal["sale", "rent", "andel"]
PropertyType = Literal[
    "house",            # villa / parcelhus / fritliggende
    "townhouse",        # rækkehus / kædehus / dobbelthus
    "apartment",        # ejerlejlighed / lejlighed
    "villa_apartment",  # villalejlighed
    "farm",             # landejendom / nedlagt landbrug
    "room",             # værelse
    "other",
]

# Address types we never want (summer houses, allotments, plots, pure business...)
EXCLUDED_TYPES = {"holiday_house", "allotment", "plot", "business", "parking", "garage"}


@dataclass
class Listing:
    source: str
    source_id: str
    category: Category
    url: str
    address: str                      # "Hyskenstræde 7, 2. th"
    zip_code: int | None
    city: str | None
    price: int | None                 # DKK: sale price, or monthly rent
    property_type: PropertyType = "other"
    title: str | None = None
    municipality: str | None = None
    municipality_code: int | None = None
    lat: float | None = None
    lon: float | None = None
    monthly_cost: int | None = None   # ejerudgift / boligafgift / aconto for rent
    deposit: int | None = None        # rent: depositum
    prepaid_rent: int | None = None   # rent: forudbetalt leje
    size_m2: int | None = None
    lot_m2: int | None = None
    rooms: float | None = None
    floor: int | None = None
    bathrooms: int | None = None
    toilets: int | None = None
    has_bathroom: bool | None = None  # explicit "own bathroom" info when a site gives it
    year_built: int | None = None
    energy_label: str | None = None
    balcony: bool | None = None
    elevator: bool | None = None
    terrace: bool | None = None
    pets_allowed: bool | None = None
    furnished: bool | None = None
    available_from: str | None = None  # ISO date
    rental_period: str | None = None   # e.g. "unlimited", "24 months"
    listed_date: str | None = None     # ISO date as reported by the source
    images: list[str] = field(default_factory=list)
    description: str | None = None
    realtor: str | None = None
    extra: dict = field(default_factory=dict)
    # filled by the pipeline
    id: str = ""
    address_key: str = ""
    first_seen: str = ""
    last_seen: str = ""
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"{self.source}:{self.source_id}"
        if not self.address_key:
            self.address_key = address_key(self.address, self.zip_code)
        if self.description:
            self.description = re.sub(r"\s+", " ", self.description).strip()[:700]
        self.images = [u for u in self.images if u][:6]
        if not self.sources:
            self.sources = [self.source]

    def to_dict(self) -> dict:
        d = asdict(self)
        # keep output compact: drop empty values
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def address_key(address: str, zip_code: int | None) -> str:
    """Stable key for deduplicating the same home across sites.

    Normalises 'Hyskenstræde 7, 2. th.' → 'hyskenstraede 7 2 th|1207'.
    """
    s = unicodedata.normalize("NFKD", address or "").lower()
    s = s.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\b(st|stuen)\b", "st", s)
    return f"{s}|{zip_code or ''}"


def stable_id(*parts: object) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
