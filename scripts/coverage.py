"""Отчёт о покрытии и сверках БД → docs/coverage.md.

    python scripts/coverage.py [путь к БД]

Таблицы: объём БД; разбор файлов; отчёты по формам, годам и формату источника;
единицы; ряды по датам (число территорий в ф. 0503317); уровни бюджета по годам;
«шахматки»; сверки (свод против суммы субъектов, «шахматка» против свода, месячный
против годового отчёта на 1 января); признаки качества; нераспознанные графы.
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
        "# Покрытие базы данных и сверки",
        "",
        f"Сгенерировано `scripts/coverage.py` {datetime.now():%Y-%m-%d %H:%M} по `{DB.name}`. Не править вручную.",
        "Что означают признаки и расхождения — [data-quality.md](data-quality.md).",
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
        "## Разбор файлов",
        "",
        "Все файлы всех пакетов (`archive_member`) по виду содержимого и формату по сигнатуре; "
        "`отчётов` — сколько книг дали хотя бы один отчёт.",
        "",
        md_table(con, """
            SELECT m.content_kind AS "вид", m.detected_format AS "формат", count(*) AS "файлов",
                   count(*) FILTER (WHERE m.member_id IN (SELECT member_id FROM report)) AS "отчётов",
                   round(sum(m.size_bytes) / 1e9, 2) AS "ГБ"
            FROM archive_member m GROUP BY ALL ORDER BY 1, 2"""),
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
        "## Единицы измерения",
        "",
        "Исходная единица отчёта (`unit_raw`) и множитель к рублям; значения в БД — в рублях.",
        "",
        md_table(con, """
            SELECT coalesce(unit_raw, '—') AS "единица", unit_multiplier AS "множитель", count(*) AS "отчётов",
                   min(report_date) AS "с", max(report_date) AS "по"
            FROM report GROUP BY ALL ORDER BY 3 DESC"""),
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
        "## Сверки",
        "",
        "### Свод по РФ (код 95) против суммы субъектов",
        "",
        "Ф. 0503317, исполнено, `cons_subject_tgvf` (`v_0503317_totals`). `совпало` — срезов, где доходы "
        "и расходы свода равны сумме субъектов с точностью до 1 руб.; расхождения — максимум по году, руб.",
        "",
        md_table(con, """
            WITH s AS (
              SELECT report_date,
                     max(income_executed) FILTER (WHERE territory_code = '95') AS svod_inc,
                     sum(income_executed) FILTER (WHERE territory_code <> '95') AS sub_inc,
                     max(expense_executed) FILTER (WHERE territory_code = '95') AS svod_exp,
                     sum(expense_executed) FILTER (WHERE territory_code <> '95') AS sub_exp,
                     count(*) FILTER (WHERE territory_code <> '95') AS n_sub
              FROM v_0503317_totals GROUP BY 1)
            SELECT year(report_date) AS "год", count(*) AS "срезов", count(svod_inc) AS "со сводом",
                   count(*) FILTER (WHERE abs(svod_inc - sub_inc) <= 1 AND abs(svod_exp - sub_exp) <= 1) AS "совпало",
                   max(abs(svod_inc - sub_inc)) AS "расх. доходов", max(abs(svod_exp - sub_exp)) AS "расх. расходов",
                   min(n_sub) AS "субъектов от", max(n_sub) AS "до"
            FROM s GROUP BY 1 ORDER BY 1"""),
        "",
        "### «Шахматки» против свода",
        "",
        "Итог доходов РФ из «шахматки» (`v_chess`, РФ, `cons_subject_tgvf`, строка 010, КБК 850…) против "
        "свода 95, а где свода нет — против суммы субъектов. Только срезы, где итог найден в «шахматке».",
        "",
        md_table(con, """
            WITH c AS (
              SELECT report_date, max(value) AS v FROM v_chess
              WHERE is_preferred AND section = 'income' AND territory_code = '00' AND budget_level = 'cons_subject_tgvf'
                AND line_code = '010' AND classification_code = '85000000000000000' GROUP BY 1),
            s AS (
              SELECT report_date, max(income_executed) FILTER (WHERE territory_code = '95') AS svod,
                     sum(income_executed) FILTER (WHERE territory_code <> '95') AS sub
              FROM v_0503317_totals GROUP BY 1)
            SELECT year(c.report_date) AS "год", count(*) AS "срезов", count(s.svod) AS "со сводом",
                   count(*) FILTER (WHERE abs(c.v - coalesce(s.svod, s.sub)) <= 1) AS "совпало",
                   round(max(abs(c.v - coalesce(s.svod, s.sub)) / c.v) * 100, 4) AS "макс. расх., %"
            FROM c LEFT JOIN s USING (report_date) GROUP BY 1 ORDER BY 1"""),
        "",
        "Срезы с расхождением больше 1 руб., млрд руб.:",
        "",
        md_table(con, """
            WITH c AS (
              SELECT report_date, max(value) AS v, count(*) AS n FROM v_chess
              WHERE is_preferred AND section = 'income' AND territory_code = '00' AND budget_level = 'cons_subject_tgvf'
                AND line_code = '010' AND classification_code = '85000000000000000' GROUP BY 1),
            s AS (
              SELECT report_date, max(income_executed) FILTER (WHERE territory_code = '95') AS svod,
                     sum(income_executed) FILTER (WHERE territory_code <> '95') AS sub
              FROM v_0503317_totals GROUP BY 1)
            SELECT c.report_date AS "срез", round(c.v / 1e9, 3) AS "шахматка", round(s.svod / 1e9, 3) AS "свод",
                   round(s.sub / 1e9, 3) AS "сумма субъектов", c.n AS "строк РФ",
                   round((c.v - coalesce(s.svod, s.sub)) / 1e9, 3) AS "разница"
            FROM c LEFT JOIN s USING (report_date)
            WHERE abs(c.v - coalesce(s.svod, s.sub)) > 1 ORDER BY 1"""),
        "",
        "### Срез на 1 января: месячный пакет против годового",
        "",
        "Свод по РФ (код 95), ф. 0503317, исполнено, `cons_subject_tgvf`, млрд руб. Годовой отчёт включает "
        "уточнения после 1 января; `is_preferred` выбирает годовой.",
        "",
        md_table(con, """
            WITH r AS (
              SELECT report_id, report_date, publication_type FROM v_report
              WHERE form_code = '0503317' AND territory_code = '95' AND month(report_date) = 1),
            t AS (
              SELECT l.report_id,
                     max(v.value) FILTER (WHERE l.section = 'income' AND (l.kbk_code LIKE '850%'
                         OR (l.is_total_marker AND l.name ILIKE 'доходы%'))) AS inc,
                     max(v.value) FILTER (WHERE l.section = 'expense' AND (l.rzpr_code = '9600'
                         OR (l.is_total_marker AND l.name ILIKE 'расходы%' AND l.rzpr_code IS NULL))) AS exp
              FROM v_line l JOIN value v USING (line_id)
              WHERE l.report_id IN (SELECT report_id FROM r)
                AND v.budget_level = 'cons_subject_tgvf' AND v.measure = 'executed'
              GROUP BY 1)
            SELECT r.report_date AS "срез",
                   round(max(inc) FILTER (WHERE publication_type = 'monthly') / 1e9, 3) AS "доходы мес.",
                   round(max(inc) FILTER (WHERE publication_type = 'annual') / 1e9, 3) AS "доходы год.",
                   round(max(exp) FILTER (WHERE publication_type = 'monthly') / 1e9, 3) AS "расходы мес.",
                   round(max(exp) FILTER (WHERE publication_type = 'annual') / 1e9, 3) AS "расходы год."
            FROM r JOIN t USING (report_id) GROUP BY 1 ORDER BY 1"""),
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
