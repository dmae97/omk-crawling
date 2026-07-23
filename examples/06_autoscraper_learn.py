"""06 — autoscraper: 예시 몇 개로 추출 규칙을 학습하고 다른 페이지에 재적용.

브라우저 불필요. 정적 HTML 반복 데이터에 최적.
    pip install autoscraper
"""
from autoscraper import AutoScraper

TRAIN_URL = "https://quotes.toscrape.com/page/1/"
# 페이지에서 원하는 값의 "예시" 몇 개 (전부 나열할 필요 없음)
WANTED = [
    "“The world as we have created it is a process of our thinking.”",
    "Albert Einstein",
]


def main() -> None:
    scraper = AutoScraper()
    scraper.build(TRAIN_URL, wanted_list=WANTED)         # 규칙 학습
    print("train page matches:", len(scraper.get_result_similar(TRAIN_URL)))

    # 같은 구조의 다른 페이지에 규칙 재적용
    more = scraper.get_result_similar("https://quotes.toscrape.com/page/2/")
    print("page-2 matches:", len(more))
    for row in more[:5]:
        print(" -", row)

    scraper.save("quotes-model")                          # 재사용 위해 저장
    print("saved -> quotes-model")


if __name__ == "__main__":
    main()
