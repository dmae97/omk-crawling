"""05 — markitdown: 파일(여기선 HTML)을 Markdown으로.

실제로는 PDF/docx/pptx/xlsx/이미지/오디오도 동일하게 .convert(path) 로 처리한다.
    pip install 'markitdown[all]'
"""
import tempfile, os
from markitdown import MarkItDown

SAMPLE_HTML = """<html><body>
<h1>분기 보고서</h1><p>매출이 <b>12%</b> 증가.</p>
<table><tr><th>지역</th><th>매출</th></tr><tr><td>APAC</td><td>4.2M</td></tr></table>
</body></html>"""


def main() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(SAMPLE_HTML)
        path = f.name
    try:
        md = MarkItDown().convert(path)      # PDF면 convert("report.pdf")
        print(md.text_content)               # 제목/표 구조가 Markdown으로 보존됨
    finally:
        os.unlink(path)


if __name__ == "__main__":
    main()
