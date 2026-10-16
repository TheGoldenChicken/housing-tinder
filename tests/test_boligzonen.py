import json
from pathlib import Path

from scraper.sources.boligzonen import last_page, parse_cards, parse_detail

FIX = Path(__file__).parent / "fixtures" / "boligzonen"


def test_cards_and_pagination():
    d = json.loads((FIX / "page1.json").read_text(encoding="utf-8"))
    cards = parse_cards(d["result_html"])
    assert len(cards) == 18 and all(c["id"] and c["url"].startswith("https://boligzonen.dk/") for c in cards)
    assert last_page(d["pagination"]) and last_page(d["pagination"]) > 100


def test_detail():
    d = parse_detail((FIX / "detail.html").read_text(encoding="utf-8"))
    assert d["zip"] and d["lat"] and d["rent"] and d["title"]
