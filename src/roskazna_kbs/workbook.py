"""Единый интерфейс чтения XLS (BIFF) и XLSX.

Обе библиотеки сводятся к структуре «лист → список строк → список значений».
Значения не преобразуются: числа остаются float/int, строки — str, пустые — None.
Номера строк и колонок — 1-based, как в Excel.
"""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
import xlrd


@dataclass
class Sheet:
    name: str
    index: int
    hidden: bool
    rows: list[list[object]] = field(default_factory=list)
    # Объединённые ячейки: (первая строка, последняя строка, первая колонка, последняя колонка),
    # 1-based, включительно. Для XLSX в режиме read_only не заполняется.
    merged: list[tuple[int, int, int, int]] = field(default_factory=list)

    @property
    def nrows(self) -> int:
        return len(self.rows)

    @property
    def ncols(self) -> int:
        return max((len(r) for r in self.rows), default=0)

    def cell(self, row: int, col: int) -> object:
        """1-based доступ; за пределами диапазона — None."""
        if row < 1 or row > len(self.rows):
            return None
        r = self.rows[row - 1]
        return r[col - 1] if 0 < col <= len(r) else None

    def last_nonempty_row(self) -> int:
        for i in range(len(self.rows), 0, -1):
            if any(v not in (None, "") for v in self.rows[i - 1]):
                return i
        return 0

    def text_head(self, nrows: int = 40) -> str:
        """Склеенный текст первых строк — для поиска кода формы, даты, единиц."""
        parts = []
        for r in self.rows[:nrows]:
            for v in r:
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
        return "\n".join(parts)


@dataclass
class Workbook:
    path: Path
    fmt: str
    sheets: list[Sheet]

    def sheet(self, name: str) -> Sheet | None:
        for s in self.sheets:
            if s.name == name:
                return s
        low = name.casefold().strip()
        for s in self.sheets:
            if s.name.casefold().strip() == low:
                return s
        return None

    @property
    def visible_sheets(self) -> list[Sheet]:
        return [s for s in self.sheets if not s.hidden]


def _trim(row: list[object]) -> list[object]:
    end = len(row)
    while end and row[end - 1] in (None, ""):
        end -= 1
    return row[:end]


def _read_xls(src: Path | bytes) -> list[Sheet]:
    data = src if isinstance(src, bytes) else Path(src).read_bytes()
    try:
        # formatting_info нужен ради объединённых ячеек шапки (merged_cells).
        return _read_xls_book(data, formatting_info=True)
    except NotImplementedError:  # formatting_info не поддерживается для этой версии BIFF
        return _read_xls_book(data, formatting_info=False)
    except (IndexError, KeyError, AssertionError):
        # Битые записи форматирования (XF) в отдельных книгах — читаем без них.
        return _read_xls_book(data, formatting_info=False)


def _read_xls_book(data: bytes, formatting_info: bool) -> list[Sheet]:
    book = xlrd.open_workbook(file_contents=data, on_demand=True, formatting_info=formatting_info)
    sheets = []
    for idx in range(book.nsheets):
        sh = book.sheet_by_index(idx)
        rows = []
        for r in range(sh.nrows):
            vals = []
            for c, (ctype, value) in enumerate(zip(sh.row_types(r), sh.row_values(r))):
                if ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    vals.append(None)
                elif ctype == xlrd.XL_CELL_ERROR:
                    vals.append(None)
                elif ctype == xlrd.XL_CELL_DATE:
                    try:
                        vals.append(xlrd.xldate.xldate_as_datetime(value, book.datemode))
                    except Exception:
                        vals.append(value)
                elif ctype == xlrd.XL_CELL_BOOLEAN:
                    vals.append(bool(value))
                else:
                    vals.append(value if value != "" else None)
            rows.append(_trim(vals))
        # Убираем пустой хвост листа
        while rows and not rows[-1]:
            rows.pop()
        merged = [(rlo + 1, rhi, clo + 1, chi) for rlo, rhi, clo, chi in getattr(sh, "merged_cells", [])]
        sheets.append(Sheet(name=sh.name, index=idx, hidden=bool(sh.visibility), rows=rows, merged=merged))
        book.unload_sheet(idx)
    book.release_resources()
    return sheets


def _read_xlsx(src: Path | bytes) -> list[Sheet]:
    book = openpyxl.load_workbook(io.BytesIO(src) if isinstance(src, bytes) else str(src), read_only=True, data_only=True)
    sheets = []
    for idx, ws in enumerate(book.worksheets):
        # В части книг «по графам» тег <dimension> = A1: без сброса read_only-режим
        # вернул бы одну ячейку.
        if hasattr(ws, "reset_dimensions"):
            ws.reset_dimensions()
        rows = []
        for row in ws.iter_rows(values_only=True):
            vals = []
            for v in row:
                if isinstance(v, str) and v == "":
                    v = None
                vals.append(v)
            rows.append(_trim(vals))
        while rows and not rows[-1]:
            rows.pop()
        sheets.append(Sheet(name=ws.title, index=idx, hidden=ws.sheet_state != "visible", rows=rows))
    book.close()
    return sheets


def open_workbook(src: Path | str | bytes, fmt: str | None = None, name: str = "") -> Workbook:
    """Открывает книгу из файла или из байтов (член ZIP-архива).

    ``fmt`` — формат по сигнатуре (``xls``/``xlsx``), см. ``archive.sniff_format``.
    Расширению доверять нельзя: книги «по графам» — XLSX с расширением ``.xls``.
    """
    if not isinstance(src, bytes):
        src = Path(src)
        name = name or str(src)
        fmt = fmt or ("xlsx" if src.suffix.lower() in (".xlsx", ".xlsm") else "xls")
    if fmt == "xlsx":
        sheets = _read_xlsx(src)
    elif fmt == "xls":
        sheets = _read_xls(src)
    else:
        raise ValueError(f"Unsupported workbook format {fmt!r} for {name}")
    return Workbook(path=Path(name), fmt=fmt, sheets=sheets)


def is_date(v: object) -> bool:
    return isinstance(v, (dt.date, dt.datetime))
