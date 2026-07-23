# `omk-crawl` CLI 레퍼런스

`pip install omk-crawl` 후 `omk-crawl` 명령 사용.

## 기본형

```bash
omk-crawl <URL> [옵션]
```

## 옵션

| 플래그 | 설명 |
|---|---|
| `--tool`, `-t` | 특정 툴 강제 (auto-escalation 건너뜀) |
| `--output`, `-o` | 결과 파일 저장 |
| `--json`, `-j` | JSON 출력 |
| `--verbose`, `-v` | 에스컬레이션 로그 |
| `--no-robots` | robots.txt 체크 건너뜀 (책임지고 사용) |
| `--min-delay N` | 같은 도메인 요청 간 최소 대기 초 (기본: 0.5) |
| `--diagnose` | 드라이런: 어떤 툴을 시도할지 표시 |
| `--tools` | 설치/미설치 툴 목록 |
| `--version` | 버전 표시 |

## 예시

```bash
omk-crawl https://example.com                    # auto-escalate
omk-crawl https://example.com --tool curl_cffi   # force specific tool
omk-crawl https://example.com -o out.md          # save markdown to file
omk-crawl https://example.com --json             # JSON output
omk-crawl https://example.com -v                 # verbose escalation log
omk-crawl https://example.com --no-robots         # skip robots.txt check
omk-crawl https://example.com --min-delay 2.0     # 2s between same-domain requests
omk-crawl --diagnose https://example.com         # dry-run: what would we try?
omk-crawl --tools                                # list installed/missing tools
omk-crawl report.pdf                             # file → markdown (markitdown)
```
