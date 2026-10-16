from scraper.models import Listing, address_key


def test_address_key_normalises():
    assert address_key("Hyskenstræde 7, 2. th.", 1207) == address_key("Hyskenstraede 7 2 th", 1207)


def test_listing_compact_dict():
    l = Listing(source="x", source_id="1", category="sale", url="u", address="Vej 1", zip_code=2100, city="Kbh", price=1)
    d = l.to_dict()
    assert d["id"] == "x:1" and "lat" not in d
