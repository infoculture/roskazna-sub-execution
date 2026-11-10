# roskazna-kbs-execution

Сбор, разбор и база данных отчётов Федерального казначейства об **исполнении
консолидированных бюджетов субъектов Российской Федерации и бюджетов
территориальных государственных внебюджетных фондов** (ТГВФ) за 2000–2026 годы.

Источник — раздел портала Казначейства
[«Исполнение бюджетов → Консолидированные бюджеты субъектов РФ»](https://roskazna.gov.ru/ispolnenie-byudzhetov/konsolidirovannye-byudzhety-subektov-rossijskoj-federacii/).
Описание источника в базе знаний «Открытые госфинансы»:
[roskazna-consolidated-subjects-execution](https://infoculture.github.io/finguide/data-sources/federal/roskazna-consolidated-subjects-execution).

Что внутри пакетов:

| Форма | Название | Пакеты |
|---|---|---|
| 0503317 | Отчёт об исполнении консолидированного бюджета субъекта РФ и бюджета ТГВФ | ежемесячные и годовые |
| 0503320 | Баланс исполнения консолидированного бюджета субъекта РФ и бюджета ТГВФ | годовые |
| 0503321 | Консолидированный отчёт о финансовых результатах деятельности | годовые |
| 0503323 | Консолидированный отчёт о движении денежных средств | годовые |

Каждая форма — по каждой территории (≈ 90 субъектов, г. Байконур и сводный
отчёт по РФ с кодом 95). Плюс сводные книги «по графам» с итогами по РФ и
федеральным округам. До 2005 г. — ф. 0524100 (тыс. руб.); за 2000–2011 гг. часть
или все отчёты есть только в HTML.

* **338 пакетов** (11 ГБ) — все публикации каталога на 05.11.2026: ежемесячные срезы
  01.04.2000 – 01.09.2026 и годовые отчёты за 2000–2025 гг.
* **36 тыс. отчётов, 54 млн строк, 353 млн значений** по формам и **34 млн значений**
  «шахматок» в DuckDB и Parquet.
* Каждое значение прослеживается до пакета, файла, листа и строки Excel.

## Быстрый старт

```bash
uv venv && uv pip install -e ".[dev]"
export ROSKAZNA_PROXY=socks5h://127.0.0.1:1081   # портал отвечает только российским IP
python -m roskazna_kbs all                        # каталог → загрузка → разбор → БД → Parquet
duckdb data/db/roskazna_kbs.duckdb -c "
SELECT period_year, round(income_executed / 1e12, 2) AS income_trn, round(expense_executed / 1e12, 2) AS expense_trn
FROM v_0503317_totals WHERE territory_code = '95' AND period_months = 12 ORDER BY 1"
```

(доходы и расходы консолидированных бюджетов субъектов РФ по годам, трлн руб.:
2011 — 8,03 / 8,05 … 2025 — 29,69 / 31,20)

## Готовые данные (релиз на GitHub)

Чтобы не собирать базу самостоятельно (и не ходить на портал через прокси), скачайте
данные из релиза [v2026.11.05](https://github.com/infoculture/roskazna-sub-execution/releases/tag/v2026.11.05)
(срез каталога на 05.11.2026):

| Файл | Содержание | Куда распаковать |
|---|---|---|
| `roskazna_kbs.duckdb.zst.part-NN` | база DuckDB со всеми таблицами и витринами (zstd, части по 1900 МиБ) | `data/db/` |
| `export-parquet.tar` | Parquet-выгрузка всех таблиц | `data/export/` |
| `raw-annual-2000-2025.tar`, `raw-monthly-<годы>.tar` | исходные пакеты портала (338 ZIP) | `data/raw/zip/` |
| `catalog.csv`, `files.csv`, `members.csv.gz` | реестры карточек, пакетов и файлов в пакетах | `data/catalog/` |
| `SHA256SUMS.txt` | контрольные суммы | — |

```bash
gh release download v2026.11.05 -R infoculture/roskazna-sub-execution -D release
(cd release && shasum -a 256 -c SHA256SUMS.txt)
mkdir -p data/db && cat release/roskazna_kbs.duckdb.zst.part-* | zstd -d -o data/db/roskazna_kbs.duckdb
tar -xf release/export-parquet.tar -C data                           # → data/export/
for f in release/raw-*.tar; do tar -xf "$f" -C data; done            # → data/raw/zip/
```

Только база — достаточно частей `roskazna_kbs.duckdb.zst.part-*` (≈ 1,7 ГБ). Распакованные
исходные пакеты позволяют пересобрать базу (`python -m roskazna_kbs build`) без доступа к
порталу.

## Документация

| Документ | Содержание |
|---|---|
| [docs/sources.md](docs/sources.md) | источник: каталог портала, доступ, TLS, периодичность, аномалии каталога, архивы |
| [docs/files.md](docs/files.md) | что лежит в пакетах: виды файлов, имена и форматы по годам |
| [docs/forms/](docs/forms/README.md) | каждая форма: листы, графы и уровни бюджета по годам, коды, особенности, примеры — [0503317](docs/forms/0503317.md), [0503320 / 0503321 / 0503323](docs/forms/annual.md), [0524100](docs/forms/legacy.md), [HTML-версии](docs/forms/html.md), [«шахматки»](docs/forms/chess.md) |
| [docs/territories.md](docs/territories.md) | коды территорий Казначейства, переиспользованные коды, объединения, федеральные округа |
| [docs/database.md](docs/database.md) | схема БД, поля таблиц, справочники, правила агрегирования, витрины, Parquet |
| [docs/data-quality.md](docs/data-quality.md) | признаки качества, учтённые особенности, известные проблемы, сверки |
| [docs/coverage.md](docs/coverage.md) | генерируемый отчёт: объём, покрытие по годам, сверки итогов |
| [docs/queries.md](docs/queries.md) | примеры SQL-запросов |
| [docs/examples.ipynb](docs/examples.ipynb) | тетрадка Jupyter: запросы, сравнения, графики |
| [docs/maintenance.md](docs/maintenance.md) | обновление данных, устройство кода, новые макеты |

## Структура репозитория

```
certs/                  корневой и промежуточный сертификаты Минцифры для TLS (см. certs/README.md)
data/catalog/           реестры: catalog.csv, files.csv (в git); members.csv, sheets.csv (не в git —
                        команды inventory, profile)
data/raw/zip/           скачанные пакеты (не в git; воспроизводятся командой download)
data/db/                части Parquet и база DuckDB (не в git; воспроизводятся командой build)
data/export/            выгрузка таблиц БД в Parquet (не в git; команда export)
docs/                   документация (см. выше); docs/forms/ — описание каждой формы
scripts/coverage.py     отчёт о покрытии и сверках → docs/coverage.md
scripts/pack_release.sh упаковка данных для релиза на GitHub
sql/schema.sql          схема БД (DDL)
sql/views.sql           витрины (представления)
sql/dicts/*.csv         справочники форм, разделов, типов публикаций
src/roskazna_kbs/       код конвейера
tests/                  тесты (pytest)
```

Модули `src/roskazna_kbs/`:

| Модуль | Назначение |
|---|---|
| `config.py` | пути, URL каталога, типы публикаций, User-Agent, интервал запросов |
| `cli.py` | команды `python -m roskazna_kbs …` |
| `catalog.py` | обход каталога портала (Livewire, фильтры 48/49 × год × страница) |
| `download.py` | загрузка ZIP с докачкой, SHA-256, проверка архива, реестр `files.csv` |
| `http.py` | HTTP-сессия: TLS с сертификатами Минцифры, прокси, темп запросов |
| `archive.py` | чтение вложенных ZIP в памяти, CP866-имена, формат по сигнатуре |
| `workbook.py` | единое чтение XLS (xlrd, с объединёнными ячейками) и XLSX (openpyxl) |
| `layout.py` | распознавание макета таблицы: строка номеров граф, шапка, роли колонок |
| `dims.py` | справочники и классификация граф: показатель (measure), уровень бюджета |
| `forms.py` | разбор книги формы: шапка → отчёт, строки → показатели, графы → значения |
| `values.py` | нормализация ячеек: коды, суммы, маркеры итога |
| `legacy.py` | макеты 2000–2004 гг. (ф. 0524100) |
| `htmlform.py` | HTML-версии отчётов `Mes*.html` |
| `chess.py` | разбор сводных книг «по графам» (итоги по РФ и федеральным округам) |
| `territory.py` | коды территорий Казначейства, `TerrList_428m.html` |
| `profile.py` | профиль листов всех книг (`sheets.csv`) — для изучения макетов |
| `build.py` | параллельный разбор пакетов в Parquet, загрузка DuckDB, экспорт |

## Установка

Python 3.11+.

```bash
uv venv && uv pip install -e ".[dev]"
```

## Доступ к порталу

Портал отвечает только с российских IP-адресов (с зарубежных — `403 Forbidden`)
и использует сертификат УЦ Минцифры (его нет в стандартных хранилищах — бандл
собирается из `certs/`, проверка TLS не отключается). Прокси с выходом в РФ
задаётся переменной `ROSKAZNA_PROXY`:

```bash
ssh -fN -D 1081 <ru-vps>                          # SOCKS-туннель через сервер в РФ
export ROSKAZNA_PROXY=socks5h://127.0.0.1:1081
```

Прокси используется только сессиями этого проекта; глобальные `HTTP(S)_PROXY`
игнорируются.

## Запуск

```bash
python -m roskazna_kbs catalog     # каталог портала → data/catalog/catalog.csv
python -m roskazna_kbs download    # пакеты → data/raw/zip, реестр data/catalog/files.csv
python -m roskazna_kbs inventory   # реестр файлов внутри пакетов → data/catalog/members.csv
python -m roskazna_kbs profile     # профиль листов всех книг → data/catalog/sheets.csv
python -m roskazna_kbs build       # разбор и сборка БД → data/db/roskazna_kbs.duckdb
python -m roskazna_kbs export      # Parquet → data/export/
python -m roskazna_kbs all         # всё по порядку
```

* Запросы к порталу идут последовательно, не чаще раза в 2 секунды.
* `download` докачивает только новые файлы; прерванная загрузка продолжается.
* `build` разбирает только новые или изменившиеся пакеты (по SHA-256);
  `--years 2024-2026` ограничивает разбор годами каталога, `--limit N` — числом пакетов.
* `python -m roskazna_kbs extract <file_id> [DIR]` распаковывает один пакет
  (со вложенными архивами) для ручного изучения.

Полный объём (05.11.2026): 338 пакетов (312 ежемесячных за 2000–2026 гг. и 26
годовых за 2000–2025 гг.), 11 ГБ ZIP, 62,6 тыс. файлов внутри. Загрузка через туннель —
4–5 часов, разбор — 15–30 минут (`--workers`, по умолчанию 8 процессов), загрузка
в DuckDB — 2–3 минуты. БД — 4,6 ГБ (≈ 36 тыс. отчётов, 54 млн строк, 353 млн значений,
34 млн значений «шахматок»), Parquet — 1,8 ГБ. Покрытие по годам — [docs/coverage.md](docs/coverage.md).

Проверки после сборки: сводный отчёт по РФ (код 95) совпадает с суммой субъектов
по доходам и расходам на 175 из 176 срезов 2012–2026 гг., где он есть (одно расхождение
в 51 230 руб.); контрольные значения из [finguide](https://infoculture.github.io/finguide/data-sources/federal/roskazna-consolidated-subjects-execution)
(расходы Башкортостана на 01.08.2026, доходы РФ по «шахматке») совпадают до копейки.
Сверки — [docs/coverage.md](docs/coverage.md#сверки), известные проблемы —
[docs/data-quality.md](docs/data-quality.md#известные-проблемы).

## Модель данных (кратко)

```
source_file (пакет ZIP) → archive_member (файл в пакете) → report (форма × территория × дата)
                                                          → line (строка отчёта) → indicator (показатель)
                                                             → value (графа: показатель × уровень бюджета)
chess_value (итоги по РФ и федеральным округам из «шахматок») → chess_indicator
```

* Показатель (`indicator`) — наименование, код строки и коды классификации по сегментам
  (`admin_code`, `kbk_code`, `rzpr_code`, `csr_code`, `vr_code`, `kosgu_code`); общий для
  всех отчётов, где встречается.
* Значение — показатель графы (`measure`: `approved`, `executed`, …), уровень бюджета
  (`budget_level`: `cons_subject_tgvf`, `subject`, `city_okrug`, `tgvf`, …), сумма в рублях.
* Витрины: `v_0503317_totals` (итоги каждого отчёта), `v_0503317_income`,
  `v_0503317_expense`, `v_0503317_sources`, `v_value`, `v_report`, `v_chess`, `v_layout_catalog`.

## Важно при анализе

* Формы печатают итоги и детализацию вместе, а графы уровней бюджета пересекаются —
  не складывайте все строки и все графы (правила — [docs/database.md](docs/database.md#как-агрегировать)).
* Свод по РФ (код 95) — не субъект: исключайте его при суммировании по территориям.
* Один отчёт приходит в нескольких пакетах — используйте `is_preferred`.
* Срез на 1 января — итоги прошлого года (`period_months = 12`).
* Коды территорий — внутренние коды Казначейства; коды 70, 81, 82, 83 в 2000-х принадлежали
  другим субъектам ([docs/territories.md](docs/territories.md)).
* До 2005 г. — старая бюджетная классификация (ф. 0524100); значения уже пересчитаны в рубли.

## Пример запроса

```sql
-- Исполнение доходов и расходов консолидированных бюджетов субъектов на 1 сентября 2026 г.
SELECT territory_code, territory_name, income_executed, expense_executed, result_executed
FROM v_0503317_totals
WHERE report_date = DATE '2026-09-01'
ORDER BY expense_executed DESC;
```

Больше примеров — [docs/queries.md](docs/queries.md); тетрадка с запросами, сравнениями и
графиками — [docs/examples.ipynb](docs/examples.ipynb) (см. ниже).

## Тетрадка с примерами (Jupyter)

[docs/examples.ipynb](docs/examples.ipynb) — запросы, сравнения и графики по собранной базе:
итоги 2005–2025 гг., исполнение внутри года, регионы, структура доходов и расходов, уровни
бюджета, федеральные округа, трансферты, проверки качества. Тетрадка хранится вместе с
результатами, поэтому её можно читать без запуска (на GitHub, в VS Code, в JupyterLab).

**Что нужно для запуска**

* собранная БД `data/db/roskazna_kbs.duckdb` (`python -m roskazna_kbs build`);
* для последнего раздела — Parquet-выгрузка `data/export/` (`python -m roskazna_kbs export`);
* зависимости тетрадки (pandas, matplotlib, JupyterLab):

```bash
uv pip install -e ".[notebook]"
```

**Интерактивный запуск в JupyterLab**

```bash
.venv/bin/jupyter lab docs/examples.ipynb
```

Откроется браузер (по умолчанию <http://localhost:8888>); ядро — «Python 3» из `.venv`.
Выполните все ячейки: меню **Run → Run All Cells** (или **Kernel → Restart Kernel and Run All Cells**),
затем меняйте SQL в ячейках и перезапускайте их по одной (`Shift+Enter`). Классический интерфейс —
`.venv/bin/jupyter notebook docs/examples.ipynb`. В VS Code и PyCharm откройте файл и выберите
интерпретатор `.venv/bin/python`.

* Первые две ячейки подключают БД и задают оформление графиков; функция `q("SELECT …")`
  возвращает результат запроса как `pandas.DataFrame`.
* Путь к БД определяется от каталога запуска (корень репозитория или `docs/`); при другом
  расположении задайте `ROOT` в первой ячейке.
* БД открывается только для чтения (`read_only=True`): тетрадку можно держать открытой вместе с
  другими читателями, но не во время `build` — сборка заменяет файл БД. После пересборки
  перезапустите ядро.

**Пересчёт без интерфейса** (например, после обновления данных — графики и таблицы в файле
обновятся):

```bash
.venv/bin/jupyter nbconvert --to notebook --execute --inplace docs/examples.ipynb
```

## Тесты

```bash
.venv/bin/python -m pytest
```

## Лицензия и источник

Данные — официальная отчётность Федерального казначейства; явные условия повторного
использования на портале не указаны. При использовании ссылайтесь на источник
(`source_file.url`). Лицензия на код репозитория пока не выбрана.
