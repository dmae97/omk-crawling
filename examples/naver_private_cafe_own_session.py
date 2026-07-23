#!/usr/bin/env python3
"""비공개 네이버 카페 크롤링 — 본인 세션 사용 (합법).

전제: 본인이 해당 카페의 *회원*이어야 합니다. 회원이라면 브라우저 로그인
세션을 그대로 주입해 권한 있는 게시글을 자동 수집합니다.

쿠키 추출 방법 (본인 브라우저에서):
  1. Chrome 확장 "Cookie-Editor" 또는 "EditThisCookie" 설치
  2. cafe.naver.com 에서 로그인
  3. 확장 → Export → JSON → my_cookies.json 으로 저장
  (또는 curl용 Netscape cookies.txt)

이 스크립트는 자격증명을 위조·우회하지 않습니다. 회원이 아닌 비공개 카페는
접근이 거부되며, 그것이 올바른 결과입니다.
"""

import sys
from pathlib import Path

from omk_crawl.cookies import CookieManager
from omk_crawl.naver import NaverCafeClient, NaverConfig


def main(cookie_file: str, club_id: str, max_pages: int = 3) -> None:
    # 1) 내 브라우저 세션 로드
    mgr = CookieManager.from_file(cookie_file)
    print(f"쿠키 로드: {mgr.report()}")
    if len(mgr) == 0:
        print("유효 쿠키 없음 — 만료되었거나 파일 오류")
        return

    # 2) 클라이언트에 내 세션 주입
    cafe = NaverCafeClient(NaverConfig(rate=1.0, cache_dir="/tmp/cafe_private_cache"))
    cafe.set_cookie_manager(mgr)

    # 3) 로그인 상태 검증
    login = cafe.check_login()
    print(f"\n로그인 검증: ok={login.ok}")
    if not login.ok:
        print(f"  → {login.error}")
        print("  세션이 만료되었습니다. 브라우저에서 다시 로그인 후 쿠키를 재추출하세요.")
        return

    # 4) 해당 카페 접근 권한 확인 (회원인지)
    access = cafe.can_access(club_id)
    print(f"\n카페 {club_id} 접근 권한: ok={access.ok}")
    if not access.ok:
        print(f"  → {access.error}")
        print("  이 계정은 해당 카페 회원이 아닙니다. (우회 불가 — 가입이 먼저입니다)")
        return

    # 5) 권한 있는 게시글 수집
    print(f"\n=== 게시글 수집 (최대 {max_pages}페이지) ===")
    result = cafe.crawl_articles(club_id, max_pages=max_pages, page_size=20)
    if result.ok and result.data:
        items = cafe.normalize_articles(result.data)
        print(f"수집 완료: {len(items)}건")
        for it in items[:10]:
            print(f"  [{it['article_id']}] {it['subject'][:40]} | {it['author']} | 조회 {it['read_count']}")
    else:
        print(f"수집 실패: {result.error}")

    cafe.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python naver_private_cafe_own_session.py <cookies.json> <club_id> [max_pages]")
        sys.exit(1)
    pages = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    main(sys.argv[1], sys.argv[2], pages)
