# `crwl` CLI 레퍼런스

`pip install crawl4ai` 후 `crwl` 명령이 생긴다. 코드 없이 크롤/추출.

## 기본형

```bash
crwl <URL> [옵션]
```

출력은 기본 stdout. 파일 저장은 `-O 파일명`.

## 출력 포맷 (`-o` / `--output`)

유효한 값은 `all` · `json` · `markdown` · `md` · `markdown-fit` · `md-fit` **뿐**이며 기본값은 `all`이다.

```bash
crwl https://example.com -o markdown       # 깨끗한 마크다운
crwl https://example.com -o markdown-fit   # fit-markdown (노이즈 제거, 필터 없으면 기본 필터)
crwl https://example.com -o json           # 구조화(추출 전략 지정 시)
crwl https://example.com                    # 기본: all (가능한 모든 산출물)
```

## 딥크롤

```bash
crwl https://docs.example.com --deep-crawl bfs  --max-pages 20
crwl https://docs.example.com --deep-crawl dfs  --max-pages 20
crwl https://docs.example.com --deep-crawl best-first --max-pages 20
```

## LLM 추출 / 질문

```bash
# 자연어 질문 → LLM이 답 추출 (프로바이더 키 필요, 예: OPENAI_API_KEY)
crwl https://example.com/pricing -q "모든 요금제와 가격을 표로"

# 스키마 파일로 구조화 추출
crwl https://example.com -e extract.json -s schema.json -o json
```

## 브라우저/크롤러 설정

```bash
crwl https://example.com -b "headless=true,viewport_width=1280"          # BrowserConfig 파라미터
crwl https://example.com -c "cache_mode=BYPASS,word_count_threshold=10"   # CrawlerRunConfig 파라미터
crwl https://example.com -c "wait_for=css:.content-loaded"                # 특정 요소까지 로드 대기
crwl https://example.com -o markdown -O out.md -v                        # 파일 저장 + verbose
```

인라인 `key=value`는 `-b`(브라우저)·`-c`(크롤러), 파일은 `-B browser.yml -C crawler.yml`,
콘텐츠 필터는 `-f filter.yml`(fit-markdown 튜닝)로 준다.

## 첫 실행/진단

```bash
crawl4ai-setup      # Playwright 브라우저 설치
crawl4ai-doctor     # 환경 점검
crwl --help         # 전체 옵션
```

## 패턴

- 확실치 않으면 `-o markdown`부터. 잡음이 많으면 `-o markdown-fit`.
- 대량 페이지는 `--deep-crawl bfs --max-pages N`으로 상한을 **항상** 건다.
- LLM 질문(`-q`)은 비용 발생 — 반복 구조면 스키마(`-s`)로 대체.
- 결과를 파일로 뽑아 읽고, 필요 없으면 정리(cleanup)한다.
