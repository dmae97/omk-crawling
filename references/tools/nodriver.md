# nodriver — CDP 네이티브 Chrome (undetected-chromedriver 후속)

- Repo: <https://github.com/ultrafunkamsterdam/nodriver>
- PyPI `nodriver` ≥0.48 · Python ≥3.9 · **AGPL-3.0**

## 언제

**DataDome, Kasada, PerimeterX/HUMAN** 같은 행동 기반 벤더 앞에서.
이들은 Playwright/Puppeteer의 자동화 아티팩트(CDC 변수, `Runtime.enable`,
isolated-world 누수, `navigator.webdriver`)를 찾는다. nodriver는 WebDriver
없이 CDP로 직결하므로 그 표면 자체가 없다.

Cloudflare 일반 케이스는 `camoufox`가 더 관리하기 쉽고, 단순 JS 렌더는
`crawl4ai`가 더 싸다. 라우팅 테이블상 DataDome/Kasada/PerimeterX 탐지 시
**1순위**다.

## 설치

```bash
pip install nodriver
# 시스템 Chrome/Chromium 필요. websockets ≥12 필요(구버전이면 ImportError).
```

## 최소 예제

```python
import nodriver as nd

async def main():
    browser = await nd.start(headless=True)
    page = await browser.get("https://example.com")
    await page.wait(2)
    html = await page.get_content()
    browser.stop()
    return html
```

## omk_crawl 어댑터

```python
from omk_crawl import crawl, crawl_async

r = await crawl_async("https://example.com", tool="nodriver")
# sync 컨텍스트에서는 crawl(...)이 asyncio.run으로 감싼다.
# async 루프 안에서 tool="nodriver"로 sync fetch를 부륩���
# ERROR("async-native … use fetch_async")를 명시적으로 반환한다 (fail-closed).
```

kwargs: `timeout`(초, 기본 30) · `headless`(기본 True) · `proxy` ·
`wait_s`(렌더 대기, 기본 2.0).

## 함정

- async 네이티브: 실행 중인 이벤트 루프 안에서 sync `fetch()`를 부륩���
  명시적 에러를 반환한다 — `crawl_async`를 써라.
- 프록시는 `--proxy-server=` 브라우저 인자로 전달한다(인증 프록시는
  별도 확장이 필요할 수 있음).
- `websockets` 구버전(<12) 환경에서는 import가 깨진다 — 업그레이드 필요.
- AGPL-3.0 — 배포 시 라이선스 검토. 남의 서버에 서비스로 얹는 경우 특히.
