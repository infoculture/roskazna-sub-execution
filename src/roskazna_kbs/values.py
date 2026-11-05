"""Нормализация значений ячеек: коды, суммы, тексты."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Маркеры «нет кода / итог»: латинские и кириллические x/X, а также прочерки.
TOTAL_MARKERS = {"x", "х", "-", "—", "–", "***", "xxx", "ххх"}

_SPACES = re.compile(r"[\s  ]+")
_NUM_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?$")


def as_text(value: object) -> str | None:
    """Строка без лишних пробелов; None для пустых."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = _SPACES.sub(" ", str(value)).strip()
    return text or None


def as_code(value: object, width: int | None = None) -> str | None:
    """Код классификации как строка.

    * число 101.0 → "101"; при ``width`` число дополняется ведущими нулями
      (строка остаётся как есть);
    * внутренние пробелы удаляются ("000 1 01 02010 01 0000 110" → "00010102010010000110");
    * маркер итога (x/х/-) возвращается как есть в нижнем регистре — его
      отличает ``is_marker``.
    """
    if value is None:
        return None
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if isinstance(value, float):
        if value.is_integer():
            value = int(value)
        else:
            value = repr(value)
    text = _SPACES.sub("", str(value)).strip()
    if not text:
        return None
    if text.casefold() in TOTAL_MARKERS:
        return text.casefold()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    # Ведущие нули восстанавливаются только у чисел (Excel хранит «010» как 10);
    # строковые коды («01», «031» старой классификации) сохраняются как есть.
    if width and numeric and text.isdigit() and len(text) < width:
        text = text.zfill(width)
    return text


def is_marker(code: str | None) -> bool:
    return code is not None and code.casefold() in TOTAL_MARKERS


def as_decimal(value: object) -> Decimal | None:
    """Сумма как Decimal. Числа Excel и строки «37 283 772 222 778,74».

    Пусто, маркеры x/х/-, нечисловые строки → None.
    Для float используется ``repr`` (кратчайшее представление), чтобы
    1480024.7100000018 сохранился без потерь, а 0.1 не стал 0.1000000000000000055.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        return Decimal(repr(value))
    text = _SPACES.sub("", str(value)).replace("−", "-")
    if not text or text.casefold() in TOTAL_MARKERS:
        return None
    if text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")
    elif text.count(",") >= 1 and text.count(".") == 1:
        text = text.replace(",", "")
    if not _NUM_RE.match(text):
        return None
    try:
        return Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None


def raw_text(value: object) -> str | None:
    """Исходное значение ячейки как строка — для поля value_raw."""
    if value is None:
        return None
    if isinstance(value, float):
        return repr(value)
    return str(value)
