"""Распознавание макета листа печатной формы.

Формы Казначейства (0503317, 0503320, 0503321, 0503323) свёрстаны одинаково:

* шапка формы (наименование, «на 1 ... г.», финансовый орган, бюджет, ОКПО,
  ОКТМО, единица измерения);
* «шапка таблицы» из нескольких строк с объединёнными ячейками: группа граф
  («Утвержденные бюджетные назначения», «Исполнено», «На начало года» …), под
  ней — уровни бюджета («бюджет субъекта Российской Федерации», «бюджеты
  городских округов» …);
* строка **номеров граф** (1, 2, 3, 4 …) — главный ориентир макета;
* строки данных. Лист может состоять из нескольких «блоков» — частей таблицы,
  поставленных рядом (утверждено слева, исполнено справа): в каждом блоке
  номера граф начинаются с 1, а графы «Наименование», «Код строки» повторяются.
  Таблица может продолжаться на следующей «странице» листа с повторённой шапкой.

Парсер ищет строку номеров граф, по шапке над ней определяет роль каждой
колонки (наименование, код строки, коды классификации, значение) и полный текст
заголовка графы-значения. Текст заголовка затем классифицируется в
:mod:`roskazna_kbs.dims` (показатель и уровень бюджета).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .values import as_text
from .workbook import Sheet

_INT_RE = re.compile(r"^\d{1,3}$")

NAME_RE = re.compile(r"наименование|^показател|^актив$|^пассив$|^статьи?$", re.I)
LINE_RE = re.compile(r"код\s*стро|^стро-?\s*ки?$|^№\s*стр|код\s+отчетност", re.I)
CODE_RE = re.compile(
    r"^код\b|бюджетной\s+классификац|косгу|^кбк|номер\s+забаланс|^счет"
    # сегменты кода в HTML-отчётах до 2011 г.
    r"|^(АП|ППП|Прз|РзПр|Рз\s*Прз|ЦСТ|ЦСР|КВР|ВР|ЭКР|ЭКЛ|СБУ|КВД|Глава|Групп\w*|Подгрупп\w*|Стат\w*|Подстат\w*|Элемент\w*|Программ\w*|Подвид\w*|Вид источника)$", re.I)
# Якорь верхней строки шапки таблицы и признак строк шапки формы (выше таблицы).
ANCHOR_RE = re.compile(r"наименование\s+показател|код\s*стро|^актив$|^пассив$|^наименование$", re.I)
# Колонтитул страницы печатной формы: «Форма 0503320 с. 11».
PAGE_LABEL_RE = re.compile(r"^форма\s+\d{7}\s*с\.?\s*\d+", re.I)
FORM_HEADER_RE = re.compile(r"единица\s+измерения|периодичность|по\s+океи|наименование\s+(бюджета|финансового)", re.I)


def norm_header(text: str) -> str:
    """Склеивает переносы в шапке: «консолидиро-ванный» → «консолидированный»."""
    t = re.sub(r"(\w)-\s+([а-яa-z])", r"\1\2", text)
    t = re.sub(r"(\w)-([а-яa-z])", r"\1\2", t)
    return re.sub(r"\s+", " ", t).strip()


def graph_number(v: object) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        if float(v).is_integer() and 0 < v < 400:
            return int(v)
        return None
    t = str(v).strip()
    if _INT_RE.match(t):
        return int(t)
    return None


def is_graph_row(row: list[object]) -> bool:
    """Строка номеров граф: первая непустая ячейка — 1, затем 2; не меньше
    четырёх номеров; кроме номеров допускается одна подпись слева."""
    cells = [v for v in row if v not in (None, "")]
    if len(cells) < 4:
        return False
    nums = [graph_number(v) for v in cells]
    if nums[0] is None:  # лист «Табл»: подпись «Выбытия» в колонке 1
        nums = nums[1:]
    if len(nums) < 4 or any(n is None for n in nums):
        return False
    return nums[0] == 1 and nums[1] == 2


def find_graph_rows(sheet: Sheet) -> list[int]:
    return [i for i, row in enumerate(sheet.rows, 1) if is_graph_row(row)]


@dataclass
class Column:
    col: int                    # номер колонки Excel (1-based)
    graph: int | None           # номер графы формы
    block: int                  # номер блока (0, 1, …)
    role: str                   # name | line_code | code | value
    header: str                 # полный текст заголовка (уровни шапки через « / »)
    code_part: int = 0          # номер подколонки кода внутри графы (0, 1, 2 …)


@dataclass
class Layout:
    header_top: int             # первая строка шапки таблицы
    graph_row: int              # строка номеров граф
    columns: list[Column] = field(default_factory=list)

    def by_role(self, role: str, block: int | None = None) -> list[Column]:
        return [c for c in self.columns if c.role == role and (block is None or c.block == block)]

    @property
    def nblocks(self) -> int:
        return 1 + max((c.block for c in self.columns), default=0)

    def signature(self) -> str:
        """Строка, описывающая макет: по ней отчёты группируются по макетам."""
        return "|".join(f"{c.block}:{c.graph}:{c.code_part}:{c.role}:{c.header}" for c in self.columns)


def _header_top(sheet: Sheet, graph_row: int, floor: int) -> int:
    """Верхняя строка шапки таблицы: самая верхняя (в пределах 12 строк над
    строкой номеров граф) строка с «Наименование показателя» или «Код строки»."""
    lo = max(floor + 1, graph_row - 12, 1)
    top = graph_row - 1
    for i in range(graph_row - 1, lo - 1, -1):
        texts = [norm_header(v) for v in sheet.rows[i - 1] if isinstance(v, str) and v.strip()]
        if any(FORM_HEADER_RE.search(t) for t in texts):
            break
        if any(ANCHOR_RE.search(t) for t in texts):
            top = i
    return top


def _header_texts(sheet: Sheet, top: int, graph_row: int, ncols: int, block_of: dict[int, int]) -> dict[int, list[str]]:
    """Тексты шапки над каждой колонкой с учётом объединённых ячеек."""
    grid: dict[tuple[int, int], str] = {}
    for i in range(top, graph_row):
        row = sheet.rows[i - 1]
        for j, v in enumerate(row, 1):
            t = norm_header(v) if isinstance(v, str) else None
            if t and not PAGE_LABEL_RE.match(t):
                grid[(i, j)] = t
    if sheet.merged:
        for r1, r2, c1, c2 in sheet.merged:
            if r2 < top or r1 >= graph_row:
                continue
            t = grid.get((r1, c1))
            if not t:
                continue
            for i in range(max(r1, top), min(r2, graph_row - 1) + 1):
                for j in range(c1, c2 + 1):
                    grid.setdefault((i, j), t)
    else:
        # Без сведений об объединениях: протягиваем текст строк-групп вправо
        # до следующей заполненной ячейки в пределах блока.
        for i in range(top, graph_row):
            filled = sorted(j for (ii, j) in grid if ii == i)
            if not filled or len(filled) > ncols // 3:
                continue
            for a, b in zip(filled, filled[1:] + [ncols + 1]):
                for j in range(a + 1, b):
                    if block_of.get(j) == block_of.get(a):
                        grid.setdefault((i, j), grid[(i, a)])
    out: dict[int, list[str]] = {}
    for j in range(1, ncols + 1):
        seq: list[str] = []
        for i in range(top, graph_row):
            t = grid.get((i, j))
            if t and (not seq or seq[-1] != t):
                seq.append(t)
        out[j] = seq
    return out


def detect_layout(sheet: Sheet, graph_row: int, floor: int = 0) -> Layout:
    """Макет таблицы, у которой строка номеров граф — ``graph_row``.

    ``floor`` — строка, выше которой шапку не искать (предыдущая таблица листа).
    """
    top = _header_top(sheet, graph_row, floor)
    grow = sheet.rows[graph_row - 1]
    ncols = max([len(grow)] + [len(sheet.rows[i - 1]) for i in range(top, graph_row)])

    graph_at = {j: n for j, v in enumerate(grow, 1) if (n := graph_number(v)) is not None}
    block_of: dict[int, int] = {}
    blk = 0
    seen_first = False
    for j in range(1, ncols + 1):
        if graph_at.get(j) == 1:
            if seen_first:
                blk += 1
            seen_first = True
        block_of[j] = blk

    texts = _header_texts(sheet, top, graph_row, ncols, block_of)

    cols: list[Column] = []
    role, graph, part = None, None, 0
    for j in range(1, ncols + 1):
        header = " / ".join(texts[j])
        if j in graph_at:
            graph, part = graph_at[j], 0
            h = header.casefold()
            first = texts[j][0] if texts[j] else ""
            if NAME_RE.search(first) or (not first and graph == 1):
                role = "name"
            elif LINE_RE.search(first):
                role = "line_code"
            elif CODE_RE.search(first):
                role = "code"
            else:
                role = "value"
            if role == "value" and not h and graph == 1:
                role = "name"
            cols.append(Column(col=j, graph=graph, block=block_of[j], role=role, header=header))
        elif role == "code":
            # Подколонки кода классификации (глава, КБК; Рз/Пр, ЦСР, ВР) без своего номера графы.
            part += 1
            cols.append(Column(col=j, graph=graph, block=block_of[j], role="code", header=cols[-1].header, code_part=part))
    return Layout(header_top=top, graph_row=graph_row, columns=cols)
