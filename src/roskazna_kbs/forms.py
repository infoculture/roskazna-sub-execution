"""Разбор книг отчётности субъектов (формы 0503317, 0503320, 0503321, 0503323).

Книга = отчёт одной территории на одну дату. Разбор:

1. шапка формы → атрибуты отчёта (код формы по ОКУД, дата, финансовый орган,
   бюджет, ОКПО, ОКТМО, периодичность, единица измерения);
2. каждый видимый лист → раздел формы (доходы, расходы, источники, …);
3. на листе — таблицы (по строкам номеров граф, см. :mod:`layout`);
4. строка таблицы → ``line`` (наименование, код строки, коды классификации);
   графы-значения → ``value`` (показатель, уровень бюджета, номер графы, сумма).

Результат возвращается словарями, готовыми к записи в Parquet (см. :mod:`build`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import legacy
from .dims import classify_columns
from .layout import Column, Layout, detect_layout, find_graph_rows, is_graph_row
from .values import as_code, as_decimal, as_text, is_marker
from .workbook import Sheet, Workbook

RU_MONTHS_GEN = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

FORM_RE = re.compile(r"(?<!\d)(0503317|0503320|0503321|0503323|0503387|0503324|0503128|0503151|0503164|0503169|0503387)(?!\d)")
OKUD_LABEL_RE = re.compile(r"форма\s+по\s+окуд", re.I)
DATE_WORDS_RE = re.compile(r"(?:^|на\s+)[«\"']?(\d{1,2})[»\"']?\s+([а-я]+)\s+(\d{4})\s*г", re.I)
DATE_NUM_RE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
UNIT_RE = re.compile(r"единица\s+измерения\s*:?\s*(.+)", re.I)

# Раздел формы по имени листа или по заголовку раздела в листе.
SECTION_RULES: list[tuple[str, re.Pattern]] = [
    ("income", re.compile(r"_Д$|^Д$|доход", re.I)),
    ("expense", re.compile(r"_Р$|^Р$|расход", re.I)),
    ("sources", re.compile(r"_ИФ$|^ИФ$|источник", re.I)),
    ("settlements", re.compile(r"_Табл$|консолидируем", re.I)),
    ("balance", re.compile(r"баланс", re.I)),
    ("offbalance", re.compile(r"справк|забаланс", re.I)),
    ("obligations", re.compile(r"таблиц", re.I)),
]

FORM_SHEET_SECTIONS = {
    "0503321": {0: "results", 1: "results_changes"},
    "0503323": {0: "cash_flows", 1: "cash_outflows_detail"},
}


@dataclass
class ReportHeader:
    form_code: str | None = None
    report_date: date | None = None
    report_date_raw: str | None = None
    fin_authority: str | None = None
    budget_name: str | None = None
    okpo: str | None = None
    oktmo: str | None = None
    okei: str | None = None
    periodicity: str | None = None
    unit_raw: str | None = None
    unit_multiplier: int = 1


@dataclass
class ParsedReport:
    header: ReportHeader
    lines: list[dict] = field(default_factory=list)
    values: list[dict] = field(default_factory=list)
    layouts: list[dict] = field(default_factory=list)
    sheets: list[dict] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)


def _cells(sheet: Sheet, upto: int):
    for i, row in enumerate(sheet.rows[:upto], 1):
        for j, v in enumerate(row, 1):
            if v not in (None, ""):
                yield i, j, v


# Код классификации: буквы и цифры без знаков препинания, хотя бы одна цифра.
_CODE_LIKE = re.compile(r"^(?=.*\d)[0-9A-Za-zА-Яа-я]{1,30}$")
_LABEL_RE = re.compile(r"^\s*(по\s+ок|инн\b|дата\b|коды\b|глава\b|форма\s+по)", re.I)


def _right_value(sheet: Sheet, i: int, j: int) -> object:
    """Первое непустое значение строки ``i`` правее колонки ``j``.

    Подписи правой части шапки («по ОКТМО», «ИНН», «Дата») значением не считаются:
    если поле слева не заполнено, возвращается None.
    """
    row = sheet.rows[i - 1]
    for k in range(j, len(row)):
        v = row[k]
        if v in (None, ""):
            continue
        if isinstance(v, str) and _LABEL_RE.match(v):
            return None
        return v
    return None


def _code_right_of_label(sheet: Sheet, i: int, j: int) -> object:
    """Значение кода справа от подписи «по ОКПО» и т. п. (подписи пропускаются не будут)."""
    row = sheet.rows[i - 1]
    for k in range(j, len(row)):
        if row[k] not in (None, ""):
            return row[k]
    return None


# «Республика Башкортостан за 2004 год», «… за январь 2004 г.» — отчёты до 2005 г.
PERIOD_RE = re.compile(r"\bза\s+(?:(?P<month>[а-я]+)\s+)?(?P<year>(?:19|20)\d{2})\s*(?:год|г\.?)?", re.I)
RU_MONTHS_NOM = {
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4, "май": 5, "июнь": 6,
    "июль": 7, "август": 8, "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}


def _period_end(m: re.Match) -> date | None:
    """«за 2004 год» → 01.01.2005; «за март 2004» → 01.04.2004 (дата среза «на 1 число»)."""
    year = int(m.group("year"))
    month = RU_MONTHS_NOM.get((m.group("month") or "").casefold())
    if m.group("month") and month is None:
        return None
    if month is None or month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def _date_from_words(text: str) -> date | None:
    m = DATE_WORDS_RE.search(text)
    if m and m.group(2).lower() in RU_MONTHS_GEN:
        try:
            return date(int(m.group(3)), RU_MONTHS_GEN[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    return None


def _code_value(v: object) -> str | None:
    """Код из правой части шапки: только цифры (подпись соседнего поля — не код)."""
    code = as_code(v)
    return code if code and code.isdigit() else None


def unit_multiplier(unit: str | None) -> int:
    u = (unit or "").casefold()
    if "млрд" in u:
        return 1_000_000_000
    if "млн" in u:
        return 1_000_000
    if "тыс" in u:
        return 1_000
    return 1


def parse_header(sheet: Sheet, upto: int = 14) -> ReportHeader:
    h = ReportHeader()
    for i, j, v in _cells(sheet, upto):
        t = as_text(v) if not isinstance(v, (int, float)) else None
        if t is None:
            continue
        tl = t.casefold()
        if h.form_code is None and OKUD_LABEL_RE.search(t):
            code = as_code(_code_right_of_label(sheet, i, j))
            if code and code.isdigit():
                h.form_code = code.zfill(7)
        if h.form_code is None and (m := FORM_RE.search(t)) and ("форма" in tl):
            h.form_code = m.group(1)
        if h.form_code is None and tl.startswith("альбом форм"):
            code = as_code(_code_right_of_label(sheet, i, j))
            if code and code.isdigit():
                h.form_code = code.zfill(7)  # 0524100 — до 2005 г.
        if (m := PERIOD_RE.search(t)) and _period_end(m):
            if h.report_date is None:
                h.report_date, h.report_date_raw = _period_end(m), t
            name = as_text(t[: m.start()])
            # «Республика Башкортостан за 2004 год» — наименование территории (до 2005 г.)
            if name and not h.budget_name and len(name) > 3 and not re.search(r"отчет|федерации\s+для", name, re.I):
                h.budget_name = name
        if h.report_date is None and (d := _date_from_words(t)):
            h.report_date, h.report_date_raw = d, t
        if tl.startswith("дата") and h.report_date is None:
            v2 = _code_right_of_label(sheet, i, j)
            if hasattr(v2, "year"):
                h.report_date = date(v2.year, v2.month, v2.day)
            elif v2 and (m := DATE_NUM_RE.search(str(v2))):
                h.report_date = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if "наименование финансового органа" in tl:
            h.fin_authority = as_text(_right_value(sheet, i, j))
        elif tl.startswith("наименование бюджета"):
            h.budget_name = as_text(_right_value(sheet, i, j))
        elif tl.startswith("по окпо"):
            h.okpo = _code_value(_code_right_of_label(sheet, i, j))
        elif tl.startswith("по октмо") or tl.startswith("по окато"):
            h.oktmo = _code_value(_code_right_of_label(sheet, i, j))
        elif tl.startswith("по океи"):
            h.okei = _code_value(_code_right_of_label(sheet, i, j))
        elif tl.startswith("периодичность"):
            h.periodicity = as_text(t.split(":", 1)[1]) if ":" in t else as_text(_right_value(sheet, i, j))
        elif m := UNIT_RE.search(t):
            h.unit_raw = as_text(m.group(1)) or as_text(_right_value(sheet, i, j))
    if h.okpo and h.okpo.isdigit():
        h.okpo = h.okpo.zfill(8)
    if h.oktmo and h.oktmo.isdigit() and len(h.oktmo) < 8:
        h.oktmo = h.oktmo.zfill(8)
    h.unit_multiplier = unit_multiplier(h.unit_raw)
    return h


SECTION_TITLE_RULES: list[tuple[str, re.Pattern]] = [
    ("income", re.compile(r"^\s*1\.\s*доходы", re.I)),
    ("expense", re.compile(r"^\s*2\.\s*расходы", re.I)),
    ("sources", re.compile(r"^\s*3\.\s*источники", re.I)),
    ("settlements", re.compile(r"^\s*4\.\s*таблица\s+консолидируемых", re.I)),
]
FORM_INDEX_SECTIONS = {
    "0503317": {0: "income", 1: "expense", 2: "sources", 3: "settlements"},
    "0503320": {0: "balance", 1: "offbalance", 2: "obligations"},
}


def sheet_section(form_code: str | None, sheet: Sheet, visible_index: int) -> str:
    """Раздел формы для листа.

    Порядок признаков: фиксированная нумерация листов (0503321, 0503323) →
    заголовок раздела в листе («1. Доходы бюджета») → имя листа
    («0503317_Д», «стр.1_8_Баланс») → номер листа по порядку для формы.
    """
    if form_code in FORM_SHEET_SECTIONS and visible_index in FORM_SHEET_SECTIONS[form_code]:
        return FORM_SHEET_SECTIONS[form_code][visible_index]
    for row in sheet.rows[:20]:
        for v in row:
            if isinstance(v, str):
                for code, rx in SECTION_TITLE_RULES:
                    if rx.search(v):
                        return code
    for code, rx in SECTION_RULES:
        if rx.search(sheet.name):
            return code
    if form_code in FORM_INDEX_SECTIONS and visible_index in FORM_INDEX_SECTIONS[form_code]:
        return FORM_INDEX_SECTIONS[form_code][visible_index]
    return f"sheet{visible_index + 1}"


# --- разбор строк -----------------------------------------------------------

# Сегменты кода с собственными графами (HTML-отчёты до 2011 г.).
_SEGMENT_FIELDS: list[tuple[str, re.Pattern]] = [
    ("admin_code", re.compile(r"^(ап|ппп|глава|администратор)$")),
    ("rzpr_code", re.compile(r"^(прз|рзпр|рз\s*прз|фкр)$")),
    ("csr_code", re.compile(r"^(цст|цср|кцср)$")),
    ("vr_code", re.compile(r"^(квр|вр)$")),
    ("kosgu_code", re.compile(r"^(экр|экл)$")),
    ("kbk_code", re.compile(r"^(кд|кивф)$")),
    ("kbk_part", re.compile(r"^(групп|подгрупп|стат|подстат|элемент|программ|подвид|вид источника)")),
]


def _code_fields(section: str, codes: list[tuple[str, int, object]]) -> dict:
    """Коды классификации строки → именованные поля.

    ``codes`` — список (заголовок графы, номер подколонки, значение).
    """
    out: dict[str, str | None] = {}
    parts: list[str] = []
    kbk_parts: list[str] = []
    for header, part, raw in codes:
        code = as_code(raw)
        if code is None:
            continue
        parts.append(code)
        h = header.casefold()
        seg = next((field for field, rx in _SEGMENT_FIELDS if rx.match(h)), None)
        if seg == "kbk_part":
            kbk_parts.append(code)
            continue
        if seg:
            out[seg] = code
            continue
        if "косгу" in h:
            out["kosgu_code"] = code
        elif "раздела" in h or "подраздел" in h:
            out["rzpr_code"] = code
        elif "забаланс" in h or "счет" in h:
            out["account_code"] = code
        elif section == "expense":
            # глава | Рз/Пр | ЦСР | ВР
            key = {0: "admin_code", 1: "rzpr_code", 2: "csr_code", 3: "vr_code"}.get(part, f"code_{part}")
            out[key] = code
        else:
            key = {0: "admin_code", 1: "kbk_code"}.get(part, f"code_{part}")
            out[key] = code
    if kbk_parts and "kbk_code" not in out:
        joined = "".join(p for p in kbk_parts if not is_marker(p))
        out["kbk_code"] = joined or None
    for k in list(out):
        if is_marker(out[k]):
            out[k] = None  # «х» в кодовой графе — признак итоговой строки, не код
    full = "".join(p for p in parts if not is_marker(p))
    out["code_raw"] = " ".join(parts) or None
    out["classification_code"] = full or None
    out["is_total_marker"] = any(is_marker(p) for p in parts)
    return out


def parse_table(sheet: Sheet, layout: Layout, first_row: int, last_row: int, section: str,
                report: ParsedReport, line_seq: list[int], multiplier: int) -> None:
    classes = classify_columns(layout.columns)
    names = layout.by_role("name")
    lines_ = layout.by_role("line_code")
    codes = layout.by_role("code")
    values = [c for c in layout.columns if c.role == "value"]
    group_name = None
    pending_label: dict | None = None
    last_label: str | None = None
    # Код строки в объединённой по вертикали ячейке (макеты 2020–2021 гг.):
    # значение есть только в первой строке диапазона.
    merged_line: dict[int, str] = {}
    line_cols = {c.col for c in lines_}
    for r1, r2, c1, c2 in sheet.merged:
        if r2 > r1 and c1 in line_cols and r2 >= first_row and r1 <= last_row:
            top = sheet.rows[r1 - 1][c1 - 1] if c1 <= len(sheet.rows[r1 - 1]) else None
            code = as_code(top, 3)
            if code:
                for i in range(r1, r2 + 1):
                    merged_line.setdefault(i, code)
    for i in range(first_row, last_row + 1):
        row = sheet.rows[i - 1]
        if not row or is_graph_row(row):
            continue

        def cell(col: int) -> object:
            return row[col - 1] if col <= len(row) else None

        name = next((as_text(cell(c.col)) for c in names if as_text(cell(c.col))), None)
        line_code = next((as_code(cell(c.col), 3) for c in lines_ if as_code(cell(c.col))), None) or merged_line.get(i)
        if line_code and not re.fullmatch(r"\d{1,4}[а-яa-z]?", line_code, re.I):
            # В графе «Код строки» текст — повтор шапки на новой странице.
            continue
        block0 = [c for c in codes if c.block == 0] or codes
        code_cells = [(c.header, c.code_part, cell(c.col)) for c in block0]
        vals = []
        for c in values:
            v = cell(c.col)
            d = as_decimal(v)
            if d is None or d == 0:
                # Нули не храним: в одних макетах пустые графы заполнены «0,00», в других
                # оставлены пустыми; отсутствие значения в БД означает «0 или прочерк».
                continue
            vals.append((c, d))
        has_code = any(_CODE_LIKE.match(as_code(raw) or "") for _, _, raw in code_cells)
        if line_code is None and not vals and not has_code:
            # Подпись без кода и значений: «в том числе:», продолжение наименования,
            # подписи руководителя. Продолжение наименования приклеиваем к строке
            # «из них:» / «в том числе:», у которой есть код.
            if name and pending_label is not None:
                pending_label["name"] = f"{pending_label['name']} {name}"
                pending_label = None
            last_label = name
            continue
        if name and last_label and name.startswith("(стр"):
            # «ДОХОДЫ» / «(стр. 020 + стр. 030 …)» — наименование и формула в разных строках.
            name = f"{last_label} {name}"
        last_label = None
        line_seq[0] += 1
        line = {
            "line_no": line_seq[0],
            "section": section,
            "sheet_name": sheet.name,
            "row_no": i,
            "name": name,
            "line_code": line_code,
            "group_name": None,
            **{k: None for k in ("admin_code", "kbk_code", "rzpr_code", "csr_code", "vr_code", "kosgu_code", "account_code")},
        }
        line.update(_code_fields(section, code_cells))
        line = {k: v for k, v in line.items() if not k.startswith("code_") or k == "code_raw"}
        if section == "settlements" and line_code:
            if line_code.endswith("0") or line_code == "899":
                group_name = name
            line["group_name"] = group_name
        pending_label = line if name and re.search(r"(из них|в том числе)\s*:?\s*$", name, re.I) else None
        report.lines.append(line)
        for c, d in vals:
            measure, level, activity = classes[c.col]
            report.values.append({
                "line_no": line_seq[0],
                "graph": c.graph,
                "measure": measure,
                "budget_level": level,
                "activity": activity,
                "value": (d * multiplier).quantize(Decimal("0.01")) if multiplier != 1 else d.quantize(Decimal("0.01")),
            })


def parse_workbook(wb: Workbook) -> ParsedReport:
    visible = [s for s in wb.sheets if not s.hidden and s.name != "XDO_METADATA"]
    if not visible:
        return ParsedReport(header=ReportHeader(), flags={"no_visible_sheets"})
    titul = next((s for s in visible if s.name.casefold().startswith(("титул", "titul"))), None)
    header = parse_header(titul, upto=45) if titul is not None else parse_header(visible[0])
    for s in visible[1:]:
        # недостающие атрибуты — с других листов (у 0503317_Р шапки нет)
        if header.form_code and header.report_date:
            break
        h2 = parse_header(s)
        for k, v in vars(h2).items():
            if getattr(header, k) in (None, 1) and v not in (None, 1):
                setattr(header, k, v)
    report = ParsedReport(header=header)
    line_seq = [0]
    legacy_mode = any(legacy.is_legacy_sheet(s) for s in visible)
    if legacy_mode:
        header.form_code = header.form_code or "0524100"
        if not header.unit_raw:
            header.unit_raw, header.unit_multiplier = "тыс. руб.", 1000
            report.flags.add("unit_assumed_thousand")
    for vi, sheet in enumerate(visible):
        if legacy_mode:
            section = legacy.sheet_section(sheet)
            if section is None:
                continue  # титульный лист
        else:
            section = sheet_section(header.form_code, sheet, vi)
        graph_rows = find_graph_rows(sheet)
        report.sheets.append({"sheet_name": sheet.name, "sheet_index": sheet.index, "section": section,
                              "nrows": sheet.nrows, "ncols": sheet.ncols, "tables": len(graph_rows)})
        if not graph_rows:
            report.flags.add(f"no_table:{sheet.name}")
            continue
        floor = 0
        prev_layout: Layout | None = None
        for k, gr in enumerate(graph_rows):
            layout = legacy.detect_legacy_layout(sheet, gr) if legacy_mode else detect_layout(sheet, gr, floor)
            if prev_layout is not None and not any(c.header for c in layout.columns) and \
                    [(c.col, c.graph) for c in layout.columns if c.code_part == 0] == \
                    [(c.col, c.graph) for c in prev_layout.columns if c.code_part == 0]:
                # Продолжение таблицы на следующей «странице» листа без текстовой шапки:
                # те же номера граф в тех же колонках — берём макет предыдущей таблицы.
                layout = Layout(header_top=gr, graph_row=gr, columns=prev_layout.columns)
            elif prev_layout is not None:
                # Отдельные пустые заголовки на «странице» — из той же графы предыдущей таблицы.
                prev_by = {(c.col, c.graph, c.code_part): c for c in prev_layout.columns}
                for i, c in enumerate(layout.columns):
                    p = prev_by.get((c.col, c.graph, c.code_part))
                    if not c.header and p is not None and p.header:
                        layout.columns[i] = Column(col=c.col, graph=c.graph, block=c.block, role=p.role,
                                                   header=p.header, code_part=c.code_part)
            prev_layout = layout
            nxt = graph_rows[k + 1] if k + 1 < len(graph_rows) else None
            if nxt is not None:
                nxt_top = (legacy.detect_legacy_layout(sheet, nxt) if legacy_mode else detect_layout(sheet, nxt, gr)).header_top
                last = nxt_top - 1
            else:
                last = sheet.nrows
            report.layouts.append({
                "sheet_name": sheet.name, "section": section, "table_no": k, "graph_row": gr,
                "header_top": layout.header_top, "first_row": gr + 1, "last_row": last,
                "columns": layout.columns,
            })
            parse_table(sheet, layout, gr + 1, last, section, report, line_seq, header.unit_multiplier)
            floor = gr
    if header.form_code is None:
        report.flags.add("no_form_code")
    if header.report_date is None:
        report.flags.add("no_report_date")
    return report
