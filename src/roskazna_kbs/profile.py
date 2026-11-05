"""Профилирование книг: какие листы есть, их размеры, код формы и дата в шапке.

Результат — ``data/catalog/sheets.csv``: одна строка на лист каждой книги всех
пакетов. Используется для обзора макетов разных лет и как вход для выбора парсера.
"""

from __future__ import annotations

import csv
import logging
import re

from .archive import iter_members, read_files
from .config import SHEETS_CSV
from .workbook import open_workbook

log = logging.getLogger(__name__)

FORM_RE = re.compile(r"(?<!\d)(05033\d\d|0503117|05031\d\d|0507\d{3}|0503[0-9]{3})(?!\d)")
DATE_NUM_RE = re.compile(r"(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)")
UNIT_RE = re.compile(r"(тыс\.?\s*руб|млн\.?\s*руб|млрд\.?\s*руб|руб(?:\.|л))", re.I)

SHEET_FIELDS = [
    "file_id", "publication_type", "filter_year", "card_title", "member_id", "member_path",
    "detected_format", "sheet_index", "sheet_name", "hidden", "nrows", "ncols",
    "last_nonempty_row", "form_codes", "header_dates", "unit_hint", "head_text", "error",
]


def profile_workbook(file_row: dict, member, payload: bytes) -> list[dict]:
    base = {
        "file_id": file_row["file_id"],
        "publication_type": file_row["publication_type"],
        "filter_year": file_row["filter_year"],
        "card_title": file_row["card_title"],
        "member_id": member.member_id,
        "member_path": member.member_path,
        "detected_format": member.detected_format,
    }
    try:
        wb = open_workbook(payload, member.detected_format, name=member.member_path)
    except Exception as exc:  # noqa: BLE001 — профилирование не должно падать на одном файле
        return [{**base, "error": f"{type(exc).__name__}: {exc}"}]
    out = []
    for sh in wb.sheets:
        head = sh.text_head(40)
        out.append(
            {
                **base,
                "sheet_index": sh.index,
                "sheet_name": sh.name,
                "hidden": int(sh.hidden),
                "nrows": sh.nrows,
                "ncols": sh.ncols,
                "last_nonempty_row": sh.last_nonempty_row(),
                "form_codes": ";".join(sorted(set(FORM_RE.findall(head)))),
                "header_dates": ";".join(sorted({"%s.%s.%s" % m for m in DATE_NUM_RE.findall(head)}))[:200],
                "unit_hint": ";".join(sorted({m.lower() for m in UNIT_RE.findall(head)})),
                "head_text": head.replace("\n", " | ")[:800],
                "error": "",
            }
        )
    return out


def profile_all(limit: int | None = None, per_inner_archive: int | None = None) -> list[dict]:
    """Профиль всех книг. ``per_inner_archive`` — сколько книг брать из каждого
    вложенного архива (книги субъектов внутри одного архива однотипны)."""
    rows: list[dict] = []
    files = read_files()
    if limit:
        files = files[:limit]
    for i, f in enumerate(files, 1):
        seen: dict[str, int] = {}
        try:
            for m, payload in iter_members(f):
                if m.detected_format not in ("xls", "xlsx"):
                    continue
                if per_inner_archive:
                    key = m.parent_member_id
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > per_inner_archive:
                        continue
                rows.extend(profile_workbook(f, m, payload))
        except Exception as exc:  # noqa: BLE001
            log.error("profile failed for %s: %s", f["local_path"], exc)
        log.info("profiled %s/%s %s", i, len(files), f["local_path"])
    SHEETS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(SHEETS_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows
