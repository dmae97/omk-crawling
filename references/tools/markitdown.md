# markitdown — 파일을 Markdown으로 (LLM 입력용)

- Repo: https://github.com/microsoft/markitdown
- PyPI `markitdown` v0.1.6 · Python ≥3.10 · **MIT**

## 언제

**웹페이지가 아니라 파일**을 LLM에 넣을 텍스트로 바꿀 때. 지원: PDF, Word(docx), PowerPoint(pptx),
Excel(xlsx), 이미지(EXIF/OCR), 오디오(전사), HTML, CSV/JSON/XML, ZIP(내부 순회), YouTube URL 등.
표·제목·리스트·링크 등 **구조를 보존**해 Markdown으로. 크롤러가 받은 첨부/문서를 색인용으로 정규화할 때 핵심.

> 웹 크롤은 crawl4ai/scrapy가, "이미 가진 파일 → MD"는 markitdown이 담당. 딥크롤로 모은 PDF/PPTX를
> markitdown으로 통일해 RAG에 넣는 조합이 흔하다.

## 설치

```bash
pip install 'markitdown[all]'            # 모든 포맷. 개별: 'markitdown[pdf,docx,pptx]'
```

## CLI

```bash
markitdown report.pdf > report.md        # stdout 리다이렉트
markitdown report.pdf -o report.md       # -o 출력 파일
cat report.pdf | markitdown              # stdin 파이프
```

## Python

```python
from markitdown import MarkItDown
md = MarkItDown()                         # enable_plugins=True 로 플러그인 사용 가능
result = md.convert("deck.pptx")
print(result.text_content)                # Markdown 문자열
```

- 좁은 진입점 권장: 신뢰 불가 입력엔 `convert_local()` / `convert_stream()` 등 필요한 것만.
- LLM 이미지 설명이 필요하면 `MarkItDown(llm_client=..., llm_model=...)`로 이미지 캡션 생성.

## MCP

`markitdown-mcp` 패키지로 MCP 서버 제공 → 다른 에이전트에서 "파일→MD" 도구로 호출 가능.

## 함정

- **보안**: 현재 프로세스 권한으로 I/O(open()/requests.get()와 동일). 신뢰 불가 환경에선 입력 정화 +
  가장 좁은 `convert_*`만 + 격리 실행. (README 보안 경고 준수)
- OCR/전사/일부 포맷은 추가 의존성/모델이 필요 → `[all]` 또는 개별 extra 설치.
- 고충실도 "사람용" 변환기가 아니라 **텍스트 분석/LLM 입력용**임을 전제.
