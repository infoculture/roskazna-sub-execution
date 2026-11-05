"""Макеты отчётов 2000–2004 гг. (альбом форм 0524100, «форма 623»).

До 2005 г. отчёт об исполнении консолидированного бюджета субъекта —
книга ``<тип><код территории>.xls`` (``002001.xls``) с листами:

* ``Титул`` — территория, период («за 2004 год»), единица измерения (тыс. руб.);
* ``Doxod`` — I. Доходы; ``Ras-ot`` — II. Расходы (функциональная структура);
* ``Fin`` — источники финансирования дефицита;
* ``R-01-ot`` … ``R-96-ot`` — расходы по подразделам раздела ФКР (с КЦСР, КВР, ЭКР);
* в 2003 г. те же листы названы по-русски: «Доходы», «Функциональная структура»,
  «Источники финансирования», «01» … «96».

Единица измерения — тыс. руб. (в части книг не указана — тогда принимается тыс. руб.,
флаг ``unit_assumed_thousand``).

Шапка таблицы «разрезана» по строкам без объединённых ячеек: «Код / стро- / ки»,
«И С П О Л Н Е Н О / всего», «в том числе: / по бюджету / субъекта / Российской /
Федерации». Колонки определяются по склеенному тексту шапки; графам-значениям
присваиваются заголовки в терминах современных форм, чтобы общий классификатор
(:mod:`dims`) дал показатель и уровень бюджета:

=========================================  =================================================
Текст шапки                                Заголовок графы
=========================================  =================================================
НАЗНАЧЕНО                                  Утвержденные бюджетные назначения / консолидированный бюджет субъекта
ИСПОЛНЕНО всего                            Исполнено / консолидированный бюджет субъекта
ИСПОЛНЕНО в том числе по бюджету субъекта  Исполнено / бюджет субъекта
ИСПОЛНЕНО в том числе по местным бюджетам  Исполнено / местные бюджеты
=========================================  =================================================
"""

from __future__ import annotations

import re

from .layout import Column, Layout, graph_number
from .values import as_text
from .workbook import Sheet

# Имена листов: латиница (2001–2002, 2004 гг.) и кириллица (2003 г.: «Доходы», «Функциональная
# структура», «Источники финансирования», «01» … «96»).
SECTION_SHEETS = [
    ("income", re.compile(r"^(doxod|доход)", re.I)),
    ("expense", re.compile(r"^(ras|функциональн)", re.I)),
    ("sources", re.compile(r"^(fin|источник)", re.I)),
    ("expense_detail", re.compile(r"^(r-\d+|\d{2}$)", re.I)),
]

_STOP_RE = re.compile(r"код\s+формы|^раздел\s+\d|^[ivx]+\.\s|распределение|^источники|##", re.I)
_SPACED_RE = re.compile(r"(?<![^\W\d_])([^\W\d_]) (?=[^\W\d_](?:\s|$))")

CODE_HEADERS = [
    (re.compile(r"фкр", re.I), "ФКР"),
    (re.compile(r"кцср", re.I), "КЦСР"),
    (re.compile(r"квр", re.I), "КВР"),
    (re.compile(r"экр", re.I), "ЭКР"),
    (re.compile(r"кивф|кивнф", re.I), "КИВФ"),
    (re.compile(r"код\s+дохода|дохода$", re.I), "КД"),
]


def is_legacy_sheet(sheet: Sheet) -> bool:
    return any(rx.search(sheet.name) for _, rx in SECTION_SHEETS)


def sheet_section(sheet: Sheet) -> str | None:
    return next((code for code, rx in SECTION_SHEETS if rx.search(sheet.name)), None)


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    # «И С П О Л Н Е Н О» → «ИСПОЛНЕНО»
    while True:
        t2 = _SPACED_RE.sub(r"\1", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)  # «стро- ки», «бюдже- там»
    return t


def detect_legacy_layout(sheet: Sheet, graph_row: int) -> Layout:
    # Верх шапки — до строки с заголовком таблицы/раздела или 8 строк вверх.
    top = graph_row - 1
    for i in range(graph_row - 1, max(0, graph_row - 9), -1):
        texts = [as_text(v) or "" for v in sheet.rows[i - 1] if isinstance(v, str) and v.strip()]
        if any(_STOP_RE.search(t.strip()) for t in texts):
            break
        top = i
    grow = sheet.rows[graph_row - 1]
    ncols = max([len(grow)] + [len(sheet.rows[i - 1]) for i in range(top, graph_row)])
    parts: dict[int, list[str]] = {j: [] for j in range(1, ncols + 1)}
    for i in range(top, graph_row):
        row = sheet.rows[i - 1]
        filled = [j for j in range(1, len(row) + 1) if isinstance(row[j - 1], str) and row[j - 1].strip()]
        for k, j in enumerate(filled):
            t = _clean(row[j - 1])
            if not t or t == "##":
                continue
            parts[j].append(t)
            # Групповые надписи («ИСПОЛНЕНО», «в том числе:») стоят над первой колонкой группы.
            low = t.casefold()
            if low.startswith("исполнено") or low.startswith("в том числе"):
                nxt = filled[k + 1] if k + 1 < len(filled) else ncols + 1
                for jj in range(j + 1, nxt):
                    parts[jj].append(t)
    cols: list[Column] = []
    for j in range(1, ncols + 1):
        g = graph_number(grow[j - 1]) if j <= len(grow) else None
        if g is None:
            continue
        joined = _clean(" ".join(parts[j]))
        low = joined.casefold()
        if "наименование" in low:
            role, header = "name", "Наименование показателя"
        elif re.search(r"код\s+строки|^строки$|стро\s*ки", low) or re.fullmatch(r"(код\s*)?стро\w*", low):
            role, header = "line_code", "Код строки"
        elif (ch := next((h for rx, h in CODE_HEADERS if rx.search(low)), None)):
            role, header = "code", ch
        elif "назначено" in low:
            role, header = "value", "Утвержденные бюджетные назначения / консолидированный бюджет субъекта Российской Федерации"
        elif "местн" in low:
            role, header = "value", "Исполнено / местные бюджеты"
        elif "субъект" in low:
            role, header = "value", "Исполнено / бюджет субъекта Российской Федерации"
        elif "исполнено" in low:
            role, header = "value", "Исполнено / консолидированный бюджет субъекта Российской Федерации"
        else:
            continue  # служебная графа (код раздела формы и т. п.)
        cols.append(Column(col=j, graph=g, block=0, role=role, header=header))
    return Layout(header_top=top, graph_row=graph_row, columns=cols)
