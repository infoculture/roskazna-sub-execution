"""Разбор HTML-версии отчётов (``Mes_<форма><период>_<код>.html``).

HTML-набор есть почти в каждом пакете, но для 2008–2011 гг. (а в части лет и
для отдельных форм) это **единственный** вариант отчётов по территориям. Файл
в CP1251; структура:

* ``<p>`` с наименованием территории, названием формы, «на ДД.ММ.ГГГГ» и
  «Единица измерения: руб.»;
* для каждого раздела — «Раздел: 1. Доходы» и таблица: первая строка ``<th>``
  с полными заголовками граф «Утвержденные бюджетные назначения: бюджет
  субъекта РФ», далее строки ``<td>``. Объединённых ячеек нет.

Внутренний номер формы в имени файла: 428 — 0503317, 430 — 0503320,
421 — 0503321, 423 — 0503323. Суффикс: ``m`` — месячный, ``y`` — годовой,
``vy`` / ``vm`` — приносящая доход деятельность (2008 г.).

Таблица превращается в «лист» (шапка + строка номеров граф + строки данных) и
разбирается тем же кодом, что и Excel (:func:`forms.parse_table`).
"""

from __future__ import annotations

import html as htmllib
import re
from datetime import date

from .dims import classify_measure
from .forms import ParsedReport, ReportHeader, parse_table, unit_multiplier
from .layout import CODE_RE, detect_layout
from .territory import decode_html
from .workbook import Sheet

# Mes_428m_01.html (с 2005 г.); Mes1.html (2000–2004 гг., номер формы не указан)
NAME_RE = re.compile(r"(?:^|/)Mes(?:_(?P<form>\d{3})(?P<kind>[a-z]+)_)?(?P<code>\d{1,3})\.html?$", re.I)
HTML_FORM_CODES = {"428": "0503317", "430": "0503320", "421": "0503321", "423": "0503323"}
TITLE_FORMS = [
    ("0503317", re.compile(r"отчет\s+об\s+исполнении\s+консолидированного\s+бюджета", re.I)),
    # «Отчет об исполнении сводной сметы доходов и расходов по приносящей доход деятельности» (2007–2008 гг.)
    ("0503317", re.compile(r"сводной\s+сметы\s+доходов\s+и\s+расходов", re.I)),
    ("0503320", re.compile(r"баланс\s+исполнения", re.I)),
    ("0503321", re.compile(r"финансовых\s+результатах", re.I)),
    ("0503323", re.compile(r"движении\s+денежных\s+средств", re.I)),
]
SECTION_TITLES = {
    "0503317": [("income", r"доход"), ("expense", r"расход"), ("sources", r"источник"),
                ("settlements", r"консолидируем")],
    "0503320": [("balance", r"баланс"), ("offbalance", r"справк|забаланс"), ("obligations", r"таблиц|обязательств")],
    "0503321": [("results_changes", r"изменени|таблиц"), ("results", r"финансов|результат")],
    "0503323": [("cash_outflows_detail", r"раздел.*подраздел"), ("cash_flows", r".")],
}

# «Раздел: 1. Доходы» (2005–2011 гг.) или «1. Доходы бюджета» (с 2012 г.)
_SECTION_RE = re.compile(r"<b>\s*(?:Раздел:\s*(?P<title>[^<]+?)|(?P<title2>\d\.\s*(?:Доходы|Расходы|Источники|Таблица)[^<]*?))\s*</b>",
                         re.I)
_P_RE = re.compile(r"<p[^>]*>(?P<t>.*?)</p>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_ROW_RE = re.compile(r"<tr[^>]*>(?P<row>.*?)(?=<tr[^>]*>|</table>)", re.I | re.S)
_CELL_RE = re.compile(r"<t(?P<kind>[hd])[^>]*>(?P<c>.*?)(?=<t[hd][^>]*>|</tr>|$)", re.I | re.S)
_DATE_RE = re.compile(r"на\s+(\d{2})\.(\d{2})\.(\d{4})")
_UNIT_RE = re.compile(r"Единица\s+измерения\s*:\s*([^<\n]+)", re.I)


def parse_member_name(path: str) -> dict | None:
    m = NAME_RE.search(path)
    if not m:
        return None
    kind = (m.group("kind") or "").lower()
    return {
        "html_form": m.group("form"),
        "form_code": HTML_FORM_CODES.get(m.group("form") or "", None),
        "kind": kind,
        "activity": "income_generating" if kind.startswith("v") else None,
        "territory_code": str(int(m.group("code"))).zfill(2),
    }


def _text(fragment: str) -> str:
    t = htmllib.unescape(_TAG_RE.sub(" ", fragment))
    return re.sub(r"\s+", " ", t).strip()


def _rows(table_html: str) -> list[tuple[str, list[str]]]:
    out = []
    for rm in _ROW_RE.finditer(table_html):
        cells = [(c.group("kind").lower(), _text(c.group("c"))) for c in _CELL_RE.finditer(rm.group("row"))]
        if cells:
            out.append(("th" if all(k == "h" for k, _ in cells) else "td", [t for _, t in cells]))
    return out


def _section(form_code: str | None, title: str, idx: int) -> str:
    t = re.sub(r"^\s*\d+\.\s*", "", title)
    for code, rx in SECTION_TITLES.get(form_code or "", []):
        if re.search(rx, t, re.I):
            return code
    return f"section{idx + 1}"


def _header_cell(text: str) -> str:
    """«Исполнено: бюджет субъекта РФ» → «Исполнено / бюджет субъекта РФ» (как в шапке Excel).

    В ф. 0503323 вид деятельности отделён запятой: «Бюджет субъекта РФ, бюджетная деятельность».
    """
    t = re.sub(r"\bИсполено\b", "Исполнено", text)  # опечатка в части файлов
    t = re.sub(r"\s*,\s*(итого|бю?джетная деятельность|приносящая доход деятельность)\s*$", r" / \1", t, flags=re.I)
    # «На начало года, бюджет субъекта …» (ф. 0503320 до 2008 г.), «Исполнено, бюджет субъекта РФ»,
    # «Доходы, утвержденные законом о бюджете, бюджет субъекта РФ» (2006 г.)
    t = re.sub(r"^(На начало года|На конец отчетного периода|На конец года|Исполнено|Утверждено[^,]*"
               r"|(?:Доходы|Расходы|Источники[^,]*),\s*утвержденн[^,]*)\s*,\s*", r"\1 / ", t, flags=re.I)
    return re.sub(r"\s*:\s*", " / ", t).strip(" /")


def parse_html_report(payload: bytes) -> ParsedReport:
    text = decode_html(payload)
    paras = [_text(line) for m in _P_RE.finditer(text) for line in re.split(r"<br\s*/?>", m.group("t"), flags=re.I)]
    paras = [p for p in paras if p]
    h = ReportHeader()
    title = ""
    for p in paras:
        if not title and any(rx.search(p) for _, rx in TITLE_FORMS):
            title = p
            h.form_code = next(code for code, rx in TITLE_FORMS if rx.search(p))
        if h.report_date is None and (m := _DATE_RE.search(p)):
            h.report_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            h.report_date_raw = p
    if paras:
        h.budget_name = paras[0] if paras[0] != title else None
    if m := _UNIT_RE.search(text):
        h.unit_raw = _text(m.group(1))
    h.unit_multiplier = unit_multiplier(h.unit_raw)
    h.periodicity = "html"
    report = ParsedReport(header=h)
    # Вид деятельности всего отчёта — из названия формы (0503317 до 2011 г.).
    report_activity = None
    if re.search(r"приносящ\w*\s+доход|внебюджетн\w*\s+деятельност", title, re.I):
        report_activity = "income_generating"  # «внебюджетная деятельность» (2005–2006 гг.)
    elif re.search(r"бюджетн\w*\s+деятельност", title, re.I):
        report_activity = "budget"
    legacy_era = h.report_date is not None and h.report_date <= date(2005, 1, 1)
    if legacy_era and h.form_code in ("0503317", None):
        h.form_code = "0524100"  # до 2005 г. — форма из альбома 0524100
    if legacy_era and not h.unit_raw:
        h.unit_raw, h.unit_multiplier = "тыс. руб.", 1000  # единица в отчётах до 2005 г. не указана
        report_flags_extra = {"unit_assumed_thousand"}
    else:
        report_flags_extra = set()

    report.flags |= report_flags_extra
    line_seq = [0]
    sections = list(_SECTION_RE.finditer(text))
    chunks: list[tuple[str, str, bool]] = []
    for k, sm in enumerate(sections):
        end = sections[k + 1].start() if k + 1 < len(sections) else len(text)
        chunks.append((sm.group("title") or sm.group("title2"), text[sm.end():end], False))
    if not sections:
        # 2000–2004 гг.: одна таблица без разделов (доходы и расходы вместе), только исполнение.
        t0 = text.lower().find('<table bordercolor')
        if t0 >= 0:
            chunks.append(("Сводные показатели", text[t0:], True))
    for k, (sec_title, chunk, executed_only) in enumerate(chunks):
        t0 = chunk.lower().find("<table")
        if t0 < 0:
            continue
        t1 = chunk.lower().find("</table>", t0)
        rows = _rows(chunk[t0: t1 + 8 if t1 >= 0 else len(chunk)])
        if not rows or rows[0][0] != "th":
            report.flags.add(f"no_header:{k + 1}")
            continue
        header = [_header_cell(c) for c in rows[0][1]]
        if executed_only:
            # Графы без показателя в шапке («Консолидированный бюджет субъекта РФ») — исполнение.
            header = [h_ if re.search(r"наименование|^код", h_, re.I) or CODE_RE.search(h_) or classify_measure(h_)
                      else f"Исполнено / {h_}" for h_ in header]
        n = len(header)
        data = [r for kind, r in rows[1:] if kind == "td"]
        sheet_rows: list[list[object]] = [header, [float(i) for i in range(1, n + 1)]]
        sheet_rows += [[c if c != "" else None for c in r] for r in data]
        section = "summary" if executed_only else _section(h.form_code, sec_title, k)
        sheet = Sheet(name=f"html:{sec_title[:60]}", index=k, hidden=False, rows=sheet_rows)
        layout = detect_layout(sheet, 2)
        report.sheets.append({"sheet_name": sheet.name, "sheet_index": k, "section": section,
                              "nrows": len(sheet_rows), "ncols": n, "tables": 1})
        report.layouts.append({"sheet_name": sheet.name, "section": section, "table_no": 0, "graph_row": 2,
                               "header_top": 1, "first_row": 3, "last_row": len(sheet_rows), "columns": layout.columns})
        parse_table(sheet, layout, 3, len(sheet_rows), section, report, line_seq, h.unit_multiplier)
    if not chunks:
        report.flags.add("no_sections")
    if report_activity:
        report.flags.add(f"activity:{report_activity}")
        for v in report.values:
            v["activity"] = v["activity"] or report_activity
    return report
