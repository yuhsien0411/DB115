"""產生提案 Word 與 PDF：proposal/proposal_題01.md（正文）＋ ER 圖 ＋ schema_proposal.sql（附錄）。

用法（在 project/ 執行）：python3 tools/build_proposal.py
輸出：題01_專題提案.docx、題01-C_專題提案.pdf（檔名依補充說明用組別）
版面依手改版：微軟正黑體、內文 10.5pt（規定 ≥ 10pt）、小節標題 12pt 藍色粗體、A4、邊界 0.5 吋；正文限一頁，附錄另起一頁。
"""
import os
import shutil
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "proposal")
DOCX = os.path.join(ROOT, "題01_專題提案.docx")
PDF_NAME = "題01-C_專題提案.pdf"

FONT = 'w:ascii="Microsoft JhengHei" w:hAnsi="Microsoft JhengHei" w:eastAsia="Microsoft JhengHei" w:cs="Microsoft JhengHei"'
CODE_FONT = 'w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Microsoft JhengHei" w:cs="Consolas"'
BODY_SZ, CODE_SZ, HEAD_SZ, TOP_SZ = 21, 20, 24, 28   # half-points：10.5、10、12、14 pt
BLUE = "4F81BD"
SECT = ('<w:pgSz w:w="11906" w:h="16838" />'
        '<w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720" w:header="300" w:footer="300" w:gutter="0" />')


def patch_styles(s):
    s = re.sub(r'<w:rFonts [^>]*/>', f'<w:rFonts {FONT} />', s)
    dd = re.search(r'<w:docDefaults>.*?</w:docDefaults>', s, re.S).group(0)
    dd2 = re.sub(r'<w:sz w:val="\d+" />', f'<w:sz w:val="{BODY_SZ}" />', dd)
    dd2 = re.sub(r'<w:szCs w:val="\d+" />', f'<w:szCs w:val="{BODY_SZ}" />', dd2)
    dd2 = re.sub(r'<w:spacing [^>]*/>', '<w:spacing w:after="0" w:line="216" w:lineRule="auto" />', dd2)
    s = s.replace(dd, dd2)

    def style(sid, fn):
        nonlocal s
        s = re.sub(rf'<w:style [^>]*w:styleId="{sid}".*?</w:style>', lambda m: fn(m.group(0)), s, count=1, flags=re.S)

    def heading(st):
        st = re.sub(r'<w:spacing [^>]*/>', '<w:spacing w:before="60" w:after="0" />', st)
        st = re.sub(r'<w:sz w:val="\d+"\s*/>', f'<w:sz w:val="{HEAD_SZ}" />', st)
        st = re.sub(r'<w:szCs w:val="\d+"\s*/>', f'<w:szCs w:val="{HEAD_SZ}" />', st)
        st = re.sub(r'<w:color [^>]*/>', f'<w:color w:val="{BLUE}" />', st)
        return st.replace('<w:i />', '')
    for sid in ("Heading1", "Heading2"):
        style(sid, heading)

    def para(st):  # 段落間距與預設一致（pandoc 預設段後 180）
        return re.sub(r'<w:spacing [^>]*/>', '<w:spacing w:before="0" w:after="0" />', st)
    for sid in ("BodyText", "FirstParagraph", "Compact"):
        style(sid, para)

    def code(st):
        st = re.sub(r'<w:rFonts [^>]*/>', f'<w:rFonts {CODE_FONT} />', st)
        st = re.sub(r'<w:sz w:val="\d+"\s*/>', f'<w:sz w:val="{CODE_SZ}" />', st)
        if '<w:sz ' not in st:
            st = st.replace('<w:rPr>', f'<w:rPr><w:sz w:val="{CODE_SZ}" />', 1)
        return st
    style("VerbatimChar", code)
    style("SourceCode", code)
    return s


def build_reference(tmp):
    src = os.path.join(tmp, "ref_default.docx")
    ref = os.path.join(tmp, "ref.docx")
    with open(src, "wb") as f:
        f.write(subprocess.check_output(["pandoc", "--print-default-data-file", "reference.docx"]))
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(ref, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/styles.xml":
                data = patch_styles(data.decode()).encode()
            zout.writestr(item, data)
    return ref


def build_source(tmp):
    md = open(os.path.join(P, "proposal_題01.md"), encoding="utf-8").read()
    body, rest = md.split("<!-- 以上為提案正文", 1)
    ai = rest.split("-->", 1)[1].split("<!-- 以下不輸出到提案 -->", 1)[0].strip()
    ai = ai.replace("**AI 使用說明**", "## 3. AI 使用說明")
    sql = open(os.path.join(P, "schema_proposal.sql"), encoding="utf-8").read().rstrip()
    appx = ("# 附錄\n\n## 1. ER 圖\n\n![](er_diagram.png){width=18cm}\n\n"
            "## 2. CREATE TABLE（各表上方為函數相依）\n\n```sql\n" + sql + "\n```\n\n" + ai + "\n")
    src = os.path.join(tmp, "proposal_src.md")
    open(src, "w", encoding="utf-8").write(body.rstrip() + "\n\n" + appx)
    return src


def patch_document(d):
    # 第一段（組別、組員）14pt
    first = re.search(r"<w:p[ >](?:(?!</w:p>).)*</w:p>", d, re.S).group(0)
    d = d.replace(first, re.sub(r"<w:rPr>", f'<w:rPr><w:sz w:val="{TOP_SZ}" /><w:szCs w:val="{TOP_SZ}" />', first), 1)

    def autoline(m):  # 圖片段落改回單行距置中，否則固定行高會裁圖
        para = m.group(0)
        if "<w:drawing>" not in para:
            return para
        sp = '<w:spacing w:before="40" w:after="40" w:line="240" w:lineRule="auto" /><w:jc w:val="center" />'
        if "<w:pPr>" in para:
            return para.replace("<w:pPr>", "<w:pPr>" + sp, 1)
        return re.sub(r"^<w:p( [^>]*)?>", lambda mm: mm.group(0) + "<w:pPr>" + sp + "</w:pPr>", para)
    d = re.sub(r"<w:p[ >](?:(?!</w:p>).)*</w:p>", autoline, d, flags=re.S)
    # 附錄標題從新頁開始（獨立的分頁段落在正文滿頁時會多出一張空白頁）
    d = re.sub(r'(<w:pStyle w:val="Heading1" />)((?:(?!</w:p>).)*?<w:t[^>]*>附錄</w:t>)',
               r'\1<w:pageBreakBefore />\2', d, count=1, flags=re.S)
    d = re.sub(r"<w:sectPr\s*/>", "", d)
    d = re.sub(r"<w:sectPr[ >].*?</w:sectPr>", "", d, flags=re.S)
    return d.replace("</w:body>", "<w:sectPr>" + SECT + "</w:sectPr></w:body>")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        ref = build_reference(tmp)
        src = build_source(tmp)
        raw = os.path.join(tmp, "raw.docx")
        subprocess.check_call(["pandoc", src, "-f", "markdown-implicit_figures", "-o", raw,
                               "--reference-doc", ref, "--resource-path", P, "--highlight-style", "tango"])
        with zipfile.ZipFile(raw) as zin, zipfile.ZipFile(DOCX, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/numbering.xml":
                    n = data.decode()
                    n = re.sub(r'<w:ind w:left="(\d+)" w:hanging="\d+"\s*/>',
                               lambda m: f'<w:ind w:left="{int(m.group(1)) // 2}" w:hanging="240" />', n)
                    data = n.encode()
                if item.filename == "word/document.xml":
                    data = patch_document(data.decode()).encode()
                zout.writestr(item, data)
        subprocess.check_call(["soffice", "--headless", "--convert-to", "pdf", "--outdir", tmp, DOCX],
                              stdout=subprocess.DEVNULL)
        pdf = os.path.join(ROOT, PDF_NAME)
        shutil.move(os.path.join(tmp, os.path.basename(DOCX)[:-5] + ".pdf"), pdf)
    pages = subprocess.check_output(["pdfinfo", pdf]).decode().split("Pages:")[1].split("\n")[0].strip()
    page1, page2 = (subprocess.check_output(["pdftotext", "-f", str(i), "-l", str(i), pdf, "-"]).decode()
                    for i in (1, 2))
    print(f"{DOCX}\n{pdf}：{pages} 頁")
    if "呂家緗 A11523037" not in page1 or not page2.lstrip().startswith("附錄"):
        print("警告：正文沒有剛好落在第 1 頁、或附錄沒有從第 2 頁開始，請縮短正文", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
