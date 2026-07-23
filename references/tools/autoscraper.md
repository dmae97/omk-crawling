# autoscraper — 예시로 추출 규칙을 학습

- Repo: https://github.com/alirezamika/autoscraper
- PyPI `autoscraper` v1.1.14 · Python ≥3.6 · **MIT**

## 언제

셀렉터를 손으로 쓰기 싫고, **원하는 값 몇 개를 예시로** 주면 나머지 유사 데이터를 알아서 뽑고 싶을 때.
브라우저 불필요, 초경량·초고속, 학습한 규칙을 저장해 **다른 유사 페이지에 재적용**. 정적 HTML의 반복
데이터(목록·가격·Q&A 등)에 최적. 동적(JS)·복잡 상호작용 페이지엔 약함 → 그때는 crawl4ai/scrapling.

## 설치

```bash
pip install autoscraper
```

## 최소 예제 — 유사 결과

```python
from autoscraper import AutoScraper

url = "https://stackoverflow.com/questions/2081586/web-scraping-with-python"
wanted_list = ["How to change the user agent"]   # 페이지에서 원하는 값의 "예시"

scraper = AutoScraper()
scraper.build(url, wanted_list)                  # 규칙 학습
# 같은 구조의 다른 페이지에 재적용:
print(scraper.get_result_similar("https://stackoverflow.com/questions/606191"))
```

## 정확 매칭 + 여러 필드

```python
url = "https://finance.yahoo.com/quote/AAPL"
wanted_list = ["150.00"]                          # 예: 가격
scraper.build(url, wanted_list)
# wanted_list에 여러 예시를 넣으면 순서대로 여러 필드를 정확 추출:
scraper.get_result_exact("https://finance.yahoo.com/quote/MSFT")
```

## 저장·재사용 / 프록시

```python
scraper.save("yahoo-price")                       # 학습 모델 저장
scraper.load("yahoo-price")                        # 재사용
# 요청 옵션(프록시·헤더)은 build/get_result에 request_args로:
scraper.build(url, wanted_list, request_args=dict(proxies={"http": "http://p:8080"}))
```

## 함정

- 페이지 구조가 크게 바뀌면 규칙이 깨짐 → 재학습(`build`) 필요.
- 값이 여러 곳에 중복되면 원치 않는 매치가 섞일 수 있음 → `get_result_exact` + 더 구체적 예시로 좁힘.
- JS로 렌더되는 값은 원본 HTML에 없어 못 잡음 → crawl4ai/scrapling으로 렌더 후 HTML을 넘겨 처리.
