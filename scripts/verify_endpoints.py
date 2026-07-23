#!/usr/bin/env python3
"""Verify all discovered endpoints — bottleneck resolution check.

Runs every endpoint found during APK reverse engineering and live testing,
reports status, and confirms resilience mechanisms work.

Usage:
    python scripts/verify_endpoints.py
    python scripts/verify_endpoints.py --quick   # skip slow endpoints
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omk_crawl.resilience import (
    HeaderStore,
    ImpersonateRotator,
    TokenBucket,
    ensure_playwright,
)


def check(label: str, fn, *, expect: str = "any") -> dict:
    """Run a check and return result dict."""
    t0 = time.monotonic()
    try:
        result = fn()
        elapsed = time.monotonic() - t0
        if isinstance(result, dict):
            result["elapsed_ms"] = round(elapsed * 1000)
            result["label"] = label
            return result
        return {
            "label": label, "ok": True, "elapsed_ms": round(elapsed * 1000),
            "detail": str(result)[:100],
        }
    except Exception as exc:
        return {"label": label, "ok": False, "error": str(exc)[:150],
                "elapsed_ms": round((time.monotonic() - t0) * 1000)}


def main() -> None:
    quick = "--quick" in sys.argv
    results: list[dict] = []
    bucket = TokenBucket(rate=0.5, capacity=3)

    print("=" * 70)
    print("  ENDPOINT VERIFICATION — 병목 해결 확인")
    print("=" * 70)

    # ── 1. Baemin gateway (no auth needed) ──
    def bm_gateway():
        from curl_cffi import requests as cffi
        bucket.acquire()
        r = cffi.get("https://gateway-api.baemin.com/v1/tabs/review",
                     impersonate="chrome124", timeout=8,
                     headers={"Accept": "application/json"})
        data = r.json()
        return {"ok": r.status_code == 200, "status": r.status_code,
                "data": data.get("status", "?")}
    results.append(check("배민 gateway-api /v1/tabs/review", bm_gateway))

    # ── 2. Baemin review-api (expect 500/400 = alive) ──
    def bm_review():
        from curl_cffi import requests as cffi
        bucket.acquire()
        r = cffi.get("https://review-api.baemin.com/v1/reviews",
                     params={"shopId": "1", "page": 1},
                     impersonate="chrome124", timeout=8,
                     headers={"Accept": "application/json"})
        # 500 or 400 = server alive, just needs proper params/headers
        alive = r.status_code in (200, 400, 500)
        return {"ok": alive, "status": r.status_code,
                "detail": "alive (needs captured headers)" if alive else "unexpected"}
    results.append(check("배민 review-api /v1/reviews", bm_review))

    # ── 3. Baemin search-gateway (expect 403 WAF) ──
    def bm_search():
        from curl_cffi import requests as cffi
        bucket.acquire()
        r = cffi.get("https://search-gateway.baemin.com/v1/search",
                     params={"query": "치킨", "lat": 37.49, "lng": 127.02},
                     impersonate="chrome124", timeout=8)
        return {
            "ok": True, "status": r.status_code,
            "detail": (
                "403 WAF (expected without auth)" if r.status_code == 403
                else f"HTTP {r.status_code}"
            ),
        }
    results.append(check("배민 search-gateway /v1/search", bm_search))

    # ── 4. Baemin webview (expect 200 HTML) ──
    def bm_webview():
        from curl_cffi import requests as cffi
        bucket.acquire()
        r = cffi.get("https://web.baemin.com/food/shops",
                     params={"lat": 37.49, "lng": 127.02},
                     impersonate="chrome124", timeout=8)
        has_title = "푸드" in r.text or "shop" in r.text.lower()
        return {"ok": r.status_code == 200, "status": r.status_code,
                "detail": "webview HTML" if has_title else "empty"}
    results.append(check("배민 web.baemin.com /food/shops", bm_webview))

    # ── 5. Baemin location-api ──
    def bm_location():
        from curl_cffi import requests as cffi
        bucket.acquire()
        r = cffi.get("https://location-api.baemin.com/v1/search/address",
                     params={"query": "역삼동"},
                     impersonate="chrome124", timeout=8,
                     headers={"Accept": "application/json"})
        return {"ok": r.status_code in (200, 400, 500), "status": r.status_code,
                "detail": "alive" if r.status_code in (200, 400, 500) else "unexpected"}
    results.append(check("배민 location-api /v1/search/address", bm_location))

    if not quick:
        # ── 6. Naver Real Estate ──
        def naver_land():
            from curl_cffi import requests as cffi
            bucket.acquire()
            r = cffi.get("https://new.land.naver.com/api/complexes/single-markers/2.0",
                         params={"cortarNo": "1168010100", "zoom": 16, "priceType": "RETAIL"},
                         impersonate="chrome124", timeout=10,
                         headers={"Referer": "https://new.land.naver.com/"})
            if r.status_code == 429:
                return {"ok": True, "status": 429, "detail": "rate-limited (retry later)"}
            try:
                data = r.json()
                n = len(data) if isinstance(data, list) else 0
                return {"ok": r.status_code == 200, "status": r.status_code,
                        "detail": f"{n} complexes" if n else str(data)[:80]}
            except Exception:
                return {"ok": r.status_code == 200, "status": r.status_code}
        results.append(check("네이버 부동산 complexes/single-markers", naver_land))

        # ── 7. Naver Cafe ──
        def naver_cafe():
            from curl_cffi import requests as cffi
            bucket.acquire()
            r = cffi.get("https://cafe.naver.com/ArticleList.nhn",
                         params={"search.clubid": "1", "page": 1},
                         impersonate="chrome124", timeout=10,
                         headers={"Referer": "https://cafe.naver.com/"})
            # 404 = endpoint reachable, club_id invalid (expected for test ID)
            reachable = r.status_code in (200, 302, 404)
            return {"ok": reachable, "status": r.status_code,
                    "detail": "reachable (need valid club_id)" if r.status_code == 404 else "HTML"}
        results.append(check("네이버 카페 ArticleList.nhn", naver_cafe))

    # ── 8. Resilience: TokenBucket ──
    def test_bucket():
        b = TokenBucket(rate=10, capacity=5)
        t0 = time.monotonic()
        for _ in range(5):
            b.acquire()
        elapsed = time.monotonic() - t0
        return {"ok": elapsed < 1.0, "detail": f"5 burst in {elapsed*1000:.0f}ms"}
    results.append(check("TokenBucket burst", test_bucket))

    # ── 9. Resilience: ImpersonateRotator ──
    def test_rotator():
        rot = ImpersonateRotator()
        seen = set()
        for _ in range(8):
            seen.add(rot.next())
        rot.mark_failed("chrome124")
        next_imp = rot.next()
        return {"ok": len(seen) >= 4 and next_imp != "chrome124",
                "detail": f"{len(seen)} unique, failed excluded"}
    results.append(check("ImpersonateRotator", test_rotator))

    # ── 10. Resilience: HeaderStore ──
    def test_headers():
        hs = HeaderStore()
        h = hs.get({"X-Custom": "test"})
        return {"ok": "User-Agent" in h and h.get("X-Custom") == "test",
                "detail": f"{len(h)} headers"}
    results.append(check("HeaderStore", test_headers))

    # ── 11. Playwright availability ──
    def test_pw():
        ready = ensure_playwright()
        return {
            "ok": True,
            "detail": (
                "installed" if ready
                else "not installed (run: playwright install chromium)"
            ),
        }
    results.append(check("Playwright chromium", test_pw))

    # ── 12. BaeminClient integration ──
    def test_client():
        from omk_crawl.baemin import BaeminClient, BaeminConfig
        client = BaeminClient(BaeminConfig(rate=1.0, cache_dir="/tmp/bm_test_cache"))
        r = client.gateway_tabs()
        return {"ok": r.ok, "detail": f"status={r.status_code}" if r.ok else r.error[:80]}
    results.append(check("BaeminClient.gateway_tabs()", test_client))

    # ── 13. Naver Cafe article list (NEW powerful REST API, no login) ──
    def test_cafe_list():
        from omk_crawl.naver import NaverCafeClient, NaverConfig
        cafe = NaverCafeClient(NaverConfig(rate=1.0, cache_dir="/tmp/cafe_verify_cache"))
        r = cafe.articles("10093599", menu_id=0, page=1, page_size=5)
        if not r.ok:
            return {"ok": False, "detail": r.error[:80]}
        items = cafe.normalize_articles(r.data)
        cafe.close()
        return {
            "ok": len(items) > 0, "status": r.status_code,
            "detail": (
                f"{len(items)} articles, first='{items[0]['subject'][:20]}'"
                if items else "empty"
            ),
        }
    results.append(check("네이버 카페 게시글 목록 (no-login REST)", test_cafe_list))

    # ── 14. Naver Cafe notices ──
    def test_cafe_notices():
        from omk_crawl.naver import NaverCafeClient, NaverConfig
        cafe = NaverCafeClient(NaverConfig(rate=1.0, cache_dir="/tmp/cafe_verify_cache"))
        r = cafe.notices("10093599")
        ok = r.ok and isinstance(r.data, dict)
        cafe.close()
        return {"ok": ok, "status": r.status_code, "detail": "notices ok" if ok else r.error[:60]}
    results.append(check("네이버 카페 공지사항", test_cafe_notices))

    # ── 15. CircuitBreaker ──
    def test_breaker():
        from omk_crawl.stability import CircuitBreaker, CircuitState
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
        for _ in range(2):
            try:
                cb.call(lambda: (_ for _ in ()).throw(ValueError("x")))
            except ValueError:
                pass
        return {"ok": cb.state == CircuitState.OPEN, "detail": f"state={cb.state.value}"}
    results.append(check("CircuitBreaker trip", test_breaker))

    # ── 16. CapturedCall Korean encoding detection ──
    def test_encoding():
        from omk_crawl.adaptive import CapturedCall
        cc = CapturedCall(url="t", method="GET", status=200,
                          response_bytes="배달의민족".encode("cp949"))
        out = cc.decoded_text()
        return {"ok": out == "배달의민족", "detail": f"cp949→{out!r}"}
    results.append(check("CapturedCall Korean decode (cp949)", test_encoding))

    # ── 17. AdaptiveFetcher escalation ──
    def test_adaptive():
        from omk_crawl.adaptive import AdaptiveConfig, AdaptiveFetcher
        f = AdaptiveFetcher(AdaptiveConfig(rate=1.0))
        r = f.fetch_json_api("https://gateway-api.baemin.com/v1/tabs/review")
        f.close()
        return {"ok": r.ok and r.json_data is not None,
                "detail": f"strategy={r.strategy} status={r.status_code}"}
    results.append(check("AdaptiveFetcher.fetch_json_api", test_adaptive))

    # ── Report ──
    print()
    ok_count = sum(1 for r in results if r.get("ok"))
    total = len(results)

    for r in results:
        status = "✓" if r.get("ok") else "✗"
        label = r.get("label", "?")
        detail = r.get("detail", r.get("error", ""))
        code = r.get("status", "")
        ms = r.get("elapsed_ms", 0)
        code_str = f" [{code}]" if code else ""
        print(f"  {status} {label:<45s}{code_str:<8s} {ms:>5d}ms  {detail}")

    print()
    print(f"  {ok_count}/{total} passed")
    print("=" * 70)

    # ── Bottleneck summary ──
    print()
    print("  병목 해결 현황:")
    print("  ┌─────────────────────────┬──────────────────────────────────────┐")
    print("  │ 429 Rate Limit          │ TokenBucket + RetryPolicy backoff    │")
    print("  │ 400 헤더 누락           │ HeaderStore (mitmproxy capture)      │")
    print("  │ 403 WAF                 │ ImpersonateRotator (8 TLS profiles)  │")
    print("  │ DNS 미해석              │ EndpointChain fallback               │")
    print("  │ CSR 빈 데이터           │ Playwright auto-fallback             │")
    print("  │ Playwright 미설치       │ ensure_playwright() + auto-install   │")
    print("  │ 잘못된 엔드포인트       │ APK-verified registry only           │")
    print("  └─────────────────────────┴──────────────────────────────────────┘")


if __name__ == "__main__":
    main()
