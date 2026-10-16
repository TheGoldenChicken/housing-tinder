from pathlib import Path

from scraper.sources.lejebolig import parse_cards, parse_detail

FIX = Path(__file__).parent / "fixtures" / "lejebolig"


def test_parse_cards():
    cards = parse_cards((FIX / "more1.html").read_text(encoding="utf-8"))
    assert len(cards) == 40
    c = cards[0]
    assert c["id"].isdigit() and c["url"].startswith("https://www.lejebolig.dk/lejebolig/")
    assert c["rent"] and c["size_m2"]


def test_parse_detail():
    d = parse_detail((FIX / "detail_1917074.html").read_text(encoding="utf-8"))
    assert d["zip"] == 2100 and d["city"] == "København Ø"
    assert d["rent"] == 14000 and d["deposit"] == 42000 and d["size_m2"] == 66
    assert d["listed_date"] == "2026-10-12"
