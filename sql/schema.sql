-- База данных «Исполнение консолидированных бюджетов субъектов РФ и бюджетов ТГВФ»
-- (Федеральное казначейство, roskazna.gov.ru). СУБД: DuckDB.
--
-- Слои:
--   1. Справочники:    publication_type, form, section, measure, budget_level, activity, territory
--   2. Происхождение:  catalog_card → source_file → archive_member → report
--   3. Факты отчётов:  report → line (строка формы) → value (значение графы)
--                      layout_column — макет таблиц (заголовки граф) каждого отчёта
--   4. Сводные данные: chess_indicator, chess_value — итоги по РФ и федеральным округам
--                      из книг «по графам» («шахматок»)
--   5. Витрины:        представления v_* — см. sql/views.sql
--
-- Денежные суммы — DECIMAL(18,2) в рублях. Отчёты в тыс. руб. пересчитаны в рубли
-- (report.unit_multiplier), исходная единица — report.unit_raw.
-- Суррогатные ключи: report_id = package_no * 10000 + № книги в пакете;
--                    line_id   = report_id * 100000 + line_no.

------------------------------------------------------------------------------
-- 1. Справочники
------------------------------------------------------------------------------

CREATE TABLE publication_type (
    publication_type     VARCHAR PRIMARY KEY,   -- monthly | annual
    filter_id            INTEGER,               -- значение фильтра «Тип» на портале (48, 49)
    name_ru              VARCHAR NOT NULL,
    description          VARCHAR
);

CREATE TABLE form (
    form_code            VARCHAR PRIMARY KEY,   -- код формы по ОКУД: 0503317, 0503320, ...
    name_ru              VARCHAR NOT NULL,
    periodicity          VARCHAR,               -- месячная, годовая
    description          VARCHAR
);

CREATE TABLE section (
    section              VARCHAR PRIMARY KEY,   -- income, expense, sources, settlements, ...
    form_code            VARCHAR,
    name_ru              VARCHAR NOT NULL,
    description          VARCHAR
);

CREATE TABLE measure (
    measure              VARCHAR PRIMARY KEY,   -- approved, executed, begin_balance, ...
    name_ru              VARCHAR NOT NULL,
    description          VARCHAR
);

CREATE TABLE budget_level (
    budget_level         VARCHAR PRIMARY KEY,   -- cons_subject_tgvf, subject, city_okrug, tgvf, ...
    name_ru              VARCHAR NOT NULL,
    sort_order           INTEGER,
    description          VARCHAR
);

-- Вид деятельности (третье измерение граф баланса 0503320 до 2013 г.).
CREATE TABLE activity (
    activity             VARCHAR PRIMARY KEY,   -- total | budget | income_generating
    name_ru              VARCHAR NOT NULL
);

-- Территория по внутреннему коду Казначейства (не ОКАТО/ОКТМО!).
CREATE TABLE territory (
    territory_code       VARCHAR PRIMARY KEY,   -- '01' … '97'
    name                 VARCHAR,               -- последнее наименование (TerrList_428m.html / «шахматки»)
    last_budget_name     VARCHAR,               -- последнее «Наименование бюджета» из шапки отчёта
    okrug_code           VARCHAR,               -- код федерального округа (по «шахматкам»): 1 … 8
    okrug_name           VARCHAR,
    first_report_date    DATE,
    last_report_date     DATE,
    n_reports            INTEGER
);

-- Все встреченные наименования территорий (история переименований, состав округов).
CREATE TABLE territory_seen (
    package_no           INTEGER,
    report_date          DATE,
    source               VARCHAR,               -- terrlist | chess | report_header
    territory_code       VARCHAR,
    territory_name       VARCHAR,
    okrug_code           VARCHAR,
    okrug_name           VARCHAR
);

------------------------------------------------------------------------------
-- 2. Происхождение
------------------------------------------------------------------------------

-- Пакет (ZIP) с портала.
CREATE TABLE source_file (
    package_no           INTEGER PRIMARY KEY,   -- постоянный номер пакета (data/catalog/files.csv)
    file_id              VARCHAR UNIQUE,        -- sha1(file_path)[:12]
    publication_type     VARCHAR,
    filter_year          INTEGER,               -- год в фильтре каталога
    card_title           VARCHAR,               -- «на 1 августа 2026», «2025 год»
    file_path            VARCHAR NOT NULL,      -- путь на портале
    url                  VARCHAR NOT NULL,
    local_path           VARCHAR,               -- относительно data/
    size_bytes           BIGINT,
    sha256               VARCHAR,
    http_status          INTEGER,
    last_modified        VARCHAR,               -- заголовок Last-Modified ответа
    zip_ok               VARCHAR,               -- результат zipfile.testzip()
    downloaded_at        TIMESTAMPTZ
);

-- Карточка каталога на портале (может ссылаться на тот же файл, что и другая карточка).
CREATE TABLE catalog_card (
    card_id              INTEGER PRIMARY KEY,
    publication_type     VARCHAR,
    filter_year          INTEGER,
    page                 INTEGER,
    position             INTEGER,
    card_title           VARCHAR,
    file_path            VARCHAR,
    file_id              VARCHAR,
    published_at_raw     VARCHAR,               -- дата публикации, как показана порталом
    published_at         TIMESTAMP,             -- для старых лет — дата миграции CMS, не публикации!
    retrieved_at         TIMESTAMPTZ
);

-- Файл внутри пакета (включая вложенные архивы).
CREATE TABLE archive_member (
    package_no           INTEGER,
    file_id              VARCHAR,
    member_id            VARCHAR PRIMARY KEY,   -- <file_id>:<i>[:<j>…] — индексы во вложенных архивах
    parent_member_id     VARCHAR,
    depth                SMALLINT,
    member_path          VARCHAR,               -- путь внутри пакета
    member_name          VARCHAR,               -- имя (CP866 → Unicode)
    member_name_raw      VARCHAR,
    name_encoding        VARCHAR,
    extension            VARCHAR,
    detected_format      VARCHAR,               -- по сигнатуре: xls | xlsx | html | zip | ...
    size_bytes           BIGINT,
    sha256               VARCHAR,
    zip_datetime         VARCHAR,
    content_kind         VARCHAR                -- form | chess | territory_list | form_html | archive | ...
);

-- Отчёт = одна книга формы по одной территории на одну дату.
CREATE TABLE report (
    report_id            BIGINT PRIMARY KEY,
    package_no           INTEGER,
    file_id              VARCHAR,
    member_id            VARCHAR,
    member_path          VARCHAR,
    source_format        VARCHAR,               -- xls | xlsx | html — из чего разобран отчёт
    publication_type     VARCHAR,               -- monthly | annual (тип пакета)
    form_code            VARCHAR,               -- по ОКУД из шапки
    activity             VARCHAR,               -- вид деятельности, если отчёт только по одному (до 2011 г.)
    report_date          DATE,                  -- «на 1 …» — дата среза (шапка, иначе имя файла)
    name_report_date     DATE,                  -- дата из имени файла
    period_year          SMALLINT,              -- финансовый год данных (срез на 1 января → прошлый год)
    period_months        TINYINT,               -- месяцев с начала года (12 для среза на 1 января)
    territory_code       VARCHAR,               -- код территории Казначейства (из имени файла)
    fin_authority        VARCHAR,               -- наименование финансового органа
    budget_name          VARCHAR,               -- наименование бюджета
    okpo                 VARCHAR,
    oktmo                VARCHAR,               -- ОКТМО (ОКАТО в старых отчётах)
    okei                 VARCHAR,               -- 383 — рубль, 384 — тыс. руб.
    periodicity          VARCHAR,
    unit_raw             VARCHAR,               -- «руб.», «тыс. руб.»
    unit_multiplier      BIGINT,                -- множитель к рублям
    sheets               VARCHAR,               -- «лист=раздел;…»
    n_lines              INTEGER,
    n_values             INTEGER,
    quality_flags        VARCHAR                -- ';'-разделённые признаки качества разбора
);

------------------------------------------------------------------------------
-- 3. Факты отчётов
------------------------------------------------------------------------------

-- Показатель: уникальная комбинация атрибутов строки формы. Строки разных отчётов
-- с одинаковыми наименованием и кодами ссылаются на один показатель.
CREATE TABLE indicator (
    indicator_id         BIGINT PRIMARY KEY,    -- 63-битный хеш атрибутов (стабилен между сборками)
    form_code            VARCHAR,
    section              VARCHAR NOT NULL,      -- раздел формы (см. section)
    line_code            VARCHAR,               -- код строки формы (010, 200, 450, 520 …)
    name                 VARCHAR,               -- наименование показателя
    group_name           VARCHAR,               -- для таблицы консолидируемых расчётов: бюджет-отправитель
    admin_code           VARCHAR,               -- код главы (3 знака; в своде — 000)
    kbk_code             VARCHAR,               -- код дохода / источника без главы (17 знаков)
    rzpr_code            VARCHAR,               -- раздел/подраздел (4 знака)
    csr_code             VARCHAR,               -- целевая статья (10 знаков; 7 — в старых отчётах)
    vr_code              VARCHAR,               -- вид расходов (3 знака)
    kosgu_code           VARCHAR,               -- КОСГУ (ф. 0503321, 0503323)
    account_code         VARCHAR,               -- номер забалансового счёта (ф. 0503320)
    classification_code  VARCHAR,               -- полный код классификации (склейка кодовых граф)
    code_raw             VARCHAR,               -- кодовые графы как в источнике, через пробел
    is_total_marker      BOOLEAN,               -- в кодовой графе «х» — итоговая строка
    n_lines              BIGINT                 -- в скольких строках отчётов встречается
);

-- Строка таблицы формы в конкретном отчёте.
CREATE TABLE line (
    line_id              BIGINT PRIMARY KEY,
    report_id            BIGINT NOT NULL,
    line_no              INTEGER NOT NULL,      -- порядок строки в отчёте
    indicator_id         BIGINT NOT NULL,
    sheet_name           VARCHAR,
    row_no               INTEGER                -- номер строки Excel
);

-- Значение графы-показателя в строке.
CREATE TABLE value (
    line_id              BIGINT NOT NULL,
    report_id            BIGINT NOT NULL,
    graph                SMALLINT,              -- номер графы формы
    measure              VARCHAR NOT NULL,      -- см. measure
    budget_level         VARCHAR NOT NULL,      -- см. budget_level
    activity             VARCHAR,               -- вид деятельности (только баланс 0503320 до 2013 г.): total | budget | income_generating
    value                DECIMAL(18, 2)         -- рубли
);

-- Макет: каждая колонка каждой таблицы каждого отчёта с распознанной ролью.
CREATE TABLE layout_column (
    report_id            BIGINT,
    sheet_name           VARCHAR,
    section              VARCHAR,
    table_no             SMALLINT,              -- номер таблицы на листе
    graph_row            INTEGER,               -- строка номеров граф
    col                  SMALLINT,              -- колонка Excel
    graph                SMALLINT,              -- номер графы
    block                SMALLINT,              -- блок таблицы (0 — левый, 1 — правый …)
    role                 VARCHAR,               -- name | line_code | code | value
    code_part            SMALLINT,              -- подколонка кода внутри графы
    header               VARCHAR,               -- текст шапки над колонкой (уровни через « / »)
    measure              VARCHAR,
    budget_level         VARCHAR,
    activity             VARCHAR
);

------------------------------------------------------------------------------
-- 4. «Шахматки»: итоги по РФ и федеральным округам (ф. 0503317, графы «Исполнено»)
------------------------------------------------------------------------------

CREATE TABLE chess_indicator (
    section              VARCHAR,               -- income | expense | sources
    indicator_code       VARCHAR,               -- составной код показателя
    line_code            VARCHAR,
    admin_code           VARCHAR,
    classification_code  VARCHAR,               -- КБК (17 знаков для доходов/источников, 17 = Рз/Пр+ЦСР+ВР для расходов)
    name                 VARCHAR,               -- последнее наименование
    first_package_no     INTEGER,
    last_package_no      INTEGER,
    PRIMARY KEY (section, indicator_code)
);

CREATE TABLE chess_value (
    package_no           INTEGER,
    member_id            VARCHAR,
    report_date          DATE,
    section              VARCHAR,
    graph                SMALLINT,              -- графа ф. 0503317 (уровень бюджета, исполнено)
    measure              VARCHAR,               -- executed
    budget_level         VARCHAR,
    territory_kind       VARCHAR,               -- country | okrug
    territory_code       VARCHAR,               -- '00' — РФ; код округа '1' … '8'
    territory_name       VARCHAR,
    indicator_code       VARCHAR,
    value                DECIMAL(18, 2)
);
