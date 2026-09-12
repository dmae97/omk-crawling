# camoufox — 안티디텍트 Firefox (C++ 레벨 지문 주입)

- Repo: <https://github.com/daijro/camoufox> · Docs: <https://camoufox.com>
- PyPI `camoufox[geoip]` ≥0.4 · Python ≥3.9 · **MPL-2.0**

## 언제

vanilla Playwright(crawl4ai)가 탐지되는 **Cloudflare급 방어** 앞에서,
브라우저 자체가 지문을 위장해야 할 때. Playwright를 JS에서 숨기고,
지문을 실제 트래픽 통계에서 생성하며(엔진 레벨 주입), `humanize`로
입력을 사람처럼 흩뿌리고, `geoip`으로 출구 지역을 프록시에 맞춘다.

DataDome/Kasada/PerimeterX 같은 행동 기반 벤더는 `nodriver`(CDP 네이티브)가
더 강하다. 단일 차단 URL은 `insane-search`가 더 가볍다.

## 설치

```bash
pip install 'camoufox[geoip]'
camoufox fetch            # 브라우저 바이너리 + geoip DB 다운로드
```

## 최소 예제

```python
from camoufox.sync_api import Camoufox

with Camoufox(headless=True, humanize=True, geoip=True) as browser:
    page = browser.new_page()
    page.goto("https://example.com", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    html = page.content()
```

## omk_crawl 어댑터

```python
from omk_crawl import crawl

# 자동 에스컬레이션: scrapling 다음 ④번으로 시도된다.
r = crawl("https://example.com", tool="camoufox", proxy="http://user:pass@host:port")
```

kwargs: `timeout`(초, 기본 30) · `headless`(기본 True) · `proxy` ·
`humanize`(기본 True) · `geoip`(proxy 있을 때 기본 True) · `wait_ms`(기본 1500).

## 함정

- 바이너리가 별도(`camoufox fetch`) — 미다운로드 시 런타임 오류.
- 무겁다: 메모리 수백 MB, 기동 수 초. 가벼운 건 `curl_cffi`가 먼저.
- UA를 수동으로 덮지 마라 — Camoufox의 존재 이유가 자체 지문 일관성이다
  (수동 덮어쓰기는 오히려 불일치를 만든다, arXiv:2606.30119).
- async는 `camoufox.async_api.AsyncCamoufox` (`fetch_async`가 사용).
