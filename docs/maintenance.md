# Сопровождение

## Обновление (новый месячный срез)

```bash
export ROSKAZNA_PROXY=socks5h://127.0.0.1:1081   # туннель через сервер в РФ
python -m roskazna_kbs catalog                    # перечитать каталог
python -m roskazna_kbs download                   # скачать только новые пакеты
python -m roskazna_kbs build                      # разобрать новые/изменённые пакеты, пересобрать БД
python -m roskazna_kbs export                     # обновить Parquet
python scripts/coverage.py                        # обновить docs/coverage.md
```

`build` разбирает пакет, только если для него нет готовых частей
(`data/db/parts/<package_no>/_SUCCESS`) или изменился SHA-256 пакета;
загрузка в DuckDB из частей занимает минуты. `build --force` разбирает всё заново.

Перед коммитом: `python -m pytest`, проверить `git diff data/catalog/`.

## Что в git, а что нет

| В git | Не в git (воспроизводится) |
|---|---|
| код, SQL, документация, тесты | `data/raw/` — пакеты (`download`) |
| `data/catalog/*.csv` — реестры каталога, пакетов, файлов | `data/db/` — части и БД (`build`) |
| `certs/` — сертификаты УЦ Минцифры | `data/export/` — Parquet (`export`) |

## Как устроен разбор

```
archive.iter_members      пакет → все файлы (вложенные ZIP/RAR, CP866-имена, формат по сигнатуре)
  ├─ build.content_kind   form | chess | form_html | territory_list | …
  ├─ workbook.open        XLS (xlrd + объединённые ячейки) / XLSX (openpyxl) → Sheet(rows, merged)
  ├─ forms.parse_workbook шапка формы → ReportHeader; листы → разделы
  │    ├─ layout          строка номеров граф → шапка → роли колонок (name, line_code, code, value)
  │    ├─ legacy          то же для отчётов 2000–2004 гг. (шапка «нарезана» по строкам)
  │    └─ parse_table     строки → line (+ коды), графы → value
  ├─ htmlform             HTML-отчёт → тот же «лист» → parse_table (2000–2011 гг.)
  ├─ chess                «шахматки» → итоги по РФ и округам
  └─ dims                 текст шапки графы → measure, budget_level, activity
```

## Если появился новый макет

Признаки: в журнале `build` ошибки разбора, у новых отчётов `quality_flags`,
в `v_layout_catalog` графы с `budget_level = 'none'` или `measure = 'amount'`
для ф. 0503317.

1. Найти пакет и распаковать для изучения:
   `python -m roskazna_kbs extract <file_id> /tmp/pkg`.
2. Посмотреть структуру листов: `python -m roskazna_kbs profile --limit 1`
   (→ `data/catalog/sheets.csv`).
3. Типовые правки:
   * новое имя книги территории/свода — `territory.MEMBER_PATTERNS`;
   * новое имя «шахматки» — `chess.NAME_RE` / `OLD_NAME_RE` / `ERA_NAME_RE` / `MONTH_NAME_RE`
     (книги без кода территории распознаются и по содержимому — `chess.looks_like_chess`,
     `chess.generic_meta`); файлы с `archive_member.content_kind = 'unparsed'` — повод посмотреть;
   * новый текст уровня бюджета или показателя — `dims._LEVEL_RULES` / `_MEASURE_RULES`
     (+ строка в `BUDGET_LEVELS` / `MEASURES` для нового кода);
   * новое имя листа — `forms.SECTION_RULES` / `SECTION_TITLE_RULES`;
   * новый заголовок кодовой графы — `layout.CODE_RE` и `forms._SEGMENT_FIELDS`.
4. Добавить тест в `tests/` на новый вариант (синтетический лист или имя файла).
5. `python -m roskazna_kbs build --years <год>` и проверить `v_layout_catalog`, `coverage.md`.

## Контрольные проверки после сборки

```sql
-- графы без уровня бюджета / показателя
SELECT * FROM v_layout_catalog WHERE role = 'value' AND (budget_level = 'none' OR measure = 'other');
-- отчёты с флагами
SELECT quality_flags, count(*) FROM report WHERE quality_flags IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;
-- пропуски территорий по датам
SELECT report_date, form_code, count(DISTINCT territory_code) FROM report GROUP BY ALL ORDER BY 1;
-- свод (95) против суммы субъектов — см. docs/queries.md
```

## Зависимости

Python 3.11+: `requests[socks]`, `certifi`, `xlrd` (BIFF), `openpyxl` (XLSX),
`rarfile` + системный `unrar` или `bsdtar` (RAR в пакетах до 2012 г.), `duckdb`,
`pyarrow`, `pytest`.
