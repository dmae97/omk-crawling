"""
네이버 카페 공개 게시글 수집 (로그인 불필요)
- 공개 카페만. 비공개 카페는 로그인 필요 → 이 스크립트 범위 밖
- 네이버는 안티봇 강함 → curl_cffi(브라우저 TLS) 또는 playwright 사용
- robots.txt·ToS 확인 필수
"""

from curl_cffi import requests
from bs4 import BeautifulSoup
import json, sys, time, re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def get_cafe_articles(club_id: str, page: int = 1):
    """카페 게시글 목록 (공개 카페)"""
    url = f"https://cafe.naver.com/ArticleList.nhn"
    params = {
        "search.clubid": club_id,
        "search.menuid": "",  # 전체 게시판
        "userDisplay": 20,
        "page": page,
    }
    r = requests.get(url, params=params, headers=HEADERS, impersonate="chrome124")
    r.raise_for_status()
    return r.text


def parse_article_list(html: str) -> list[dict]:
    """게시글 목록 파싱"""
    soup = BeautifulSoup(html, "html.parser")
    articles = []

    # 네이버 카페 게시글 테이블
    for row in soup.select("table.list-table tbody tr"):
        cells = row.select("td")
        if len(cells) < 4:
            continue

        title_el = row.select_one(".article a")
        if not title_el:
            title_el = row.select_one("td.title a")
        if not title_el:
            continue

        title = title_el.get_text(strip=True)
        href = title_el.get("href", "")
        author = row.select_one(".td_author, .author")
        date = row.select_one(".td_date, .date")

        # articleid 추출
        m = re.search(r"articleid=(\d+)", href)
        article_id = m.group(1) if m else ""

        articles.append({
            "title": title,
            "article_id": article_id,
            "author": author.get_text(strip=True) if author else "",
            "date": date.get_text(strip=True) if date else "",
            "url": f"https://cafe.naver.com{href}" if href.startswith("/") else href,
        })

    return articles


def get_article_content(club_id: str, article_id: str) -> str:
    """개별 게시글 본문"""
    url = f"https://cafe.naver.com/ArticleRead.nhn"
    params = {"clubid": club_id, "articleid": article_id}
    r = requests.get(url, params=params, headers=HEADERS, impersonate="chrome124")
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    # 본문 영역
    content_el = soup.select_one("#tbody, .se-main-container, .article_content")
    if content_el:
        return content_el.get_text(strip=True)
    return ""


def main():
    # 예시: 공개 카페 ID (네이버 카페 URL에서 clubid 확인)
    # https://cafe.naver.com/XXXX → 소스보기에서 clubid 추출
    club_id = sys.argv[1] if len(sys.argv) > 1 else ""

    if not club_id:
        print("사용법: python naver_cafe_public.py <clubid> [pages]")
        print("  clubid: 네이버 카페 URL 또는 페이지 소스에서 확인")
        print("  예: python naver_cafe_public.py 12345678 3")
        print()
        print("※ 비공개 카페는 로그인 필요 → 이 스크립트로 불가")
        print("※ 네이버 ToS·robots.txt 확인 후 사용")
        return

    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    all_articles = []

    for page in range(1, max_pages + 1):
        print(f"[페이지 {page}/{max_pages}] 수집 중...")
        try:
            html = get_cafe_articles(club_id, page)
            articles = parse_article_list(html)

            if not articles:
                print("  게시글 없음 (비공개 카페거나 구조 변경)")
                # 디버그: HTML 일부 출력
                if "로그인" in html or "login" in html.lower():
                    print("  → 로그인 요구됨. 비공개 카페입니다.")
                break

            all_articles.extend(articles)
            print(f"  {len(articles)}건 수집")

            for a in articles[:5]:
                print(f"    - {a['title'][:60]} ({a['date']})")

        except Exception as e:
            print(f"  오류: {e}")
            break

        if page < max_pages:
            time.sleep(1.5)  # 매너 딜레이

    if all_articles:
        out = f"naver_cafe_{club_id}.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(all_articles, f, ensure_ascii=False, indent=2)
        print(f"\n→ {out} 저장 완료 ({len(all_articles)}건)")

        # 본문 수집 (옵션)
        print("\n본문도 수집하려면: --with-content 플래그 추가")
        if "--with-content" in sys.argv:
            for a in all_articles[:20]:  # 처음 20건만
                if a["article_id"]:
                    print(f"  본문: {a['title'][:40]}...")
                    a["content"] = get_article_content(club_id, a["article_id"])
                    time.sleep(1)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(all_articles, f, ensure_ascii=False, indent=2)
            print(f"  → 본문 포함 재저장 완료")


if __name__ == "__main__":
    main()
