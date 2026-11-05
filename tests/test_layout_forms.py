"""Разбор макета и строк на синтетическом листе, повторяющем ф. 0503317 (доходы)."""

from decimal import Decimal

from roskazna_kbs.dims import classify_level, classify_measure
from roskazna_kbs.forms import parse_workbook
from roskazna_kbs.layout import detect_layout, find_graph_rows, norm_header
from roskazna_kbs.territory import territory_from_member_name
from roskazna_kbs.workbook import Sheet, Workbook


def _income_sheet() -> Sheet:
    rows = [
        [None, "ОТЧЕТ ОБ ИСПОЛНЕНИИ КОНСОЛИДИРОВАННОГО БЮДЖЕТА СУБЪЕКТА РОССИЙСКОЙ ФЕДЕРАЦИИ"],
        [None] * 16 + ["Форма по ОКУД ", "0503317"],
        [None] * 6 + ["на 1 сентября 2026 г."] + [None] * 9 + ["Дата ", "01.09.2026"],
        ["Наименование финансового органа ", None, None, "МИНФИН РЕСПУБЛИКИ"] + [None] * 12 + ["по ОКПО ", "00067754"],
        ["Наименование бюджета", None, None, "Бюджет Республики"] + [None] * 12 + ["по ОКТМО ", "80000000"],
        ["Периодичность : Месячная"],
        ["Единица измерения: руб."] + [None] * 15 + ["по ОКЕИ ", "383"],
        ["1. Доходы бюджета"],
        # шапка таблицы: два блока (утверждено | исполнено)
        ["Наименование \nпоказателя", "Код стро-ки", "Код дохода по бюджетной классификации", None,
         "Утвержденные бюджетные назначения", None, None,
         "Наименование \nпоказателя", "Код стро-ки", "Код дохода по бюджетной классификации", None,
         "Исполнено", None, None],
        [None, None, None, None, "консолидиро-ванный бюджет субъекта Российской Федерации и территориального "
         "государственного внебюджетного фонда", "бюджет субъекта Российской Федерации", "бюджеты городских округов",
         None, None, None, None, "консолидированный бюджет субъекта Российской Федерации и территориального "
         "государственного внебюджетного фонда", "бюджет субъекта Российской Федерации", "бюджеты городских округов"],
        [1.0, 2.0, 3.0, None, "4", "5", "6", 1.0, 2.0, 3.0, None, 7.0, 8.0, 9.0],
        ["Доходы бюджета - всего", "010", "X", None, 100.5, 80.0, 20.5,
         "Доходы бюджета - всего", "010", "X", None, 50.25, 40.0, 10.25],
        ["в том числе:"],
        ["НАЛОГ НА ПРИБЫЛЬ", "010", "000", "10101000000000110", 60.0, 60.0, None,
         "НАЛОГ НА ПРИБЫЛЬ", "010", "000", "10101000000000110", 30.0, 30.0, "x"],
        ["Руководитель", None, None, "(подпись)"],
    ]
    # объединения шапки: группы «Утверждено» (5–7) и «Исполнено» (12–14), коды (3–4, 10–11)
    merged = [(9, 9, 5, 7), (9, 9, 12, 14), (9, 10, 3, 4), (9, 10, 10, 11), (9, 10, 1, 1), (9, 10, 2, 2)]
    return Sheet(name="0503317_Д", index=0, hidden=False, rows=rows, merged=merged)


def test_find_graph_rows_and_layout():
    sh = _income_sheet()
    assert find_graph_rows(sh) == [11]
    lay = detect_layout(sh, 11)
    assert lay.header_top == 9
    roles = [(c.col, c.graph, c.role, c.code_part) for c in lay.columns]
    assert (1, 1, "name", 0) in roles
    assert (2, 2, "line_code", 0) in roles
    assert (3, 3, "code", 0) in roles and (4, 3, "code", 1) in roles
    assert (12, 7, "value", 0) in roles
    vals = {c.graph: c.header for c in lay.columns if c.role == "value"}
    assert vals[4].startswith("Утвержденные бюджетные назначения / консолидированный бюджет")
    assert vals[9] == "Исполнено / бюджеты городских округов"


def test_parse_workbook_income():
    rep = parse_workbook(Workbook(path=None, fmt="xls", sheets=[_income_sheet()]))
    h = rep.header
    assert (h.form_code, str(h.report_date), h.okpo, h.oktmo, h.okei, h.unit_multiplier) == (
        "0503317", "2026-09-01", "00067754", "80000000", "383", 1)
    assert [ln["name"] for ln in rep.lines] == ["Доходы бюджета - всего", "НАЛОГ НА ПРИБЫЛЬ"]
    total, tax = rep.lines
    assert total["is_total_marker"] and total["classification_code"] is None
    assert tax["admin_code"] == "000" and tax["kbk_code"] == "10101000000000110"
    assert tax["classification_code"] == "00010101000000000110"
    got = {(v["line_no"], v["measure"], v["budget_level"]): v["value"] for v in rep.values}
    assert got[(1, "approved", "cons_subject_tgvf")] == Decimal("100.50")
    assert got[(1, "executed", "city_okrug")] == Decimal("10.25")
    assert got[(2, "executed", "subject")] == Decimal("30.00")
    assert (2, "executed", "city_okrug") not in got  # «x» — не значение


def test_classifiers():
    assert norm_header("консолидиро-ванный бюджет внутри- городских") == "консолидированный бюджет внутригородских"
    assert classify_level("Исполнено / суммы, подлежащие исключению в рамках консолидированного бюджета субъекта "
                          "Российской Федерации и бюджета территориального государственного внебюджетного фонда") \
        == "cons_subject_tgvf_elim"
    assert classify_level("бюджеты муниципальных округов, городских округов") == "municipal_city_okrug"
    assert classify_level("бюджеты городских округов с внутригородским делением") == "city_okrug_div"
    assert classify_level("бюджеты внутригородских муниципальных образований городов федерального значения") == "intracity_mo"
    assert classify_level("бюджет территориального государственного внебюджетного фонда") == "tgvf"
    assert classify_level("ИТОГО") == "total"
    assert classify_measure("На конец отчетного периода / бюджет субъекта") == "end_balance"
    assert classify_measure("бюджет субъекта") is None


def test_territory_from_member_name():
    assert territory_from_member_name("01_01.09.2026_0503317.XLS") == "01"
    assert territory_from_member_name("СВОД_01.01.2019_0503317.XLS") == "95"
    assert territory_from_member_name("Mes_428m_01.html") is None


def test_territory_name_variants():
    assert territory_from_member_name("0503317-3_FO_001-2582480.XLS") == "01"
    assert territory_from_member_name("0503320-5_FO_096-2552667.XLS") == "96"
    assert territory_from_member_name("0503317_SVOD_01012017_Y.xls") == "95"
    assert territory_from_member_name("0503317-3_ROSKAZNA_1_17_01012018.xls") is None


def test_combined_settlements_level():
    assert classify_level("Исполнено / бюджеты городских и сельских поселений") == "settlement"
    assert classify_level("бюджеты сельских поселений") == "rural_settlement"
    assert classify_level("бюджеты городских поселений") == "urban_settlement"


def test_chess_and_summary_name_variants():
    from roskazna_kbs.chess import is_chess_member, parse_member_name

    assert parse_member_name("x.zip/0503317-ROSKAZNA_2_22_01092026.xls") == {
        "section": "expense", "graph": 22, "report_date": "2026-09-01"}
    assert parse_member_name("Доходы.zip/гр14_0503317_01012015.xls")["section"] == "income"
    assert parse_member_name("Расходы.zip/Расходы/14_0503317_Р.xls", "2014-01-01") == {
        "section": "expense", "graph": 14, "report_date": "2014-01-01"}
    assert is_chess_member("Источники.zip/Источники/22_0503317_И.xls")
    assert not is_chess_member("0503317/0503317-3_FO_014-927393.XLS")
    assert territory_from_member_name("0503323_010114.xls") == "95"
    assert territory_from_member_name("0503317_01012014.xls") == "95"


def test_old_chess_and_svod_names():
    from roskazna_kbs.chess import parse_member_name

    assert parse_member_name("Доходы.rar/Доходы/Гр.19_0503317_Y.xls", "2013-01-01") == {
        "section": "income", "graph": 19, "report_date": "2013-01-01"}
    assert territory_from_member_name("0503320_сводный.xls") == "95"
    assert territory_from_member_name("0503317)сводный.xls") == "95"


def test_activity_dimension():
    from roskazna_kbs.dims import classify_activity

    h = "На начало года / консолидированный бюджет субъекта Российской Федерации / итого"
    assert classify_level(h) == "cons_subject" and classify_activity(h) == "total"
    h2 = "На начало года / бюджет субъекта Российской Федерации / деятельность / приносящая доход"
    assert classify_level(h2) == "subject" and classify_activity(h2) == "income_generating"
    assert classify_activity("Исполнено / бюджеты городских округов") is None
    assert classify_level("ИТОГО") == "total"


def test_era_chess_names_and_codes():
    from roskazna_kbs.chess import parse_member_name, split_indicator

    assert parse_member_name("2007/Otch3/M8/отчет за август 2007.rar/428 за август 2007 года доходы гр.1.1.xls") == {
        "section": "income", "graph": 101, "report_date": "2007-09-01"}
    assert parse_member_name("2007/Otch3/M12/отчет за декабрь 2007.rar/расходы/гр12.xls") == {
        "section": "expense", "graph": 12, "report_date": "2008-01-01"}
    ind = split_indicator("000 101 01000 00 0000 110 010")
    assert (ind["line_code"], ind["admin_code"], ind["classification_code"]) == ("010", "000", "10101000000000110")


def test_svod_before_numeric_prefix():
    assert territory_from_member_name("428 свод год 2011.xls") == "95"
    assert territory_from_member_name("002030.xls") == "30"


def test_monthly_name_variants_2008_2019():
    from roskazna_kbs.chess import parse_member_name

    assert territory_from_member_name("0503317_01_01072012_M.XLS") == "01"
    assert territory_from_member_name("0503317_SVOD_01072018_M.xls") == "95"
    assert parse_member_name("M6/июнь 2008 доходы.rar/июнь 2008 доходы 16 гр.xls") == {
        "section": "income", "graph": 16, "report_date": "2008-07-01"}
    assert parse_member_name("M6/Июнь 2011 источники.rar/Июнь 2011 источники гр 14.xls") == {
        "section": "sources", "graph": 14, "report_date": "2011-07-01"}
    assert parse_member_name("Доходы(ver1).zip/Доходы/гр.14_0503317_01072014_Д.xls") == {
        "section": "income", "graph": 14, "report_date": "2014-07-01"}


def test_chess_generic_detection():
    from roskazna_kbs.chess import generic_meta, looks_like_chess, split_indicator

    rows = [["Выборка из форм отчетности"], [], ["Исполнено: бюджет субъекта РФ"],
            ["Наименование источника данных", "*** 9000 0000 00 0000 000 500 ", "000 0100 0000 00 0000 000 520 ИСТОЧНИКИ"],
            ["Исполнено: бюджет субъекта РФ", "Исполнено: бюджет субъекта РФ"],
            ["Ф.428 Рз.3 Гр.14", "Ф.428 Рз.3 Гр.14"],
            ["Все источники", -157.0, -42.0], ["Российская Федерация", -157.0, -42.0],
            ["1-Центральный федеральный округ", -10.0, 2.0]]
    wb = Workbook(path=None, fmt="xls", sheets=[Sheet(name="1", index=0, hidden=False, rows=rows)])
    assert looks_like_chess(wb)
    assert generic_meta("M1/отчет за январь 2008.rar/январь 2008 ичсточники 14 гр .xls", wb, "2008-02-01") == {
        "section": "sources", "graph": 14, "report_date": "2008-02-01"}
    assert generic_meta("Доходы.rar/Доходы/1_23_0503317.xls", wb, "2013-12-01")["graph"] == 23
    ind = split_indicator("*** 9000 0000 00 0000 000 500")
    assert (ind["line_code"], ind["admin_code"]) == ("500", None)


def test_code_00_is_summary():
    assert territory_from_member_name("00_01.03.2020_0503317.XLS") == "95"
    assert classify_level("Утверждено на год субъекта Российской Федерации") == "subject"
    assert classify_level("Исполнено / Консолидированный бюджет") == "cons_subject"
