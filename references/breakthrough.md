# 돌파(Breakthrough) 레이어 — fingerprint · behavior · warmup

> v2.12 신규. 목표: 접근 권한이 있는 콘텐츠에 대해 어떤 안티봇 스택 앞에서도
> 바이트를 확보한다. 승부처는 트릭 수가 아니라 **일관성·행동·세션 재사용**이다.

## 왜 (연구 근거)

| 연구 | 핵심 | 적용 |
| ------ | ------ | ------ |
| arXiv:2406.07647 (FP-Inconsistent) | 우회 봇은 **속성 간·시간축 불일치**에서 잡힌다 | fingerprint 일관성 + 사이트별 지문 고정 |
| arXiv:2606.30119 (멀티레이어 지문) | 어설픈 스텔스 패치는 **탐지를 높인다** | 엔진 레벨 위장(camoufox) 또는 CDP 네이티브(nodriver)만 |
| arXiv:2602.09606 (TLS/JA4) | 핸드셰이크만으로 bad-bot 검출 | impersonation과 토큰 리플레이의 정합성 |
| arXiv:2606.14525 (봇 탐지 분류) | 벤더별 기법 분류(2026) | KASADA/PERIMETERX/AWS_WAF 탐지 추가 |
| arXiv:2502.01608 (실사용자 지문) | 정적 크롤은 인증·상호작용 트리거를 놓친다 | 사람처럼 진입하는 웜업 플로우 |

## ① fingerprint.py — 크로스레이어 지문 일관성

```python
from omk_crawl import profile_for, coherence_issues, match_impersonate

profile = profile_for("https://example.com")   # 사이트별 결정적(시간축 일관성)
profile.headers()        # UA + Client Hints + Accept-Language (한 가지 이야기)
profile.curl_kwargs()    # {"impersonate": ..., "headers": ...} — TLS와 헤더 일치
profile.browser_context_kwargs()  # playwright 컨텍스트 (locale/tz/viewport)

# 임의 헤더 세트 감사 — 레이어가 서로 다른 이야기를 하면 잡아낸다
issues = coherence_issues(headers, impersonate="chrome124")

# 기존 impersonate 타깃에 맞는 일관 프로필
match_impersonate("firefox133")
```

감사 5-룰: ①Chromium이 아닌데 Client Hints 전송 ②UA OS ↔ Sec-CH-UA-Platform
불일치 ③UA Chrome 메이저 ↔ Client Hints 메이저 불일치 ④모바일 토큰 ↔
Sec-CH-UA-Mobile 불일치 ⑤TLS impersonate ↔ UA 패밀리/버전 불일치.

## ② behavior.py — 시드 결정적 인간 행동

```python
from omk_crawl import BehaviorClock

clock = BehaviorClock("example.com|session-1")
clock.think_time()              # 사고 정지 (lognormal ~1.1s, clamp 0.4–4.0)
clock.dwell_time(8000)          # 읽기 체류 (~180 chars/s + jitter)
clock.inter_request_delay()     # 요청 간 페이싱 (버스트 인간 케이던스)
clock.mouse_path((0, 0), (400, 300))   # Bezier + jitter 궤적
clock.scroll_plan(8000, 1080)          # 청크 스크롤 정지점들
```

같은 시드 → 같은 열(재현·테스트 가능). 난수형 스텔스의 시간축 불일치 방지.

## ③ warmup.py — 세션 웜업 & 클리어런스 재사용

한 번 통과(cf_clearance·datadome·_abck·aws-waf-token 수확) → 같은 지문으로
가볍게 N회 리플레이. 벤더의 토큰이 지문 신호에 바인딩되므로 TLS/헤더/쿠키가
반드시 일치해야 한다.

```python
from omk_crawl import warm_crawl, SessionWarmup

r = warm_crawl("https://protected.example.com/article")
# 1. 캐시된 유효 세션 재사용 (도메인별 TTL, 기본 30분)
# 2. 없으면 브라우저(nodriver→patchright→camoufox)로 루트에 사람처럼 진입
# 3. WarmSession.curl_kwargs()로 curl_cffi 리플레이 (TLS+헤더+쿠키 일치)
# 4. 차단 시 세션 폐기 → SmartRouter 전체 래더로 폴��

manager = SessionWarmup()          # ~/.cache/omk-crawl/warm-sessions.json (0600)
manager.record(url, cookies, tool="manual")   # 내 브라우저 세션 수동 주입도 가능
manager.get(url); manager.invalidate(url)
```

## 어댑터 선택 (라우팅 테이블 1순위)

| 탐지 | 1순위 | 이유 |
| ------ | ------- | ------ |
| Cloudflare / Akamai / Imperva / WAF | insane_search → camoufox | 브레이커 → 엔진 레벨 위장 |
| DataDome / Kasada / PerimeterX | nodriver | CDP 네이티브, 자동화 아티팩트無 |
| AWS WAF | camoufox | 토큰 챌린지, 안티디텍트 강함 |
| TLS fingerprint | insane_search → curl_cffi | 프로필 로테이션 |

## 가드레일 (헌법 P3)

- AUTH_REQUIRED(401/로그인)는 절대 우회하지 않는다 — 라우팅 테이블 `[]`.
- 웜업·스텔스·지문 일관성은 접근 자격이 있는 콘텐츠의 클라이언트 차별에 한정.
- 세션 쿠키는 로컬 0600 캐시, TTL 만료 자동 폐기, 같은 도메인에만 리플레이.
