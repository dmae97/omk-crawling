# Docker 서버 · REST API · MCP 연결

Python 설치 없이 격리 실행하거나, 다른 에이전트(Claude Code/OMK)에서 **MCP 도구**로 호출하고 싶을 때.

## 서버 띄우기

```bash
docker pull unclecode/crawl4ai:latest
docker run -d -p 11235:11235 --name crawl4ai --shm-size=1g unclecode/crawl4ai:latest

# 대시보드   http://localhost:11235/dashboard   (실시간 시스템/브라우저 풀 메트릭)
# 플레이그라운드 http://localhost:11235/playground (요청을 만들고 코드 생성)
```

LLM 추출을 서버에서 쓰려면 프로바이더 키를 환경변수로 주입:

```bash
docker run -d -p 11235:11235 --shm-size=1g \
  -e OPENAI_API_KEY=sk-... \
  --name crawl4ai unclecode/crawl4ai:latest
```

## REST API

```python
import requests

r = requests.post("http://localhost:11235/crawl",
                  json={"urls": ["https://example.com"], "priority": 10})
data = r.json()          # {"results": [...]} 또는 비동기면 {"task_id": ...}
```

주요 엔드포인트(서버 버전에 따라): `/crawl`(크롤 잡), `/md`(마크다운), `/html`,
`/screenshot`, `/pdf`, `/execute_js`, `/task/{id}`(결과 폴링), `/monitor/health`.
정확한 스키마는 `http://localhost:11235/schema` 또는 대시보드에서 확인.

> 보안: `/crawl`과 hooks는 신뢰된 입력에만. 과거 역직렬화 RCE/LFI 이력 → v0.8.5+ 사용,
> hooks 기본 off(`CRAWL4AI_HOOKS_ENABLED=false`), API에서 `file://` 차단. 공개 포트에 그대로 노출 금지.

## MCP 연결 (Claude Code / OMK)

crawl4ai 서버는 MCP 엔드포인트를 노출한다:

- SSE:        `http://localhost:11235/mcp/sse`
- WebSocket:  `http://localhost:11235/mcp/ws`
- 스키마 확인: `http://localhost:11235/mcp/schema`

노출 도구(대표): `md`(마크다운), `html`, `screenshot`, `pdf`, `execute_js`, `crawl`, `ask`.
정확한 목록은 항상 `/mcp/schema`로 검증한다.

### Claude Code에 추가

```bash
claude mcp add --transport sse c4ai-sse http://localhost:11235/mcp/sse
claude mcp list          # 연결 확인
```

### OMK MCP 레지스트리에 추가

OMK 런타임은 다수 MCP 서버를 config로 관리한다(예: chrome-devtools, playwright, firecrawl 등).
crawl4ai도 같은 방식으로 SSE 서버 항목을 추가하면 된다. 예시 항목:

```jsonc
// mcpServers 에 추가
"crawl4ai": {
  "transport": "sse",
  "url": "http://localhost:11235/mcp/sse"
}
```

추가 후 세션을 재시작하고 `/mcp/schema`로 도구가 보이는지 확인한다. 스킬(문서)만으로 충분하면
서버·MCP 등록은 선택이다 — 로컬 `pip` + CLI/코드 경로만으로도 모든 기능을 쓸 수 있다.

## 언제 서버/MCP를 쓰나

| 상황 | 권장 |
|------|------|
| 일회성/스크립트/노트북 | 로컬 `pip install crawl4ai` + 코드/CLI |
| 여러 에이전트·툴이 공유하는 크롤 백엔드 | Docker 서버 + REST |
| Claude Code/OMK에서 도구로 직접 호출 | Docker 서버 + **MCP(SSE)** |
| CI/격리·재현성 | Docker |
