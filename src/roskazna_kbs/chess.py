"""Разбор сводных книг «по графам» («шахматок»).

Архив ``Доходы_расходы_источники по графам на <дата>.zip`` содержит книги
``0503317-ROSKAZNA_<раздел>_<графа>_<ДДММГГГГ>.xls`` (это XLSX, несмотря на
расширение). Раздел: 1 — доходы, 2 — расходы, 3 — источники финансирования.
Графа — номер графы формы 0503317 («Исполнено» по уровню бюджета: 18 —
консолидированный бюджет субъекта и ТГВФ, 20 — консолидированный бюджет
субъекта, 22 — бюджет субъекта, …, 31 — бюджет ТГВФ).

Каждый лист книги — часть широкой таблицы:

* строка 8: в колонке 1 — уровень бюджета, далее — показатели
  «<код строки><глава><КБК>\\n<наименование>» (23 знака) или
  «<код строки> *** <КБК>\\n<наименование>» для итогов;
* строки 10+: территории «<код> - <наименование>»: Российская Федерация,
  «Федеральные округа», затем федеральный округ и его субъекты.

Только здесь на месячном срезе есть итоги по РФ и федеральным округам, поэтому
в БД сохраняются строки РФ и округов (``territory_kind`` = ``country`` /
``okrug``); строки субъектов повторяют их собственные отчёты и по умолчанию
пропускаются.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .dims import classify_level, classify_measure
from .values import as_decimal, as_text
from .workbook import Workbook

# Имена книг «по графам»:
#   0503317-ROSKAZNA_1_18_01092026.xls     — с 2018 г.: раздел_графа_дата
#   0503317-3_ROSKAZNA_1_17_01012018.xls   — 2016–2017 гг.
#   Доходы/гр14_0503317_01012016.xls       — 2014–2015 гг.: графа и дата в имени, раздел — папка
#   Доходы/14_0503317_Д.xls                — до 2013 г.: графа и буква раздела, дата — по пакету
NAME_RE = re.compile(r"0503317(?:-\d+)?[-_]ROSKAZNA_(?P<section>\d)_(?P<graph>\d+)_(?P<date>\d{8})", re.I)
#   Доходы/Гр.19_0503317_Y.xls             — 2012 г. (в RAR): графа, раздел — папка, дата — по пакету
OLD_NAME_RE = re.compile(r"(?:^|/)(?:гр\.?\s*)?(?P<graph>\d{1,2})[_ ]0503317"
                         r"(?:[_ ](?P<date>\d{8}))?(?:[_ ](?P<letter>[ДРИ]))?(?:[_ ][A-Z])?\.xlsx?$", re.I)
#   отчет за август 2007.rar/428 за август 2007 года доходы гр.1.1.xls — 2005–2011 гг.
#   отчет за декабрь 2007.rar/доходы/гр12.xls
ERA_NAME_RE = re.compile(r"(?:^|/)(?:\d{3}\s+за\s+[а-я]+\s+\d{4}\s+года\s+(?P<section>доходы|расходы|источники)\s+)?"
                         r"гр\.?\s*(?P<graph>\d+(?:\.\d+)?)\.xlsx?$", re.I)
#   июнь 2008 доходы.rar/июнь 2008 доходы 16 гр.xls, Июнь 2011 источники гр 14.xls — месячные 2008–2011 гг.
MONTH_NAME_RE = re.compile(r"(?:^|/)(?P<month>[а-я]+)\s+(?P<year>(?:19|20)\d{2})\s+(?P<section>доходы|расходы|источники)\s+"
                           r"(?:(?P<g1>\d+(?:\.\d+)?)\s*гр\.?|гр\.?\s*(?P<g2>\d+(?:\.\d+)?))\.xlsx?$", re.I)
PERIOD_RE = re.compile(r"(?:за\s+|^|/)(?P<month>[а-я]+)\s+(?P<year>(?:19|20)\d{2})", re.I)
RU_MONTHS_NOM = {"январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6, "июль": 7,
                 "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12}
LETTER_SECTIONS = {"д": "income", "р": "expense", "и": "sources"}
SECTIONS = {"1": "income", "2": "expense", "3": "sources"}
FOLDER_SECTIONS = [("income", re.compile(r"доход", re.I)), ("expense", re.compile(r"расход", re.I)),
                   ("sources", re.compile(r"источник", re.I))]
TERR_RE = re.compile(r"^\s*(?:(?P<code>\d+)\s*-\s*)?(?P<name>.+?)\s*$")
IND_RE = re.compile(r"^\s*(?P<code>\d[\d *]*?)\s*\n\s*(?P<name>.*)$", re.S)
# 2005–2011 гг.: код и наименование в одной строке, код строки — последний сегмент:
# «000 100 00000 00 0000 000 010 ДОХОДЫ»
ERA_IND_RE = re.compile(r"^\s*(?P<code>(?:\*\*\*|\d{3,4})(?:\s+\d{1,10}){3,7})\s*(?P<name>\D.*)?$", re.S)
KNOWN_LINES = {"010", "200", "450", "500", "520", "620", "700", "710", "720", "800", "810", "820"}


def graph_code(text: str) -> int:
    """«18» → 18; «1.1» → 101 (подграфы старых «шахматок»)."""
    major, _, minor = text.partition(".")
    return int(major) * 100 + int(minor) if minor else int(major)


def is_chess_member(path: str) -> bool:
    return bool(NAME_RE.search(path) or OLD_NAME_RE.search(path) or ERA_NAME_RE.search(path)
                or MONTH_NAME_RE.search(path))


def _period_date(path: str) -> str | None:
    """«отчет за август 2007» → 2007-09-01 (дата среза «на 1 число» следующего месяца)."""
    m = None
    for cand in PERIOD_RE.finditer(path):
        if cand.group("month").casefold() in RU_MONTHS_NOM:
            m = cand
    if not m:
        return None
    year, month = int(m.group("year")), RU_MONTHS_NOM[m.group("month").casefold()]
    return f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"


def parse_member_name(path: str, default_date: str | None = None) -> dict | None:
    """Раздел, графа и дата по пути книги в пакете (имя файла и папка).

    ``default_date`` (ISO) — дата среза пакета, если её нет в имени книги.
    """
    m = NAME_RE.search(path)
    if m:
        d = m.group("date")
        return {"section": SECTIONS.get(m.group("section"), m.group("section")), "graph": int(m.group("graph")),
                "report_date": f"{d[4:]}-{d[2:4]}-{d[:2]}"}
    m = MONTH_NAME_RE.search(path)
    if m and m.group("month").casefold() in RU_MONTHS_NOM:
        section = {"доходы": "income", "расходы": "expense", "источники": "sources"}[m.group("section").casefold()]
        date_iso = _period_date(f"за {m.group('month')} {m.group('year')}")
        return {"section": section, "graph": graph_code(m.group("g1") or m.group("g2")), "report_date": date_iso}
    m = ERA_NAME_RE.search(path)
    if m and not OLD_NAME_RE.search(path):
        section = (m.group("section") or "").casefold()
        section = {"доходы": "income", "расходы": "expense", "источники": "sources"}.get(section)
        if section is None:
            folders = path.split("/")[:-1]
            section = next((code for f in reversed(folders) for code, rx in FOLDER_SECTIONS if rx.search(f)), None)
        date_iso = _period_date(path) or default_date
        if section is None or date_iso is None:
            return None
        return {"section": section, "graph": graph_code(m.group("graph")), "report_date": date_iso}
    m = OLD_NAME_RE.search(path)
    if not m:
        return None
    if m.group("letter"):
        section = LETTER_SECTIONS[m.group("letter").casefold()]
    else:
        folders = path.split("/")[:-1]
        section = next((code for f in reversed(folders) for code, rx in FOLDER_SECTIONS if rx.search(f)), None)
    d = m.group("date")
    date_iso = f"{d[4:]}-{d[2:4]}-{d[:2]}" if d else default_date
    if section is None or date_iso is None:
        return None
    return {"section": section, "graph": int(m.group("graph")), "report_date": date_iso}


# --- распознавание «шахматки» по содержимому -------------------------------------
# Имена книг «по графам» в пакетах 2008–2016 гг. — десятки вариантов («Доходы гр. 14.xls»,
# «1_23_0503317.xls», «Д_гр.14_0503317.xls», «Январь_2011_Доходы_группа_14.xls» …).
# Поэтому книга без кода территории в имени проверяется по содержимому: строка показателей
# («код\nнаименование» или «000 100 … 010 ДОХОДЫ») и строка «Российская Федерация» под ней.

_RF_RE = re.compile(r"^\s*(?:\d+\s*-\s*)?(российская\s+федерация|все\s+источники)", re.I)
_GRAPH_NAME_RES = [
    re.compile(r"(?:гр|графа|группа)\.?\s*_?(\d+(?:\.\d+)?)", re.I),
    re.compile(r"^\d_(\d{1,2})_0503317", re.I),           # 1_23_0503317.xls — раздел_графа
    re.compile(r"^(\d{1,2})_0503317", re.I),               # 14_0503317_М.xls
    re.compile(r"_(\d{1,2})\.xlsx?$", re.I),              # 0503317_01012015_1_д_14.xls
]
_GRAPH_CONTENT_RE = re.compile(r"Гр\.\s*(\d+(?:\.\d+)?)")
_SECTION_NAME_RES = [
    ("income", re.compile(r"доход|(?:^|[_/])д_|_д_", re.I)),
    ("expense", re.compile(r"расход|(?:^|[_/])р_|_р_", re.I)),
    ("sources", re.compile(r"источник|ичсточник|(?:^|[_/])и_|_и_", re.I)),
]
_DATE_IN_NAME_RES = [
    re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)"),
]
_LINE_SECTIONS = {"010": "income", "200": "expense", "450": "expense"}


def _header_row(sheet) -> int | None:
    for i in range(1, min(sheet.nrows, 15) + 1):
        row = sheet.rows[i - 1]
        if len(row) > 1 and isinstance(row[1], str) and (IND_RE.match(row[1]) or ERA_IND_RE.match(row[1])):
            return i
    return None


def looks_like_chess(wb: Workbook) -> bool:
    for sheet in wb.sheets:
        if sheet.hidden or sheet.name == "XDO_METADATA" or sheet.nrows < 5:
            continue
        hdr = _header_row(sheet)
        if hdr is None:
            continue
        for row in sheet.rows[hdr: hdr + 8]:
            if row and isinstance(row[0], str) and _RF_RE.match(row[0]):
                return True
    return False


def generic_meta(path: str, wb: Workbook, default_date: str | None) -> dict | None:
    """Раздел, графа и дата «шахматки» с нестандартным именем (по имени, папке и содержимому)."""
    name = path.rsplit("/", 1)[-1]
    tail = "/".join(path.split("/")[-3:])
    section = next((code for code, rx in _SECTION_NAME_RES if rx.search(name)), None) or \
        next((code for code, rx in _SECTION_NAME_RES if rx.search(tail)), None)
    sheet = next((s for s in wb.sheets if not s.hidden and s.name != "XDO_METADATA" and _header_row(s)), None)
    if sheet is None:
        return None
    if section is None:
        hdr = sheet.rows[_header_row(sheet) - 1]
        m = IND_RE.match(hdr[1]) or ERA_IND_RE.match(hdr[1])
        line = split_indicator(m.group("code"))["line_code"] if m else None
        section = _LINE_SECTIONS.get(line or "", "sources" if line and line[0] in "5678" else None)
    graph = None
    for rx in _GRAPH_NAME_RES:
        if (m := rx.search(name)):
            graph = graph_code(m.group(1))
            break
    if graph is None:
        for row in sheet.rows[:15]:
            for v in row[:3]:
                if isinstance(v, str) and (m := _GRAPH_CONTENT_RE.search(v)):
                    graph = graph_code(m.group(1))
                    break
            if graph is not None:
                break
    date_iso = None
    for rx in _DATE_IN_NAME_RES:
        if (m := rx.search(name)):
            date_iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            break
    date_iso = date_iso or _period_date(tail) or default_date
    if section is None or date_iso is None:
        return None
    return {"section": section, "graph": graph or 0, "report_date": date_iso}


def split_indicator(code: str) -> dict:
    """«01000010101000000000110» → код строки 010, глава 000, КБК (17 знаков).
    Для расходов КБК — Рз/Пр (4) + ЦСР (10) + ВР (3)."""
    c = re.sub(r"\s+", " ", code).strip()
    segs = c.split(" ")
    if ("***" not in c or segs[0] == "***") and len(segs) >= 3:
        # Сегменты через пробел (до 2013 г.). Положение кода строки и главы разное:
        #   «010 000 10000000000000000»            — строка, глава, КБК (2013 г.)
        #   «000 100 00000 00 0000 000 010»        — глава, КБК, строка (доходы 2005–2011 гг.)
        #   «0100 0000000 000 000 200 000»         — Рз/Пр, ЦСР, ВР, КОСГУ, строка, глава (расходы)
        li = max((i for i, sg in enumerate(segs) if sg in KNOWN_LINES), default=None)
        if li is not None:
            if li == 0:
                ai = 1 if segs[1] == "000" else None
            elif segs[0] in ("000", "***") and li == len(segs) - 1:
                ai = 0
            elif segs[-1] == "000" and li == len(segs) - 2:
                ai = len(segs) - 1
            else:
                ai = None
            cls = "".join(sg for i, sg in enumerate(segs) if i not in (li, ai))
            admin = segs[ai] if ai is not None and segs[ai] != "***" else ""
            return {"indicator_code": segs[li] + (admin or "000") + cls, "line_code": segs[li],
                    "admin_code": admin or None, "classification_code": cls or None}
    if "***" in c:
        line, _, rest = c.partition("***")
        c = f"{line.replace(' ', '')} *** {rest.replace(' ', '')}"
    else:
        c = c.replace(" ", "")  # до 2013 г. сегменты кода разделены пробелами
    out = {"indicator_code": c, "line_code": None, "admin_code": None, "classification_code": None}
    if "***" in c:
        line, _, rest = c.partition(" *** ")
        out["line_code"] = line
        out["classification_code"] = rest or None
    elif re.fullmatch(r"\d{23}", c):
        out["line_code"], out["admin_code"], out["classification_code"] = c[:3], c[3:6], c[6:]
    return out


def parse_chess(wb: Workbook, meta: dict, keep_subjects: bool = False) -> tuple[list[dict], list[dict]]:
    """Возвращает (indicators, values)."""
    indicators: dict[str, dict] = {}
    values: list[dict] = []
    for sheet in wb.sheets:
        if sheet.nrows < 10 or sheet.hidden or sheet.name == "XDO_METADATA":
            continue
        hdr_row = _header_row(sheet)
        if hdr_row is None:
            continue
        hdr = sheet.rows[hdr_row - 1]
        nxt = sheet.rows[hdr_row] if hdr_row < sheet.nrows else []
        # Уровень бюджета: в колонке 1 строки показателей (с 2012 г.) или под ней
        # («Исполнено: консолидированный бюджет …», 2005–2011 гг.).
        labels = [as_text(hdr[0]) or "", as_text(nxt[1]) if len(nxt) > 1 and isinstance(nxt[1], str) else "",
                  as_text(nxt[0]) if nxt and isinstance(nxt[0], str) else ""]
        level_label = next((lab for lab in labels if lab and classify_level(lab) != "none"), labels[0])
        level = classify_level(level_label)
        measure = classify_measure(level_label) or "executed"
        cols: dict[int, str] = {}
        for j, v in enumerate(hdr[1:], start=2):
            if not isinstance(v, str):
                continue
            m = IND_RE.match(v) or ERA_IND_RE.match(v)
            if not m:
                continue
            ind = split_indicator(m.group("code"))
            ind["name"] = as_text(m.group("name") or "")
            ind["section"] = meta["section"]
            indicators.setdefault(ind["indicator_code"], ind)
            cols[j] = ind["indicator_code"]
        okrug_code = None
        okrug_name = None
        for i in range(hdr_row + 1, sheet.nrows + 1):
            row = sheet.rows[i - 1]
            if not row or not isinstance(row[0], str):
                continue
            m = TERR_RE.match(row[0])
            if not m:
                continue
            code, name = m.group("code"), m.group("name")
            low = name.casefold()
            if low.startswith("российская федерация"):
                kind = "country"
            elif low.startswith("федеральные округа") or low.startswith("все источники"):
                continue  # итог по округам = итог по РФ
            elif code is None:
                continue
            elif "федеральный округ" in low:
                kind, okrug_code, okrug_name = "okrug", code, name
            else:
                kind = "subject"
                if not keep_subjects:
                    continue
            for j, ind_code in cols.items():
                v = row[j - 1] if j <= len(row) else None
                d = as_decimal(v)
                if d is None:
                    continue
                values.append({
                    "report_date": meta["report_date"],
                    "section": meta["section"],
                    "graph": meta["graph"],
                    "measure": measure,
                    "budget_level": level,
                    "territory_kind": kind,
                    "territory_code": code if kind != "country" else "00",
                    "territory_name": name,
                    "okrug_code": okrug_code if kind == "subject" else None,
                    "indicator_code": ind_code,
                    "value": d.quantize(Decimal("0.01")),
                })
    return list(indicators.values()), values


def okrug_membership(wb: Workbook) -> list[dict]:
    """Состав федеральных округов по первому листу книги: [{okrug_code, okrug_name, territory_code, territory_name}]."""
    out = []
    if not wb.sheets:
        return out
    sheet = next((s for s in wb.sheets if not s.hidden and s.name != "XDO_METADATA"), wb.sheets[0])
    okrug = None
    for row in sheet.rows:
        if not row or not isinstance(row[0], str):
            continue
        m = TERR_RE.match(row[0])
        if not m:
            continue
        code, name = m.group("code"), m.group("name")
        low = name.casefold()
        if code is None or low.startswith("российская федерация") or low.startswith("федеральные округа"):
            continue
        if "федеральный округ" in low:
            okrug = (code, name)
            continue
        if okrug:
            out.append({"okrug_code": okrug[0], "okrug_name": okrug[1], "territory_code": code.zfill(2), "territory_name": name})
    return out
