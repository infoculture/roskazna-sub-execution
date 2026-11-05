"""Разбор всех пакетов и сборка базы DuckDB.

Этапы:

1. **parse** — каждый пакет (ZIP) разбирается в отдельном процессе; результат —
   Parquet-файлы в ``data/db/parts/<package_no>/``: ``report``, ``line``,
   ``value``, ``layout_column``, ``member``, ``chess_indicator``,
   ``chess_value``, ``territory_seen``. Пакет пропускается, если его части уже
   есть и SHA-256 пакета не изменился (файл ``_SUCCESS``).
2. **load** — DuckDB создаёт таблицы из ``sql/schema.sql``, заливает справочники
   и все части, затем выполняет ``sql/views.sql``.
3. **export** — выгрузка таблиц в Parquet (``data/export/``), факты — с
   разбиением по форме и году.

Суррогатные ключи:

* ``report_id = package_no * 10000 + порядковый номер книги в пакете``;
* ``line_id = report_id * 100000 + номер строки в отчёте``.

``package_no`` постоянен (присваивается при первой загрузке пакета, см.
``files.csv``), поэтому ключи не меняются между пересборками, пока не меняется
содержимое пакета.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import chess as chess_mod
from .archive import iter_members, read_files
from .config import DB_DIR, DB_PATH, EXPORT_DIR, ROOT
from .dims import ACTIVITIES, BUDGET_LEVELS, MEASURES, classify_columns
from .forms import parse_workbook
from . import htmlform
from .territory import SUMMARY_CODE, parse_terrlist, territory_from_member_name
from .workbook import open_workbook

log = logging.getLogger(__name__)

PARTS_DIR = DB_DIR / "parts"
SQL_DIR = ROOT / "sql"
DEC = pa.decimal128(18, 2)

LINE_FIELDS = ["admin_code", "kbk_code", "rzpr_code", "csr_code", "vr_code", "kosgu_code", "account_code"]

SCHEMAS = {
    "member": pa.schema([
        ("package_no", pa.int32()), ("file_id", pa.string()), ("member_id", pa.string()),
        ("parent_member_id", pa.string()), ("depth", pa.int16()), ("member_path", pa.string()),
        ("member_name", pa.string()), ("member_name_raw", pa.string()), ("name_encoding", pa.string()),
        ("extension", pa.string()), ("detected_format", pa.string()), ("size_bytes", pa.int64()),
        ("sha256", pa.string()), ("zip_datetime", pa.string()), ("content_kind", pa.string()),
    ]),
    "report": pa.schema([
        ("report_id", pa.int64()), ("package_no", pa.int32()), ("file_id", pa.string()),
        ("member_id", pa.string()), ("member_path", pa.string()), ("source_format", pa.string()),
        ("publication_type", pa.string()), ("form_code", pa.string()), ("activity", pa.string()),
        ("report_date", pa.date32()), ("name_report_date", pa.date32()),
        ("period_year", pa.int16()), ("period_months", pa.int8()),
        ("territory_code", pa.string()), ("fin_authority", pa.string()), ("budget_name", pa.string()),
        ("okpo", pa.string()), ("oktmo", pa.string()), ("okei", pa.string()),
        ("periodicity", pa.string()), ("unit_raw", pa.string()), ("unit_multiplier", pa.int64()),
        ("sheets", pa.string()), ("n_lines", pa.int32()), ("n_values", pa.int32()), ("quality_flags", pa.string()),
    ]),
    "line": pa.schema([
        ("line_id", pa.int64()), ("report_id", pa.int64()), ("line_no", pa.int32()), ("indicator_id", pa.int64()),
        ("form_code", pa.string()), ("section", pa.string()), ("sheet_name", pa.string()), ("row_no", pa.int32()),
        ("name", pa.string()), ("line_code", pa.string()), ("group_name", pa.string()),
        *[(f, pa.string()) for f in LINE_FIELDS],
        ("classification_code", pa.string()), ("code_raw", pa.string()), ("is_total_marker", pa.bool_()),
    ]),
    "value": pa.schema([
        ("line_id", pa.int64()), ("report_id", pa.int64()), ("graph", pa.int16()),
        ("measure", pa.string()), ("budget_level", pa.string()), ("activity", pa.string()), ("value", DEC),
    ]),
    "layout_column": pa.schema([
        ("report_id", pa.int64()), ("sheet_name", pa.string()), ("section", pa.string()),
        ("table_no", pa.int16()), ("graph_row", pa.int32()), ("col", pa.int16()), ("graph", pa.int16()),
        ("block", pa.int16()), ("role", pa.string()), ("code_part", pa.int16()), ("header", pa.string()),
        ("measure", pa.string()), ("budget_level", pa.string()), ("activity", pa.string()),
    ]),
    "chess_indicator": pa.schema([
        ("package_no", pa.int32()), ("section", pa.string()), ("indicator_code", pa.string()),
        ("line_code", pa.string()), ("admin_code", pa.string()), ("classification_code", pa.string()),
        ("name", pa.string()),
    ]),
    "chess_value": pa.schema([
        ("package_no", pa.int32()), ("member_id", pa.string()), ("report_date", pa.date32()),
        ("section", pa.string()), ("graph", pa.int16()), ("measure", pa.string()), ("budget_level", pa.string()),
        ("territory_kind", pa.string()), ("territory_code", pa.string()), ("territory_name", pa.string()),
        ("indicator_code", pa.string()), ("value", DEC),
    ]),
    "territory_seen": pa.schema([
        ("package_no", pa.int32()), ("report_date", pa.date32()), ("source", pa.string()),
        ("territory_code", pa.string()), ("territory_name", pa.string()),
        ("okrug_code", pa.string()), ("okrug_name", pa.string()),
    ]),
}


# --- разбор одного пакета ----------------------------------------------------

INDICATOR_ATTRS = ["form_code", "section", "line_code", "name", "group_name", *LINE_FIELDS,
                   "classification_code", "code_raw", "is_total_marker"]


def indicator_id(attrs: dict) -> int:
    """Стабильный идентификатор показателя — 63-битный хеш его атрибутов.

    Показатель = уникальная комбинация (форма, раздел, код строки, наименование,
    коды классификации). Один и тот же показатель в разных отчётах получает один id
    без общего счётчика между параллельными процессами разбора.
    """
    key = "\x1f".join("" if attrs.get(k) is None else str(attrs.get(k)) for k in INDICATOR_ATTRS)
    return int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "big") >> 1


def _period(report_date: date | None, publication_type: str) -> tuple[int | None, int | None]:
    """Финансовый год и число месяцев с начала года для среза «на 1 число».

    Срез на 1 января — итоги прошлого года (12 месяцев)."""
    if report_date is None:
        return None, None
    if report_date.month == 1 and report_date.day == 1:
        return report_date.year - 1, 12
    return report_date.year, report_date.month - 1


def _name_date(name: str) -> date | None:
    m = re.search(r"(?<!\d)(\d{2})\.?(\d{2})\.?(\d{4})(?!\d)", name)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def content_kind(member) -> str:
    if member.detected_format == "zip":
        return "archive"
    if member.detected_format in ("xls", "xlsx"):
        low = member.member_name.casefold()
        if low.startswith("log_") or "роспись" in low or low.startswith("информация об ад"):
            # журналы выгрузки (2003 г.); «Полная роспись …» (2000–2005 гг., заполнены только
            # строки субъектов); «Информация об АД …» (2022 г., не форма отчётности)
            return "other"
        return "chess" if chess_mod.is_chess_member(member.member_path) else "form"
    if member.detected_format == "html":
        n = member.member_name.casefold()
        if n.startswith("terrlist"):
            return "territory_list"
        if n.startswith("reportscatalog"):
            return "html_index"
        return "form_html"
    return "other"


class _Buf:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {k: [] for k in SCHEMAS}

    def write(self, out_dir: Path) -> dict[str, int]:
        out_dir.mkdir(parents=True, exist_ok=True)
        counts = {}
        for name, rows in self.rows.items():
            table = pa.Table.from_pylist(rows, schema=SCHEMAS[name])
            pq.write_table(table, out_dir / f"{name}.parquet", compression="zstd")
            counts[name] = len(rows)
        return counts


def parse_package(file_row: dict, out_root: str) -> dict:
    """Разбор пакета. Два прохода по архиву:

    1. книги Excel (формы и «шахматки»), справочник территорий;
    2. HTML-отчёты — только для пар (форма, территория), которых нет в Excel
       этого пакета (для 2008–2011 гг. HTML — единственный источник).
    """
    t0 = time.time()
    package_no = int(file_row["package_no"])
    out_dir = Path(out_root) / f"{package_no:04d}"
    buf = _Buf()
    seq = [0]
    errors: list[str] = []
    seen_sha: set[str] = set()
    covered: set[tuple[str | None, str | None]] = set()
    pkg_date = _name_date(file_row.get("local_path", ""))
    html_members: list[str] = []
    for m, payload in iter_members(file_row):
        kind = content_kind(m)
        if kind in ("form", "chess"):
            if m.sha256 in seen_sha:
                kind = "duplicate"  # та же книга ещё раз в другом вложенном архиве пакета
            seen_sha.add(m.sha256)
        if kind == "form_html":
            html_members.append(m.member_id)
        _member_row(buf, package_no, m, kind)
        try:
            if kind == "form":
                wb = open_workbook(payload, m.detected_format, name=m.member_path)
                terr = territory_from_member_name(m.member_name)
                if terr is None and chess_mod.looks_like_chess(wb):
                    meta = chess_mod.generic_meta(m.member_path, wb, pkg_date.isoformat() if pkg_date else None)
                    if meta is not None:
                        _emit_chess(buf, package_no, m, wb, meta)
                        buf.rows["member"][-1]["content_kind"] = "chess"
                        continue
                r = parse_workbook(wb)
                if r.header.form_code is None and not r.lines:
                    buf.rows["member"][-1]["content_kind"] = "unparsed"  # не форма и не «шахматка»
                    continue
                _emit_report(buf, file_row, package_no, seq, m, r, terr, _name_date(m.member_name), pkg_date)
                covered.add((r.header.form_code, terr))
            elif kind == "chess":
                _parse_chess(buf, package_no, m, payload, pkg_date)
            elif kind == "territory_list":
                for code, name in parse_terrlist(payload):
                    buf.rows["territory_seen"].append({"package_no": package_no, "report_date": pkg_date, "source": "terrlist",
                                                       "territory_code": code, "territory_name": name,
                                                       "okrug_code": None, "okrug_name": None})
        except Exception as exc:  # noqa: BLE001 — одна битая книга не должна останавливать сборку
            errors.append(f"{m.member_path}: {type(exc).__name__}: {exc}")
            log.exception("failed %s", m.member_path)
    if html_members:
        wanted = set(html_members)
        kinds = {}
        for m, payload in iter_members(file_row):
            if m.member_id not in wanted:
                continue
            meta = htmlform.parse_member_name(m.member_path)
            if meta is None:
                kinds[m.member_id] = "html_other"
                continue
            terr = htmlform_territory(meta["territory_code"])
            if (meta["form_code"], terr) in covered:
                kinds[m.member_id] = "form_html_dup"  # есть Excel-версия того же отчёта
                continue
            try:
                r = htmlform.parse_html_report(payload)
                if meta["activity"] and not any(f.startswith("activity:") for f in r.flags):
                    for v in r.values:
                        v["activity"] = v["activity"] or meta["activity"]
                    r.flags.add(f"activity:{meta['activity']}")
                _emit_report(buf, file_row, package_no, seq, m, r, terr, None, pkg_date)
                kinds[m.member_id] = "form_html"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{m.member_path}: {type(exc).__name__}: {exc}")
                log.exception("failed %s", m.member_path)
        for row in buf.rows["member"]:
            if row["member_id"] in kinds:
                row["content_kind"] = kinds[row["member_id"]]
    counts = buf.write(out_dir)
    (out_dir / "_SUCCESS").write_text(json.dumps({"sha256": file_row["sha256"], "counts": counts, "errors": errors,
                                                  "seconds": round(time.time() - t0, 1)}, ensure_ascii=False))
    return {"package_no": package_no, "counts": counts, "errors": errors, "seconds": round(time.time() - t0, 1)}


def htmlform_territory(code: str) -> str:
    """В HTML-наборе итог по РФ — файл с кодом 00; в Excel — 95."""
    return SUMMARY_CODE if code == "00" else code


def _member_row(buf: _Buf, package_no: int, m, kind: str) -> None:
    rec = {k: getattr(m, k) for k in ("file_id", "member_id", "parent_member_id", "depth", "member_path",
                                       "member_name", "member_name_raw", "name_encoding", "extension",
                                       "detected_format", "size_bytes", "sha256", "zip_datetime")}
    buf.rows["member"].append({"package_no": package_no, **rec, "content_kind": kind})


def _emit_report(buf: _Buf, file_row: dict, package_no: int, seq: list[int], m, r, terr: str | None,
                 name_date: date | None, pkg_date: date | None = None) -> None:
    seq[0] += 1
    report_id = package_no * 10000 + seq[0]
    h = r.header
    report_date = h.report_date or name_date
    if h.report_date and name_date and h.report_date != name_date:
        r.flags.add("date_mismatch_name")
    if report_date is None and pkg_date is not None:
        report_date = pkg_date  # дата не указана ни в шапке, ни в имени файла — дата пакета
        r.flags.add("date_from_package")
    year, months = _period(report_date, file_row["publication_type"])
    if terr is None and re.search(r"межрегиональное\s+операционное\s+управление", h.fin_authority or "", re.I):
        # Свод по РФ составляет МОУ Федерального казначейства; в имени файла кода нет (0503317.txt.xls)
        terr = SUMMARY_CODE
        r.flags.add("territory_from_header")
    if terr is None:
        r.flags.add("no_territory_code")
    activity = next((f.split(":", 1)[1] for f in r.flags if f.startswith("activity:")), None)
    buf.rows["report"].append({
        "report_id": report_id, "package_no": package_no, "file_id": m.file_id, "member_id": m.member_id,
        "member_path": m.member_path, "source_format": "html" if m.detected_format == "html" else m.detected_format,
        "publication_type": file_row["publication_type"],
        "form_code": h.form_code, "activity": activity, "report_date": report_date, "name_report_date": name_date,
        "period_year": year, "period_months": months, "territory_code": terr,
        "fin_authority": h.fin_authority, "budget_name": h.budget_name, "okpo": h.okpo, "oktmo": h.oktmo,
        "okei": h.okei, "periodicity": h.periodicity, "unit_raw": h.unit_raw, "unit_multiplier": h.unit_multiplier,
        "sheets": ";".join(f"{s['sheet_name']}={s['section']}" for s in r.sheets),
        "n_lines": len(r.lines), "n_values": len(r.values), "quality_flags": ";".join(sorted(r.flags)) or None,
    })
    if terr and h.budget_name:
        buf.rows["territory_seen"].append({"package_no": package_no, "report_date": report_date, "source": "report_header",
                                           "territory_code": terr, "territory_name": h.budget_name,
                                           "okrug_code": None, "okrug_name": None})
    lines = buf.rows["line"]
    for ln in r.lines:
        attrs = {
            "form_code": h.form_code, "section": ln["section"], "name": ln["name"],
            "line_code": ln["line_code"], "group_name": ln.get("group_name"),
            **{f: ln.get(f) for f in LINE_FIELDS},
            "classification_code": ln.get("classification_code"), "code_raw": ln.get("code_raw"),
            "is_total_marker": bool(ln.get("is_total_marker")),
        }
        lines.append({
            "line_id": report_id * 100000 + ln["line_no"], "report_id": report_id, "line_no": ln["line_no"],
            "indicator_id": indicator_id(attrs), "sheet_name": ln["sheet_name"], "row_no": ln["row_no"], **attrs,
        })
    values = buf.rows["value"]
    for v in r.values:
        values.append({"line_id": report_id * 100000 + v["line_no"], "report_id": report_id, "graph": v["graph"],
                       "measure": v["measure"], "budget_level": v["budget_level"], "activity": v["activity"],
                       "value": v["value"]})
    lc = buf.rows["layout_column"]
    for L in r.layouts:
        classes = classify_columns(L["columns"])
        for c in L["columns"]:
            meas, lev, act = classes.get(c.col, (None, None, None))
            lc.append({"report_id": report_id, "sheet_name": L["sheet_name"], "section": L["section"],
                       "table_no": L["table_no"], "graph_row": L["graph_row"], "col": c.col, "graph": c.graph,
                       "block": c.block, "role": c.role, "code_part": c.code_part, "header": c.header,
                       "measure": meas, "budget_level": lev, "activity": act})


def _parse_chess(buf: _Buf, package_no: int, m, payload: bytes, pkg_date: date | None = None) -> None:
    meta = chess_mod.parse_member_name(m.member_path, pkg_date.isoformat() if pkg_date else None)
    if meta is None:
        return
    wb = open_workbook(payload, m.detected_format, name=m.member_path)
    _emit_chess(buf, package_no, m, wb, meta)


def _emit_chess(buf: _Buf, package_no: int, m, wb, meta: dict) -> None:
    inds, vals = chess_mod.parse_chess(wb, meta)
    for ind in inds:
        buf.rows["chess_indicator"].append({"package_no": package_no, "section": ind["section"],
                                            "indicator_code": ind["indicator_code"], "line_code": ind["line_code"],
                                            "admin_code": ind["admin_code"],
                                            "classification_code": ind["classification_code"], "name": ind["name"]})
    rd = date.fromisoformat(meta["report_date"])
    for v in vals:
        buf.rows["chess_value"].append({"package_no": package_no, "member_id": m.member_id, "report_date": rd,
                                        **{k: v[k] for k in ("section", "graph", "measure", "budget_level",
                                                             "territory_kind", "territory_code", "territory_name",
                                                             "indicator_code", "value")}})
    if meta["section"] == "income" and meta["graph"] == 18:
        for r in chess_mod.okrug_membership(wb):
            buf.rows["territory_seen"].append({"package_no": package_no, "report_date": rd, "source": "chess",
                                               **r})


def parse_all(years: set[int] | None = None, limit: int | None = None, workers: int | None = None,
              force: bool = False) -> list[dict]:
    files = read_files()
    missing = [f for f in files if not f.get("package_no")]
    if missing:
        raise RuntimeError("В files.csv нет package_no — запустите `roskazna_kbs download` для обновления реестра")
    if years:
        files = [f for f in files if int(f["filter_year"]) in years]
    files.sort(key=lambda f: -int(f.get("size_bytes") or 0))  # крупные — раньше, для равномерной загрузки
    if limit:
        files = files[:limit]
    todo = []
    for f in files:
        done = PARTS_DIR / f"{int(f['package_no']):04d}" / "_SUCCESS"
        if not force and done.exists():
            try:
                if json.loads(done.read_text())["sha256"] == f["sha256"]:
                    continue
            except (ValueError, KeyError):
                pass
        todo.append(f)
    log.info("parse: %s packages to parse (%s up to date)", len(todo), len(files) - len(todo))
    results = []
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 2))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(parse_package, f, str(PARTS_DIR)): f for f in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            f = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.error("package %s failed: %s", f["local_path"], exc)
                continue
            results.append(res)
            log.info("[%s/%s] %s: %s values, %s chess, %ss%s", i, len(todo), f["local_path"],
                     res["counts"].get("value"), res["counts"].get("chess_value"), res["seconds"],
                     f", {len(res['errors'])} errors" if res["errors"] else "")
    return results


# --- загрузка в DuckDB ----------------------------------------------------------

def _exec_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text(encoding="utf-8"))


def load_database(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp.duckdb")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET enable_progress_bar = false")
    _exec_sql_file(con, SQL_DIR / "schema.sql")
    _load_dims(con)
    _load_provenance(con)
    parts = str(PARTS_DIR / "*" / "{}.parquet")
    for table in ("member", "report", "line", "value", "layout_column", "chess_indicator", "chess_value", "territory_seen"):
        t0 = time.time()
        src = f"read_parquet('{parts.format(table)}', union_by_name = true)"
        if table == "member":
            con.execute(f"INSERT INTO archive_member SELECT * EXCLUDE (content_kind), content_kind FROM {src}")
        elif table == "chess_indicator":
            con.execute(f"""INSERT INTO chess_indicator
                SELECT section, indicator_code, any_value(line_code), any_value(admin_code),
                       any_value(classification_code), arg_max(name, package_no), min(package_no), max(package_no)
                FROM {src} GROUP BY section, indicator_code""")
        elif table == "layout_column":
            con.execute(f"INSERT INTO layout_column SELECT * FROM {src}")
        elif table == "line":
            con.execute(f"""INSERT INTO indicator
                SELECT indicator_id, {', '.join(f'any_value({a})' for a in INDICATOR_ATTRS)},
                       count(*) AS n_lines
                FROM {src} GROUP BY indicator_id""")
            con.execute(f"INSERT INTO line SELECT line_id, report_id, line_no, indicator_id, sheet_name, row_no FROM {src}")
        elif table == "territory_seen":
            con.execute(f"INSERT INTO territory_seen SELECT DISTINCT * FROM {src}")
        else:
            con.execute(f"INSERT INTO {table} SELECT * FROM {src}")
        n = con.execute(f"SELECT count(*) FROM {'archive_member' if table == 'member' else table}").fetchone()[0]
        log.info("load %s: %s rows, %.1fs", table, n, time.time() - t0)
    _build_territory(con)
    _exec_sql_file(con, SQL_DIR / "views.sql")
    con.execute("CHECKPOINT")
    con.close()
    if db_path.exists():
        db_path.unlink()
    shutil.move(tmp, db_path)
    log.info("database: %s", db_path)


def _load_dims(con: duckdb.DuckDBPyConnection) -> None:
    con.executemany("INSERT INTO budget_level VALUES (?, ?, ?, ?)", BUDGET_LEVELS)
    con.executemany("INSERT INTO measure VALUES (?, ?, ?)", MEASURES)
    con.executemany("INSERT INTO activity VALUES (?, ?)", ACTIVITIES)
    for name in ("form", "section", "publication_type"):
        path = SQL_DIR / "dicts" / f"{name}.csv"
        con.execute(f"INSERT INTO {name} SELECT * FROM read_csv('{path}', header = true, all_varchar = false)")


def _load_provenance(con: duckdb.DuckDBPyConnection) -> None:
    from .config import CATALOG_CSV, FILES_CSV

    con.execute(f"""
        INSERT INTO source_file
        SELECT CAST(package_no AS INTEGER), file_id, publication_type, CAST(filter_year AS INTEGER), card_title,
               file_path, url, local_path, TRY_CAST(size_bytes AS BIGINT), sha256, TRY_CAST(http_status AS INTEGER),
               last_modified, zip_ok, TRY_CAST(downloaded_at AS TIMESTAMPTZ)
        FROM read_csv('{FILES_CSV}', header = true, all_varchar = true)""")
    con.execute(f"""
        INSERT INTO catalog_card
        SELECT row_number() OVER (ORDER BY c.publication_type_id, CAST(c.filter_year AS INTEGER) DESC,
                                           CAST(c.page AS INTEGER), CAST(c.position AS INTEGER)),
               c.publication_type, CAST(c.filter_year AS INTEGER), CAST(c.page AS INTEGER), CAST(c.position AS INTEGER),
               c.card_title, c.file_path, f.file_id, c.published_at_raw, TRY_CAST(c.published_at AS TIMESTAMP),
               TRY_CAST(c.retrieved_at AS TIMESTAMPTZ)
        FROM read_csv('{CATALOG_CSV}', header = true, all_varchar = true) c
        LEFT JOIN source_file f USING (file_path)""")


def _build_territory(con: duckdb.DuckDBPyConnection) -> None:
    """Справочник территорий: код Казначейства → последнее наименование и федеральный округ."""
    con.execute("""
        INSERT INTO territory
        WITH names AS (
            SELECT territory_code, territory_name, report_date, source,
                   row_number() OVER (PARTITION BY territory_code
                                      ORDER BY report_date DESC NULLS LAST,
                                               CASE source WHEN 'terrlist' THEN 0 WHEN 'chess' THEN 1 ELSE 2 END) AS rn
            FROM territory_seen WHERE source IN ('terrlist', 'chess')
        ), okr AS (
            SELECT territory_code, arg_max(okrug_code, report_date) AS okrug_code, arg_max(okrug_name, report_date) AS okrug_name
            FROM territory_seen WHERE source = 'chess' GROUP BY territory_code
        ), span AS (
            SELECT territory_code, min(report_date) AS first_report_date, max(report_date) AS last_report_date, count(*) AS n_reports
            FROM report WHERE territory_code IS NOT NULL GROUP BY territory_code
        ), hdr AS (
            SELECT territory_code, arg_max(budget_name, report_date) AS budget_name FROM report
            WHERE territory_code IS NOT NULL AND budget_name IS NOT NULL GROUP BY territory_code
        )
        SELECT s.territory_code,
               coalesce(n.territory_name, h.budget_name) AS name,
               h.budget_name AS last_budget_name,
               o.okrug_code, o.okrug_name,
               s.first_report_date, s.last_report_date, s.n_reports
        FROM span s
        LEFT JOIN names n ON n.territory_code = s.territory_code AND n.rn = 1
        LEFT JOIN okr o ON o.territory_code = s.territory_code
        LEFT JOIN hdr h ON h.territory_code = s.territory_code
    """)


def build_database(years: set[int] | None = None, limit: int | None = None, force: bool = False,
                   workers: int | None = None) -> None:
    parse_all(years=years, limit=limit, force=force, workers=workers)
    load_database()


def export_parquet(db_path: Path = DB_PATH, out_dir: Path = EXPORT_DIR) -> None:
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute("SET preserve_insertion_order = false")
    con.execute("SET enable_progress_bar = false")
    out_dir.mkdir(parents=True, exist_ok=True)
    small = [r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' "
        "AND table_name NOT IN ('value', 'line', 'chess_value', 'layout_column') ORDER BY 1").fetchall()]
    for t in small:
        con.execute(f"COPY {t} TO '{out_dir / (t + '.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    for t in ("value", "line", "layout_column"):
        target = out_dir / t
        if target.exists():
            shutil.rmtree(target)
        con.execute(f"""COPY (SELECT x.*, r.form_code, year(r.report_date) AS report_year
                              FROM {t} x JOIN report r USING (report_id))
                        TO '{target}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (form_code, report_year))""")
    target = out_dir / "chess_value"
    if target.exists():
        shutil.rmtree(target)
    con.execute(f"""COPY (SELECT *, year(report_date) AS report_year FROM chess_value)
                    TO '{target}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (report_year))""")
    con.close()
    log.info("export: %s", out_dir)
