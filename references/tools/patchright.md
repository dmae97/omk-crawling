# patchright — 패치된 undetected Playwright (드롭인)

- Repo: <https://github.com/MindsightsAI/patchright>
- PyPI `patchright` ≥1.55 · Python ≥3.8 · **Apache-2.0**

## 언제

기존 Playwright 코드/멘탈 모델을 **그대로** 유지하면서 탐지 표멧���
지우고 싶을 때. Akamai급 벤더가 찾는 자동화 아티팩트(isolated-world 누수,
CDC 변수, `Runtime.enable` CDP 텔)를 드라이버 레벨에서 패치해 둔 드롭인
Playwright다. crawl4ai(vanilla Playwright)가 잡히기 시작할 때의 다음
계단이며, camoufox/nodriver까지는 과할 때의 중간 강도다.

최강 스텔스가 필요하면 `camoufox`(엔진 레벨)나 `nodriver`(CDP 네이티브).

## 설치

```bash
pip install patchright
patchright install chromium      # 패치된 Chromium 다운로드
```

## 최소 예제

```python
from patchright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://example.com", wait_until="domcontentloaded")
    html = page.content()
    browser.close()
```

## omk_crawl 어댑터

```python
from omk_crawl import crawl

# 에스컬레이션 체인 ⑤번. 컨텍스트는 사이트별 지문 프로필과 일관된다:
# user_agent/locale/timezone/viewport를 fingerprint.profile_for(url)에서 가져온다.
r = crawl("https://example.com", tool="patchright", fingerprint="chrome-mac")
```

kwargs: `timeout`(초, 기본 30) · `headless`(기본 True) · `proxy` ·
`wait_ms`(기본 1200) · `fingerprint`(프로필 이름, 미지정 시 사이트별 자동).

## 함정

- 바이너리가 별도(`patchright install chromium`).
- Playwright API와 거의 동일하지만 100% 호환은 아니다 — 패치가 일부
  저수준 CDP 호출의 동작을 바꾼다.
- 지문 프로필을 수동으로 섞지 마라(P7): 어댑터가 주는 일관 컨텍스트를 쓰는
  것이 탐지 회피의 핵심이다.
