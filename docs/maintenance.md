# Сопровождение

## Окружение

```bash
uv venv && uv pip install -e ".[dev]"          # конвейер и тесты
uv pip install -e ".[notebook]"                # тетрадка: pandas, matplotlib, JupyterLab
.venv/bin/python -m pytest                     # тесты разбора макетов, кодов, значений
```

* Python ≥ 3.11 (сборка проверена на 3.13).
* Зависимости: `requests[socks]`, `certifi`, `xlrd` (BIFF), `openpyxl` (XLSX), `rarfile` +
  системный `unrar` или `bsdtar` (RAR в пакетах до 2012 г. и пакет 01.02.2020), `duckdb`,
  `pyarrow`; для тестов — `pytest`.
* Доступ к порталу — только с российских IP: `ROSKAZNA_PROXY=socks5h://127.0.0.1:1081`
  (SSH-туннель `ssh -fN -D 1081 <сервер в РФ>`), см. [sources.md](sources.md#доступ).
* Данные можно держать вне репозитория: `ROSKAZNA_KBS_DATA=/путь/к/data`.

## Обновление (новый месячный срез)

Шаги идемпотентны: повторный запуск докачивает и разбирает только новое.

```bash
export ROSKAZNA_PROXY=socks5h://127.0.0.1:1081   # туннель через сервер в РФ
python -m roskazna_kbs catalog                    # 1. каталог → data/catalog/catalog.csv (≈ 80 запросов)
python -m roskazna_kbs download                   # 2. только новые пакеты → data/raw/zip, data/catalog/files.csv
python -m roskazna_kbs inventory                  # 3. реестр файлов в пакетах → data/catalog/members.csv
python -m roskazna_kbs build                      # 4. разобрать новые/изменённые пакеты, пересобрать БД
python -m roskazna_kbs export                     # 5. Parquet → data/export/
python scripts/coverage.py                        # 6. docs/coverage.md: покрытие и сверки
jupyter nbconvert --to notebook --execute --inplace docs/examples.ipynb   # 7. пересчитать тетрадку
```

`build` разбирает пакет, только если для него нет готовых частей
(`data/db/parts/<package_no>/_SUCCESS`) или изменился SHA-256 пакета; загрузка в DuckDB из
частей занимает 2–3 минуты. Полный разбор (`build --force`) — 15–30 минут.
`--workers N` ограничивает число процессов (по умолчанию `min(8, CPU − 2)`),
`--years 2024-2026` — годы каталога, `--limit N` — число пакетов (для отладки).

После обновления проверьте [coverage.md](coverage.md): новая дата в таблице территорий,
сверка «свод = сумма субъектов» и «шахматка = свод» на новой дате, пустой раздел
«Нераспознанные графы» (кроме известной справки 2005 г.).

Перед коммитом: `python -m pytest`, `git diff data/catalog/` (новые карточки и пакеты,
изменившиеся SHA-256).

## Релиз с данными

Данные публикуются релизом на GitHub (лимит — 2 ГиБ на файл): исходные пакеты по группам
лет в tar, Parquet-выгрузка в tar, база DuckDB в zstd по частям, реестры и `SHA256SUMS.txt`.

```bash
mkdir -p /tmp/release && scripts/pack_release.sh /tmp/release     # ≈ 15 ГБ, 10–20 минут
TAG=v$(date +%Y.%m.%d)
gh release create "$TAG" --draft --title "Данные на $(date +%d.%m.%Y)" --notes-file notes.md
gh release upload "$TAG" /tmp/release/*                           # загрузка, можно повторять
gh release edit "$TAG" --draft=false
```

Перед упаковкой проверьте, что каждый tar меньше 2 ГиБ (границы групп лет — в
`scripts/pack_release.sh`), и обновите тег и таблицу файлов в README («Готовые данные»).

## Устройство кода

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

| Модуль | Назначение |
|---|---|
| `config.py` | пути (`ROSKAZNA_KBS_DATA`), URL каталога, типы публикаций (48, 49), User-Agent, интервал запросов |
| `http.py` | HTTP-сессия: CA-бандл с сертификатами Минцифры, прокси `ROSKAZNA_PROXY`, темп и повторы |
| `catalog.py` | обход Livewire-каталога (`filter_type`, `filter_year`, `page`), разбор карточек |
| `download.py` | загрузка с докачкой (`Range`), проверка архива, SHA-256, постоянные `package_no` в `files.csv` |
| `archive.py` | чтение вложенных ZIP/RAR в памяти, декодирование имён, формат по сигнатуре, `members.csv` |
| `workbook.py` | единое чтение XLS и XLSX в `Sheet(rows, merged)`; обход битых XLS и `<dimension ref="A1">` |
| `layout.py` | распознавание макета таблицы: строка номеров граф, шапка, роли колонок, блоки |
| `dims.py` | справочники и классификация граф: показатель, уровень бюджета, вид деятельности |
| `values.py` | нормализация ячеек: коды (строки с ведущими нулями), суммы, маркеры итога |
| `forms.py` | разбор книги формы: шапка → отчёт, листы → разделы, строки → показатели, графы → значения |
| `legacy.py` | макеты 2000–2004 гг. (ф. 0524100): листы и «нарезанные» шапки |
| `htmlform.py` | HTML-версии отчётов `Mes*.html` |
| `chess.py` | «шахматки»: распознавание по имени и содержимому, разложение кода показателя |
| `territory.py` | код территории по имени книги, `TerrList_428m.html` |
| `profile.py` | профиль листов всех книг (`sheets.csv`) — для изучения макетов |
| `build.py` | параллельный разбор пакетов в Parquet-части, загрузка DuckDB, экспорт |
| `cli.py` | команды `catalog`, `download`, `inventory`, `profile`, `build`, `export`, `all`, `extract` |
| `scripts/coverage.py` | отчёт о покрытии и сверках → `docs/coverage.md` |

Тесты (`tests/`): `test_values.py` — коды и суммы; `test_catalog.py` — разбор карточек;
`test_layout_forms.py` — макеты, шапки, имена книг, «шахматки»; `test_htmlform.py` — HTML.

## Если появился новый макет

Признаки: в журнале `build` ошибки разбора, у новых отчётов `quality_flags`,
в `v_layout_catalog` графы с `budget_level = 'none'` или `measure = 'amount'`
для ф. 0503317, файлы с `content_kind = 'unparsed'`.

1. Найти пакет и распаковать для изучения:
   `python -m roskazna_kbs extract <file_id> /tmp/pkg`.
2. Посмотреть структуру листов: `python -m roskazna_kbs profile --limit 1`
   (→ `data/catalog/sheets.csv`).
3. Типовые правки:
   * новое имя книги территории/свода — `territory.MEMBER_PATTERNS`;
   * новое имя «шахматки» — `chess.NAME_RE` / `OLD_NAME_RE` / `ERA_NAME_RE` / `MONTH_NAME_RE`
     (книги без кода территории распознаются и по содержимому — `chess.looks_like_chess`,
     `chess.generic_meta`);
   * новый текст уровня бюджета или показателя — `dims._LEVEL_RULES` / `_MEASURE_RULES`
     (+ строка в `BUDGET_LEVELS` / `MEASURES` для нового кода);
   * новое имя листа — `forms.SECTION_RULES` / `SECTION_TITLE_RULES` / `FORM_SHEET_SECTIONS`;
   * новый заголовок кодовой графы — `layout.CODE_RE` и `forms._SEGMENT_FIELDS`;
   * новый вариант HTML — `htmlform.NAME_RE`, `SECTION_TITLES`, `_header_cell`.
4. Добавить тест в `tests/` на новый вариант (синтетический лист или имя файла).
   «Магические» номера граф не вводить: смысл графы — по тексту шапки.
5. `python -m roskazna_kbs build --years <год>` и проверить `v_layout_catalog`,
   [coverage.md](coverage.md), [data-quality.md](data-quality.md).

## Контрольные проверки после сборки

```sql
-- графы без уровня бюджета / показателя
SELECT * FROM v_layout_catalog WHERE role = 'value' AND (budget_level = 'none' OR measure = 'other');
-- отчёты с флагами
SELECT quality_flags, count(*) FROM report WHERE quality_flags IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;
-- пропуски территорий по датам
SELECT report_date, form_code, count(DISTINCT territory_code) FROM report GROUP BY ALL ORDER BY 1;
-- файлы, которые не распознаны
SELECT member_path, detected_format FROM archive_member WHERE content_kind = 'unparsed';
-- свод (95) против суммы субъектов — см. docs/queries.md и раздел «Сверки» в coverage.md
```

## Что в git, а что нет

| В git | Не в git (воспроизводится) |
|---|---|
| код, SQL, документация, тесты, тетрадка с результатами | `data/raw/` — пакеты, ≈ 11 ГБ (`download`) |
| `data/catalog/catalog.csv`, `files.csv` — реестры каталога и пакетов с SHA-256 | `data/catalog/members.csv`, `sheets.csv` — большие реестры (`inventory`, `profile`) |
| `certs/` — сертификаты УЦ Минцифры | `data/db/` — части и БД, ≈ 4,6 ГБ (`build`) |
| | `data/export/` — Parquet, ≈ 1,8 ГБ (`export`) |
| | `data/.cache/` — CA-бандл и HTML страниц каталога |

`package_no` присваивается пакету при первой загрузке и хранится в `files.csv`: не
перенумеровывайте его — на нём держатся `report_id` и `line_id`.

## Изменения на портале

* **Новый пакет** — новая карточка в `catalog.csv` и строка в `files.csv`; `build` разберёт его
  при следующем запуске.
* **Перезалив по тому же адресу.** `download` не перекачивает существующие файлы; для
  проверки — `download --force` и `git diff data/catalog/files.csv` (изменившийся SHA-256).
  `build` пересоберёт пакеты с новым SHA-256.
* **Смена адреса или имени** (переезд со старого сайта на `storage/page-files/512/…`,
  кириллические имена с 2026 г.) — новый `file_id`; пакет с тем же содержимым получит новый
  `package_no`. Проверьте дубли дат в `v_report` (`is_preferred` выберет более поздний пакет).
* **Портал недоступен** (403, таймауты) — проверьте туннель и выход в РФ; не уменьшайте
  `REQUEST_INTERVAL` в `config.py` и не подменяйте заголовки браузера.
