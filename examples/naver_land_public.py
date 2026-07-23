"""
네이버 부동산 공개 매물 수집 (로그인 불필요)
============================================
실제 API: new.land.naver.com/api/...
- complexes/single-markers/2.0 : 지역별 단지 마커 (이름, 면적, 가격, 세대수)
- cortars : 행정구역 정보
- 매물 상세(연락처 등)는 로그인 필요 → 수집 안 함

cortarNo (행정동 코드):
  강남구 역삼동 = 1168010100
  서초구 서초동 = 1165010100
  송파구 잠실동 = 1171010100
  전체: https://new.land.naver.com/api/cortars?cortarNo=1168010100

※ 네이버 ToS·robots.txt 확인 필수
※ Rate limit 있음: 요청 간 2초 이상 딜레이
"""

from curl_cffi import requests
import json, sys, time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://new.land.naver.com/",
    "Accept": "application/json",
}

BASE = "https://new.land.naver.com/api"

# 주요 행정동 코드
CORTAR_CODES = {
    "강남구 역삼동": "1168010100",
    "강남구 삼성동": "1168010400",
    "서초구 서초동": "1165010100",
    "송파구 잠실동": "1171010100",
    "마포구 망원동": "1144011400",
    "용산구 이태원동": "1117010800",
}


def get_complexes(cortar_no: str, zoom: int = 16) -> list[dict]:
    """지역별 단지(아파트/오피스텔) 마커 데이터"""
    url = f"{BASE}/complexes/single-markers/2.0"
    params = {
        "cortarNo": cortar_no,
        "zoom": zoom,
        "priceType": "RETAIL",  # RETAIL=매매, JEONSE=전세
    }
    r = requests.get(url, params=params, headers=HEADERS, impersonate="chrome124", timeout=10)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, dict) and not data.get("success", True):
        raise RuntimeError(f"API 오류: {data.get('code')} - {data.get('message')}")

    return data if isinstance(data, list) else data.get("result", data.get("data", []))


def get_cortar_info(cortar_no: str) -> dict:
    """행정구역 정보"""
    url = f"{BASE}/cortars"
    params = {"cortarNo": cortar_no}
    r = requests.get(url, params=params, headers=HEADERS, impersonate="chrome124", timeout=10)
    r.raise_for_status()
    return r.json()


def format_price(val) -> str:
    """가격 포맷 (만원 → 억/만)"""
    if not val:
        return "-"
    try:
        v = int(val)
        if v >= 10000:
            return f"{v // 10000}억 {v % 10000:,}만"
        return f"{v:,}만"
    except (ValueError, TypeError):
        return str(val)


def main():
    # 지역 선택
    if len(sys.argv) > 1:
        region = sys.argv[1]
        cortar_no = CORTAR_CODES.get(region, region)  # 직접 코드 입력도 가능
    else:
        print("사용법: python naver_land_public.py <지역명|cortarNo>")
        print("\n지원 지역:")
        for name, code in CORTAR_CODES.items():
            print(f"  {name} = {code}")
        print("\n예: python naver_land_public.py '강남구 역삼동'")
        print("    python naver_land_public.py 1168010100")
        return

    print(f"[네이버 부동산] {region} (cortarNo={cortar_no})")
    print("※ 공개 데이터만 수집. 연락처 등 로그인 필요 영역 제외.\n")

    try:
        complexes = get_complexes(cortar_no)

        if not complexes:
            print("  단지 데이터 없음.")
            return

        print(f"  {len(complexes)}개 단지 발견\n")
        print(f"  {'단지명':<20} {'유형':<6} {'준공':<8} {'세대':<6} {'면적(㎡)':<14} {'가격'}")
        print(f"  {'-'*20} {'-'*6} {'-'*8} {'-'*6} {'-'*14} {'-'*12}")

        for c in complexes[:30]:
            name = c.get("complexName", "?")
            rtype = c.get("realEstateTypeName", "?")
            year = c.get("completionYearMonth", "?")[:6] if c.get("completionYearMonth") else "?"
            households = c.get("totalHouseholdCount", "?")
            min_area = c.get("minArea", "?")
            max_area = c.get("maxArea", "?")
            price = c.get("tradePrice", c.get("price", ""))

            area_str = f"{min_area}~{max_area}" if min_area != max_area else str(min_area)
            price_str = format_price(price) if price else "-"

            print(f"  {name:<20} {rtype:<6} {year:<8} {households:<6} {area_str:<14} {price_str}")

        # JSON 저장
        out = f"naver_land_{cortar_no}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(complexes, f, ensure_ascii=False, indent=2)
        print(f"\n  → {out} 저장 완료 ({len(complexes)}건)")

        # 개별 단지 상세 (complexNo로 추가 조회 가능)
        if complexes and "--detail" in sys.argv:
            print("\n  [단지 상세 조회]")
            for c in complexes[:5]:
                cno = c.get("markerId", c.get("complexNo", ""))
                cname = c.get("complexName", "?")
                if cno:
                    time.sleep(2)  # rate limit
                    try:
                        detail_url = f"{BASE}/complexes/{cno}"
                        dr = requests.get(detail_url, headers=HEADERS, impersonate="chrome124", timeout=10)
                        if dr.status_code == 200:
                            d = dr.json()
                            print(f"    {cname}: {json.dumps(d, ensure_ascii=False)[:200]}")
                    except Exception as e:
                        print(f"    {cname}: {e}")

    except RuntimeError as e:
        print(f"  API 오류: {e}")
        if "TOO_MANY_REQUESTS" in str(e):
            print("  → Rate limit. 30초 후 재시도하세요.")
    except requests.exceptions.HTTPError as e:
        print(f"  HTTP 오류: {e}")
    except Exception as e:
        print(f"  오류: {e}")


if __name__ == "__main__":
    main()
