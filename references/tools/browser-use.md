# browser-use — LLM 에이전트가 브라우저를 조작

- Repo: https://github.com/browser-use/browser-use · Docs: https://docs.browser-use.com
- PyPI `browser-use` v0.13.6 · Python ≥3.11 · **MIT**

## 언제

로그인·다단계 내비게이션·클릭·폼 입력·"사람처럼" 탐색이 필요한 작업. 고정 셀렉터로는 안 되는,
페이지 구조가 매번 달라지는 상호작용. **정형 대량 수집은 crawl4ai/scrapy/crawlee가 더 싸고 빠르다** —
browser-use는 LLM이 매 스텝 판단하므로 느리고 비결정적이며 API 비용이 든다.

## 설치

```bash
pip install browser-use
# LLM 키 필요: .env 에 OPENAI_API_KEY / ANTHROPIC_API_KEY / BROWSER_USE_API_KEY 등
# 브라우저(Chromium)는 Playwright로 자동 준비. 실패 시: playwright install chromium
```

## 최소 예제

```python
import asyncio
from browser_use import Agent, ChatBrowserUse   # 또는 ChatOpenAI, ChatAnthropic

async def main():
    agent = Agent(
        task="browser-use 레포의 스타 수를 찾아서 알려줘",
        llm=ChatBrowserUse(model="bu-2-0"),      # 또는 ChatOpenAI(model="gpt-4o")
    )
    history = await agent.run()
    print(history.final_result())

asyncio.run(main())
```

## 패턴

- **결과를 코드로 회수**: `history.final_result()`, `history.urls()`, `history.extracted_content()`.
- **로그인 재사용**: 브라우저 프로필/유저데이터 디렉토리를 지정해 세션을 남기고, 이후 대량 수집은
  crawl4ai/scrapy로 넘긴다(쿠키/스토리지 전달).
- **범위 제한**: `task`를 좁게, 스텝/시간 상한을 두어 무한 루프·비용 폭주를 막는다.
- **MCP/CLI**: `browser-use`는 CLI와 MCP(`com.browser-use/browser-use`)도 제공 — 다른 에이전트에서 도구로 호출 가능.

## 함정

- 비결정적: 같은 task도 실행마다 경로가 다를 수 있음 → 재현이 중요하면 산출 셀렉터를 뽑아 결정적 크롤러로 고정.
- 비용: 스텝마다 LLM 호출. 반복 작업엔 부적합 → 한 번 길을 찾은 뒤 crawl4ai/scrapy 스크립트로 대체.
- 권한: 로그인·결제·삭제 등 부작용 있는 조작은 사용자 승인 범위 안에서만.
