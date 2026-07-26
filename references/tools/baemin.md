# Baemin (배달의민족) target client

Layer **target** — geo shop list via webview food-shop-list API.

## Live path (no app login)

```
GET https://food-shop-list.baemin.com/api/display-group/{group}
    /display-category/{category}/shops
```

| Item | Value |
|------|--------|
| Working group | `FOOD_CATEGORY` |
| Default category | `FOOD_CATEGORY_ALL` |
| Geo headers | `X-BAEMIN-LATITUDE`, `X-BAEMIN-LONGITUDE` |
| Device | `X-BAEMIN-DEVICE-ID` |
| Paging | `shops.limit`, `shops.offset`, `shops.excludeShops` |
| Stats fields | `shop.statics.latestReviewCount`, `shop.statics.starScore` |

Discovered from `web.baemin.com/food/shops` prefetch script (2026-07).

## CLI

```bash
omk-crawl baemin://status
omk-crawl baemin://36.8330,127.1303
omk-crawl 'baemin://shops?lat=36.833&lng=127.130&limit=40' --json -o out.json
```

## Python

```python
from omk_crawl.baemin import BaeminClient, BaeminConfig, rank_shops, shops_to_markdown

client = BaeminClient(BaeminConfig(lat=36.833, lng=127.130, rate=1.0))
shops = client.collect_shops(max_shops=100)
top = rank_shops(shops, limit=20)
print(shops_to_markdown(top))
```

## Not working without capture

| Host | Symptom |
|------|---------|
| `search-gateway.baemin.com` | 403 WAF |
| `bm-store-api.baemin.com` | DNS NXDOMAIN (public) |
| `review-api.baemin.com` | 4xx/5xx without app session |

Use `examples/baemin_mitm_capture.py` + `capture_file=` for those.

## Guardrails

- Respect ToS / rate limits (`BaeminConfig.rate` default 0.5 rps).
- Public shop cards only via list API; do not scrape PII or private reviews without authorization.
- Prefer official partner APIs for production products.
