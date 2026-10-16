from scraper.models import Listing
from scraper.pipeline import _dedupe


def mk(source, sid, address="Vej 1", zip_code=2100, size=80, floor=None, **kw):
    return Listing(source=source, source_id=sid, category="rent", url=f"https://{source}/{sid}", address=address,
                   zip_code=zip_code, city="X", price=10000, size_m2=size, floor=floor, **kw)


def test_merges_same_home_across_sites_and_keeps_fields():
    a = mk("boligsiden", "a", year_built=1950)
    b = mk("home", "b", floor=None, balcony=True)
    out = _dedupe([a, b])
    assert len(out) == 1
    m = out[0]
    assert m.source == "boligsiden" and m.sources == ["boligsiden", "home"]
    assert m.year_built == 1950 and m.balcony is True and set(m.extra["urls"]) == {"boligsiden", "home"}


def test_same_site_same_address_are_separate_units():
    out = _dedupe([mk("boligsiden", "a"), mk("boligsiden", "b")])
    assert len(out) == 2 and out[0].id != out[1].id


def test_different_floor_or_size_not_merged():
    out = _dedupe([mk("boligsiden", "a", floor=1), mk("home", "b", floor=2)])
    assert len(out) == 2


def test_ids_are_stable():
    first = _dedupe([mk("boligsiden", "a")])[0].id
    second = _dedupe([mk("boligsiden", "zzz")])[0].id  # same home, different source id
    assert first == second
