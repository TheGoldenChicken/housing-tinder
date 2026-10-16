"""CLI: uv run scrape --out data [--sources boligsiden,boliga] [--max-pages N] [--state-url URL]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from .pipeline import run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scrape", description=__doc__)
    p.add_argument("--out", type=Path, default=Path("data"), help="output directory (default: data)")
    p.add_argument("--sources", help="comma-separated source names (default: all enabled in config)")
    p.add_argument("--max-pages", type=int, help="limit pages per source (for testing)")
    p.add_argument("--state-url", help="base URL of the previously deployed data dir, e.g. https://you.github.io/repo/data")
    p.add_argument("--config", type=Path, default=Path("config.yaml"))
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = (yaml.safe_load(args.config.read_text()) if args.config.exists() else None) or {}
    sources = args.sources.split(",") if args.sources else None
    args.out.mkdir(parents=True, exist_ok=True)
    index = run(args.out, sources, args.max_pages, args.state_url or config.get("state_url"), config)
    print(json.dumps({"counts": index["counts"], "new_today": index["new_today"], "sources": index["sources"]}, indent=1))
    return 0 if any(s["ok"] for s in index["sources"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
