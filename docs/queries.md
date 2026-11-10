# Примеры запросов

Все запросы — к `data/db/roskazna_kbs.duckdb` (`duckdb data/db/roskazna_kbs.duckdb`).
Запросы с графиками (итоги по годам, исполнение внутри года, регионы, структура доходов и
расходов, уровни бюджета, федеральные округа, трансферты) — в тетрадке [examples.ipynb](examples.ipynb).
Примеры по каждой форме — в [forms/](forms/README.md); как не задвоить суммы —
[database.md](database.md#как-агрегировать).

## Итоги по территориям

```sql
-- Доходы, расходы и дефицит/профицит консолидированных бюджетов субъектов (включая ТГВФ)
-- на 1 сентября 2026 г., по убыванию расходов. Код 95 — сводный отчёт по РФ.
SELECT territory_code, territory_name, okrug_name,
       round(income_executed / 1e9, 1)  AS income_bn,
       round(expense_executed / 1e9, 1) AS expense_bn,
       round(result_executed / 1e9, 1)  AS result_bn
FROM v_0503317_totals
WHERE report_date = DATE '2026-09-01'
ORDER BY expense_executed DESC;
```

## Ряд по одному КБК

```sql
-- НДФЛ (КБК 1 01 02000 01 0000 110), исполнено бюджетом субъекта, годовые итоги по годам
SELECT r.period_year, r.territory_code, r.territory_name, sum(v.value) AS ndfl
FROM v_value v
JOIN v_report r USING (report_id)
WHERE v.form_code = '0503317' AND v.section = 'income'
  AND v.kbk_code = '10102000010000110'
  AND v.measure = 'executed' AND v.budget_level = 'subject'
  AND r.period_months = 12 AND r.is_preferred
GROUP BY ALL
ORDER BY r.territory_code, r.period_year;
```

## Расходы по разделам

```sql
-- Исполнение расходов консолидированного бюджета субъекта по разделам (Рз/Пр = XX00)
SELECT territory_name, rzpr_code, name, executed_cons_tgvf
FROM v_0503317_expense
WHERE report_date = DATE '2026-01-01' AND is_preferred
  AND rzpr_code LIKE '__00' AND csr_code = '0000000000' AND vr_code = '000'
  AND territory_code = '73'
ORDER BY rzpr_code;
```

## Уровни бюджета

```sql
-- Структура исполнения доходов по уровням бюджета (Башкортостан, 01.09.2026)
SELECT b.name_ru, v.value
FROM v_value v
JOIN budget_level b USING (budget_level)
WHERE v.territory_code = '01' AND v.report_date = DATE '2026-09-01' AND v.is_preferred
  AND v.form_code = '0503317' AND v.section = 'income' AND v.is_total_marker
  AND v.name ILIKE 'доходы бюджета%' AND v.measure = 'executed'
ORDER BY b.sort_order;
```

## Межбюджетные трансферты (таблица консолидируемых расчётов)

```sql
SELECT territory_name, group_name AS sender, name AS transfer_type, line_code,
       budget_level AS receiver, value
FROM v_value
WHERE form_code = '0503317' AND section = 'settlements'
  AND report_date = DATE '2026-01-01' AND territory_code = '01' AND is_preferred
ORDER BY line_no, graph;
```

## Итоги по РФ и федеральным округам («шахматки»)

```sql
-- «Доходы бюджета — итого» по РФ и федеральным округам (округа — по наименованию:
-- их коды в «шахматках» — порядковые номера строк, см. forms/chess.md)
SELECT report_date, territory_name, indicator_name, value
FROM v_chess
WHERE is_preferred AND section = 'income' AND budget_level = 'cons_subject_tgvf'
  AND line_code = '010' AND classification_code = '85000000000000000'
  AND report_date >= DATE '2025-01-01'
ORDER BY report_date, territory_kind, territory_name;
```

## Сверка: свод (код 95) против суммы субъектов

```sql
WITH s AS (
    SELECT report_date, sum(income_executed) FILTER (WHERE territory_code <> '95') AS sum_subjects,
           max(income_executed) FILTER (WHERE territory_code = '95') AS svod
    FROM v_0503317_totals GROUP BY report_date
)
SELECT *, svod - sum_subjects AS diff FROM s ORDER BY report_date;
```

## Происхождение значения

```sql
SELECT v.value, l.sheet_name, l.row_no, r.member_path, f.url, f.sha256
FROM value v
JOIN line l USING (line_id)
JOIN report r ON r.report_id = v.report_id
JOIN source_file f USING (package_no)
WHERE v.line_id = 10001 * 100000 + 1;
```

## Контроль классификации граф

```sql
-- Заголовки граф, для которых не распознан уровень бюджета
SELECT * FROM v_layout_catalog WHERE role = 'value' AND budget_level = 'none' ORDER BY n_reports DESC;
```
