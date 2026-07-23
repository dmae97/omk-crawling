# curl-impersonate — 브라우저 TLS/JA3·HTTP2 핑거프린트 위장

- Repo: https://github.com/lwthiker/curl-impersonate (C 도구) · **MIT**
- Python 바인딩: `curl_cffi` (PyPI v0.15.0, Python ≥3.10, MIT) — 실무에선 이걸 가장 많이 씀
- 위장 가능: Chrome, Edge, Safari, Firefox (버전별 타깃)

## 언제

접근 자격은 있는데 서버가 **TLS Client Hello / JA3 / HTTP2 핑거프린트**로 비브라우저 클라이언트를
차별해 즉시 403/빈 응답을 줄 때. 일반 `requests`/`curl`은 핑거프린트가 브라우저와 달라 막힌다.
curl-impersonate는 핸드셰이크를 **실제 브라우저와 동일**하게 만들어 통과시킨다.

**브라우저 렌더가 필요 없다** — JS 실행 없이 순수 HTTP로 가장 가볍게 벽을 넘는 1순위 도구.
JS 렌더·Cloudflare Turnstile 상호작용까지 필요하면 scrapling(스텔스 브라우저)로 에스컬레이션.

## 설치

```bash
# Python 바인딩 (권장)
pip install curl_cffi

# 네이티브 CLI: 배포판 패키지 / GitHub 릴리스 프리빌드 / docker
docker pull lwthiker/curl-impersonate:0.6-chrome    # 예: Chrome 빌드
```

## 사용 — Python (curl_cffi)

```python
from curl_cffi import requests

# 최신 Chrome 핑거프린트로 요청
r = requests.get("https://example.com", impersonate="chrome124")
print(r.status_code, len(r.text))

# 세션 + 프록시 + 위장 유지
s = requests.Session(impersonate="chrome124")
s.proxies = {"https": "http://user:pass@host:8080"}
html = s.get("https://example.com/list").text
```

받은 `html`은 crawl4ai `arun("raw:<html>")` 또는 scrapling `Selector(html)`로 넘겨 구조화한다.

## 사용 — 네이티브 CLI (래퍼 스크립트)

```bash
curl_chrome116 https://example.com        # Chrome 116 위장 (버전별 curl_chromeNN / curl_ffNN)
curl_ff117 https://example.com            # Firefox 117 위장
```

래퍼는 curl-impersonate 바이너리에 브라우저별 헤더·TLS 옵션을 미리 채워 호출한다.

## 함정

- 핑거프린트 위장은 **정당한 접근의 클라이언트 차별을 우회**할 때만. 페이월/인증 우회 아님.
- 브라우저 버전 타깃은 시간이 지나면 낡음 → `curl_cffi`의 최신 `impersonate` 값 사용.
- JS로 생성되는 콘텐츠는 잡지 못함(HTTP만) → 렌더 필요 시 브라우저 계열로.
