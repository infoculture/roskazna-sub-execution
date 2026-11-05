"""Отчёт о покрытии БД → docs/coverage.md.

    python scripts/coverage.py [путь к БД]

Таблицы: объём БД; отчёты по формам, годам и формату источника; ряды по датам
(число территорий в ф. 0503317); уровни бюджета по годам; признаки качества.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "db" / "roskazna_kbs.duckdb"
OUT = ROOT / "docs" / "coverage.md"


def md_table(con: duckdb.DuckDBPyConnection, sql: str) -> str:
    rel = con.sql(sql)
    cols = rel.columns
    rows = rel.fetchall()
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else (f"{v:,}".replace(",", " ")
                                                           if isinstance(v, int) and abs(v) >= 10000 else str(v))
                                     for v in r) + " |")
    return "\n".join(out)


def main() -> None:
    con = duckdb.connect(str(DB), read_only=True)
    parts = [
        "# Покрытие базы данных",
        "",
        f"Сгенерировано `scripts/coverage.py` {datetime.now():%Y-%m-%d %H:%M} по `{DB.name}`. Не править вручную.",
        "",
        "## Объём",
        "",
        md_table(con, """
            SELECT t AS "таблица", n AS "строк" FROM (
              SELECT 'source_file' t, count(*) n FROM source_file UNION ALL
              SELECT 'archive_member', count(*) FROM archive_member UNION ALL
              SELECT 'report', count(*) FROM report UNION ALL
              SELECT 'indicator', count(*) FROM indicator UNION ALL
              SELECT 'line', count(*) FROM line UNION ALL
              SELECT 'value', count(*) FROM value UNION ALL
              SELECT 'layout_column', count(*) FROM layout_column UNION ALL
              SELECT 'chess_indicator', count(*) FROM chess_indicator UNION ALL
              SELECT 'chess_value', count(*) FROM chess_value UNION ALL
              SELECT 'territory', count(*) FROM territory)"""),
        "",
        "## Пакеты",
        "",
        md_table(con, """
            SELECT publication_type AS "тип", count(*) AS "пакетов", min(filter_year) AS "с", max(filter_year) AS "по",
                   round(sum(size_bytes) / 1e9, 2) AS "ГБ"
            FROM source_file GROUP BY 1 ORDER BY 1"""),
        "",
        "## Отчёты по формам и годам",
        "",
        "Год — год среза (`report_date`); `terr` — число территорий (включая свод 95); "
        "`fmt` — из чего разобраны отчёты.",
        "",
        md_table(con, """
            SELECT year(report_date) AS "год", form_code AS "форма",
                   string_agg(DISTINCT source_format, ', ' ORDER BY source_format) AS fmt,
                   count(*) AS "отчётов", count(DISTINCT report_date) AS "дат",
                   max(nt) AS terr, sum(n_values) AS "значений"
            FROM (SELECT *, count(DISTINCT territory_code) OVER (PARTITION BY form_code, report_date) AS nt FROM v_report WHERE is_preferred)
            GROUP BY ALL ORDER BY 1, 2"""),
        "",
        "## Ф. 0503317: территории по датам среза",
        "",
        md_table(con, """
            SELECT year(report_date) AS "год",
                   string_agg(strftime(report_date, '%m') || ':' || nt, ' ' ORDER BY report_date) AS "месяц:территорий"
            FROM (SELECT report_date, count(DISTINCT territory_code) nt FROM v_report
                  WHERE is_preferred AND form_code IN ('0503317', '0524100') GROUP BY 1)
            GROUP BY 1 ORDER BY 1"""),
        "",
        "## Уровни бюджета по годам (ф. 0503317 / 0524100)",
        "",
        md_table(con, """
            SELECT budget_level AS "уровень", min(year(r.report_date)) AS "с", max(year(r.report_date)) AS "по",
                   count(DISTINCT r.report_id) AS "отчётов"
            FROM layout_column lc JOIN report r USING (report_id)
            WHERE lc.role = 'value' AND r.form_code IN ('0503317', '0524100')
            GROUP BY 1 ORDER BY 2, 1"""),
        "",
        "## «Шахматки»: итоги по РФ и округам",
        "",
        md_table(con, """
            SELECT year(report_date) AS "год", count(DISTINCT report_date) AS "дат", count(*) AS "значений",
                   string_agg(DISTINCT budget_level, ', ' ORDER BY budget_level) AS "уровни"
            FROM chess_value GROUP BY 1 ORDER BY 1"""),
        "",
        "## Признаки качества разбора",
        "",
        md_table(con, """
            SELECT coalesce(quality_flags, '—') AS "флаги", count(*) AS "отчётов"
            FROM report GROUP BY 1 ORDER BY 2 DESC LIMIT 30"""),
        "",
        "## Нераспознанные графы",
        "",
        md_table(con, """
            SELECT form_code AS "форма", section AS "раздел", header AS "заголовок", measure, budget_level,
                   first_date AS "с", last_date AS "по", n_reports AS "отчётов"
            FROM v_layout_catalog
            WHERE role = 'value' AND (budget_level = 'none' OR measure = 'other'
                  OR (form_code IN ('0503317', '0524100') AND measure = 'amount'))
            ORDER BY n_reports DESC LIMIT 30"""),
        "",
    ]
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
