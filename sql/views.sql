-- Витрины поверх таблиц report / line / value / chess_value.
-- Создаются командой `build` после загрузки данных.

-- Строка отчёта вместе с атрибутами показателя.
CREATE OR REPLACE VIEW v_line AS
SELECT l.line_id, l.report_id, l.line_no, l.sheet_name, l.row_no, i.*
FROM line l
JOIN indicator i USING (indicator_id);

-- Последняя версия отчёта: если отчёт на ту же дату по той же форме и территории
-- есть и в месячном, и в годовом пакете (срез на 1 января), предпочтителен годовой.
CREATE OR REPLACE VIEW v_report AS
SELECT r.*,
       t.name        AS territory_name,
       t.okrug_code,
       t.okrug_name,
       row_number() OVER (
           PARTITION BY r.form_code, r.report_date, r.territory_code, r.activity
           ORDER BY CASE r.publication_type WHEN 'annual' THEN 0 ELSE 1 END, r.package_no DESC
       ) = 1 AS is_preferred
FROM report r
LEFT JOIN territory t USING (territory_code);

-- Все значения с атрибутами отчёта и строки (длинный формат).
CREATE OR REPLACE VIEW v_value AS
SELECT r.report_id, r.form_code, r.report_date, r.period_year, r.period_months, r.publication_type,
       r.territory_code, r.territory_name, r.okrug_code, r.is_preferred,
       l.section, l.line_no, l.name, l.line_code, l.group_name,
       l.admin_code, l.kbk_code, l.rzpr_code, l.csr_code, l.vr_code, l.kosgu_code, l.account_code,
       l.classification_code, l.is_total_marker,
       v.graph, v.measure, v.budget_level, v.activity, v.value
FROM value v
JOIN v_line l USING (line_id)
JOIN v_report r ON r.report_id = v.report_id;

-- Ф. 0503317, раздел 1 «Доходы»: утверждено и исполнено по основным уровням (широкий формат).
CREATE OR REPLACE VIEW v_0503317_income AS
SELECT r.report_id, r.report_date, r.period_year, r.period_months, r.publication_type, r.is_preferred,
       r.territory_code, r.territory_name, r.okrug_code,
       l.line_no, l.name, l.line_code, l.admin_code, l.kbk_code, l.classification_code, l.is_total_marker,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'cons_subject_tgvf') AS approved_cons_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'cons_subject_tgvf') AS executed_cons_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'cons_subject')      AS approved_cons,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'cons_subject')      AS executed_cons,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'subject')           AS approved_subject,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'subject')           AS executed_subject,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'tgvf')              AS approved_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'tgvf')              AS executed_tgvf
FROM v_line l
JOIN v_report r USING (report_id)
LEFT JOIN value v USING (line_id)
WHERE r.form_code = '0503317' AND l.section = 'income'
GROUP BY ALL;

-- Ф. 0503317, раздел 2 «Расходы».
CREATE OR REPLACE VIEW v_0503317_expense AS
SELECT r.report_id, r.report_date, r.period_year, r.period_months, r.publication_type, r.is_preferred,
       r.territory_code, r.territory_name, r.okrug_code,
       l.line_no, l.name, l.line_code, l.admin_code, l.rzpr_code, l.csr_code, l.vr_code,
       l.classification_code, l.is_total_marker,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'cons_subject_tgvf') AS approved_cons_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'cons_subject_tgvf') AS executed_cons_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'cons_subject')      AS approved_cons,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'cons_subject')      AS executed_cons,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'subject')           AS approved_subject,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'subject')           AS executed_subject,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'tgvf')              AS approved_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'tgvf')              AS executed_tgvf
FROM v_line l
JOIN v_report r USING (report_id)
LEFT JOIN value v USING (line_id)
WHERE r.form_code = '0503317' AND l.section = 'expense'
GROUP BY ALL;

-- Ф. 0503317, раздел 3 «Источники финансирования дефицита».
CREATE OR REPLACE VIEW v_0503317_sources AS
SELECT r.report_id, r.report_date, r.period_year, r.period_months, r.publication_type, r.is_preferred,
       r.territory_code, r.territory_name, r.okrug_code,
       l.line_no, l.name, l.line_code, l.admin_code, l.kbk_code, l.classification_code, l.is_total_marker,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'cons_subject_tgvf') AS approved_cons_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'cons_subject_tgvf') AS executed_cons_tgvf,
       sum(v.value) FILTER (WHERE v.measure = 'approved' AND v.budget_level = 'subject')           AS approved_subject,
       sum(v.value) FILTER (WHERE v.measure = 'executed' AND v.budget_level = 'subject')           AS executed_subject
FROM v_line l
JOIN v_report r USING (report_id)
LEFT JOIN value v USING (line_id)
WHERE r.form_code = '0503317' AND l.section = 'sources'
GROUP BY ALL;

-- Ключевые итоги ф. 0503317 по территориям и датам: доходы, расходы и результат
-- исполнения (дефицит/профицит) консолидированного бюджета субъекта и ТГВФ.
-- Итоговые строки — первые строки разделов с «х» в кодовой графе, наименование «Доходы…» / «Расходы…»
-- (коды строк в макетах 2020–2021 гг. не заполнены, до 2010 г. — «Доходы - всего»).
CREATE OR REPLACE VIEW v_0503317_totals AS
SELECT r.report_id, r.report_date, r.period_year, r.period_months, r.territory_code, r.territory_name, r.okrug_code,
       max(v.value) FILTER (WHERE l.section = 'income'  AND l.name ILIKE 'доходы%'  AND v.measure = 'approved') AS income_approved,
       max(v.value) FILTER (WHERE l.section = 'income'  AND l.name ILIKE 'доходы%'  AND v.measure = 'executed') AS income_executed,
       max(v.value) FILTER (WHERE l.section = 'expense' AND l.name ILIKE 'расходы%' AND v.measure = 'approved') AS expense_approved,
       max(v.value) FILTER (WHERE l.section = 'expense' AND l.name ILIKE 'расходы%' AND v.measure = 'executed') AS expense_executed,
       max(v.value) FILTER (WHERE l.section = 'expense' AND l.name ILIKE 'результат исполнения%' AND v.measure = 'approved') AS result_approved,
       max(v.value) FILTER (WHERE l.section = 'expense' AND l.name ILIKE 'результат исполнения%' AND v.measure = 'executed') AS result_executed
FROM v_report r
JOIN v_line l USING (report_id)
JOIN value v USING (line_id)
WHERE r.form_code = '0503317' AND r.is_preferred AND v.budget_level = 'cons_subject_tgvf'
  AND l.section IN ('income', 'expense') AND l.is_total_marker
GROUP BY ALL;

-- «Шахматки» с наименованием показателя. Срез может прийти в нескольких пакетах
-- (на 1 января — в месячном и годовом): is_preferred отмечает годовой, затем более поздний пакет.
CREATE OR REPLACE VIEW v_chess AS
WITH pkg AS (
    SELECT report_date, section, graph, package_no,
           row_number() OVER (PARTITION BY report_date, section, graph
                              ORDER BY CASE f.publication_type WHEN 'annual' THEN 0 ELSE 1 END, package_no DESC) AS rn
    FROM (SELECT DISTINCT report_date, section, graph, package_no FROM chess_value)
    JOIN source_file f USING (package_no)
)
SELECT c.report_date, c.section, c.graph, c.measure, c.budget_level,
       c.territory_kind, c.territory_code, c.territory_name,
       c.indicator_code, i.line_code, i.admin_code, i.classification_code, i.name AS indicator_name,
       c.value, c.package_no, p.rn = 1 AS is_preferred
FROM chess_value c
JOIN pkg p USING (report_date, section, graph, package_no)
LEFT JOIN chess_indicator i USING (section, indicator_code);

-- Каталог макетов: уникальные заголовки граф по формам и годам (для контроля классификации).
CREATE OR REPLACE VIEW v_layout_catalog AS
SELECT r.form_code, lc.section, lc.role, lc.graph, lc.header, lc.measure, lc.budget_level,
       min(r.report_date) AS first_date, max(r.report_date) AS last_date, count(DISTINCT r.report_id) AS n_reports
FROM layout_column lc
JOIN report r USING (report_id)
GROUP BY ALL;
