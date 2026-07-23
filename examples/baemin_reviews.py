"""
배달의민족 리뷰 크롤러 — APK 역공학 + mitmproxy 기반
=====================================================
1. baemin_mitm_capture.py로 헤더 캡처
2. 이 스크립트로 리뷰 대량 수집

발견된 API (APK v16.15.0 역분석):
  - review-api.baemin.com/v1/reviews          ← 리뷰 목록
  - review-api.baemin.com/v1/reviews/{id}     ← 리뷰 상세
  - search-gateway.baemin.com/v1/search       ← 가게 검색
  - gateway-api.baemin.com/v1/tabs/review     ← 리뷰 탭 (인증 불필요)
  - web.baemin.com/food/shopReviews           ← 웹뷰 리뷰 페이지
  - shop-detail-api.baemin.com/               ← 가게 상세

사용법:
  python baemin_reviews.py                     # 캡처된 헤더로 리뷰 수집
  python baemin_reviews.py --shop <shopId>     # 특정 가게
  python baemin_reviews.py --search "치킨"     # 검색 후 첫 가게 리뷰
"""

import json
import os
import sys
import time
import uuid
from pathlib import Path

from curl_cffi import requests

SCRIPT_DIR = Path(__file__).parent
CAPTURED_FILE = SCRIPT_DIR / "baemin_captured_headers.json"

# APK에서 추출한 API 엔드포인트
REVIEW_API = "https://review-api.baemin.com/v1/reviews"
SEARCH_API = "https://search-gateway.baemin.com/v1/search"
GATEWAY_REVIEW = "https://gateway-api.baemin.com/v1/tabs/review"
SHOP_DETAIL_API = "https://shop-detail-api.baemin.com"


def load_headers() -> dict:
    """mitmproxy로 캡처한 헤더 로드"""
    if CAPTURED_FILE.exists():
        with open(CAPTURED_FILE, encoding="utf-8") as f:
            data = json.load(f)
        headers = data.get("headers", {})
        print(f"[✓] 캡처된 헤더 로드: {len(headers)}개")

        # 리뷰 API 전용 헤더가 있으면 우선 사용
        review_api = data.get("review_api")
        if review_api and review_api.get("headers"):
            print("[✓] 리뷰 API 전용 헤더 사용")
            return review_api["headers"]
        return headers
    else:
        print("[!] 캡처된 헤더 없음. 기본 헤더 사용 (제한적)")
        print(f"    먼저: mitmweb -s {SCRIPT_DIR / 'baemin_mitm_capture.py'}")
        return {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
                          "AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json",
            "X-BAEMIN-DEVICE-ID": str(uuid.uuid4()),
            "X-TRACE-ID": str(uuid.uuid4()),
        }


def get_reviews(shop_id: str, headers: dict, page: int = 1, size: int = 20) -> dict | None:
    """가게 리뷰 조회"""
    params = {"shopId": shop_id, "page": page, "size": size}
    try:
        r = requests.get(REVIEW_API, params=params, headers=headers,
                         impersonate="chrome124", timeout=10)
        data = r.json()
        if data.get("status") == "SUCCESS":
            return data.get("data")
        else:
            print(f"  API 오류: {data.get('message')}")
            return None
    except Exception as e:
        print(f"  요청 실패: {e}")
        return None


def search_shops(query: str, headers: dict, lat: float = 37.4979, lng: float = 127.0276) -> list:
    """가게 검색"""
    params = {"query": query, "lat": lat, "lng": lng, "page": 1}
    try:
        r = requests.get(SEARCH_API, params=params, headers=headers,
                         impersonate="chrome124", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("data", {}).get("results", [])
        else:
            print(f"  검색 실패: HTTP {r.status_code}")
            return []
    except Exception as e:
        print(f"  검색 요청 실패: {e}")
        return []


def crawl_reviews(shop_id: str, headers: dict, max_pages: int = 5) -> list:
    """리뷰 크롤링 (페이지네이션)"""
    all_reviews = []
    for page in range(1, max_pages + 1):
        print(f"  페이지 {page}/{max_pages}...")
        data = get_reviews(shop_id, headers, page=page)
        if not data:
            break

        reviews = data if isinstance(data, list) else data.get("reviews", data.get("items", []))
        if not reviews:
            print("  리뷰 없음")
            break

        all_reviews.extend(reviews)
        print(f"  {len(reviews)}건 수집 (누적 {len(all_reviews)})")

        # 리뷰 미리보기
        for rev in reviews[:3]:
            rating = rev.get("rating", rev.get("star", "?"))
            content = rev.get("content", rev.get("reviewContent", ""))[:60]
            print(f"    ★{rating} {content}")

        if len(reviews) < 20:  # 마지막 페이지
            break
        time.sleep(1.5)  # 매너 딜레이

    return all_reviews


def main():
    headers = load_headers()

    # 모드 선택
    if "--search" in sys.argv:
        idx = sys.argv.index("--search")
        query = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "치킨"
        print(f"\n[검색] '{query}'")
        shops = search_shops(query, headers)
        if not shops:
            print("  검색 결과 없음 (인증 헤더 필요할 수 있음)")
            return
        shop = shops[0]
        shop_id = shop.get("shopId", shop.get("id", shop.get("uuid", "")))
        shop_name = shop.get("shopName", shop.get("name", "?"))
        print(f"  첫 가게: {shop_name} (id={shop_id})")
    elif "--shop" in sys.argv:
        idx = sys.argv.index("--shop")
        shop_id = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if not shop_id:
            print("사용법: python baemin_reviews.py --shop <shopId>")
            return
    else:
        # 캡처된 리뷰 API에서 shopId 추출 시도
        if CAPTURED_FILE.exists():
            with open(CAPTURED_FILE, encoding="utf-8") as f:
                data = json.load(f)
            review_api = data.get("review_api", {})
            url = review_api.get("url", "")
            import re
            m = re.search(r"shopId=(\w+)", url)
            if m:
                shop_id = m.group(1)
                print(f"\n[캡처된 shopId] {shop_id}")
            else:
                print("\n사용법:")
                print("  python baemin_reviews.py --shop <shopId>")
                print("  python baemin_reviews.py --search '치킨'")
                print("\n캡처된 헤더로 앱에서 열었던 가게 리뷰를 자동 수집합니다.")
                return
        else:
            print("\n사용법:")
            print("  1. mitmweb -s baemin_mitm_capture.py")
            print("  2. 배민 앱에서 리뷰 페이지 열기")
            print("  3. python baemin_reviews.py")
            return

    max_pages = 5
    if "--pages" in sys.argv:
        idx = sys.argv.index("--pages")
        max_pages = int(sys.argv[idx + 1])

    print(f"\n[리뷰 크롤링] shopId={shop_id} (최대 {max_pages}페이지)")
    reviews = crawl_reviews(shop_id, headers, max_pages)

    if reviews:
        out = f"baemin_reviews_{shop_id}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(reviews, f, ensure_ascii=False, indent=2)
        print(f"\n[✓] {out} 저장 완료 ({len(reviews)}건)")
    else:
        print("\n[!] 리뷰 수집 실패")
        print("    → mitmproxy로 캡처한 인증 헤더가 필요합니다")
        print("    → baemin_mitm_capture.py 참조")


if __name__ == "__main__":
    main()
