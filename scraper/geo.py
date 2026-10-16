"""Municipality lookups. DAWA (the public Danish address API) was retired, so the
zip→municipality map is learned from listings that carry both and persisted
between runs (data/zip_municipality.json), seeded from resources/."""
from __future__ import annotations

import json
from pathlib import Path

RES = Path(__file__).parent / "resources"


class Geo:
    def __init__(self, extra_zip_map: dict[str, int] | None = None):
        self.municipalities: dict[int, str] = {
            int(code): m["name"] for code, m in json.loads((RES / "municipalities.json").read_text()).items()
        }
        self.zip_map: dict[int, int] = {}
        seed = RES / "zip_municipality.json"
        if seed.exists():
            self.zip_map.update({int(k): int(v) for k, v in json.loads(seed.read_text()).items()})
        if extra_zip_map:
            self.zip_map.update({int(k): int(v) for k, v in extra_zip_map.items()})

    def learn(self, zip_code: int | None, municipality_code: int | None) -> None:
        if zip_code and municipality_code and municipality_code in self.municipalities:
            self.zip_map.setdefault(int(zip_code), int(municipality_code))

    def municipality_code_for_zip(self, zip_code: int | None) -> int | None:
        if zip_code is None:
            return None
        return self.zip_map.get(int(zip_code))

    def name(self, code: int | None) -> str | None:
        return self.municipalities.get(int(code)) if code else None

    def export_zip_map(self) -> dict[str, int]:
        return {str(k): v for k, v in sorted(self.zip_map.items())}
