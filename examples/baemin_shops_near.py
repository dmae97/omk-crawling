#!/usr/bin/env python3
"""List Baemin shops near a geo point (food-shop-list live path).

  PYTHONPATH=. python examples/baemin_shops_near.py
  PYTHONPATH=. python examples/baemin_shops_near.py --lat 36.833 --lng 127.130 --limit 30
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# repo root on path when run as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omk_crawl.baemin import BaeminClient, BaeminConfig, rank_shops, shops_to_markdown


def main() -> None:
    p = argparse.ArgumentParser(description="Baemin shops near lat/lng")
    p.add_argument("--lat", type=float, default=36.8330294, help="latitude")
    p.add_argument("--lng", type=float, default=127.1302718, help="longitude")
    p.add_argument("--limit", type=int, default=40, help="max shops to fetch")
    p.add_argument("--top", type=int, default=15, help="print top-N by reviews")
    p.add_argument("-o", "--output", help="write full JSON list")
    p.add_argument("--json", action="store_true", help="print top as JSON")
    args = p.parse_args()

    client = BaeminClient(
        BaeminConfig(lat=args.lat, lng=args.lng, rate=1.0, cache_ttl=60)
    )
    shops = client.collect_shops(max_shops=args.limit)
    if not shops:
        print("no shops — check network / rate limit", file=sys.stderr)
        sys.exit(1)

    top = rank_shops(shops, limit=args.top)
    if args.json:
        payload = [
            {
                "rank": i,
                "name": s.name,
                "number": s.number,
                "latestReviewCount": s.latest_review_count,
                "starScore": s.star_score,
                "menus": list(s.menus),
            }
            for i, s in enumerate(top, 1)
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            shops_to_markdown(
                top, title=f"Baemin near {args.lat:.4f},{args.lng:.4f}"
            )
        )

    if args.output:
        Path(args.output).write_text(
            json.dumps([s.raw for s in shops], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"wrote {len(shops)} shops → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
