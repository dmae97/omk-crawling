"""07 — curl_cffi: 브라우저 TLS/JA3 핑거프린트로 fetch (curl-impersonate 파이썬 바인딩).

TLS 핑거프린트 차단(403)을 브라우저 없이 통과. 받은 HTML은 crawl4ai/scrapling에 넘겨 구조화.
    pip install curl_cffi
"""
from curl_cffi import requests


def main() -> None:
    # 최신 Chrome 핑거프린트로 요청 (impersonate 값은 curl_cffi 버전 따라 최신으로)
    r = requests.get("https://quotes.toscrape.com/", impersonate="chrome124", timeout=30)
    print("status:", r.status_code, "| bytes:", len(r.content))

    # 세션 + 프록시 + 위장 유지 (프록시는 예시)
    with requests.Session(impersonate="chrome124") as s:
        # s.proxies = {"https": "http://user:pass@host:8080"}
        html = s.get("https://quotes.toscrape.com/page/2/", timeout=30).text
        print("page-2 bytes:", len(html))
        # 이후: crawl4ai arun("raw:" + html) 또는 scrapling Selector(html) 로 구조화


if __name__ == "__main__":
    main()
