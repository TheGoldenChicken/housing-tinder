"""Run sources → dedupe → merge with previous state → write static JSON."""
from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .geo import Geo
from .http import Http
from .models import Listing, stable_id
from .sources import load_all

log = logging.getLogger("pipeline")

# When the same home appears on several sites, the first source in this list wins
# as the base record; missing fields are filled from the others.
SOURCE_PRIORITY = ["boligsiden", "boligportal", "boliga", "findbolig", "edc", "home", "lejebolig", "boligzonen"]
RETAIN_UNSEEN_DAYS = 30


def _load_prev(state_url: str | None, out_dir: Path, name: str, http: Http) -> dict:
    """Previous run's state: from the live site if given, else from out_dir."""
    if state_url:
        try:
            data = http.get_json(f"{state_url.rstrip('/')}/{name}")
            log.info("loaded previous %s from %s (%d entries)", name, state_url, len(data))
            return data
        except Exception as exc:  # noqa: BLE001
            log.warning("no previous %s at %s (%s)", name, state_url, exc)
    local = out_dir / name
    if local.exists():
        return json.loads(local.read_text())
    return {}


def _dedupe(listings: list[Listing]) -> list[Listing]:
    groups: dict[tuple, list[Listing]] = defaultdict(list)
    for item in listings:
        street_part, _, zip_part = item.address_key.partition("|")
        has_number = any(ch.isdigit() for ch in street_part)
        if street_part and zip_part and has_number:
            # Some sites omit floor/door, so two flats at "Slippen 2" would collide.
            # Floor + size disambiguate while still matching the same home across sites.
            key = (item.category, item.address_key, item.floor if item.floor is not None else "", item.size_m2 or "")
        else:
            key = (item.category, item.id)
        groups[key].append(item)

    merged: list[Listing] = []
    for key, group in groups.items():
        # Two listings from the SAME site at one key are different units (e.g. project
        # rentals without floor/door), so only merge across sites: split the group into
        # sub-groups holding at most one listing per source.
        buckets: list[list[Listing]] = []
        for item in sorted(group, key=lambda x: x.source_id):
            for bucket in buckets:
                if all(b.source != item.source for b in bucket):
                    bucket.append(item)
                    break
            else:
                buckets.append([item])
        for n, items in enumerate(buckets):
            merged.append(_merge(items, key if n == 0 else (*key, items[0].id)))
    return merged


def _merge(items: list[Listing], key: tuple) -> Listing:
    """Fold several sites' records of one home into the highest-priority one."""
    items.sort(key=lambda x: SOURCE_PRIORITY.index(x.source) if x.source in SOURCE_PRIORITY else 99)
    base = items[0]
    urls = {base.source: base.url}
    for other in items[1:]:
        urls[other.source] = other.url
        for field_name, value in vars(other).items():
            if field_name in ("id", "source", "source_id", "sources", "extra", "images", "url"):
                continue
            if getattr(base, field_name) in (None, "", []) and value not in (None, "", []):
                setattr(base, field_name, value)
        if not base.images:
            base.images = other.images
        for k, v in other.extra.items():
            base.extra.setdefault(k, v)
    base.sources = [i.source for i in items]
    if len(urls) > 1:
        base.extra["urls"] = urls
    base.id = "h" + stable_id(*key)
    return base


def run(
    out_dir: Path,
    source_names: list[str] | None = None,
    max_pages: int | None = None,
    state_url: str | None = None,
    config: dict | None = None,
) -> dict:
    config = config or {}
    http = Http(delay=float(config.get("request_delay", 0.8)))
    registry = load_all()
    source_names = source_names or config.get("sources") or list(registry)
    today = date.today().isoformat()

    prev_seen = _load_prev(state_url, out_dir, "seen.json", http)
    geo = Geo(_load_prev(state_url, out_dir, "zip_municipality.json", http))

    listings: list[Listing] = []
    status: dict[str, dict] = {}
    for name in source_names:
        if name not in registry:
            log.error("unknown source %s (known: %s)", name, ", ".join(registry))
            status[name] = {"ok": False, "error": "unknown source", "count": 0}
            continue
        cache = _load_prev(state_url, out_dir, f"cache/{name}.json", http)
        src = registry[name](http, (config.get("source_config") or {}).get(name), cache)
        count = 0
        try:
            for item in src.fetch(max_pages=max_pages):
                listings.append(item)
                geo.learn(item.zip_code, item.municipality_code)
                count += 1
            status[name] = {"ok": True, "count": count}
        except Exception as exc:  # noqa: BLE001
            log.exception("source %s failed after %d listings", name, count)
            status[name] = {"ok": False, "error": str(exc)[:300], "count": count}
        log.info("source %s: %d listings", name, count)
        if src.cache:
            (out_dir / "cache").mkdir(parents=True, exist_ok=True)
            (out_dir / f"cache/{name}.json").write_text(json.dumps(src.cache, ensure_ascii=False, separators=(",", ":")))

    for item in listings:
        if not item.municipality_code:
            item.municipality_code = geo.municipality_code_for_zip(item.zip_code)
        if not item.municipality and item.municipality_code:
            item.municipality = geo.name(item.municipality_code)

    merged = _dedupe(listings)
    log.info("%d raw → %d unique listings", len(listings), len(merged))

    seen: dict[str, dict] = {}
    for item in merged:
        prev = prev_seen.get(item.id)
        item.first_seen = prev["f"] if prev else today
        item.last_seen = today
        seen[item.id] = {"f": item.first_seen, "l": today}
    for lid, entry in prev_seen.items():
        if lid not in seen:
            try:
                age = (date.today() - date.fromisoformat(entry["l"])).days
            except (KeyError, ValueError):
                continue
            if age < RETAIN_UNSEEN_DAYS:
                seen[lid] = entry

    return _write(out_dir, merged, seen, geo, status, today)


def _write(out_dir: Path, merged: list[Listing], seen: dict, geo: Geo, status: dict, today: str) -> dict:
    for cat in ("sale", "rent", "andel"):
        shutil.rmtree(out_dir / cat, ignore_errors=True)
        (out_dir / cat).mkdir(parents=True, exist_ok=True)

    by_file: dict[tuple[str, int], list[dict]] = defaultdict(list)
    counts = {"sale": 0, "rent": 0, "andel": 0}
    muni_counts: dict[int, dict] = defaultdict(lambda: {"sale": 0, "rent": 0, "andel": 0})
    new_counts = {"sale": 0, "rent": 0, "andel": 0}
    for item in merged:
        code = item.municipality_code or 0
        by_file[(item.category, code)].append(item.to_dict())
        counts[item.category] += 1
        muni_counts[code][item.category] += 1
        if item.first_seen == today:
            new_counts[item.category] += 1

    files: dict[str, dict[str, str]] = {"sale": {}, "rent": {}, "andel": {}}
    all_by_cat: dict[str, list[dict]] = defaultdict(list)
    for (cat, code), items in sorted(by_file.items()):
        items.sort(key=lambda d: (d.get("first_seen", ""), d.get("id")), reverse=True)
        rel = f"{cat}/{code}.json"
        (out_dir / rel).write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")))
        files[cat][str(code)] = rel
        all_by_cat[cat].extend(items)
    for cat, items in all_by_cat.items():
        (out_dir / f"{cat}/all.json").write_text(json.dumps(items, ensure_ascii=False, separators=(",", ":")))

    municipalities = [
        {"code": code, "name": geo.name(code) or "Ukendt kommune", **muni_counts.get(code, {"sale": 0, "rent": 0, "andel": 0})}
        for code in sorted(set(geo.municipalities) | set(muni_counts))
    ]
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": today,
        "counts": counts,
        "new_today": new_counts,
        "sources": status,
        "municipalities": municipalities,
        "files": files,
    }
    (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))
    (out_dir / "seen.json").write_text(json.dumps(seen, separators=(",", ":")))
    (out_dir / "zip_municipality.json").write_text(json.dumps(geo.export_zip_map(), separators=(",", ":")))
    log.info("wrote %s: %s", out_dir, counts)
    return index
