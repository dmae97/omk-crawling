"""
배민 API 헤더/토큰 자동 캡처 (mitmproxy addon)
================================================
사용법:
  1. pip install mitmproxy
  2. mitmweb -s baemin_mitm_capture.py -p 8080
  3. Android 에뮬레이터/폰 WiFi 프록시 → 호스트IP:8080
  4. mitmproxy CA 인증서 설치 (http://mitm.it)
  5. 배민 앱 실행 → 가게/리뷰 페이지 열기
  6. 캡처된 헤더가 baemin_captured_headers.json에 저장됨

인증서 피닝 시:
  - 루팅된 디바이스 + Frida: objection -g com.sampleapp explore → android sslpinning disable
  - 또는 Android 7 이하 에뮬레이터 (시스템 CA 신뢰)
"""

import json
import os
from mitmproxy import http

CAPTURE_FILE = os.path.join(os.path.dirname(__file__), "baemin_captured_headers.json")
BAEMIN_DOMAINS = ["baemin.com", "woowahan.com"]

captured = {
    "headers": {},
    "review_api": None,
    "search_api": None,
    "shop_api": None,
    "cookies": {},
    "auth_tokens": [],
}


def _is_baemin(url: str) -> bool:
    return any(d in url for d in BAEMIN_DOMAINS)


def _save():
    with open(CAPTURE_FILE, "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=2)
    print(f"[baemin-capture] saved → {CAPTURE_FILE}")


def request(flow: http.HTTPFlow):
    """배민 API 요청에서 헤더 캡처"""
    url = flow.request.pretty_url
    if not _is_baemin(url):
        return

    # 모든 배민 헤더 캡처
    for key, value in flow.request.headers.items():
        kl = key.lower()
        if kl.startswith("x-baemin") or kl.startswith("x-trace") or kl in (
            "authorization", "x-api-key", "app-id", "client-sdk",
            "device-baedal", "protocol-version", "session-id",
            "request-sent-timestamp", "cookie",
        ):
            captured["headers"][key] = value

    # API별 분류
    if "review-api" in url or "/reviews" in url:
        captured["review_api"] = {
            "url": url,
            "method": flow.request.method,
            "headers": dict(flow.request.headers),
            "body": flow.request.get_text()[:500] if flow.request.content else None,
        }
        print(f"[baemin-capture] REVIEW API: {flow.request.method} {url}")

    elif "search-gateway" in url or "/search" in url:
        captured["search_api"] = {
            "url": url,
            "method": flow.request.method,
            "headers": dict(flow.request.headers),
        }
        print(f"[baemin-capture] SEARCH API: {flow.request.method} {url}")

    elif "shop-detail" in url or "shop-home" in url or "/shops" in url:
        captured["shop_api"] = {
            "url": url,
            "method": flow.request.method,
            "headers": dict(flow.request.headers),
        }
        print(f"[baemin-capture] SHOP API: {flow.request.method} {url}")

    # Authorization 토큰
    auth = flow.request.headers.get("authorization", "")
    if auth and auth not in captured["auth_tokens"]:
        captured["auth_tokens"].append(auth)
        print(f"[baemin-capture] AUTH TOKEN captured")

    _save()


def response(flow: http.HTTPFlow):
    """배민 API 응답에서 데이터 구조 확인"""
    url = flow.request.pretty_url
    if not _is_baemin(url):
        return

    if "review" in url and flow.response.status_code == 200:
        try:
            data = json.loads(flow.response.get_text())
            print(f"[baemin-capture] REVIEW RESPONSE: {json.dumps(data, ensure_ascii=False)[:300]}")
        except Exception:
            pass
